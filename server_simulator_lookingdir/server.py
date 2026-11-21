"""WebSocket server: connects the simulator and the driver camera client, and tells the
simulator where the driver is looking.

  sim.py     parses the simulator's messages      (messages with an "event" field)
  camera.py  parses the camera client's messages  ("type": "driver_state")
  server.py  routes each message to one of them and sends gaze_update to the simulator

For now the gaze region is MOCKED: a random one of the 9 regions, once per second.

Environment variables: HOST (default localhost; 0.0.0.0 in Docker), PORT (default 8765),
LOG_LEVEL (INFO prints every simulator message and every gaze_update sent; DEBUG also prints
every camera message).
"""
import asyncio
import json
import logging
import os
import random
import time

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

import camera
import sim

HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8765"))
SEND_INTERVAL_S = 1.0   # how often the (mock) gaze_update is sent

REGIONS = ["front_left", "front_center", "front_right", "side_left", "side_right",
           "mirror_center", "mirror_left", "mirror_right", "away"]

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


def decide_gaze():
    """Returns (status, region). MOCK region: random. Status comes from the camera client when
    it has reported; region is None unless status is "ok"."""
    d = camera.driver
    if d.received_at is not None:
        if d.face_detected is False:
            return "no_face", None
        if d.eyes == "CLOSED":
            return "eyes_closed", None
        if d.calibration == "CALIBRATING":
            return "calibrating", None
    return "ok", random.choice(REGIONS)


async def send_gaze_loop():
    """Send gaze_update to every client that is not the camera. The simulator sends
    nothing before GO, so it can't be recognised earlier; it gets the message anyway."""
    while True:
        await asyncio.sleep(SEND_INTERVAL_S)
        status, region = decide_gaze()
        text = json.dumps({"event": "gaze_update", "timestamp": time.time(),
                           "status": status, "region": region})
        sent = 0
        for ws, role in list(clients.items()):
            if role != "camera":
                try:
                    await ws.send(text)
                    sent += 1
                except Exception:   # a failed send must never break the server
                    pass
        if sent:                    # nothing is logged while no simulator is connected
            log.info("SENT %s", text)


async def main():
    async with serve(handler, HOST, PORT):
        log.info("listening on ws://%s:%d", HOST, PORT)
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
