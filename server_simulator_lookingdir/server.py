"""WebSocket server: connects the simulator, the driver camera client and the dashboard, and tells the
simulator where the driver is looking.

  sim.py        parses the simulator's messages      (messages with an "event" field)
  camera.py     parses the camera client's messages  ("type": "driver_state")
  attention.py  did the driver look at each simulator object in time? (attention_event / _summary)
  server.py     routes each message to one of them, sends gaze_update to the simulator and the dashboard,
                and to the dashboard only: the latest driver_state (DASHBOARD_HZ), simulation_started /
                _ended, the attention messages and every chat_message

The gaze region comes from the camera client's gaze.zone (camera.region()); without a camera client
(or when it has sent nothing for CAMERA_TIMEOUT_S) the status is "no_gaze_client".

Environment variables: HOST (default localhost; 0.0.0.0 in Docker), PORT (default 8765),
GAZE_HZ (gaze_update rate, default 10), DASHBOARD_HZ (driver_state rate to the dashboard, default 10),
CAMERA_TIMEOUT_S (default 1.0), FRONT_SPLIT_DEG (default 7),
LOG_LEVEL (INFO prints every simulator message and every CHANGE of the gaze_update sent; DEBUG also
prints every gaze_update and every camera message).
"""
import asyncio
import json
import logging
import os
import time

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import attention
import camera
import sim

HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8765"))
GAZE_HZ = float(os.environ.get("GAZE_HZ", "10"))                    # gaze_update messages per second
DASHBOARD_HZ = float(os.environ.get("DASHBOARD_HZ", "10"))          # driver_state to the dashboard per second
CAMERA_TIMEOUT_S = float(os.environ.get("CAMERA_TIMEOUT_S", "1.0"))  # older camera data = no camera
FRONT_SPLIT_DEG = float(os.environ.get("FRONT_SPLIT_DEG", "7"))      # |yaw| beyond this: front_left/right
ATTENTION_HZ = 20                                                    # how often look deadlines are checked

log = logging.getLogger("server")
clients = {}   # ws -> "simulator" | "camera" | "dashboard" | "chat" | None (not known yet)
NO_GAZE_UPDATE = {"camera", "chat"}   # roles that don't get gaze_update
latest_driver_state = None            # the last driver_state text, as received (for the dashboard)
watch = attention.Attention()         # the attention state of the current simulation run


def route(raw):
    """All clients use the same port, so look at each message to see who sent it.
    Returns (role, forward): role is "simulator", "camera", "dashboard", "chat" or None (message ignored);
    forward is a list of texts to send to every dashboard now."""
    global latest_driver_state
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
        latest_driver_state = raw
        return "camera", []
    if kind == "hello" and msg.get("role") == "dashboard":
        return "dashboard", []
    if kind == "chat_message":   # from the voice assistant (speech to text / AI answer): to the dashboard
        if msg.get("role") not in ("user", "assistant") or not isinstance(msg.get("text"), str):
            log.warning("message ignored: chat_message needs role user/assistant and a text")
            return "chat", []
        return "chat", [raw]
    if "event" in msg:
        try:
            sim.handle(msg)
        except ValueError as e:
            log.warning("message ignored: %s", e)
            return "simulator", []
        return "simulator", simulator_event(msg["event"], raw)
    log.warning("message ignored: not a driver_state, hello, chat_message or simulator event")
    return None, []


def current_region():
    """The gaze region the attention check uses: None unless the camera status is "ok"."""
    status, region = decide_gaze()
    return region if status == "ok" else None


def simulator_event(event, raw, now=None):
    """Feed a (valid) simulator message to the attention check. Returns the texts for the dashboard:
    simulation_started / _ended as received, plus the attention messages (ticks themselves are not sent)."""
    now = time.monotonic() if now is None else now
    if event == "simulation_started":
        return [raw] + [json.dumps(m) for m in watch.start(now)]
    if event == "simulation_tick":
        objects = list(sim.world.objects.values())
        return [json.dumps(m) for m in watch.on_tick(now, objects, current_region())]
    if event == "simulation_ended":
        return [json.dumps(m) for m in watch.end(now, current_region())] + [raw]
    return []


async def handler(ws: ServerConnection):
    clients[ws] = None
    try:
        async for raw in ws:
            role, forward = route(raw)
            if role in ("simulator", "chat"):
                log.info("RECEIVED %s", raw)    # exactly as received, one JSON per line
            elif role == "camera":
                log.debug("RECEIVED %s", raw)   # ~30 Hz: only with LOG_LEVEL=DEBUG
            for text in forward:
                await send_to_dashboards(text)
            if role and clients[ws] is None:
                clients[ws] = role
                log.info("%s connected", role)
    except ConnectionClosed:
        pass
    role = clients.pop(ws, None)
    if role:
        log.info("%s disconnected", role)


def decide_gaze(now=None):
    """Returns (status, region). region is None unless status is "ok"."""
    d = camera.driver
    now = time.monotonic() if now is None else now
    if d.received_at is None or now - d.received_at > CAMERA_TIMEOUT_S:
        return "no_gaze_client", None
    if d.face_detected is False:
        return "no_face", None
    if d.eyes == "CLOSED":
        return "eyes_closed", None
    if d.calibration == "CALIBRATING":
        return "calibrating", None
    return "ok", camera.region(d.zone, d.yaw_deg, FRONT_SPLIT_DEG)


async def send_to_dashboards(text):
    """Send one text to every dashboard. Returns how many got it."""
    sent = 0
    for ws, role in list(clients.items()):
        if role == "dashboard":
            try:
                await ws.send(text)
                sent += 1
            except Exception:   # a failed send must never break the server
                pass
    return sent


async def send_dashboard_loop():
    """Send the latest driver_state DASHBOARD_HZ times per second to the dashboards (the camera sends
    ~30/s, more than a page needs). Nothing is sent while no new driver_state arrived."""
    last = None
    while True:
        await asyncio.sleep(1.0 / DASHBOARD_HZ)
        text = latest_driver_state
        if text is not None and text is not last:
            await send_to_dashboards(text)
            last = text


async def attention_loop():
    """Check the look deadlines and dwell ATTENTION_HZ times per second (between simulator ticks)."""
    while True:
        await asyncio.sleep(1.0 / ATTENTION_HZ)
        for m in watch.update(time.monotonic(), current_region()):
            await send_to_dashboards(json.dumps(m))


async def send_gaze_loop():
    """Send gaze_update GAZE_HZ times per second to every client that is not the camera or a chat source. The
    simulator sends nothing before GO, so it can't be recognised earlier; it gets the message anyway.
    Only changes are logged at INFO (at 10 Hz every message would flood the console)."""
    last = None
    while True:
        await asyncio.sleep(1.0 / GAZE_HZ)
        status, region = decide_gaze()
        text = json.dumps({"event": "gaze_update", "timestamp": time.time(),
                           "status": status, "region": region})
        sent = 0
        for ws, role in list(clients.items()):
            if role not in NO_GAZE_UPDATE:
                try:
                    await ws.send(text)
                    sent += 1
                except Exception:   # a failed send must never break the server
                    pass
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
        log.info("listening on ws://%s:%d (gaze_update %g Hz, dashboard %g Hz)", HOST, PORT, GAZE_HZ,
                 DASHBOARD_HZ)
        await asyncio.gather(send_gaze_loop(), send_dashboard_loop(), attention_loop())   # run until Ctrl+C


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("websockets").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except OSError as e:  # most often: port already used, e.g. simulator/test_server.py is running
        log.error("could not start on port %d: %s", PORT, e)
        raise SystemExit(1)
