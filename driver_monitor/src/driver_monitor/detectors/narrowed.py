"""EYES_NARROWED: the eyes stay clearly narrower than this driver's normal opening (heavy eyelids).

An early tiredness sign that is milder than EYES_ALMOST_CLOSED: the eyes are open, just not as open as
usual. Measured as a RATIO to the driver's learned normal opening (state/eye_baseline.py), so it works the
same for narrow and wide eyes.

Starts when the ratio (3 s median, blinks excluded) stays below enter_ratio (0.85) for start_after_s (5 s);
ends when it is back above exit_ratio (0.9) for end_after_s. Nothing is judged before the driver's normal
opening has been learned (~30 s of clean samples after calibration). INFO, WARNING when narrower than warning_ratio
(0.7) or narrowed for warning_after_s (20 s). Looking down, smiling and yawning are excluded by the
baseline's sample filter (they narrow the eyes for innocent reasons).
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, Severity
from driver_monitor.common.timeseries import Debounce
from driver_monitor.config import NarrowedEyesConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r


class EyesNarrowedDetector:
    _MAX_UNKNOWN_S = 10.0  # no clean eye samples this long ends an episode

    def __init__(self, cfg: NarrowedEyesConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.EYES_NARROWED)
        self._debounce = Debounce(cfg.start_after_s, cfg.end_after_s)
        self._min = self._sum = self._dur = 0.0
        self._last_t: float | None = None
        self._unknown_since: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self._debounce.reset()
        self._last_t = self._unknown_since = None

    @property
    def active(self) -> bool:
        return self.tracker.is_active

    @property
    def duration_s(self) -> float:
        a = self.tracker.active
        return a.duration_s if a is not None else 0.0

    def update(self, ratio: float | None, baseline: float, learned: bool, t: float) -> None:
        c = self.cfg
        dt = t - self._last_t if self._last_t is not None else 0.0
        self._last_t = t
        if not learned:  # judged only against the driver's own learned normal, never a generic value
            ratio = None
        if ratio is None:
            if self._unknown_since is None:
                self._unknown_since = t
            if self.tracker.is_active and t - self._unknown_since >= self._MAX_UNKNOWN_S:
                self._debounce.reset()
                self.tracker.end(t, self._unknown_since)
            return  # hold the current state through short gaps (blinks, a glance down)
        self._unknown_since = None

        cond = ratio < (c.exit_ratio if self.tracker.is_active else c.enter_ratio)
        if cond:
            if not self.tracker.is_active and self._debounce.since is None:  # a new candidate run
                self._min, self._sum, self._dur = ratio, 0.0, 0.0
            self._min = min(self._min, ratio)
            self._sum += ratio * dt
            self._dur += dt
        event = self._debounce.update(cond, t)
        details = {
            "openness_ratio": r(ratio),
            "min_ratio": r(self._min),
            "mean_ratio": r(self._sum / self._dur) if self._dur > 0 else r(ratio),
            "normal_openness": r(baseline),
            "normal_learned": learned,
            "narrowed_below_ratio": c.enter_ratio,
        }
        if event == "on" and self._debounce.since is not None:
            self.tracker.start(t, self._debounce.since, Severity.INFO, details)
        elif event == "off":
            self.tracker.end(t, details=details)
            return
        if self.tracker.is_active:
            strong = self._min < c.warning_ratio or self.duration_s >= c.warning_after_s
            self.tracker.update(t, details, Severity.WARNING if strong else None)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
