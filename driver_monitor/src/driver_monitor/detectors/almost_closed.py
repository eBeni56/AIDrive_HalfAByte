"""EYES_ALMOST_CLOSED: eyes held nearly shut (heavy eyelids) for >= start_after_s.

Counts while the eye state is ALMOST_CLOSED (short blinks inside the period don't break it).
Suppressed when the narrowing has an innocent explanation:
  - looking down (eyelids follow the eyeball down)   -> eyes.looking_down
  - smiling / laughing (cheeks push the lower lids)   -> eyes.smiling
  - yawning (eyes squeeze during a yawn)              -> yawn active
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, EyeState, Severity
from driver_monitor.common.timeseries import Debounce
from driver_monitor.config import AlmostClosedConfig, EyeClosureConfig, EyesConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.signals.eyes import EyesSignal


class EyesAlmostClosedDetector:
    def __init__(self, cfg: AlmostClosedConfig, eyes_cfg: EyesConfig, closure_cfg: EyeClosureConfig) -> None:
        self.cfg, self.eyes_cfg, self.closure_cfg = cfg, eyes_cfg, closure_cfg
        self.tracker = EpisodeTracker(DetectionType.EYES_ALMOST_CLOSED)
        self._debounce = Debounce(cfg.start_after_s, cfg.end_after_s)
        self._sum = self._dur = 0.0
        self._min = 1.0
        self._last_t: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self._debounce.reset()
        self._last_t = None

    def update(self, eyes: EyesSignal, yawning: bool, t: float) -> None:
        blink_inside = eyes.state is EyeState.CLOSED and eyes.state_for_s < self.closure_cfg.eyes_closed_s
        condition = (
            eyes.reliable
            and (eyes.state is EyeState.ALMOST_CLOSED or blink_inside)
            and not eyes.looking_down
            and not eyes.smiling
            and not yawning
        )
        if condition and eyes.openness is not None and not blink_inside:
            dt = t - self._last_t if self._last_t is not None else 0.0
            if self._debounce.since is None:
                self._sum = self._dur = 0.0
                self._min = eyes.openness
            self._sum += eyes.openness * dt
            self._dur += dt
            self._min = min(self._min, eyes.openness)
        self._last_t = t

        event = self._debounce.update(condition, t)
        if event == "on" and self._debounce.since is not None:
            self.tracker.start(t, self._debounce.since, Severity.WARNING, {})
        elif event == "off":
            self.tracker.end(t, details=self._details(eyes))
            return
        if self.tracker.is_active:
            self.tracker.update(t, self._details(eyes))

    def _details(self, eyes: EyesSignal) -> dict[str, object]:
        return {
            "openness": r(eyes.openness),
            "mean_openness": r(self._sum / self._dur) if self._dur > 0 else None,
            "min_openness": r(self._min),
            "almost_closed_below": self.eyes_cfg.almost_enter,
            "closed_below": self.eyes_cfg.closed_enter,
            "left_openness": r(eyes.left.openness) if eyes.left else None,
            "right_openness": r(eyes.right.openness) if eyes.right else None,
            "looking_down": eyes.looking_down,
            "smiling": eyes.smiling,
        }

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
