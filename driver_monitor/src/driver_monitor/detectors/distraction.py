"""DISTRACTED ("eyes on the road"): the driver looks at none of the places there is a reason to look at.

The road, the three mirrors and the side windows are all fine (zones ROAD, *_MIRROR, LEFT/RIGHT_SIDE: the
8 regions of the simulator). Looking UP or DOWN (phone, lap, console, roof) is not:
  away >= start_s (1.0 s)    DISTRACTED, WARNING
  away >= danger_s (2.5 s)   DANGER
It ends when the gaze has been back in one of the fine zones for end_s (ended_t = when it came back).
Unknown gaze (no face, eyes closed) is "not away": closed eyes are EYES_CLOSED, not distraction.
details: zone (UP / DOWN: where the driver looks), away_s.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, GazeZone, Severity
from driver_monitor.config import DistractionConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r


class DistractionDetector:
    def __init__(self, cfg: DistractionConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.DISTRACTED)
        self._away_since: float | None = None
        self._back_since: float | None = None

    def reset(self) -> None:
        self.tracker.reset()
        self._away_since = self._back_since = None

    def update(self, zone: GazeZone, t: float) -> None:
        c = self.cfg
        if zone.is_away:
            self._back_since = None
            if self._away_since is None:
                self._away_since = t
            away = t - self._away_since
            severity = Severity.DANGER if away >= c.danger_s else Severity.WARNING
            details = {"zone": zone.value, "away_s": r(away)}
            if self.tracker.is_active:
                self.tracker.update(t, details, severity)
            elif away >= c.start_s:
                self.tracker.start(t, self._away_since, severity, details)
            return

        if self._away_since is None:
            return
        if self._back_since is None:
            self._back_since = t
        if t - self._back_since >= c.end_s:
            if self.tracker.is_active:
                self.tracker.end(t, self._back_since, {"away_s": r(self._back_since - self._away_since)})
            self._away_since = self._back_since = None
        else:
            self.tracker.update(t)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
