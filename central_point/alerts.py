"""WARNINGS: what the driver must be told right now. Deterministic, instant, one fixed line each.

Two sources:
  road users   attention "missed" (a road user that needed a look and did not get one in time)
               -> one warning per object and zone
  the driver   driver_state detections (the driver monitor decides when they start):
               EYES_CLOSED  once it has lasted EYES_CLOSED_VOICE_S, then every EYES_CLOSED_REPEAT_S
               PHONE_USE    at the start, then every ATTENTION_REPEAT_S while it lasts
               DISTRACTED   (looking up / down) the same; both stay quiet while the eyes-closed line repeats

priority: eyes closed 3 > road user 2 > phone / looking away 1. A warning message:
  {"type": "warning", "timestamp", "t_s", "kind", "category", "priority", "repeat", "say",
   "object": {"object_id", "type", "zone", "distance_m"} | null,
   "detection": {"id", "type", "duration_s"} | null}
kind: road_user, eyes_closed, phone, looking_away. category (for the coach's patterns): left, right, ahead,
behind, eyes_closed, phone, looking_away. t_s: seconds since simulation_started (null outside a run).

No networking and no clock in here: every method takes `now` (time.monotonic()).
"""
import os
import time

EYES_CLOSED_VOICE_S = float(os.environ.get("EYES_CLOSED_VOICE_S", "1.0"))
EYES_CLOSED_REPEAT_S = float(os.environ.get("EYES_CLOSED_REPEAT_S", "3.0"))
ATTENTION_REPEAT_S = float(os.environ.get("ATTENTION_REPEAT_S", "5.0"))
DRIVER_FRESH_S = 1.0          # older driver_state: the camera is gone, no driver warnings

PRIORITY = {"eyes_closed": 3, "road_user": 2, "phone": 1, "looking_away": 1}

# ---- words shared with runlog.py -----------------------------------------------------------------
NAME = {"car": "Car", "truck": "Truck", "bicycle": "Bike", "motorcycle": "Motorbike", "pedestrian": "Pedestrian"}
A_TYPE = {"car": "a car", "truck": "a truck", "bicycle": "a bicycle", "motorcycle": "a motorcycle",
          "pedestrian": "a pedestrian"}
# zone -> (spoken warning with {N}, place for the coach's log)
ZONE_WORDS = {
    "front_left": ("{N} ahead on your left!", "ahead on the left"),
    "front_center": ("{N} ahead of you!", "ahead in our lane"),
    "front_right": ("{N} ahead on your right!", "ahead on the right"),
    "side_left": ("{N} in your left blind spot!", "in the left blind spot"),
    "side_right": ("{N} in your right blind spot!", "in the right blind spot"),
    "mirror_left": ("{N} coming up on your left!", "coming up in the left mirror"),
    "mirror_center": ("{N} right behind you!", "right behind us (rear mirror)"),
    "mirror_right": ("{N} coming up on your right!", "coming up in the right mirror"),
}
ZONE_CATEGORY = {"front_left": "left", "side_left": "left", "mirror_left": "left",
                 "front_right": "right", "side_right": "right", "mirror_right": "right",
                 "front_center": "ahead", "mirror_center": "behind"}
DRIVER_LINES = {
    "eyes_closed": "Open your eyes! Watch the road!",
    "phone": "Put the phone down! Watch the road!",
    "looking_down": "Stop looking down! Watch the road!",
    "looking_up": "Eyes back on the road!",
}
DETECTION_KIND = {"EYES_CLOSED": "eyes_closed", "PHONE_USE": "phone", "DISTRACTED": "looking_away"}


def place(zone):
    return ZONE_WORDS[zone][1] if zone in ZONE_WORDS else "near the car"


def _number(v):
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


class Alerts:
    def __init__(self):
        self.active = []          # the driver monitor's active detections (dicts), latest driver_state
        self.driver_at = None     # when the latest driver_state arrived
        self.said = {}            # detection id -> when its line was last said

    # ---- input ----

    def on_driver_state(self, msg, now):
        dets = msg.get("detections")
        self.active = [d for d in (dets if isinstance(dets, list) else [])
                       if isinstance(d, dict) and d.get("type") in DETECTION_KIND
                       and d.get("status") != "ENDED" and isinstance(d.get("id"), str)]
        self.driver_at = now

    def driver_problems(self, now):
        """The active detections, or [] when the driver monitor has been silent too long."""
        if self.driver_at is None or now - self.driver_at > DRIVER_FRESH_S:
            return []
        return self.active

    # ---- output ----

    def on_attention(self, ev, t_s):
        """A road-user warning for an attention "missed" event, else None."""
        if ev.get("event") != "attention_event" or ev.get("kind") != "missed" or ev.get("zone") not in ZONE_WORDS:
            return None
        zone, kind = ev["zone"], "road_user"
        return self._warning(kind, ZONE_CATEGORY[zone], False,
                             ZONE_WORDS[zone][0].format(N=NAME.get(ev.get("type"), "Something")), t_s,
                             obj={"object_id": ev.get("object_id"), "type": ev.get("type"), "zone": zone,
                                  "distance_m": ev.get("distance_m")})

    def update(self, now, t_s):
        """The driver warnings due now (called ~20 times per second)."""
        out = []
        active = self.driver_problems(now)
        ids = {d["id"] for d in active}
        self.said = {i: at for i, at in self.said.items() if i in ids}   # ended episodes: forget
        eyes_closed_talking = False
        for d in sorted(active, key=lambda d: d["type"] != "EYES_CLOSED"):   # eyes closed first
            kind = DETECTION_KIND[d["type"]]
            dur = _number(d.get("duration_s"))
            last = self.said.get(d["id"])
            if kind == "eyes_closed":
                if dur < EYES_CLOSED_VOICE_S:
                    continue                                   # a long blink: the dashboard shows it
                eyes_closed_talking = True
                due = last is None or now - last >= EYES_CLOSED_REPEAT_S
                line = DRIVER_LINES["eyes_closed"]
            else:
                if eyes_closed_talking:
                    continue
                due = last is None or now - last >= ATTENTION_REPEAT_S
                if kind == "phone":
                    line = DRIVER_LINES["phone"]
                else:
                    details = d.get("details")
                    up = isinstance(details, dict) and details.get("zone") == "UP"
                    line = DRIVER_LINES["looking_up" if up else "looking_down"]
            if due:
                self.said[d["id"]] = now
                out.append(self._warning(kind, kind, last is not None, line, t_s,
                                         det={"id": d["id"], "type": d["type"], "duration_s": round(dur, 1)}))
        return out

    @staticmethod
    def _warning(kind, category, repeat, say, t_s, obj=None, det=None):
        return {"type": "warning", "timestamp": time.time(), "t_s": t_s, "kind": kind, "category": category,
                "priority": PRIORITY[kind], "repeat": repeat, "say": say, "object": obj, "detection": det}
