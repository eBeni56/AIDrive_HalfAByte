"""TIRED_EYES: a slow, heavy-eyed pattern over the last minute (no single dramatic event needed).

Reasons (any of):
  HEAVY_EYELIDS          mean openness of the open eyes < heavy_eyelids_below (0.8) of the driver's
                         learned normal opening (state/eye_baseline.py)
  SLOW_BLINKS            mean blink duration > slow_blink_ms (with >= min_blinks_for_duration blinks)
  FREQUENT_LONG_BLINKS   >= long_blinks closures of 0.5-1.0 s in the window
Severity INFO with one reason, WARNING with two or more. Ends after end_after_s without any reason.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, Severity
from driver_monitor.config import TiredEyesConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.state.eye_stats import EyeStats


class TiredEyesDetector:
    def __init__(self, cfg: TiredEyesConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.TIRED_EYES)
        self._clear_since: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self._clear_since = None

    def reasons(self, s: EyeStats) -> list[str]:
        c = self.cfg
        out: list[str] = []
        if s.warming_up:
            return out
        rel = s.mean_openness_ratio if s.mean_openness_ratio is not None else s.mean_openness
        if rel is not None and rel < c.heavy_eyelids_below:
            out.append("HEAVY_EYELIDS")
        if (
            s.mean_blink_duration_ms is not None
            and s.blink_count >= c.min_blinks_for_duration
            and s.mean_blink_duration_ms > c.slow_blink_ms
        ):
            out.append("SLOW_BLINKS")
        if s.long_blinks >= c.long_blinks:
            out.append("FREQUENT_LONG_BLINKS")
        return out

    def update(self, stats: EyeStats, t: float) -> None:
        reasons = self.reasons(stats)
        details = {
            "window_s": self.cfg.window_s,
            "reasons": reasons,
            "mean_openness_60s": r(stats.mean_openness),
            "mean_openness_ratio_60s": r(stats.mean_openness_ratio),
            "mean_blink_duration_ms": r(stats.mean_blink_duration_ms, 0),
            "long_blinks_60s": stats.long_blinks,
            "perclos": r(stats.perclos, 3),
        }
        severity = Severity.WARNING if len(reasons) >= 2 else Severity.INFO
        if reasons:
            self._clear_since = None
            if not self.tracker.is_active:
                self.tracker.start(t, t, severity, details)
            else:
                self.tracker.update(t, details, severity)
            return
        if not self.tracker.is_active:
            return
        if self._clear_since is None:
            self._clear_since = t
        if t - self._clear_since >= self.cfg.end_after_s:
            self.tracker.end(t, self._clear_since, details)
            self._clear_since = None
        else:
            # keep the reasons that started the episode visible while it fades out
            self.tracker.update(t, {k: v for k, v in details.items() if k != "reasons"})

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
