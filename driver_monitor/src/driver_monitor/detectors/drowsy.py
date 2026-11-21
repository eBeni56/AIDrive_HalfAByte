"""DROWSY: the overall tiredness verdict as an episode (active while the level is above ALERT).

Severity follows the level: SLIGHTLY_DROWSY INFO, DROWSY WARNING, VERY_DROWSY DANGER,
SEVERELY_DROWSY CRITICAL. Unlike other detections the severity can also go down within the episode,
because the level itself decays when the driver recovers (fatigue load half-life 5 min, one step per
30 s at most); the episode ends when the level is back at ALERT.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, DrowsinessLevel, Severity
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.state.drowsiness import DrowsinessState

LEVEL_SEVERITY = {
    DrowsinessLevel.ALERT: Severity.NONE,
    DrowsinessLevel.SLIGHTLY_DROWSY: Severity.INFO,
    DrowsinessLevel.DROWSY: Severity.WARNING,
    DrowsinessLevel.VERY_DROWSY: Severity.DANGER,
    DrowsinessLevel.SEVERELY_DROWSY: Severity.CRITICAL,
}


class DrowsyDetector:
    def __init__(self) -> None:
        self.tracker = EpisodeTracker(DetectionType.DROWSY)

    def reset(self) -> None:
        self.tracker.reset()

    def update(self, s: DrowsinessState, t: float) -> None:
        details: dict[str, object] = {
            "level": s.level.value,
            "level_index": s.level.number,
            "score": r(s.score),
            "previous_level": s.previous_level.value,
            "level_since_t": round(s.level_since_t, 3),
            "contributing": s.contributing,
            "fatigue_load": r(s.fatigue_load),
            "floor_level": s.floor_level.value,
            "next_possible_drop_t": r(s.next_possible_drop_t, 1),
        }
        if s.level is DrowsinessLevel.ALERT:
            if self.tracker.is_active:
                self.tracker.end(t, details=details)
            return
        severity = LEVEL_SEVERITY[s.level]
        if not self.tracker.is_active:
            self.tracker.start(t, t, severity, details)
        else:
            self.tracker.update(t, details, severity, allow_lower=True)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
