"""PHONE_USE: a mobile phone is visible in front of the driver - in practice: held in the hand.

Input: the newest PhoneSighting (perception/phone_finder.py, ~6 per second) or None.
Starts when a phone has been seen for start_s, with gaps shorter than end_s (the detector flickers): WARNING;
DANGER after danger_s. Ends when no phone has been seen for end_s (ended_t = last time it was seen).
It needs no calibration, so it is never reset (it works from the first frame, also while calibrating).
details: score (latest), max_score, box {x, y, w, h} in pixels of the raw frame, seen_for_s.
"""

from __future__ import annotations

from typing import Any

from driver_monitor.common.enums import DetectionType, Severity
from driver_monitor.config import PhoneConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.perception.phone_finder import PhoneSighting


class PhoneDetector:
    def __init__(self, cfg: PhoneConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.PHONE_USE)
        self._first_seen: float | None = None  # first sighting of the current run of sightings
        self._last_seen: float | None = None
        self._max_score = 0.0

    def update(self, sighting: PhoneSighting | None, t: float) -> None:
        c = self.cfg
        if sighting is None:
            if self._first_seen is not None and self._last_seen is not None and t - self._last_seen >= c.end_s:
                if self.tracker.is_active:
                    self.tracker.end(t, self._last_seen, {"seen_for_s": r(self._last_seen - self._first_seen)})
                self._first_seen = self._last_seen = None
            elif self.tracker.is_active:
                self.tracker.update(t)  # a short gap: still holding the phone
            return

        if self._first_seen is None:
            self._first_seen, self._max_score = sighting.t, 0.0
        self._last_seen = sighting.t if self._last_seen is None else max(self._last_seen, sighting.t)
        self._max_score = max(self._max_score, sighting.score)
        seen_for = t - self._first_seen
        x, y, w, h = sighting.box
        details: dict[str, Any] = {
            "score": r(sighting.score),
            "max_score": r(self._max_score),
            "box": {"x": x, "y": y, "w": w, "h": h},
            "seen_for_s": r(seen_for),
        }
        severity = Severity.DANGER if seen_for >= c.danger_s else Severity.WARNING
        if self.tracker.is_active:
            self.tracker.update(t, details, severity)
        elif seen_for >= c.start_s:
            self.tracker.start(t, self._first_seen, severity, details)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
