"""central_point: the WebSocket hub every part connects to (one port). It only routes messages and runs the
clocks; the work is done by the modules:

  sim.py        parses the simulator's messages                 ("event" field)
  camera.py     the driver's gaze region from driver_state      ("type": "driver_state")
  attention.py  did the driver look at each road user in time?  (attention_event / _summary)
  alerts.py     the warnings to give right now                  (warning)
  runlog.py     what happened in this run, as text for the LLM
  coach.py      when to ask the LLM for a tip or a debrief       (coach_request)

Who sends / gets what:
  simulator       -> calibration_started, simulation_started /    <- gaze_update (GAZE_HZ, only during a run:
                     _tick / _ended / _reset                         simulation_started to _ended)
  driver monitor  -> driver_state (~30/s)                         <- recenter_gaze (at calibration_started: set
                                                                     the straight-ahead look again)
  dashboard       -> {"type": "hello", "role": "dashboard"}       <- gaze_update, driver_state (DASHBOARD_HZ),
                                                                     simulation_started / _ended, attention_event /
                                                                     _summary, chat_message (every warning, tip and
                                                                     debrief, plus any client's chat_message)
  safety_ai       -> {"type": "hello", "role": "safety_ai"},      <- simulation_started / _ended / _reset, warning
                     coach_result (the LLM's tip / debrief)          (speaks it), coach_request (asks the LLM); its
                                                                     coach_result goes to the dashboard
Outside a run (before GO, after simulation_ended) nothing new is sent but the end-of-drive debrief (and a tip that
was already being written): no gaze_update, no driver_state, no warnings, no new tips.

Environment variables: HOST (default localhost; 0.0.0.0 in Docker), PORT (8765), GAZE_HZ (10), DASHBOARD_HZ (10),
CAMERA_TIMEOUT_S (1.0), FRONT_SPLIT_DEG (7), EYES_CLOSED_VOICE_S / EYES_CLOSED_REPEAT_S / ATTENTION_REPEAT_S
(alerts.py), LOG_LEVEL (INFO prints every simulator message, warning, coach request / result and every CHANGE of
the gaze_update sent; DEBUG also every gaze_update and every camera message).
"""
import asyncio
import json
import logging
import os
import time

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import alerts
import attention
import camera
import coach
import runlog
import sim

HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8765"))
GAZE_HZ = float(os.environ.get("GAZE_HZ", "10"))                    # gaze_update messages per second
DASHBOARD_HZ = float(os.environ.get("DASHBOARD_HZ", "10"))          # driver_state to the dashboard per second
CAMERA_TIMEOUT_S = float(os.environ.get("CAMERA_TIMEOUT_S", "1.0"))  # older camera data = no camera
FRONT_SPLIT_DEG = float(os.environ.get("FRONT_SPLIT_DEG", "7"))      # |yaw| beyond this: front_left/right
TICK_HZ = 20                                                         # look deadlines, warnings, coach

log = logging.getLogger("server")
HELLO_ROLES = {"dashboard", "safety_ai"}
NO_GAZE_UPDATE = {"camera", "chat", "safety_ai"}   # roles that don't get gaze_update

clients = {}                  # ws -> "simulator" | "camera" | "dashboard" | "safety_ai" | "chat" | None (unknown)
latest_driver_state = None    # the last driver_state text, as received (for the dashboard)
run_started_raw = None        # the running run's simulation_started text (for a dashboard that connects later)
watch = attention.Attention()
warner = alerts.Alerts()
run = runlog.RunLog()
coaching = coach.Coach()


def chat(text):
    return json.dumps({"type": "chat_message", "role": "assistant", "text": text})


# ---------------------------------------------------------------------------------------- routing
def route(raw, now=None):
    """One received message. Returns (role, out): role is the sender's role or None (message ignored); out is
    a list of (audience, text) to send now, audience "dashboard" or "safety_ai"."""
    global latest_driver_state
    now = time.monotonic() if now is None else now
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("message ignored: not valid JSON text")
        return None, []
    if not isinstance(msg, dict):
        log.warning("message ignored: expected a JSON object")
        return None, []

    kind = msg.get("type")
    if kind == "driver_state":
        camera.handle(msg)
        warner.on_driver_state(msg, now)
        run.on_driver_state(msg, now)
        latest_driver_state = raw
        return "camera", []
    if kind == "hello" and msg.get("role") in HELLO_ROLES:
        return msg["role"], []
    if kind == "coach_result":
        return "safety_ai", coach_result(msg, now)
    if kind == "chat_message":   # any client may put a line into the dashboard's chat
        if msg.get("role") not in ("user", "assistant") or not isinstance(msg.get("text"), str):
            log.warning("message ignored: chat_message needs role user/assistant and a text")
            return "chat", []
        return "chat", [("dashboard", raw)]
    if "event" in msg:
        try:
            sim.handle(msg)
        except ValueError as e:
            log.warning("message ignored: %s", e)
            return "simulator", []
        return "simulator", simulator_event(msg["event"], raw, now)
    log.warning("message ignored: not a driver_state, hello, chat_message, coach_result or simulator event")
    return None, []


def simulator_event(event, raw, now):
    global run_started_raw
    region = current_region(now)
    if event == "calibration_started":   # the driver looks at the screen centre: the camera's new 0,0
        text = json.dumps({"type": "recenter_gaze", "timestamp": time.time()})
        log.info("SENT to the camera %s", text)
        return [("camera", text)]
    if event == "simulation_reset":      # a new simulation is being prepared: the voice stops talking
        return [("safety_ai", raw)]
    if event == "simulation_started":
        run.start(now)
        coaching.start(now)
        run_started_raw = raw
        return [("dashboard", raw), ("safety_ai", raw)] + attention_out(watch.start(now), now)
    if event == "simulation_tick":
        if not run.running:   # e.g. the server was restarted in the middle of a run
            run.start(now)
            coaching.start(now)
        return attention_out(watch.on_tick(now, list(sim.world.objects.values()), region), now)
    if event == "simulation_ended":
        # safety_ai gets the end before the debrief request: it finishes the tips, then only the debrief comes
        out = attention_out(watch.end(now, region), now) + [("dashboard", raw), ("safety_ai", raw)]
        run_started_raw = None
        req = coaching.debrief(now, run.end(now))
        if req:
            out.append(("safety_ai", coach_request(req)))
        return out
    return []


def attention_out(msgs, now):
    """attention messages -> the dashboard, the run log, the coach, and a warning for each miss."""
    out = []
    for m in msgs:
        out.append(("dashboard", json.dumps(m)))
        run.on_attention(m)
        coaching.on_attention(m)
        w = warner.on_attention(m, run.t_s(now))
        if w:
            out += warning_out(w, now)
    return out


def warning_out(w, now):
    """A warning -> safety_ai speaks it; the dashboard's chat shows it (not the repeats)."""
    coaching.on_warning(w, now)
    run.on_warning(w)
    log.log(logging.DEBUG if w["repeat"] else logging.INFO, "WARNING p%d %s: %s", w["priority"], w["kind"], w["say"])
    out = [("safety_ai", json.dumps(w))]
    if not w["repeat"]:
        out.append(("dashboard", chat(w["say"])))
    return out


def coach_request(req):
    log.info("COACH REQUEST %s:\n%s", req["id"], req["report"])
    return json.dumps(req)


def coach_result(msg, now):
    """safety_ai's answer to a coach_request -> the run log (tips given) and the dashboard's chat."""
    say, kind = msg.get("say"), msg.get("kind")
    if kind not in ("tip", "debrief") or not isinstance(say, str) or not say.strip():
        log.info("COACH %s: nothing to say", msg.get("request_id"))
        return []
    if kind == "tip":
        run.on_tip(say.strip(), now)
    log.info("COACH %s %s: %s", kind.upper(), msg.get("request_id"), say.strip())
    out = [("dashboard", chat(say.strip()))]
    if kind == "debrief":   # the analysis' short screen version: the verdict and the next drive's focus
        verdict, focus = msg.get("verdict"), msg.get("next_focus")
        if isinstance(verdict, str) and isinstance(focus, str) and focus.strip():
            out.append(("dashboard", chat(f"Drive: {verdict}. Next drive: {focus.strip()}")))
        log.info("COACH DEBRIEF details: %s", json.dumps({k: msg.get(k) for k in ("verdict", "mistakes", "went_well")}))
    return out


# ---------------------------------------------------------------------------------------- gaze
def decide_gaze(now=None):
    """Returns (status, region). region is None unless status is "ok"."""
    d = camera.driver
    now = time.monotonic() if now is None else now
    if d.received_at is None or now - d.received_at > CAMERA_TIMEOUT_S:
        return "no_gaze_client", None
    if d.face_detected is False:
        return "no_face", None
    # A blink: the driver monitor keeps the last gaze zone (up to its blink_hold_s, 0.5 s) while the eyes are
    # CLOSED, so the driver still looks there. Only a longer closure (zone UNKNOWN) is "eyes_closed".
    if d.eyes == "CLOSED" and d.zone in (None, "UNKNOWN"):
        return "eyes_closed", None
    if d.calibration == "CALIBRATING":
        return "calibrating", None
    return "ok", camera.region(d.zone, d.yaw_deg, FRONT_SPLIT_DEG)


def current_region(now=None):
    """The gaze region the attention check uses: None unless the camera status is "ok"."""
    status, region = decide_gaze(now)
    return region if status == "ok" else None


# ---------------------------------------------------------------------------------------- sending
async def broadcast(text, wants):
    """Send one text to every client whose role passes `wants(role)`. Returns how many got it."""
    sent = 0
    for ws, role in list(clients.items()):
        if wants(role):
            try:
                await ws.send(text)
                sent += 1
            except Exception:   # a failed send must never break the server
                pass
    return sent


async def deliver(out):
    for audience, text in out:
        await broadcast(text, lambda role, a=audience: role == a)


# ---------------------------------------------------------------------------------------- loops
async def handler(ws: ServerConnection):
    clients[ws] = None
    try:
        async for raw in ws:
            role, out = route(raw)
            if role in ("simulator", "chat"):
                log.info("RECEIVED %s", raw)    # exactly as received, one JSON per line
            elif role == "camera":
                log.debug("RECEIVED %s", raw)   # ~30 Hz: only with LOG_LEVEL=DEBUG
            await deliver(out)
            if role and clients.get(ws) is None:
                clients[ws] = role
                log.info("%s connected", role)
                if role == "dashboard" and run.running and run_started_raw:   # joined during a run
                    await ws.send(run_started_raw)
    except ConnectionClosed:
        pass
    role = clients.pop(ws, None)
    if role:
        log.info("%s disconnected", role)


async def tick_loop():
    """TICK_HZ times per second: look deadlines and dwell (between simulator ticks), driver warnings, coach."""
    while True:
        await asyncio.sleep(1.0 / TICK_HZ)
        now = time.monotonic()
        out = attention_out(watch.update(now, current_region(now)), now)
        if run.running:   # driver warnings only during a run: none before GO, none over the end-of-drive analysis
            for w in warner.update(now, run.t_s(now)):
                out += warning_out(w, now)
        calm = not warner.driver_problems(now) and not watch.has_running_timer()
        req = coaching.update(now, calm, run)
        if req:
            out.append(("safety_ai", coach_request(req)))
        await deliver(out)


async def send_dashboard_loop():
    """Send the latest driver_state DASHBOARD_HZ times per second to the dashboards (the camera sends
    ~30/s, more than a page needs), only during a run. Nothing is sent while no new driver_state arrived."""
    last = None
    while True:
        await asyncio.sleep(1.0 / DASHBOARD_HZ)
        if not run.running:
            continue
        text = latest_driver_state
        if text is not None and text is not last:
            await broadcast(text, lambda role: role == "dashboard")
            last = text


async def send_gaze_loop():
    """gaze_update GAZE_HZ times per second to the simulator and the dashboard, only during a run (GO to
    simulation_ended): before it the straight-ahead look is not set yet (the countdown sets it), after it the
    drive is over. Also counts the run's gaze time shares. Only changes are logged at INFO."""
    last = None
    while True:
        await asyncio.sleep(1.0 / GAZE_HZ)
        if not run.running:
            last = None
            continue
        now = time.monotonic()
        status, region = decide_gaze(now)
        run.on_gaze(now, status, region)
        text = json.dumps({"event": "gaze_update", "timestamp": time.time(), "status": status, "region": region})
        sent = await broadcast(text, lambda role: role not in NO_GAZE_UPDATE)
        if sent:                    # nothing is logged while no simulator is connected
            if (status, region) != last:
                log.info("SENT %s", text)
            else:
                log.debug("SENT %s", text)
            last = (status, region)
        else:
            last = None


async def main():
    async with serve(handler, HOST, PORT):
        log.info("listening on ws://%s:%d (gaze_update %g Hz, dashboard %g Hz)", HOST, PORT, GAZE_HZ, DASHBOARD_HZ)
        await asyncio.gather(send_gaze_loop(), send_dashboard_loop(), tick_loop())   # run until Ctrl+C


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except OSError as e:  # most often: the port is already used (another server is running)
        log.error("could not start on port %d: %s", PORT, e)
        raise SystemExit(1) from None
