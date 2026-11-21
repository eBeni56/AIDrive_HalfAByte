"""Enumerations shared by signals, detectors and the JSON output (values are the JSON strings)."""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    NONE = "NONE"
    WARNING = "WARNING"
    DANGER = "DANGER"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @staticmethod
    def max(*values: Severity) -> Severity:
        return max(values, key=lambda s: s.rank, default=Severity.NONE)


_SEVERITY_RANK = {Severity.NONE: 0, Severity.WARNING: 1, Severity.DANGER: 2}


class DetectionType(StrEnum):
    EYES_CLOSED = "EYES_CLOSED"  # eyes closed longer than a blink
    DISTRACTED = "DISTRACTED"  # looking at none of the road / mirror / side-window zones (up or down)
    PHONE_USE = "PHONE_USE"  # a mobile phone in the hand


class DetectionStatus(StrEnum):
    STARTED = "STARTED"
    ONGOING = "ONGOING"
    ENDED = "ENDED"


class EyeState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class EyeSource(StrEnum):
    BOTH = "BOTH"
    LEFT_ONLY = "LEFT_ONLY"
    RIGHT_ONLY = "RIGHT_ONLY"


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
    def is_away(self) -> bool:
        """None of the places a driver has a reason to look at (road, mirrors, side windows)."""
        return self in (GazeZone.UP, GazeZone.DOWN)


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
