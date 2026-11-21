"""Off-road glance bookkeeping: current glance duration, cumulative off-road time, glance statistics.

Off-road = the combined gaze (head + eyes) is outside the on-road box.
Unknown gaze (eyes closed > a blink, face lost) neither extends nor ends a glance - closed eyes are a
drowsiness problem, not distraction, and must not be counted twice. A long unknown period ends the glance.
"""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.common.enums import AttentionState, GazeDirection
from driver_monitor.common.timeseries import EventLog, TimeWeightedWindow
from driver_monitor.config import DistractionConfig
from driver_monitor.signals.gaze import GazeSignal


@dataclass(slots=True)
class AttentionSnapshot:
    state: AttentionState
    eyes_off_road_s: float  # current glance (accumulated off-road time)
    off_road_window_s: float  # total off-road time in the cumulative window (30 s)
    longest_glance_s: float  # in the stats window (60 s), including the current glance
    glances: int  # in the stats window
    last_off_road_gaze: GazeSignal | None  # last gaze sample while off-road (for messages)
    on_road_for_s: float


class AttentionTracker:
    _UNKNOWN_RESET_S = 2.0

    def __init__(self, cfg: DistractionConfig) -> None:
        self.cfg = cfg
        self._window = TimeWeightedWindow(cfg.cumulative_window_s, 0.5)
        self._glances: EventLog[float] = EventLog(cfg.stats_window_s)
        self._glance_s = 0.0
        self._in_glance = False
        self._last_t: float | None = None
        self._unknown_since: float | None = None
        self._on_road_since: float | None = None
        self._last_off: GazeSignal | None = None

    def reset(self) -> None:
        self._window.clear()
        self._glances.clear()
        self._glance_s, self._in_glance = 0.0, False
        self._last_t = self._unknown_since = self._on_road_since = None
        self._last_off = None

    def _end_glance(self, t: float) -> None:
        if self._in_glance and self._glance_s >= self.cfg.min_glance_s:
            self._glances.add(t, self._glance_s)
        self._in_glance, self._glance_s = False, 0.0

    def update(self, gaze: GazeSignal, t: float) -> AttentionSnapshot:
        dt = min(t - self._last_t, 0.5) if self._last_t is not None else 0.0
        self._last_t = t
        self._glances.prune(t)
        known = gaze.on_road is not None and gaze.direction is not GazeDirection.UNKNOWN
        if not known:
            self._window.add(t, None)
            if self._unknown_since is None:
                self._unknown_since = t
            if t - self._unknown_since >= self._UNKNOWN_RESET_S:
                self._end_glance(t)
            self._on_road_since = None
            state = AttentionState.UNKNOWN
        else:
            self._unknown_since = None
            off = not gaze.on_road
            self._window.add(t, 1.0 if off else 0.0)
            if off:
                self._in_glance = True
                self._glance_s += dt
                self._last_off = gaze
                self._on_road_since = None
                state = AttentionState.GLANCING_AWAY
            else:
                self._end_glance(t)
                if self._on_road_since is None:
                    self._on_road_since = t
                state = AttentionState.ATTENTIVE

        coverage = self._window.coverage_s()
        on_fraction = self._window.fraction_at_or_below(0.5)
        off_time = coverage * (1.0 - on_fraction) if on_fraction is not None else 0.0
        past = [d for _, d in self._glances.since(t, self.cfg.stats_window_s)]
        current = self._glance_s if self._in_glance else 0.0
        count = len(past) + (1 if current >= self.cfg.min_glance_s else 0)
        return AttentionSnapshot(
            state=state,
            eyes_off_road_s=current,
            off_road_window_s=off_time,
            longest_glance_s=max([*past, current]),
            glances=count,
            last_off_road_gaze=self._last_off,
            on_road_for_s=t - self._on_road_since if self._on_road_since is not None else 0.0,
        )
