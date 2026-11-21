"""DISTRACTED: eyes open but pointed away from the road for too long.

LONG_GLANCE  one off-road glance >= long_glance_s (2 s, NHTSA-style single-glance limit) -> WARNING,
             >= danger_glance_s (3.5 s) -> DANGER
CUMULATIVE   >= cumulative_s (10 s) off-road within the last cumulative_window_s (30 s) -> WARNING
             (many short glances, e.g. fiddling with the radio)
Ends when the gaze has been back on the road for end_on_road_s and the cumulative time is below
cumulative_end_s. Mirror checks (< 2 s) never trigger it.
"""

from __future__ import annotations

from driver_monitor.common.enums import AttentionState, DetectionType, Severity
from driver_monitor.config import DistractionConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.state.attention import AttentionSnapshot


class DistractionDetector:
    def __init__(self, cfg: DistractionConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.DISTRACTED)
        self._reason = "LONG_GLANCE"
        self._max_glance = 0.0

    def reset(self) -> None:
        self.tracker.reset()

    @property
    def active(self) -> bool:
        return self.tracker.is_active

    def update(self, att: AttentionSnapshot, t: float) -> None:
        c = self.cfg
        long = att.eyes_off_road_s >= c.long_glance_s
        cumulative = att.off_road_window_s >= c.cumulative_s
        if not self.tracker.is_active:
            if not (long or cumulative):
                return
            self._reason = "LONG_GLANCE" if long else "CUMULATIVE"
            self._max_glance = att.eyes_off_road_s
            started = t - att.eyes_off_road_s if long else t
            self.tracker.start(t, started, Severity.WARNING, {})
        self._max_glance = max(self._max_glance, att.eyes_off_road_s)

        g = att.last_off_road_gaze
        details: dict[str, object] = {
            "reason": self._reason,
            "direction": g.direction.value if g else None,
            "horizontal": g.horizontal.value if g else None,
            "vertical": g.vertical.value if g else None,
            "intensity": g.intensity.value if g else None,
            "gaze_yaw_deg": r(g.yaw, 1) if g else None,
            "gaze_pitch_deg": r(g.pitch, 1) if g else None,
            "head_yaw_deg": r(g.head_yaw, 1) if g else None,
            "head_pitch_deg": r(g.head_pitch, 1) if g else None,
            "glance_s": r(att.eyes_off_road_s),
            "longest_glance_s": r(self._max_glance),
            "long_glance_threshold_s": c.long_glance_s,
            "danger_threshold_s": c.danger_glance_s,
            "off_road_last_30s_s": r(att.off_road_window_s, 1),
            "cumulative_threshold_s": c.cumulative_s,
        }
        ended = (
            att.state is AttentionState.ATTENTIVE
            and att.on_road_for_s >= c.end_on_road_s
            and att.off_road_window_s < c.cumulative_end_s
        )
        if ended:
            self.tracker.end(t, t - att.on_road_for_s, details)
            return
        severity = Severity.DANGER if att.eyes_off_road_s >= c.danger_glance_s else Severity.WARNING
        self.tracker.update(t, details, severity)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
