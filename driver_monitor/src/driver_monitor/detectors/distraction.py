"""DISTRACTED: eyes open but pointed away from the road for too long - with different limits per zone.

LOOKING_DOWN  one glance with >= down_warning_s (1.2 s) spent looking DOWN (phone, lap, console) -> WARNING,
              >= down_danger_s (2 s) -> DANGER. There is no reason to look down while driving.
LONG_GLANCE   one glance away from the road >= side_warning_s (3 s) -> WARNING, >= side_danger_s (5 s)
              -> DANGER. Sideways (traffic, side roads), up, or STARING at a mirror; normal mirror checks
              and short looks to the side never trigger it.
CUMULATIVE    >= cumulative_s (10 s) of WEIGHTED off-road time within cumulative_window_s (30 s): down
              counts fully, sideways/up half, mirrors not at all (many short glances at the phone/radio).
Ends when the gaze has been back on the road for end_on_road_s and the weighted cumulative time is below
cumulative_end_s.
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
        down = att.glance_down_s >= c.down_warning_s
        long = att.eyes_off_road_s >= c.side_warning_s
        cumulative = att.weighted_off_road_s >= c.cumulative_s
        if not self.tracker.is_active:
            if not (down or long or cumulative):
                return
            self._reason = "LOOKING_DOWN" if down else "LONG_GLANCE" if long else "CUMULATIVE"
            self._max_glance = att.eyes_off_road_s
            started = t - att.eyes_off_road_s if (down or long) else t
            self.tracker.start(t, started, Severity.WARNING, {})
        self._max_glance = max(self._max_glance, att.eyes_off_road_s)

        g = att.last_off_road_gaze
        details: dict[str, object] = {
            "reason": self._reason,
            "zone": g.zone.value if g else None,
            "direction": g.direction.value if g else None,
            "horizontal": g.horizontal.value if g else None,
            "vertical": g.vertical.value if g else None,
            "intensity": g.intensity.value if g else None,
            "gaze_yaw_deg": r(g.yaw, 1) if g else None,
            "gaze_pitch_deg": r(g.pitch, 1) if g else None,
            "head_yaw_deg": r(g.head_yaw, 1) if g else None,
            "head_pitch_deg": r(g.head_pitch, 1) if g else None,
            "glance_s": r(att.eyes_off_road_s),
            "glance_down_s": r(att.glance_down_s),
            "longest_glance_s": r(self._max_glance),
            "down_threshold_s": c.down_warning_s,
            "side_threshold_s": c.side_warning_s,
            "off_road_last_30s_s": r(att.off_road_window_s, 1),
            "weighted_off_road_30s_s": r(att.weighted_off_road_s, 1),
            "cumulative_threshold_s": c.cumulative_s,
        }
        ended = (
            att.state is AttentionState.ATTENTIVE
            and att.on_road_for_s >= c.end_on_road_s
            and att.weighted_off_road_s < c.cumulative_end_s
        )
        if ended:
            self.tracker.end(t, t - att.on_road_for_s, details)
            return
        danger = att.glance_down_s >= c.down_danger_s or att.eyes_off_road_s >= c.side_danger_s
        if down and self._reason != "LOOKING_DOWN":  # a later look down within the same episode
            self._reason = details["reason"] = "LOOKING_DOWN"
        self.tracker.update(t, details, Severity.DANGER if danger else Severity.WARNING)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
