"""Off-road glance bookkeeping per gaze zone: current glance, cumulative off-road time, glance statistics.

A glance = a continuous run of gaze outside the ROAD zone (mirrors, sideways, up, down). Within a glance
the time spent looking DOWN is tracked separately: looking down has much shorter limits (DistractionConfig).
The cumulative window holds WEIGHTED off-road seconds: down counts fully, sideways/up half, mirror checks
not at all (they are part of driving).
Unknown gaze (eyes closed > a blink, face lost) neither extends nor ends a glance - closed eyes are a
drowsiness problem, not distraction, and must not be counted twice. A long unknown period ends the glance.
"""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.common.enums import AttentionState, GazeZone
from driver_monitor.common.timeseries import EventLog, TimeWeightedWindow
from driver_monitor.config import DistractionConfig
from driver_monitor.signals.gaze import GazeSignal


@dataclass(slots=True)
class AttentionSnapshot:
    state: AttentionState
    eyes_off_road_s: float  # current glance (accumulated time outside the road zone)
    glance_down_s: float  # ... of which looking DOWN
    off_road_window_s: float  # total off-road time in the cumulative window (30 s), unweighted
    weighted_off_road_s: float  # ... weighted per zone (what the CUMULATIVE rule uses)
    longest_glance_s: float  # in the stats window (60 s), including the current glance
    glances: int  # in the stats window
    last_off_road_gaze: GazeSignal | None  # last gaze sample while off-road (for messages)
    on_road_for_s: float
    zone: GazeZone = GazeZone.UNKNOWN


class AttentionTracker:
    _UNKNOWN_RESET_S = 2.0

    def __init__(self, cfg: DistractionConfig) -> None:
        self.cfg = cfg
        self._window = TimeWeightedWindow(cfg.cumulative_window_s, 0.5)
        self._weighted = TimeWeightedWindow(cfg.cumulative_window_s, 0.5)
        self._glances: EventLog[float] = EventLog(cfg.stats_window_s)
        self._glance_s = self._down_s = 0.0
        self._in_glance = False
        self._last_t: float | None = None
        self._unknown_since: float | None = None
        self._on_road_since: float | None = None
        self._last_off: GazeSignal | None = None

    def reset(self) -> None:
        self._window.clear()
        self._weighted.clear()
        self._glances.clear()
        self._glance_s = self._down_s = 0.0
        self._in_glance = False
        self._last_t = self._unknown_since = self._on_road_since = None
        self._last_off = None

    def _end_glance(self, t: float) -> None:
        if self._in_glance and self._glance_s >= self.cfg.min_glance_s:
            self._glances.add(t, self._glance_s)
        self._in_glance, self._glance_s, self._down_s = False, 0.0, 0.0

    def _weight(self, zone: GazeZone) -> float:
        c = self.cfg
        if zone is GazeZone.DOWN:
            return c.down_weight
        if zone.is_mirror:
            return c.mirror_weight
        return c.side_weight

    def update(self, gaze: GazeSignal, t: float) -> AttentionSnapshot:
        dt = min(t - self._last_t, 0.5) if self._last_t is not None else 0.0
        self._last_t = t
        self._glances.prune(t)
        zone = gaze.zone
        if zone is GazeZone.UNKNOWN:
            self._window.add(t, None)
            self._weighted.add(t, None)
            if self._unknown_since is None:
                self._unknown_since = t
            if t - self._unknown_since >= self._UNKNOWN_RESET_S:
                self._end_glance(t)
            self._on_road_since = None
            state = AttentionState.UNKNOWN
        else:
            self._unknown_since = None
            off = zone is not GazeZone.ROAD
            self._window.add(t, 1.0 if off else 0.0)
            self._weighted.add(t, self._weight(zone) if off else 0.0)
            if off:
                self._in_glance = True
                self._glance_s += dt
                if zone is GazeZone.DOWN:
                    self._down_s += dt
                self._last_off = gaze
                self._on_road_since = None
                state = AttentionState.CHECKING_MIRROR if zone.is_mirror else AttentionState.GLANCING_AWAY
            else:
                self._end_glance(t)
                if self._on_road_since is None:
                    self._on_road_since = t
                state = AttentionState.ATTENTIVE

        past = [d for _, d in self._glances.since(t, self.cfg.stats_window_s)]
        current = self._glance_s if self._in_glance else 0.0
        count = len(past) + (1 if current >= self.cfg.min_glance_s else 0)
        return AttentionSnapshot(
            state=state,
            eyes_off_road_s=current,
            glance_down_s=self._down_s if self._in_glance else 0.0,
            off_road_window_s=self._window.weighted_sum(),
            weighted_off_road_s=self._weighted.weighted_sum(),
            longest_glance_s=max([*past, current]),
            glances=count,
            last_off_road_gaze=self._last_off,
            on_road_for_s=t - self._on_road_since if self._on_road_since is not None else 0.0,
            zone=zone,
        )
