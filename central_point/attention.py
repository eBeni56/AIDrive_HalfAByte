"""ATTENTION: did the driver look at each object in time after it entered a zone around the car?

Every object is in one of 8 zones (or in none). When it enters a zone AND needs a look there, that
zone's timer starts (WINDOW_S). If the gaze region stays in ACCEPTED_LOOKS[zone] for LOOK_DWELL_S
before the timer runs out, the object is "noticed", otherwise "missed". A zone's name is also where
to look (the front_* zones: anywhere through the windshield, gaze region "front").

Needs a look (needs_look): always in a blind spot (side_*) and in our lane ahead (front_center);
anywhere else only while it is getting closer to the car or to our lane (CLOSING_M within the last
CLOSING_WINDOW_S of ticks). So an overtaking car behind us gets a timer, the same car ahead of us and
pulling away does not; a pedestrian walking toward our lane does, one walking away does not. An object
seen for the first time inside such a zone has no history yet: the decision (and its appeared /
zone_change event) waits until CLOSING_WINDOW_S of positions exist; its timer starts then.

No networking and no clock in here: every method takes `now` (server time.monotonic() seconds)
and returns the messages to send (dicts); server.py sends them to the dashboard. Tests pass a fake `now`.

attention_event kinds: appeared (an object is seen for the first time in this run), zone_change (it
moved into another zone), noticed (looked at in time; reaction_s), missed (not looked at in time).
window_s is null when there is no timer: outside every zone, or the object does not need a look
(needs_look false). distance_m: meters from the ego car's center. looking: the driver's gaze is already in an
accepted region for it (the dashboard then shows no "look there" message: nothing to warn about).

  start(now)                       simulation_started: forget everything
  on_tick(now, objects, region)    simulation_tick: zone changes (objects need object_id, type,
                                   x_px, y_px: sim.SimObject)
  update(now, region)              ~20 Hz: dwell of the current gaze region, all deadlines
  end(now, region)                 simulation_ended: last check, then attention_summary
"""
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

log = logging.getLogger("attention")


class Span(NamedTuple):
    """An interval of pixels; lo_in / hi_in say whether each end belongs to it."""
    lo: float
    hi: float
    lo_in: bool = True
    hi_in: bool = True

    def contains(self, v):
        return ((self.lo < v or (self.lo_in and v == self.lo))
                and (v < self.hi or (self.hi_in and v == self.hi)))


# ---- Tunable constants -------------------------------------------------------------------------
# Coordinates from simulation_tick: x_px + right of the ego car's center, y_px + ahead.
# 1 m = 31.43 px (lane = 110 px = 3.5 m).

# Columns. 55 = edge of the center lane; left/right include the grass beyond the road.
X_LEFT = Span(-math.inf, -55, hi_in=False)        # x < -55
X_CENTER = Span(-55, 55)                          # -55 <= x <= 55
X_RIGHT = Span(55, math.inf, lo_in=False)         # x > 55
# Rows. 71 = ego car's front/rear (4.5 m long), -157 = blind spot ends ~3 m behind the rear
# bumper, 943 = 30 m: farther than that is no zone.
Y_FRONT = Span(71, 943, lo_in=False)              # 71 < y <= 943
Y_SIDE = Span(-157, 71)                           # -157 <= y <= 71   (blind spot)
Y_MIRROR = Span(-943, -157, hi_in=False)          # -943 <= y < -157
Y_MIRROR_CENTER = Span(-943, -71, hi_in=False)    # -943 <= y < -71   (right behind the ego car)

# zone -> (x span, y span). The rectangles don't overlap; any other point is no zone (None):
# beyond 30 m, or overlapping the ego car in the center lane.
ZONES = {
    "front_left": (X_LEFT, Y_FRONT),
    "front_center": (X_CENTER, Y_FRONT),
    "front_right": (X_RIGHT, Y_FRONT),
    "side_left": (X_LEFT, Y_SIDE),          # blind spot: look fully left
    "side_right": (X_RIGHT, Y_SIDE),        # blind spot: look fully right
    "mirror_left": (X_LEFT, Y_MIRROR),
    "mirror_center": (X_CENTER, Y_MIRROR_CENTER),
    "mirror_right": (X_RIGHT, Y_MIRROR),
}

# zone -> seconds the driver has to look after the object entered it.
WINDOW_S = {
    "front_left": 0.5, "front_center": 0.5, "front_right": 0.5,
    "side_left": 1.0, "side_right": 1.0,
    "mirror_left": 2.0, "mirror_center": 2.0, "mirror_right": 2.0,
}

# zone -> gaze regions that count as looking at it. The simulator shows 4 regions: the windshield ("front"), the
# left / right mirror (looking leftish / rightish) and the rear-view mirror (a bit up). The front zones are all
# seen through the windshield. A road user beside us (blind spot, side_*) is checked by looking leftish /
# rightish (the mirror) or by turning the head fully (side_*); the mirror zones behind us need the mirror.
ACCEPTED_LOOKS = {
    "front_left": {"front"}, "front_center": {"front"}, "front_right": {"front"},
    "side_left": {"mirror_left", "side_left"}, "side_right": {"mirror_right", "side_right"},
    "mirror_left": {"mirror_left"}, "mirror_center": {"mirror_center"}, "mirror_right": {"mirror_right"},
}

# A look counts only after the gaze stays in an accepted region this long (a sweep doesn't).
LOOK_DWELL_S = 0.15

# Zones where an object always needs a look; in the others only while it is getting closer.
ALWAYS_LOOK = {"side_left", "side_right", "front_center"}
PX_PER_M = 31.43
CLOSING_WINDOW_S = 0.4       # compare with the position this long ago (4 ticks at 10 Hz)
CLOSING_M = 0.1              # closer by at least this much (to the car, or sideways to our lane)
HISTORY_S = 1.5              # positions kept per object
# -----------------------------------------------------------------------------------------------

_EPS = 1e-9   # float tolerance when comparing a look's completion time


def zone_of(x_px, y_px):
    """The zone the point (x_px, y_px) falls in, or None."""
    for zone, (xs, ys) in ZONES.items():
        if xs.contains(x_px) and ys.contains(y_px):
            return zone
    return None


def distance_m(x_px, y_px):
    return math.hypot(x_px, y_px) / PX_PER_M


def needs_look(zone, history, now):
    """True / False, or None while there is not enough history to tell (see the module docstring).
    history: deque of (time, x_px, y_px), oldest first, the newest is the current position."""
    if zone in ALWAYS_LOOK:
        return True
    old = None
    for t, x, y in history:          # the newest sample at least CLOSING_WINDOW_S old
        if t <= now - CLOSING_WINDOW_S:
            old = (x, y)
        else:
            break
    if old is None:
        return None
    _, x, y = history[-1]
    closer = math.hypot(*old) - math.hypot(x, y)       # px, to the ego car's center
    toward_lane = abs(old[0]) - abs(x)                  # px, sideways toward our lane
    return max(closer, toward_lane) >= CLOSING_M * PX_PER_M


@dataclass
class Tracked:
    """One object, in its current zone."""
    object_id: str
    type: str
    zone: str | None
    zone_entered_at: float
    deadline: float | None          # None: no timer (no zone, does not need a look, or undecided)
    noticed: bool = False
    missed: bool = False
    look_since: float | None = None  # gaze continuously in ACCEPTED_LOOKS[zone] since (>= entry)
    undecided: str | None = None     # "appeared" / "zone_change" while needs_look is not known yet


class Attention:
    """Attention state of one simulation run."""

    def __init__(self):
        self.running = False
        self.started_at = None
        self.objects = {}        # object_id -> Tracked
        self.history = {}        # object_id -> deque of (now, x_px, y_px), the last HISTORY_S
        self.reactions = []      # reaction_s of every "noticed" event
        self.misses = []         # summary entries of every "missed" event

    # ---- simulator events ----

    def start(self, now):
        """simulation_started: forget everything from the previous run."""
        self.__init__()
        self.running, self.started_at = True, now
        log.info("attention reset, timers running")
        return []

    def on_tick(self, now, objects, region):
        """simulation_tick: check what has expired so far, then apply zone changes. Objects
        missing from the tick are forgotten silently."""
        if not self.running:   # e.g. the server was restarted in the middle of a run
            log.info("tick without simulation_started: attention starts now")
            self.start(now)
        out = self.update(now, region)
        current = {}
        for obj in objects:
            h = self.history.setdefault(obj.object_id, deque())
            h.append((now, obj.x_px, obj.y_px))
            while h and h[0][0] < now - HISTORY_S:
                h.popleft()
            zone = zone_of(obj.x_px, obj.y_px)
            old = self.objects.get(obj.object_id)
            if old is not None and old.zone == zone:
                current[obj.object_id] = old       # same zone: nothing changes, unless still undecided
                if old.undecided:
                    self._decide(old, h, now, region, old.undecided, out)
                continue
            # new object or new zone: fresh state (a running, unnoticed timer is dropped)
            t = Tracked(obj.object_id, obj.type, zone, now, None)
            current[obj.object_id] = t
            self._decide(t, h, now, region, "appeared" if old is None else "zone_change", out)
        self.objects = current
        for oid in [oid for oid in self.history if oid not in current]:
            del self.history[oid]              # gone from the map: forget its positions
        return out

    def _decide(self, t, history, now, region, kind, out):
        """Start the timer of `t` if it needs a look; announce it (`kind` event) once that is known."""
        if t.zone is None:
            out.append(self._event(kind, t, now))
            return
        need = needs_look(t.zone, history, now)
        if need is None:
            t.undecided = kind                     # wait for more positions; no event yet
            return
        t.undecided = None
        if need:
            t.zone_entered_at, t.deadline = now, now + WINDOW_S[t.zone]
            if region in ACCEPTED_LOOKS[t.zone]:
                t.look_since = now                 # already looking: counts from the entry
        out.append(self._event(kind, t, now))

    def has_running_timer(self):
        """True while some object's look window is still open (the driver may have to look now)."""
        return any(t.deadline is not None and not (t.noticed or t.missed) for t in self.objects.values())

    def end(self, now, region):
        """simulation_ended (also on abort): last check, stop, and the summary."""
        if not self.running:
            log.info("simulation_ended without a running simulation: no summary")
            return []
        out = self.update(now, region)
        self.running = False
        self.objects = {}
        self.history = {}
        avg = round(sum(self.reactions) / len(self.reactions), 2) if self.reactions else None
        summary = {"event": "attention_summary", "timestamp": time.time(),
                   "duration_s": round(now - self.started_at, 2),
                   "noticed_count": len(self.reactions), "missed_count": len(self.misses),
                   "avg_reaction_s": avg, "missed": list(self.misses)}
        log.info("SUMMARY %.2f s: noticed %d, missed %d, avg reaction %s", summary["duration_s"],
                 summary["noticed_count"], summary["missed_count"],
                 "-" if avg is None else f"{avg:.2f} s")
        for m in self.misses:
            log.info("SUMMARY   missed %.2f s %s %s in %s", m["t_s"], m["object_id"], m["type"],
                     m["zone"])
        out.append(summary)
        return out

    # ---- the ~20 Hz check ----

    def update(self, now, region):
        """Read the current gaze region (None = no data / no face / eyes closed), update every
        object's dwell and check every deadline."""
        if not self.running:
            return []
        out = []
        for t in self.objects.values():
            if t.deadline is None or t.noticed or t.missed:
                continue
            if region in ACCEPTED_LOOKS[t.zone]:
                if t.look_since is None:
                    t.look_since = now
                done = t.look_since + LOOK_DWELL_S       # when the look completes
                if done <= now + _EPS and done <= t.deadline + _EPS:
                    t.noticed = True
                    reaction = round(done - t.zone_entered_at, 2)
                    self.reactions.append(reaction)
                    out.append(self._event("noticed", t, done, reaction))
                    continue
            else:
                t.look_since = None
            if now >= t.deadline:
                t.missed = True
                ev = self._event("missed", t, t.deadline)
                self.misses.append({"t_s": ev["t_s"], "object_id": t.object_id,
                                    "type": t.type, "zone": t.zone})
                out.append(ev)
        return out

    # ---- messages ----

    def _event(self, kind, t, at, reaction_s=None):
        """attention_event for object `t`; `at` = when it happened (monotonic seconds)."""
        h = self.history.get(t.object_id)
        dist = round(distance_m(h[-1][1], h[-1][2]), 1) if h else None
        ev = {"event": "attention_event", "timestamp": time.time(),
              "t_s": round(at - self.started_at, 2), "kind": kind,
              "object_id": t.object_id, "type": t.type, "zone": t.zone,
              "window_s": WINDOW_S[t.zone] if t.deadline is not None else None,
              "reaction_s": reaction_s, "distance_m": dist, "looking": t.look_since is not None}
        head = f"{ev['t_s']:.2f} s {t.object_id} {t.type}"
        if kind in ("appeared", "zone_change"):
            where = "no zone"
            if t.zone:
                where = f"{t.zone} ({ev['window_s']} s)" if ev["window_s"] else f"{t.zone} (no look needed)"
            log.info("%s %s-> %s", head, "appeared " if kind == "appeared" else "", where)
        elif kind == "noticed":
            log.info("%s in %s noticed in %.2f s", head, t.zone, reaction_s)
        else:
            log.info("%s in %s MISSED", head, t.zone)
        return ev
