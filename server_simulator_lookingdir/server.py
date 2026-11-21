"""WebSocket server: connects the simulator and the driver camera client, and tells the
simulator where the driver is looking.

  sim.py     parses the simulator's messages      (messages with an "event" field)
  camera.py  parses the camera client's messages  ("type": "driver_state")
  server.py  routes each message to one of them and sends gaze_update to the simulator

The gaze region comes from the camera client's gaze.zone (camera.region()); without a camera client
(or when it has sent nothing for CAMERA_TIMEOUT_S) the status is "no_gaze_client". Every gaze_update also
carries "driver": the driver's condition from the camera client (tired, not paying attention, alerts,
drowsiness level and history; camera.driver_block()), or null without fresh camera data.

Environment variables: HOST (default localhost; 0.0.0.0 in Docker), PORT (default 8765),
GAZE_HZ (gaze_update rate, default 30), CAMERA_TIMEOUT_S (default 1.0), FRONT_SPLIT_DEG (default 7),
RECENT_ALERT_S (default 5, see camera.py), LOG_LEVEL (INFO prints every simulator message, every alert
that starts / changes / ends, and every gaze_update whose status, region or alerts changed; DEBUG also
prints every gaze_update and every camera message).
"""
import asyncio
import json
import logging
import os
import time

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import camera
import sim

HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8765"))
GAZE_HZ = float(os.environ.get("GAZE_HZ", "30"))                    # gaze_update messages per second
CAMERA_TIMEOUT_S = float(os.environ.get("CAMERA_TIMEOUT_S", "1.0"))  # older camera data = no camera
FRONT_SPLIT_DEG = float(os.environ.get("FRONT_SPLIT_DEG", "7"))      # |yaw| beyond this: front_left/right

log = logging.getLogger("server")
clients = {}   # ws -> "simulator" | "camera" | None (not known yet)


def route(raw):
    """Both clients use the same port, so look at each message to see who sent it.
    Returns "simulator", "camera" or None (message ignored)."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("message ignored: not valid JSON text")
        return None
    if not isinstance(msg, dict):
        log.warning("message ignored: expected a JSON object")
        return None

    if msg.get("type") == "driver_state":
        camera.handle(msg)
        return "camera"
    if "event" in msg:
        try:
            sim.handle(msg)
        except ValueError as e:
            log.warning("message ignored: %s", e)
        return "simulator"
    log.warning("message ignored: neither a driver_state nor a simulator event")
    return None


async def handler(ws: ServerConnection):
    clients[ws] = None
    try:
        async for raw in ws:
            role = route(raw)
            if role == "simulator":
                log.info("RECEIVED %s", raw)    # exactly as received, one JSON per line
            elif role == "camera":
                log.debug("RECEIVED %s", raw)   # ~30 Hz: only with LOG_LEVEL=DEBUG
            if role and clients[ws] is None:
                clients[ws] = role
                log.info("%s connected", role)
    except ConnectionClosed:
        pass
    role = clients.pop(ws, None)
    if role:
        log.info("%s disconnected", role)


def camera_fresh(now):
    d = camera.driver
    return d.received_at is not None and now - d.received_at <= CAMERA_TIMEOUT_S


def decide_gaze(now=None):
    """Returns (status, region). region is None unless status is "ok"."""
    d = camera.driver
    now = time.monotonic() if now is None else now
    if not camera_fresh(now):
        return "no_gaze_client", None
    if d.face_detected is False:
        return "no_face", None
    if d.eyes == "CLOSED":
        return "eyes_closed", None
    if d.calibration == "CALIBRATING":
        return "calibrating", None
    return "ok", camera.region(d.zone, d.yaw_deg, FRONT_SPLIT_DEG)


def gaze_message(now=None):
    """The gaze_update to send now, and a key that changes when status, region or the alerts change."""
    now = time.monotonic() if now is None else now
    status, region = decide_gaze(now)
    drv = camera.driver_block(now) if camera_fresh(now) else None
    msg = {"event": "gaze_update", "timestamp": time.time(), "status": status, "region": region,
           "driver": drv}
    alert_key = tuple((a["id"], a["severity"]) for a in drv["alerts"]) if drv else None
    return msg, (status, region, alert_key)


async def send_gaze_loop():
    """Send gaze_update GAZE_HZ times per second to every client that is not the camera. The
    simulator sends nothing before GO, so it can't be recognised earlier; it gets the message anyway.
    Only changes are logged at INFO (at 30 Hz every message would flood the console)."""
    last = None
    while True:
        await asyncio.sleep(1.0 / GAZE_HZ)
        msg, key = gaze_message()
        text = json.dumps(msg)
        sent = 0
        for ws, role in list(clients.items()):
            if role != "camera":
                try:
                    await ws.send(text)
                    sent += 1
                except Exception:   # a failed send must never break the server
                    pass
        if sent:                    # nothing is logged while no simulator is connected
            if key != last:
                log.info("SENT %s", text)
            else:
                log.debug("SENT %s", text)
            last = key
        else:
            last = None


async def main():
    async with serve(handler, HOST, PORT):
        log.info("listening on ws://%s:%d (gaze_update %g Hz)", HOST, PORT, GAZE_HZ)
        await send_gaze_loop()   # runs until Ctrl+C


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
