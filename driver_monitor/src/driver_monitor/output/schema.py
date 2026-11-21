"""The `driver_state` message (schema_version 1). Source of truth for the JSON format.

Export the JSON Schema with:  uv run python -m driver_monitor --print-schema
Human-readable description and examples: docs/detection-schema.md
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from driver_monitor.common.enums import (
    AttentionState,
    CalibrationPhase,
    CalibrationStatus,
    DetectionStatus,
    DetectionType,
    DrowsinessLevel,
    EyeSource,
    EyeState,
    EyeTrend,
    GazeDirection,
    Horizontal,
    Intensity,
    MouthState,
    QualityIssue,
    Severity,
    Vertical,
)

SCHEMA_VERSION = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Summary(_M):
    highest_severity: Severity = Severity.NONE
    primary: DetectionType | None = None
    active: list[DetectionType] = Field(default_factory=list)


class Quality(_M):
    fps: float  # frames the pipeline actually processed per second
    camera_fps: float | None = None  # frames the camera delivers per second (measured); None for video files
    latency_ms: float  # processing time of this frame (MediaPipe + all detectors), not including capture
    face_detected: bool
    faces_in_view: int
    face_lost_for_s: float
    eye_metrics_reliable: bool
    issues: list[QualityIssue] = Field(default_factory=list)


class HeadAngles(_M):
    yaw_deg: float | None
    pitch_deg: float | None
    roll_deg: float | None


class PerEye(_M):
    left: float
    right: float


class Calibration(_M):
    status: CalibrationStatus
    phase: CalibrationPhase | None = None
    progress: float
    message: str | None = None
    neutral_head: HeadAngles | None = None
    open_ear: PerEye | None = None


class EyeReadingOut(_M):
    openness: float | None
    ear: float
    blink_score: float
    state: EyeState


class LastBlink(_M):
    ended_t: float
    duration_ms: float


class Eyes(_M):
    left: EyeReadingOut | None = None
    right: EyeReadingOut | None = None
    openness: float | None = None
    state: EyeState = EyeState.UNKNOWN
    state_for_s: float = 0.0
    trend: EyeTrend = EyeTrend.STABLE
    openness_velocity_per_s: float = 0.0
    source: EyeSource = EyeSource.BOTH
    lid_lowered_by_gaze: bool = False
    last_blink: LastBlink | None = None


class Head(_M):
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    yaw_velocity_dps: float | None = None
    pitch_velocity_dps: float | None = None
    roll_velocity_dps: float | None = None


class Gaze(_M):
    direction: GazeDirection = GazeDirection.UNKNOWN
    horizontal: Horizontal = Horizontal.CENTER
    vertical: Vertical = Vertical.CENTER
    intensity: Intensity = Intensity.NONE
    yaw_deg: float | None = None
    pitch_deg: float | None = None
    head_yaw_deg: float | None = None
    head_pitch_deg: float | None = None
    eye_yaw_deg: float | None = None
    eye_pitch_deg: float | None = None
    on_road: bool | None = None
    direction_for_s: float = 0.0


class Mouth(_M):
    jaw_open: float | None = None
    mar: float | None = None
    state: MouthState = MouthState.UNKNOWN
    state_for_s: float = 0.0


class Attention(_M):
    state: AttentionState = AttentionState.UNKNOWN
    eyes_off_road_s: float = 0.0
    off_road_last_30s_s: float = 0.0
    longest_glance_60s_s: float = 0.0
    glances_60s: int = 0


class Indicators(_M):
    perclos: float | None = None
    perclos_window_s: float = 0.0
    blink_rate_per_min: float | None = None
    mean_blink_duration_ms: float | None = None
    long_blinks_60s: int = 0
    mean_openness_60s: float | None = None
    yawns_5min: int = 0
    head_nods_5min: int = 0
    head_falls_10min: int = 0
    microsleeps_10min: int = 0


class HistoryWindow(_M):
    """Fatigue events in the last `window_s` seconds (from the fatigue ledger)."""

    window_s: float
    long_eye_closures: int = 0  # eye closures >= 0.5 s (incl. microsleeps)
    microsleeps: int = 0
    closed_eyes_s: float = 0.0  # total time spent in those closures
    yawns: int = 0
    head_nods: int = 0
    head_falls: int = 0


class Drowsiness(_M):
    level: DrowsinessLevel = DrowsinessLevel.ALERT
    level_index: int = 0
    score: float = 0.0
    peak_level: DrowsinessLevel = DrowsinessLevel.ALERT
    warming_up: bool = True
    contributing: list[str] = Field(default_factory=list)
    # decaying sum of fatigue events (eye closures >= 0.5 s weighted by duration: 1 s 0.5, 2 s 1.1, 5 s 2.0;
    # head fall 1.0, nod 0.6, yawn 0.4; half-life 5 min)
    fatigue_load: float = 0.0
    history: list[HistoryWindow] = Field(default_factory=list)  # last 1 / 5 / 15 min
    indicators: Indicators = Field(default_factory=Indicators)


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
    mouth: Mouth
    attention: Attention
    drowsiness: Drowsiness
    detections: list[DetectionOut] = Field(default_factory=list)

    def active_detections(self) -> list[DetectionOut]:
        return [d for d in self.detections if d.status is not DetectionStatus.ENDED]
