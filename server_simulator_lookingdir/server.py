"""WebSocket server: receives the driving simulator's messages and parses them.

Messages (from the simulator):
  simulation_started  {event, timestamp}
  simulation_tick     {event, timestamp, objects: [{object_id, type, x_px, y_px}, ...]}   10 Hz
  simulation_ended    {event, timestamp}

Environment variables: HOST (default localhost; 0.0.0.0 in Docker), PORT (default 8765),
LOG_LEVEL (INFO; DEBUG also prints every tick).
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

HOST = os.environ.get("HOST", "localhost")
PORT = int(os.environ.get("PORT", "8765"))
OBJECT_TYPES = {"car", "truck", "bicycle", "motorcycle", "pedestrian"}

log = logging.getLogger("server")


@dataclass(frozen=True)
class SimObject:
    object_id: str
    type: str
    x_px: int   # + right of the ego car's center
    y_px: int   # + ahead of the ego car's center, - behind


class World:
    """What the simulator last reported."""
    running = False
    timestamp = None   # timestamp of the latest message
    objects = {}       # object_id -> SimObject: the objects visible right now


world = World()


def _number(d, key):
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{key!r} must be a number, got {v!r}")
    return v


def _string(d, key):
    v = d.get(key)
    if not isinstance(v, str) or not v:
        raise ValueError(f"{key!r} must be a non-empty string, got {v!r}")
    return v


def parse_object(item):
    if not isinstance(item, dict):
        raise ValueError(f"object entry must be a JSON object, got {item!r}")
    obj_type = _string(item, "type")
    if obj_type not in OBJECT_TYPES:
        raise ValueError(f"unknown object type {obj_type!r}")
    return SimObject(_string(item, "object_id"), obj_type,
                     round(_number(item, "x_px")), round(_number(item, "y_px")))


def handle(raw):
    """Parse one message and update `world`. Raises ValueError if it is malformed;
    a malformed message changes nothing."""
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("not valid JSON text") from None
    if not isinstance(msg, dict):
        raise ValueError("expected a JSON object")

    event = msg.get("event")
    ts = _number(msg, "timestamp")

    if event == "simulation_started":
        world.running, world.timestamp, world.objects = True, ts, {}
        log.info("simulation started")

    elif event == "simulation_tick":
        items = msg.get("objects")
        if not isinstance(items, list):
            raise ValueError(f"'objects' must be a list, got {items!r}")
        objects = {}
        for obj in map(parse_object, items):
            if obj.object_id in objects:
                raise ValueError(f"duplicate object_id {obj.object_id!r}")
            objects[obj.object_id] = obj
        for oid in objects.keys() - world.objects.keys():
            o = objects[oid]
            log.info("%s (%s) visible at x_px=%d y_px=%d", oid, o.type, o.x_px, o.y_px)
        for oid in world.objects.keys() - objects.keys():
            log.info("%s no longer visible", oid)
        world.running, world.timestamp, world.objects = True, ts, objects
        log.debug("tick: %s", list(objects.values()))

    elif event == "simulation_ended":
        world.running, world.timestamp, world.objects = False, ts, {}
        log.info("simulation ended")

    else:
        raise ValueError(f"unknown event {event!r}")


async def handler(ws: ServerConnection):
    log.info("simulator connected")
    try:
        async for raw in ws:
            try:
                handle(raw)
            except ValueError as e:
                log.warning("message ignored: %s", e)
    except ConnectionClosed:
        pass
    log.info("simulator disconnected")


async def main():
    async with serve(handler, HOST, PORT):
        log.info("listening on ws://%s:%d", HOST, PORT)
        await asyncio.Future()  # run until Ctrl+C


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
