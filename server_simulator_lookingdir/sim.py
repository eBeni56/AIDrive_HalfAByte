"""Input from the SIMULATOR: parse its messages and keep the current state in `world`.

Messages:
  simulation_started  {event, timestamp}
  simulation_tick     {event, timestamp, objects: [{object_id, type, x_px, y_px}, ...]}   10 Hz
  simulation_ended    {event, timestamp}
"""
import logging
from dataclasses import dataclass

OBJECT_TYPES = {"car", "truck", "bicycle", "motorcycle", "pedestrian"}

log = logging.getLogger("sim")


@dataclass(frozen=True)
class SimObject:
    object_id: str
    type: str
    x_px: int   # + right of the ego car's center
    y_px: int   # + ahead of the ego car's center, - behind


class World:
    """What the simulator last reported."""

    def __init__(self):
        self.objects = {}   # object_id -> SimObject: the objects on the map right now


world = World()


def _number(d, key):
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float):
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


def handle(msg):
    """Handle one simulator message (already a dict) and update `world`.
    Raises ValueError if it is malformed; a malformed message changes nothing."""
    event = msg.get("event")
    _number(msg, "timestamp")   # required in every message (the server itself uses its own clock)

    if event == "simulation_started":
        world.objects = {}
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
        world.objects = objects
        log.debug("tick: %s", list(objects.values()))

    elif event == "simulation_ended":
        world.objects = {}
        log.info("simulation ended")

    else:
        raise ValueError(f"unknown event {event!r}")
