"""PHONE_USE: a mobile phone is visible in front of the driver - in practice: held in the hand.

Input: the newest PhoneSighting (perception/phone_finder.py, ~6 per second) or None.
Starts when a phone has been seen for start_s, with gaps shorter than end_s (the detector flickers): WARNING;
DANGER after danger_s. Ends when no phone has been seen for end_s (ended_t = last time it was seen).
details: score (latest), max_score, box {x, y, w, h} in pixels of the raw frame, seen_for_s.
"""

from __future__ import annotations

from driver_monitor.common.enums import DetectionType, Severity
from driver_monitor.config import PhoneConfig
from driver_monitor.detectors.base import Detection, EpisodeTracker, r
from driver_monitor.perception.phone_finder import PhoneSighting


class PhoneDetector:
    def __init__(self, cfg: PhoneConfig) -> None:
        self.cfg = cfg
        self.tracker = EpisodeTracker(DetectionType.PHONE_USE)
        self._first_seen: float | None = None
        self._last_seen: float | None = None
        self._max_score = 0.0

    def reset(self) -> None:
        self.tracker.reset()
        self._first_seen = self._last_seen = None
        self._max_score = 0.0

    @property
    def active(self) -> bool:
        return self.tracker.is_active

    def update(self, sighting: PhoneSighting | None, t: float) -> None:
        c = self.cfg
        if sighting is not None:
            if self._first_seen is None:
                self._first_seen, self._max_score = sighting.t, 0.0
            self._last_seen = max(self._last_seen or sighting.t, sighting.t)
            self._max_score = max(self._max_score, sighting.score)
        elif self._last_seen is not None and t - self._last_seen >= c.end_s:
            if self.tracker.is_active:
                self.tracker.end(t, self._last_seen, {"seen_for_s": r(self._last_seen - (self._first_seen or t))})
            self._first_seen = self._last_seen = None
            return
        if sighting is None or self._first_seen is None:
            if self.tracker.is_active:
                self.tracker.update(t)  # a short gap: still holding the phone
            return

        x, y, w, h = sighting.box
        details: dict[str, object] = {
            "score": r(sighting.score),
            "max_score": r(self._max_score),
            "box": {"x": x, "y": y, "w": w, "h": h},
            "seen_for_s": r(t - self._first_seen),
        }
        seen_for = t - self._first_seen
        severity = Severity.DANGER if seen_for >= c.danger_s else Severity.WARNING
        if not self.tracker.is_active:
            if seen_for >= c.start_s:
                self.tracker.start(t, self._first_seen, severity, details)
        else:
            self.tracker.update(t, details, severity)

    def emit(self) -> list[Detection]:
        return self.tracker.emit()
