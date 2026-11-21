"""Enumerations shared by signals, detectors and the JSON output (values are the JSON strings)."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    NONE = "NONE"
    INFO = "INFO"
    WARNING = "WARNING"
    DANGER = "DANGER"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @staticmethod
    def max(*values: Severity) -> Severity:
        return max(values, key=lambda s: s.rank, default=Severity.NONE)


_SEVERITY_RANK = {
    Severity.NONE: 0,
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.DANGER: 3,
    Severity.CRITICAL: 4,
}


class DetectionType(StrEnum):
    EYES_CLOSED = "EYES_CLOSED"
    MICROSLEEP = "MICROSLEEP"
    EYES_ALMOST_CLOSED = "EYES_ALMOST_CLOSED"
    EYES_NARROWED = "EYES_NARROWED"
    TIRED_EYES = "TIRED_EYES"
    YAWN = "YAWN"
    HEAD_NOD = "HEAD_NOD"
    HEAD_FALLING = "HEAD_FALLING"
    DISTRACTED = "DISTRACTED"
    FACE_NOT_VISIBLE = "FACE_NOT_VISIBLE"
    PHONE_USE = "PHONE_USE"
    DROWSY = "DROWSY"


class DetectionStatus(StrEnum):
    STARTED = "STARTED"
    ONGOING = "ONGOING"
    ENDED = "ENDED"


class EyeState(StrEnum):
    OPEN = "OPEN"
    ALMOST_CLOSED = "ALMOST_CLOSED"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class EyeTrend(StrEnum):
    CLOSING = "CLOSING"
    OPENING = "OPENING"
    STABLE = "STABLE"


class EyeSource(StrEnum):
    BOTH = "BOTH"
    LEFT_ONLY = "LEFT_ONLY"
    RIGHT_ONLY = "RIGHT_ONLY"


class Horizontal(StrEnum):
    LEFT = "LEFT"
    CENTER = "CENTER"
    RIGHT = "RIGHT"


class Vertical(StrEnum):
    UP = "UP"
    CENTER = "CENTER"
    DOWN = "DOWN"


class GazeDirection(StrEnum):
    CENTER = "CENTER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP = "UP"
    DOWN = "DOWN"
    UP_LEFT = "UP_LEFT"
    UP_RIGHT = "UP_RIGHT"
    DOWN_LEFT = "DOWN_LEFT"
    DOWN_RIGHT = "DOWN_RIGHT"
    UNKNOWN = "UNKNOWN"

    @staticmethod
    def combine(h: Horizontal, v: Vertical) -> GazeDirection:
        if h is Horizontal.CENTER and v is Vertical.CENTER:
            return GazeDirection.CENTER
        if v is Vertical.CENTER:
            return GazeDirection(h.value)
        if h is Horizontal.CENTER:
            return GazeDirection(v.value)
        return GazeDirection(f"{v.value}_{h.value}")


class GazeZone(StrEnum):
    """Where the driver looks (config: gaze_zones + gaze.on_road_*)."""

    ROAD = "ROAD"
    REAR_MIRROR = "REAR_MIRROR"
    LEFT_MIRROR = "LEFT_MIRROR"
    RIGHT_MIRROR = "RIGHT_MIRROR"
    LEFT_SIDE = "LEFT_SIDE"
    RIGHT_SIDE = "RIGHT_SIDE"
    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"

    @property
    def is_mirror(self) -> bool:
        return self in (GazeZone.REAR_MIRROR, GazeZone.LEFT_MIRROR, GazeZone.RIGHT_MIRROR)


class Intensity(StrEnum):
    NONE = "NONE"
    SLIGHT = "SLIGHT"
    MODERATE = "MODERATE"
    STRONG = "STRONG"
    EXTREME = "EXTREME"


class MouthState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    WIDE_OPEN = "WIDE_OPEN"
    UNKNOWN = "UNKNOWN"


class AttentionState(StrEnum):
    ATTENTIVE = "ATTENTIVE"
    CHECKING_MIRROR = "CHECKING_MIRROR"
    GLANCING_AWAY = "GLANCING_AWAY"
    DISTRACTED = "DISTRACTED"
    UNKNOWN = "UNKNOWN"


class DrowsinessLevel(StrEnum):
    ALERT = "ALERT"
    SLIGHTLY_DROWSY = "SLIGHTLY_DROWSY"
    DROWSY = "DROWSY"
    VERY_DROWSY = "VERY_DROWSY"
    SEVERELY_DROWSY = "SEVERELY_DROWSY"

    @property
    def number(self) -> int:
        """0 (ALERT) .. 4 (SEVERELY_DROWSY)."""
        return list(DrowsinessLevel).index(self)

    @staticmethod
    def from_index(i: int) -> DrowsinessLevel:
        levels = list(DrowsinessLevel)
        return levels[max(0, min(i, len(levels) - 1))]


class CalibrationStatus(StrEnum):
    CALIBRATING = "CALIBRATING"
    DONE = "DONE"
    DEFAULTS = "DEFAULTS"


class CalibrationPhase(StrEnum):
    OPEN_EYES = "OPEN_EYES"
    CLOSED_EYES = "CLOSED_EYES"


class QualityIssue(StrEnum):
    CALIBRATING = "CALIBRATING"
    HEAD_TURNED_TOO_FAR = "HEAD_TURNED_TOO_FAR"
    FACE_TOO_SMALL = "FACE_TOO_SMALL"
    LOW_LIGHT = "LOW_LIGHT"
    LOW_FPS = "LOW_FPS"
    FACE_PARTIALLY_OUT_OF_FRAME = "FACE_PARTIALLY_OUT_OF_FRAME"
