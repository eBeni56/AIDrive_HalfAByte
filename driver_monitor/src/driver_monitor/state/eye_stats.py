"""Rolling eye statistics: PERCLOS (P80), mean openness of open eyes, blink rate / duration, long blinks.

PERCLOS is time-weighted (not frame-counted) and only uses frames where the eye measurement is
trustworthy: no face, head turned too far, or looking down with not-closed eyes (lids lowered by gaze)
are GAPS, never "closed" and never "open".
"""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.common.enums import EyeState
from driver_monitor.common.timeseries import TimeWeightedWindow
from driver_monitor.config import DrowsinessConfig, EyeClosureConfig, EyesConfig
from driver_monitor.detectors.eye_closure import EyeClosureDetector
from driver_monitor.signals.eyes import EyesSignal


@dataclass(slots=True)
class EyeStats:
    perclos: float | None
    coverage_s: float
    mean_openness: float | None  # time-weighted mean openness while the eyes were not closed
    blink_rate_per_min: float | None
    mean_blink_duration_ms: float | None
    blink_count: int
    long_blinks: int
    warming_up: bool


class EyeStatistics:
    def __init__(self, cfg: DrowsinessConfig, eyes_cfg: EyesConfig, closure_cfg: EyeClosureConfig) -> None:
        self.cfg, self.eyes_cfg, self.closure_cfg = cfg, eyes_cfg, closure_cfg
        self._window = TimeWeightedWindow(cfg.perclos_window_s, cfg.max_gap_s)
        self._started_t: float | None = None

    def reset(self) -> None:
        self._window.clear()
        self._started_t = None

    def update(self, eyes: EyesSignal, t: float) -> None:
        if self._started_t is None:
            self._started_t = t
        # Looking down narrows open eyes: those frames are gaps. Clearly CLOSED eyes still count when the
        # head is down (a drooping head with shut eyes is exactly what PERCLOS must see).
        valid = (
            eyes.reliable
            and eyes.openness is not None
            and (not eyes.looking_down or eyes.state is EyeState.CLOSED)
        )
        self._window.add(t, eyes.openness if valid else None)

    def stats(self, closure: EyeClosureDetector, t: float) -> EyeStats:
        c = self.cfg
        coverage = self._window.coverage_s()
        warming = coverage < c.min_coverage_s
        perclos = None if warming else self._window.fraction_at_or_below(c.perclos_closed_openness)
        mean_open = None if warming else self._window.mean_above(self.eyes_cfg.closed_enter)

        blinks = [rec.duration_s for _, rec in closure.blinks.since(t, c.blink_window_s)]
        elapsed = min(t - self._started_t, c.blink_window_s) if self._started_t is not None else 0.0
        rate = len(blinks) / (elapsed / 60.0) if elapsed >= 20.0 else None
        mean_ms = 1000.0 * sum(blinks) / len(blinks) if blinks else None
        long_blinks = sum(1 for d in blinks if d >= self.closure_cfg.eyes_closed_s)
        return EyeStats(perclos, coverage, mean_open, rate, mean_ms, len(blinks), long_blinks, warming)
