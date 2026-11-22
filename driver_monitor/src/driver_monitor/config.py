"""All tunable settings. Defaults live here; a YAML file can override any subset.

Every threshold used by a detector comes from this module - nothing is hardcoded in the logic.
Times are seconds, angles degrees, openness is 0 (closed) .. 1 (this driver's normal open eye).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CameraConfig(_Section):
    # Camera 1 = the external "USB Live Camera" on the dev machine (better image than the integrated webcam,
    # which is camera 0). If the preferred index cannot be opened, the fallback indices are tried in order.
    index: int = 1
    fallback_indices: list[int] = Field(default_factory=lambda: [0])
    width: int = 1280
    height: int = 720
    fps: int = 30
    # auto = Media Foundation first (30 fps at 720p), DirectShow as fallback (often stuck at 10 fps: it
    # ignores the MJPG request and stays in raw YUY2, which USB 2.0 cannot carry faster at 720p).
    backend: Literal["auto", "dshow", "msmf", "any"] = "auto"


class LandmarkerConfig(_Section):
    model_path: str = "models/face_landmarker.task"
    num_faces: int = 2  # track up to 2, pick the largest (closest) face = the driver
    min_face_detection_confidence: float = 0.5
    min_face_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5


class PoseConfig(_Section):
    """Smoothing of the head angles (One-Euro filter)."""

    filter_min_cutoff: float = 1.0
    filter_beta: float = 0.05


class CalibrationConfig(_Section):
    open_phase_s: float = 6.0  # "look straight ahead, eyes open normally"
    closed_phase_s: float = 3.0  # "close your eyes" (set use_closed_phase=false to skip)
    use_closed_phase: bool = True
    closed_phase_settle_s: float = 0.8  # ignore the first moments of the closed phase (reaction time)
    min_valid_fraction: float = 0.7  # of the open phase with a face, else restart
    max_head_std_deg: float = 6.0  # head must be roughly still during the open phase
    # Recenter (at every simulation start, asked by the server: "look at the centre of the screen"): only the
    # straight-ahead look (the 0,0 of every angle) is measured again; the eye baselines are kept. Keep it shorter
    # than the simulator's countdown (5 s).
    recenter_s: float = 4.0
    recenter_settle_s: float = 1.0  # ignore the first moments (the driver turns to the centre)
    min_ear_margin: float = 0.04  # open - closed EAR must exceed this, else default ratio
    min_blink_margin: float = 0.15
    default_closed_ear_ratio: float = 0.4  # closed EAR ~ 0.4 x open EAR when no closed sample
    default_closed_blink_offset: float = 0.5  # closed blink score ~ open + 0.5 (capped at 0.9)
    # Population defaults used with --no-calibration
    default_open_ear: float = 0.28
    default_open_blink: float = 0.05


class EyesConfig(_Section):
    ear_weight: float = 0.5  # fusion weights of EAR-based and blendshape-based openness
    blink_weight: float = 0.5
    smoothing_tau_s: float = 0.04  # light smoothing, must not erase 100 ms blinks
    max_openness: float = 1.3
    # Eye state hysteresis (on fused openness): CLOSED below closed_enter, OPEN again above closed_exit
    closed_enter: float = 0.25
    closed_exit: float = 0.35
    # Pose gating
    single_eye_yaw_deg: float = 20.0  # beyond this use only the eye facing the camera
    unreliable_yaw_deg: float = 40.0
    unreliable_pitch_down_deg: float = 35.0
    unreliable_pitch_up_deg: float = 30.0
    min_interocular_px: float = 40.0
    # Looking down (eyelids follow the eyeball down, openness drops to ~0.3-0.5 with OPEN eyes).
    # Detected from the HEAD pitch, never from the eyeLookDown blendshape: real recordings showed that
    # eyeLookDown rises to 0.3-0.6 when the eyes CLOSE and stays low (0.1-0.2) when the head looks down.
    # While looking down the eyes are NOT assumed open - a closure only has to be clearer (stricter threshold).
    look_down_head_pitch_deg: float = -10.0  # head pitched below this (relative to neutral) = looking down
    looking_down_ear_weight: float = 0.8  # EAR is more trustworthy than eyeBlink when looking down
    closed_enter_looking_down: float = 0.15
    closed_exit_looking_down: float = 0.25


class EyeClosureConfig(_Section):
    closed_s: float = 0.5  # closed this long -> EYES_CLOSED (WARNING); a normal blink is ~0.1-0.3 s
    danger_s: float = 1.0  # ... this long -> DANGER (microsleep)
    reopen_s: float = 0.2  # eyes must stay open this long to end it (landmark jitter does not split it)
    unknown_hold_s: float = 1.0  # a closure survives tracking gaps (eyes UNKNOWN) this long


class GazeConfig(_Section):
    # eye-in-head angle from iris position along the eye axis (0..1) and from eyeLook* blendshapes
    iris_yaw_gain_deg: float = 150.0
    blend_yaw_gain_deg: float = 40.0
    iris_weight_h: float = 0.5
    iris_pitch_gain_deg: float = 120.0
    blend_pitch_gain_deg: float = 35.0
    iris_weight_v: float = 0.3
    max_eye_angle_deg: float = 45.0
    filter_min_cutoff: float = 1.5
    filter_beta: float = 0.05
    blink_hold_s: float = 0.5  # keep last eye angles during a blink
    # eyeLookUp/Down react to the LIDS (half-closed, squinting or closing eyes read as "looking down"), so the
    # blendshape part of the vertical eye angle is used only while the eyes are clearly open; below this
    # openness the vertical eye angle comes from the iris position alone.
    blend_pitch_min_openness: float = 0.6
    # The road box (gaze angles). Outside it: a mirror (gaze_zones), else DOWN below it, UP above it and
    # LEFT_SIDE / RIGHT_SIDE far beside it. Its hysteresis is gaze_zones.hysteresis_deg.
    on_road_yaw_deg: float = 20.0
    on_road_pitch_down_deg: float = 15.0
    on_road_pitch_up_deg: float = 15.0


class ZoneBox(_Section):
    """A rectangle of gaze angles (head + eyes, relative to the calibrated straight-ahead)."""

    yaw_min_deg: float
    yaw_max_deg: float
    pitch_min_deg: float
    pitch_max_deg: float

    def contains(self, yaw: float, pitch: float, margin: float = 0.0) -> bool:
        return (
            self.yaw_min_deg - margin <= yaw <= self.yaw_max_deg + margin
            and self.pitch_min_deg - margin <= pitch <= self.pitch_max_deg + margin
        )


class GazeZonesConfig(_Section):
    """Where the mirrors are. Gaze angles in deg: yaw - left / + right, pitch - down / + up, relative to the
    straight-ahead look recorded during calibration. A mirror box is checked before the road box.

    Defaults: a driving SIMULATOR in full screen. Looking from the middle outwards, per side:
      the road (|yaw| <= gaze.on_road_yaw_deg = 20 deg)   -> front_left / front_right, unchanged
      the SIDE MIRROR, yaw 20..55 deg, at any height      -> from the screen's lower corners up to eye level
      the side window, beyond 55 deg                      -> LEFT_SIDE / RIGHT_SIDE (a shoulder check)
    The side mirror is a wide band, not a small box: a look at the screen edge is mostly done with the eyes
    and the measured angles vary a lot (eyeLookDown alone is -30..-50 deg), so it must not depend on how low
    or how far out exactly the driver looks. The rear-view mirror is the top centre.
    Adjust to the actual screen layout; the overlay shows the live gaze angles and zone.
    The road box itself is gaze.on_road_*. Outside the road and the mirrors: DOWN below the road box, UP
    above it, LEFT_SIDE / RIGHT_SIDE beside it.
    """

    rear_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=-12.0, yaw_max_deg=12.0, pitch_min_deg=8.0, pitch_max_deg=25.0
        )
    )
    left_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=-55.0, yaw_max_deg=-20.0, pitch_min_deg=-90.0, pitch_max_deg=15.0
        )
    )
    right_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=20.0, yaw_max_deg=55.0, pitch_min_deg=-90.0, pitch_max_deg=15.0
        )
    )
    hysteresis_deg: float = 3.0  # a zone (mirror, road, down) is kept until the gaze is this far outside its box


class DistractionConfig(_Section):
    """DISTRACTED: the gaze is in none of the places a driver has a reason to look at - the road, the three
    mirrors, the side windows - i.e. it is UP or DOWN (phone, lap, console, roof)."""

    start_s: float = 1.0  # away this long -> DISTRACTED (WARNING)
    danger_s: float = 2.5  # ... this long -> DANGER
    end_s: float = 0.5  # back on the road / a mirror / a side window this long -> it ends


class PhoneConfig(_Section):
    """PHONE_USE: a mobile phone is visible in front of the driver (held in the hand). MediaPipe Object
    Detector (EfficientDet-Lite0, COCO class "cell phone") runs in the background at detect_hz, so it never
    slows down the face pipeline. Without the model file it is switched off with a warning."""

    enabled: bool = True
    model_path: str = "models/efficientdet_lite0.tflite"
    detect_hz: float = 6.0  # object detection runs per second (it is much slower than the face landmarker)
    min_score: float = 0.35  # "cell phone" confidence needed to count as seen
    start_s: float = 0.4  # seen (with gaps shorter than end_s) this long -> PHONE_USE starts (WARNING)
    danger_s: float = 3.0  # ... this long -> DANGER
    end_s: float = 1.0  # not seen this long -> it ends
    max_age_s: float = 0.6  # a detection result older than this is not "seen now"


class QualityConfig(_Section):
    low_light_mean: float = 50.0  # mean gray level 0..255
    low_fps: float = 15.0


class OutputConfig(_Section):
    terminal_hz: float = 5.0
    status_in_place: bool = True  # rewrite one status line instead of scrolling
    color: bool = True
    json_print_interval_s: float = 1.0


class WebSocketConfig(_Section):
    """Streams every driver_state JSON message to a WebSocket server while the camera runs.

    The address normally comes from config/driver_monitor.yaml (loaded automatically) or any --config file.
    Every processed frame is sent (~30 per second). The sender runs in its own thread: a missing or slow
    server never slows down the camera loop; the program keeps reconnecting and, while disconnected or
    behind, only the newest messages are kept.
    """

    enabled: bool = True
    url: str = "ws://localhost:8765"
    reconnect_s: float = 2.0  # wait between connection attempts


class CameraStreamConfig(_Section):
    """Serves the preview image (with the overlay) as an MJPEG stream for the dashboard:
    http://<host>:<port>/stream.mjpg. Frames are drawn and encoded only while someone watches."""

    enabled: bool = True
    host: str = "localhost"  # 0.0.0.0 in Docker
    port: int = 8090
    fps: float = 15.0  # at most this many frames per second to the viewers
    jpeg_quality: int = 70
    max_width: int = 960  # larger frames are scaled down (0 = keep the camera size)


class AppConfig(_Section):
    camera: CameraConfig = Field(default_factory=CameraConfig)
    landmarker: LandmarkerConfig = Field(default_factory=LandmarkerConfig)
    pose: PoseConfig = Field(default_factory=PoseConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    eyes: EyesConfig = Field(default_factory=EyesConfig)
    eye_closure: EyeClosureConfig = Field(default_factory=EyeClosureConfig)
    gaze: GazeConfig = Field(default_factory=GazeConfig)
    gaze_zones: GazeZonesConfig = Field(default_factory=GazeZonesConfig)
    distraction: DistractionConfig = Field(default_factory=DistractionConfig)
    phone: PhoneConfig = Field(default_factory=PhoneConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    websocket: WebSocketConfig = Field(default_factory=WebSocketConfig)
    camera_stream: CameraStreamConfig = Field(default_factory=CameraStreamConfig)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


DEFAULT_CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "driver_monitor.yaml"


def load_config(path: str | Path | None = None) -> AppConfig:
    """Defaults, overridden by the YAML file at `path` (if given). Unknown keys are an error."""
    if path is None:
        return AppConfig()
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    merged = _deep_merge(AppConfig().model_dump(), data)
    return AppConfig.model_validate(merged)


def dump_config(config: AppConfig) -> str:
    text: str = yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    return text
