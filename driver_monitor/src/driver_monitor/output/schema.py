"""The `driver_state` message (schema_version 2). Source of truth for the JSON format.

Export the JSON Schema with:  uv run python -m driver_monitor --print-schema
Field-by-field reference and examples: docs/detection-schema.md; machine-readable: docs/driver_state.schema.json
(a test keeps it equal to this model).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from driver_monitor.common.enums import (
    CalibrationPhase,
    CalibrationStatus,
    DetectionStatus,
    DetectionType,
    EyeSource,
    EyeState,
    GazeZone,
    QualityIssue,
    Severity,
)

SCHEMA_VERSION = 2


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Summary(_M):
    highest_severity: Severity = Severity.NONE
    active: list[DetectionType] = Field(default_factory=list)  # the detections going on now


class Quality(_M):
    fps: float  # frames the pipeline actually processed per second
    camera_fps: float | None = None  # frames the camera delivers per second (measured); None for video files
    latency_ms: float  # processing time of this frame, not including capture
    face_detected: bool
    faces_in_view: int
    issues: list[QualityIssue] = Field(default_factory=list)


class Calibration(_M):
    status: CalibrationStatus
    phase: CalibrationPhase | None = None
    progress: float
    message: str | None = None


class Eyes(_M):
    state: EyeState = EyeState.UNKNOWN  # OPEN, CLOSED, UNKNOWN (no face / not measurable)
    state_for_s: float = 0.0
    openness: float | None = None  # 1 = this driver's normal open eye, 0 = closed
    source: EyeSource = EyeSource.BOTH  # which eye(s) were measured (one eye when the head is turned)


class Head(_M):
    yaw_deg: float | None = None  # relative to the calibrated straight-ahead: - left / + right
    pitch_deg: float | None = None  # - down / + up
    roll_deg: float | None = None


class Gaze(_M):
    zone: GazeZone = GazeZone.UNKNOWN  # ROAD, REAR/LEFT/RIGHT_MIRROR, LEFT/RIGHT_SIDE, UP, DOWN, UNKNOWN
    zone_for_s: float = 0.0
    yaw_deg: float | None = None  # head + eyes: - left / + right
    pitch_deg: float | None = None  # head + eyes: - down / + up


class DetectionOut(_M):
    id: str
    type: DetectionType
    severity: Severity
    status: DetectionStatus
    started_t: float
    ended_t: float | None
    duration_s: float
    details: dict[str, Any] = Field(default_factory=dict)


class DriverState(_M):
    type: Literal["driver_state"] = "driver_state"
    schema_version: int = SCHEMA_VERSION
    t: float
    frame: int
    summary: Summary
    quality: Quality
    calibration: Calibration
    eyes: Eyes
    head: Head
    gaze: Gaze
    detections: list[DetectionOut] = Field(default_factory=list)

    def active_detections(self) -> list[DetectionOut]:
        return [d for d in self.detections if d.status is not DetectionStatus.ENDED]
