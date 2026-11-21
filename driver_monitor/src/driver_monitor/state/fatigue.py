"""Fatigue ledger: the history of fatigue EVENTS and their decaying "load".

An acute event (eye closure, microsleep, head nod, head fall, yawn) is alarmed immediately by its own
detector. For the overall drowsiness level it is only EVIDENCE that accumulates over time:

    load(t) = sum over events of  weight * 0.5 ** ((t - t_event) / half_life)

Every eye closure >= 0.5 s is an event weighted by its DURATION (closure_weight_curve: 0.5 s 0.2,
1 s 0.5, 2 s 1.1, 3 s 1.5, 5 s 2.0), so both drowsiness patterns are covered:
  acute    one long closure weighs a lot at once, then fades while the driver stays alert
  chronic  frequent shorter closures (long blinks) weigh little each, but stack up while they keep coming
A running microsleep is updated every frame, so its weight grows while the eyes stay shut.
An EYES_NARROWED episode (heavy eyelids) is added when it ends, weighted by its duration (0.5 per minute,
at most 1.0), so repeated episodes stack up as well.

The ledger also keeps per-window counts (last 1 / 5 / 15 min) of long eye closures, microsleeps, total
closed time, yawns, nods and head falls for the JSON history. Events come from the detection stream
(STARTED / ENDED messages), so the ledger has no coupling to the detector internals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from driver_monitor.common.enums import DetectionStatus, DetectionType
from driver_monitor.config import DrowsinessConfig
from driver_monitor.detectors.base import Detection


def curve(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear map through `points` (clamped at both ends; x may be given decreasing)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if xs[0] > xs[-1]:  # decreasing x (e.g. openness: lower = worse)
        xs, ys = xs[::-1], ys[::-1]
    return float(np.interp(x, xs, ys))


@dataclass(slots=True)
class FatigueEvent:
    t: float
    # MICROSLEEP | HEAD_FALL | HEAD_NOD | YAWN | EYES_CLOSED (long closure that reopened < 1 s)
    # | EYES_NARROWED (eyes clearly narrower than normal for a while)
    kind: str
    weight: float
    closed_s: float = 0.0  # eye-closure duration (closures) / episode duration (EYES_NARROWED)


@dataclass(slots=True)
class WindowCounts:
    window_s: float
    long_eye_closures: int = 0  # closures >= 0.5 s (EYES_CLOSED and MICROSLEEP)
    microsleeps: int = 0
    closed_eyes_s: float = 0.0  # total time in those closures
    yawns: int = 0
    head_nods: int = 0
    head_falls: int = 0
    narrowed_eyes: int = 0  # EYES_NARROWED episodes that ended in the window
    narrowed_eyes_s: float = 0.0


@dataclass(slots=True)
class FatigueSnapshot:
    load: float
    load_by_kind: dict[str, float]
    windows: list[WindowCounts] = field(default_factory=list)


class FatigueLedger:
    def __init__(self, cfg: DrowsinessConfig) -> None:
        self.cfg = cfg
        self.events: list[FatigueEvent] = []
        self._keep_s = max([*cfg.history_windows_s, 0.0]) + 1.0
        self._open_microsleep: FatigueEvent | None = None

    def reset(self) -> None:
        self.events.clear()
        self._open_microsleep = None

    def _add(self, t: float, kind: str, closed_s: float = 0.0) -> FatigueEvent:
        c = self.cfg
        if kind in ("EYES_CLOSED", "MICROSLEEP"):
            weight = curve(closed_s, c.closure_weight_curve)
        elif kind == "EYES_NARROWED":  # closed_s holds the episode duration here
            weight = min(c.narrowed_weight_per_min * closed_s / 60.0, c.narrowed_max_weight)
        else:
            weight = self.cfg.event_weights.get(kind, 0.0)
        ev = FatigueEvent(t, kind, weight, closed_s)
        self.events.append(ev)
        return ev

    def ingest(self, detections: list[Detection], t: float) -> None:
        """Record the events found in this frame's detection messages."""
        for d in detections:
            kind, status = d.type, d.status
            if kind is DetectionType.MICROSLEEP:
                closed = max(float(d.details.get("closed_for_s") or 0.0), t - d.started_t)
                if status is DetectionStatus.STARTED or self._open_microsleep is None:
                    self._open_microsleep = self._add(d.started_t, "MICROSLEEP", closed)
                else:  # still closed (or just reopened): the event grows with the closure
                    ev = self._open_microsleep
                    if status is DetectionStatus.ENDED:
                        closed = float(d.details.get("closed_for_s") or ev.closed_s)
                    else:
                        closed = max(ev.closed_s, closed)
                    ev.closed_s = closed
                    ev.weight = curve(closed, self.cfg.closure_weight_curve)
                if status is DetectionStatus.ENDED:
                    self._open_microsleep = None
            elif kind is DetectionType.EYES_CLOSED and status is DetectionStatus.ENDED:
                if d.details.get("outcome") == "REOPENED":  # escalations are counted as microsleeps
                    self._add(d.started_t, "EYES_CLOSED", float(d.details.get("closed_for_s") or 0.0))
            elif kind is DetectionType.HEAD_NOD and status is DetectionStatus.ENDED:
                self._add(t, "HEAD_NOD")
            elif kind is DetectionType.HEAD_FALLING and status is DetectionStatus.STARTED:
                self._add(t, "HEAD_FALL")
            elif kind is DetectionType.YAWN and status is DetectionStatus.ENDED:
                self._add(t, "YAWN")
            elif kind is DetectionType.EYES_NARROWED and status is DetectionStatus.ENDED:
                self._add(t, "EYES_NARROWED", d.duration_s)  # fades from its end on
        self.events = [e for e in self.events if t - e.t <= self._keep_s or e is self._open_microsleep]

    def snapshot(self, t: float) -> FatigueSnapshot:
        c = self.cfg
        by_kind: dict[str, float] = {}
        for e in self.events:
            decay = 0.5 ** (max(t - e.t, 0.0) / c.fatigue_half_life_s)
            by_kind[e.kind] = by_kind.get(e.kind, 0.0) + e.weight * decay
        windows = []
        for w in c.history_windows_s:
            wc = WindowCounts(w)
            for e in self.events:
                if t - e.t > w:
                    continue
                if e.kind in ("EYES_CLOSED", "MICROSLEEP"):
                    wc.long_eye_closures += 1
                    wc.closed_eyes_s += e.closed_s
                    wc.microsleeps += e.kind == "MICROSLEEP"
                elif e.kind == "YAWN":
                    wc.yawns += 1
                elif e.kind == "HEAD_NOD":
                    wc.head_nods += 1
                elif e.kind == "HEAD_FALL":
                    wc.head_falls += 1
                elif e.kind == "EYES_NARROWED":
                    wc.narrowed_eyes += 1
                    wc.narrowed_eyes_s += e.closed_s
            windows.append(wc)
        return FatigueSnapshot(sum(by_kind.values()), by_kind, windows)
