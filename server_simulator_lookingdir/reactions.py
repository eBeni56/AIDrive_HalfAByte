"""REACTIONS: when the AI copilot should SAY something, and the line it says (spoken at once, no LLM wait).

The server decides; the copilot (copilot/ai-server.py, connected with {"type": "hello", "role": "copilot"})
only speaks what it gets. Three kinds, most urgent first:

  priority 3  eyes_closed           EYES_CLOSED has lasted EYES_CLOSED_VOICE_S (default 1.0 s = when the
                                    camera makes it DANGER; shorter closures are long blinks - the dashboard
                                    shows them, the voice stays quiet). "Open your eyes! Pay attention to the road!", again every
                                    EYES_CLOSED_REPEAT_S while the eyes stay closed.
  priority 2  missed_object         attention.py: a car / bike / person entered a zone around the car and
                                    the driver did NOT look there in time ("missed"). "Car to the left of
                                    you!" - once per object and zone. Looked in time ("noticed"): silence.
  priority 1  not_paying_attention  PHONE_USE: "Put the phone down! Pay attention to the road!"
                                    DISTRACTED looking down: "Stop looking down! Pay attention to the road!"
                                    DISTRACTED looking up: "Pay attention to the road!"
                                    At once when it starts, again every ATTENTION_REPEAT_S while it lasts;
                                    quiet while the eyes-closed line is being repeated.

No networking and no clock in here: every method takes `now` (server time.monotonic() seconds) and returns
the copilot_alert messages (dicts) to send; server.py sends them. Tests pass a fake `now`.

  copilot_alert {event, timestamp, kind, priority, say, repeat, detection, object}
    detection  {id, type, severity, duration_s} of the camera detection (eyes_closed, not_paying_attention)
    object     {object_id, type, zone, side} of the simulator object (missed_object)

Environment variables: EYES_CLOSED_VOICE_S (1.0), EYES_CLOSED_REPEAT_S (3.0), ATTENTION_REPEAT_S (5.0).
"""
import logging
import os
import time

log = logging.getLogger("reactions")

EYES_CLOSED_VOICE_S = float(os.environ.get("EYES_CLOSED_VOICE_S", "1.0"))
EYES_CLOSED_REPEAT_S = float(os.environ.get("EYES_CLOSED_REPEAT_S", "3.0"))
ATTENTION_REPEAT_S = float(os.environ.get("ATTENTION_REPEAT_S", "5.0"))

PRIORITY = {"eyes_closed": 3, "missed_object": 2, "not_paying_attention": 1}

EYES_CLOSED_LINE = "Open your eyes! Pay attention to the road!"
PHONE_LINE = "Put the phone down! Pay attention to the road!"
LOOKING_DOWN_LINE = "Stop looking down! Pay attention to the road!"
ATTENTION_LINE = "Pay attention to the road!"

# simulator object type -> how the copilot calls it
THING = {"car": "Car", "truck": "Truck", "bicycle": "Bike", "motorcycle": "Motorbike", "pedestrian": "Person"}
# attention.py zone -> (where it is, side)
WHERE = {
    "front_left": ("ahead on your left", "left"),
    "front_center": ("in front of you", "ahead"),
    "front_right": ("ahead on your right", "right"),
    "side_left": ("to the left of you", "left"),
    "side_right": ("to the right of you", "right"),
    "mirror_left": ("coming up on your left", "left"),
    "mirror_center": ("right behind you", "behind"),
    "mirror_right": ("coming up on your right", "right"),
}


def object_line(obj_type, zone):
    """"Car to the left of you!" for a missed object."""
    where = WHERE.get(zone, ("near you", None))[0]
    return f"{THING.get(obj_type, 'Something')} {where}!"


def attention_line(alert):
    if alert["type"] == "PHONE_USE":
        return PHONE_LINE
    if (alert.get("details") or {}).get("zone") == "DOWN":
        return LOOKING_DOWN_LINE
    return ATTENTION_LINE


class Reactions:
    """What has been said (per detection episode / object) and when."""

    def __init__(self):
        self.queued = []          # missed_object alerts waiting for the next update()
        self.eyes_said = {}       # EYES_CLOSED episode id -> when it was last said
        self.attention_said = {}  # DISTRACTED / PHONE_USE episode id -> when it was last said
        self.attention_last = None

    def reset(self):
        self.__init__()

    # ---- simulator side (attention.py events) ----

    def on_attention(self, now, events):
        """attention_event / attention_summary messages from attention.py: queue a line for every "missed"."""
        for ev in events:
            if ev.get("event") != "attention_event" or ev.get("kind") != "missed":
                continue
            zone = ev.get("zone")
            obj = {"object_id": ev.get("object_id"), "type": ev.get("type"), "zone": zone,
                   "side": WHERE.get(zone, (None, None))[1]}
            self.queued.append(self._msg("missed_object", object_line(ev.get("type"), zone), False, obj=obj))

    # ---- camera side (the active alerts of camera.py), ~10 Hz ----

    def update(self, now, active):
        """`active`: camera.driver.active (alert dicts: id, type, category, severity, duration_s, details) or
        [] when there is no fresh camera data. Returns everything to say now, most urgent first."""
        out, self.queued = self.queued, []
        eyes = [a for a in active if a["category"] == "eyes_closed"]
        att = [a for a in active if a["category"] == "not_paying_attention"]
        ids = {a["id"] for a in active}
        self.eyes_said = {k: v for k, v in self.eyes_said.items() if k in ids}
        self.attention_said = {k: v for k, v in self.attention_said.items() if k in ids}

        speaking_eyes = False
        for a in eyes:
            if (a.get("duration_s") or 0.0) < EYES_CLOSED_VOICE_S:
                continue
            speaking_eyes = True
            last = self.eyes_said.get(a["id"])
            if last is None or now - last >= EYES_CLOSED_REPEAT_S:
                self.eyes_said[a["id"]] = now
                out.append(self._msg("eyes_closed", EYES_CLOSED_LINE, last is not None, det=a))

        if att and not speaking_eyes:
            top = next((a for a in att if a["type"] == "PHONE_USE"), att[0])   # the phone is the clearer message
            last = self.attention_said.get(top["id"])
            if last is None or now - last >= ATTENTION_REPEAT_S:
                # a new episode right after another one was just said (e.g. PHONE_USE starting while
                # DISTRACTED is going on): wait for the repeat interval instead of talking twice in a row
                recent = self.attention_last is not None and now - self.attention_last < ATTENTION_REPEAT_S / 2
                if last is not None or not recent:
                    for a in att:
                        self.attention_said[a["id"]] = now
                    self.attention_last = now
                    out.append(self._msg("not_paying_attention", attention_line(top), last is not None, det=top))
        out.sort(key=lambda m: -m["priority"])
        return out

    # ---- messages ----

    def _msg(self, kind, say, repeat, det=None, obj=None):
        m = {"event": "copilot_alert", "timestamp": time.time(), "kind": kind, "priority": PRIORITY[kind],
             "say": say, "repeat": repeat,
             "detection": {k: det.get(k) for k in ("id", "type", "severity", "duration_s")} if det else None,
             "object": obj}
        log.info("SAY [%s%s] %s", kind, " again" if repeat else "", say)
        return m
