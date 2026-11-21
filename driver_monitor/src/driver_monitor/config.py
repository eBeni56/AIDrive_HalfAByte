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
    mjpg: bool = True  # request MJPG on DirectShow (MSMF negotiates a compressed format itself)


class LandmarkerConfig(_Section):
    model_path: str = "models/face_landmarker.task"
    num_faces: int = 2  # track up to 2, pick the largest (closest) face = the driver
    min_face_detection_confidence: float = 0.5
    min_face_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5


class PoseConfig(_Section):
    """Sign conversion from the matrix decomposition to the schema convention.

    The decomposition (tutorial ch. 6) gives +yaw = driver's left, +pitch = down, +roll = driver's right.
    The schema wants  +yaw = driver's right, +pitch = up, +roll = toward the right shoulder.
    If the live test shows a wrong sign on your setup, flip it here - nowhere else.
    """

    yaw_sign: float = -1.0
    pitch_sign: float = -1.0
    roll_sign: float = 1.0
    filter_min_cutoff: float = 1.0  # One-Euro filter for head angles
    filter_beta: float = 0.05
    velocity_tau_s: float = 0.15
    history_s: float = 12.0


class CalibrationConfig(_Section):
    enabled: bool = True
    open_phase_s: float = 6.0  # "look straight ahead, eyes open normally"
    closed_phase_s: float = 3.0  # "close your eyes" (set use_closed_phase=false to skip)
    use_closed_phase: bool = True
    closed_phase_settle_s: float = 0.8  # ignore the first moments of the closed phase (reaction time)
    min_valid_fraction: float = 0.7  # of the open phase with a face, else restart
    max_head_std_deg: float = 6.0  # head must be roughly still during the open phase
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
    velocity_tau_s: float = 0.12
    max_openness: float = 1.3
    # Eye state hysteresis (on fused openness)
    closed_enter: float = 0.25
    closed_exit: float = 0.35
    almost_enter: float = 0.55
    almost_exit: float = 0.62
    trend_velocity_per_s: float = 0.4  # |d openness / dt| above this -> CLOSING/OPENING
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
    # Smile / squint guard
    smile_threshold: float = 0.5  # mean mouthSmile
    cheek_squint_threshold: float = 0.4  # mean cheekSquint


class EyeClosureConfig(_Section):
    min_blink_s: float = 0.05  # shorter closures are landmark glitches
    eyes_closed_s: float = 0.5  # EYES_CLOSED warning (longer than any normal blink)
    microsleep_s: float = 1.0  # MICROSLEEP critical
    reopen_confirm_s: float = 0.3  # eyes must stay not-closed this long to end a microsleep
    flicker_s: float = 0.15  # a closure survives not-closed blips shorter than this (landmark jitter)
    unknown_hold_s: float = 2.0  # keep a closure alive through short tracking gaps


class AlmostClosedConfig(_Section):
    start_after_s: float = 2.0
    end_after_s: float = 1.0


class EyeBaselineConfig(_Section):
    """This driver's NORMAL eye opening while driving, learned right after calibration.

    Calibration asks for "eyes open normally", but people often open them wider while staring at the camera,
    and eyes differ a lot between people. So every "narrowed" / "heavy eyelids" threshold is a RATIO to
    this learned value - nothing depends on the absolute size of anyone's eyes.
    Learned from the first `learn_s` seconds of clean samples (eyes open, head near straight ahead, not
    looking down, smiling or yawning, no blink); until then the calibrated open eye (1.0) is used.
    """

    learn_s: float = 30.0
    percentile: float = 75.0  # robust "normal": ignores blinks and short narrowing in the learning period
    max_head_angle_deg: float = 15.0
    after_blink_s: float = 0.25  # samples this soon after a closure are skipped (lids still moving)


class NarrowedEyesConfig(_Section):
    """EYES_NARROWED: eyes clearly narrower than this driver's normal for a while (heavy eyelids, a typical
    early tiredness sign) - milder than EYES_ALMOST_CLOSED. Ratios are to the learned personal baseline."""

    window_s: float = 3.0  # median of the clean samples in this window (blinks excluded)
    min_valid_fraction: float = 0.5  # of the window, else the ratio is unknown
    enter_ratio: float = 0.85
    exit_ratio: float = 0.90
    start_after_s: float = 5.0
    end_after_s: float = 2.0
    warning_ratio: float = 0.70  # narrower than this -> WARNING (else INFO) ...
    warning_after_s: float = 20.0  # ... or narrowed for this long


class TiredEyesConfig(_Section):
    window_s: float = 60.0
    min_coverage_s: float = 30.0
    heavy_eyelids_below: float = 0.80  # mean openness in the window as a fraction of the driver's normal
    slow_blink_ms: float = 300.0
    min_blinks_for_duration: int = 3
    long_blinks: int = 3
    end_after_s: float = 30.0


class YawnConfig(_Section):
    # A yawn = one continuous mouth opening (short dips are tolerated) that is either WIDE and >= wide_min_s,
    # or MODERATE and long (>= moderate_min_s, natural yawns rarely open fully). Speech stays below
    # open_mar most of the time and keeps opening/closing, so it does not form one long opening.
    open_mar: float = 0.30  # inner-lip MAR at or above this = mouth open (part of a possible yawn)
    open_jaw: float = 0.40
    dip_s: float = 0.15  # the opening may dip below the open level this long without ending (speech: longer)
    wide_mar: float = 0.55
    wide_jaw: float = 0.65
    wide_min_s: float = 0.4  # speech never opened this wide in the test recordings (MAR stayed < 0.3)
    moderate_mar: float = 0.40
    moderate_jaw: float = 0.50
    moderate_min_s: float = 1.5
    # jawOpen reads ~0.6 with a CLOSED mouth when the head is pitched down (chin compresses): ignore it then
    ignore_jaw_below_pitch_deg: float = -10.0
    end_after_s: float = 0.5  # mouth below the open level this long ends the yawn
    eyes_squeezed_below: float = 0.55


class MouthConfig(_Section):
    open_mar: float = 0.15
    wide_open_mar: float = 0.5
    open_jaw: float = 0.2
    wide_open_jaw: float = 0.45
    smoothing_tau_s: float = 0.08


class NodConfig(_Section):
    drop_deg: float = 12.0
    drop_window_s: float = 0.8
    min_velocity_dps: float = 25.0
    recover_within_s: float = 2.0
    recover_fraction: float = 0.5
    # The dip must start from a normal head position (not from looking up/down) ...
    start_min_pitch_deg: float = -8.0
    start_max_pitch_deg: float = 8.0
    max_yaw_deg: float = 30.0
    # ... and the eyes must be MEASURED closed during it, longer than the blink that comes with a gaze shift
    # (a glance down lowers the lids to ~0.4 with open eyes; unknown eyes never count) and mostly not open:
    # eyes OPEN for more than max_open_fraction of the dip = the driver looked at something down there.
    min_closed_s: float = 0.4
    max_open_fraction: float = 0.3
    danger_closed_s: float = 0.5  # closed this long during the dip -> DANGER, else WARNING
    window_s: float = 300.0


class HeadFallingConfig(_Section):
    # A falling head ALWAYS needs measured closed eyes. Looking down at a phone (head 15-25 deg down, lids
    # lowered, openness ~0.5) is distraction, never head falling.
    pitch_down_deg: float = 15.0
    hold_s: float = 1.0  # head down AND eyes closed together for this long
    drift_window_s: float = 3.0
    drift_min_deg: float = 10.0
    drift_min_slope_dps: float = 2.0
    drift_closed_s: float = 0.5  # a slow drift counts once the eyes have been closed this long
    max_yaw_deg: float = 30.0  # beyond this the pitch/roll estimates are unreliable (near profile view)
    sudden_drop_deg: float = 12.0
    sudden_drop_window_s: float = 0.8
    roll_deg: float = 20.0
    roll_drift_deg: float = 15.0
    backward_pitch_deg: float = 20.0
    backward_closed_s: float = 1.0
    critical_drop_deg: float = 30.0
    still_falling_dps: float = 2.0
    end_within_deg: float = 8.0
    end_roll_deg: float = 10.0
    end_after_s: float = 1.0
    end_eyes_open_s: float = 2.0  # eyes open again for this long ends it even if the head stays down
    window_s: float = 600.0


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
    horizontal_deg: float = 12.0
    up_deg: float = 12.0
    down_deg: float = 10.0
    direction_hysteresis_deg: float = 2.0
    intensity_steps_deg: list[float] = Field(default_factory=lambda: [12.0, 25.0, 45.0, 65.0])
    on_road_yaw_deg: float = 20.0
    on_road_pitch_down_deg: float = 15.0
    on_road_pitch_up_deg: float = 15.0
    on_road_hysteresis_deg: float = 3.0


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

    Defaults: a driving SIMULATOR (driver ~60-80 cm from the screen, rear-view mirror inset at the top
    centre of the screen, side mirrors in the lower left / right corners). Adjust to the actual screen
    layout; the overlay shows the live gaze angles and zone. For a real left-hand-drive car something like:
      rear_mirror  yaw 18..45   pitch 3..30
      left_mirror  yaw -70..-30 pitch -25..5
      right_mirror yaw 45..85   pitch -25..5
    The road box itself is gaze.on_road_*; anything below it is DOWN, beside it LEFT_SIDE / RIGHT_SIDE,
    above it UP.
    """

    rear_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=-12.0, yaw_max_deg=12.0, pitch_min_deg=8.0, pitch_max_deg=25.0
        )
    )
    left_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=-38.0, yaw_max_deg=-12.0, pitch_min_deg=-25.0, pitch_max_deg=-3.0
        )
    )
    right_mirror: ZoneBox = Field(
        default_factory=lambda: ZoneBox(
            yaw_min_deg=12.0, yaw_max_deg=38.0, pitch_min_deg=-25.0, pitch_max_deg=-3.0
        )
    )
    hysteresis_deg: float = 2.0  # a zone is kept until the gaze is this far outside its box


class DistractionConfig(_Section):
    """Different rules per gaze zone. A driver has reasons to look left/right (traffic, side roads) and at
    the mirrors, but no reason to look DOWN (phone, lap, console): that is judged much faster."""

    down_warning_s: float = 1.2  # one glance with this much time DOWN -> WARNING
    down_danger_s: float = 2.0
    side_warning_s: float = 3.0  # one glance away from the road (sideways, up, or staring at a mirror)
    side_danger_s: float = 5.0
    # Many short glances: weighted off-road seconds in the window (DOWN counts fully, sideways/up half,
    # mirror checks not at all).
    cumulative_window_s: float = 30.0
    cumulative_s: float = 10.0
    cumulative_end_s: float = 8.0
    down_weight: float = 1.0
    side_weight: float = 0.5
    mirror_weight: float = 0.0
    end_on_road_s: float = 1.0
    stats_window_s: float = 60.0
    min_glance_s: float = 0.2  # shorter off-road blips are not counted as glances


class FaceVisibilityConfig(_Section):
    lost_after_s: float = 2.0
    danger_after_s: float = 5.0
    recover_s: float = 0.5
    edge_margin: float = 0.12
    turned_yaw_deg: float = 35.0
    turned_yaw_velocity_dps: float = 60.0
    dropped_pitch_deg: float = -20.0
    dropped_pitch_velocity_dps: float = -20.0
    context_s: float = 0.6  # how far back the "last known pose" looks


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


class DrowsinessConfig(_Section):
    perclos_window_s: float = 60.0
    perclos_closed_openness: float = 0.2  # P80: eyes >= 80 % closed
    min_coverage_s: float = 30.0
    max_gap_s: float = 0.3  # longer frame gaps are not counted as data
    blink_window_s: float = 60.0
    yawn_window_s: float = 300.0
    nod_window_s: float = 300.0
    fall_window_s: float = 600.0
    microsleep_window_s: float = 600.0
    score_rise_tau_s: float = 3.0
    score_fall_tau_s: float = 15.0
    level_thresholds: list[float] = Field(default_factory=lambda: [0.25, 0.45, 0.65, 0.80])
    others_weight: float = 0.2  # score = max(sub) + others_weight * sum(other subs)
    contributing_min: float = 0.25
    level_drop_interval_s: float = 30.0  # the level drops at most one step per this many seconds
    # Fatigue memory: once the level reached memory_min_peak (VERY_DROWSY) it never drops below
    # peak - memory_levels in this session. A single short event never sets a memory.
    memory_levels: int = 2
    memory_min_peak: int = 3
    # Fatigue events. Acute dangers are alarmed instantly by their own detections (EYES_CLOSED at 0.5 s,
    # MICROSLEEP CRITICAL at 1 s); for the level they are evidence that ADDS UP over time and decays:
    #   load = sum(weight * 0.5 ** (age / half_life))
    # This covers both patterns: one long closure (heavy weight, then fades) and many shorter closures
    # over minutes (each light, but they stack while they keep coming).
    fatigue_half_life_s: float = 300.0
    # every eye closure >= eye_closure.eyes_closed_s (0.5 s) is an event, weighted by its duration
    # (smooth - no cliff at the 1 s microsleep threshold)
    closure_weight_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.5, 0.2), (1.0, 0.5), (1.5, 0.9), (2.0, 1.1), (3.0, 1.5), (5.0, 2.0)]
    )
    event_weights: dict[str, float] = Field(
        default_factory=lambda: {"HEAD_FALL": 1.0, "HEAD_NOD": 0.6, "YAWN": 0.4}
    )
    # load -> evidence: one 2 s closure ~ slightly drowsy (fades within minutes), a 5 s closure or two
    # microsleeps close together ~ drowsy; steady long blinks (0.8 s, 2/min) ~ drowsy after ~5 min and
    # very drowsy after ~10 min
    load_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.0, 0.0), (1.0, 0.3), (2.0, 0.55), (3.0, 0.7), (4.0, 0.85)]
    )
    history_windows_s: list[float] = Field(default_factory=lambda: [60.0, 300.0, 900.0])
    # Narrowed eyes (EYES_NARROWED, ratio to the driver's normal opening): evidence while it lasts
    # (0.3 = slightly drowsy, ~0.5 = drowsy), a bonus when it lasts long, and a fatigue event when it ends
    # (weight grows with its duration) so that repeated episodes stack up like closures do.
    narrowed_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.85, 0.3), (0.75, 0.4), (0.65, 0.5), (0.55, 0.6)]
    )
    narrowed_long_s: float = 30.0
    narrowed_long_bonus: float = 0.15
    narrowed_weight_per_min: float = 0.5
    narrowed_max_weight: float = 1.0
    # piecewise-linear maps indicator value -> evidence 0..1 (0.25 slight, 0.5 drowsy, 0.75 very)
    perclos_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.04, 0.0), (0.08, 0.25), (0.15, 0.5), (0.25, 0.75), (0.40, 1.0)]
    )
    # 60 s mean openness as a fraction of the driver's learned normal opening
    openness_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.85, 0.0), (0.75, 0.25), (0.65, 0.5), (0.50, 0.75), (0.35, 1.0)]
    )
    blink_duration_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(220.0, 0.0), (300.0, 0.25), (400.0, 0.5), (550.0, 0.75), (800.0, 1.0)]
    )
    long_blink_curve: list[tuple[float, float]] = Field(
        default_factory=lambda: [(0.0, 0.0), (2.0, 0.25), (4.0, 0.5), (7.0, 0.75), (10.0, 1.0)]
    )


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
    The sender runs in its own thread: a missing or slow server never slows down the camera loop; the program
    keeps reconnecting and, while disconnected or behind, only the newest messages are kept.
    """

    enabled: bool = True
    url: str = "ws://localhost:8765"
    max_rate_hz: float = 0.0  # 0 = every processed frame (~30/s); e.g. 10 = at most 10 messages per second
    reconnect_s: float = 2.0  # wait between connection attempts
    connect_timeout_s: float = 3.0
    queue_size: int = 30  # messages buffered while sending is slower than the camera (oldest dropped)


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
    almost_closed: AlmostClosedConfig = Field(default_factory=AlmostClosedConfig)
    eye_baseline: EyeBaselineConfig = Field(default_factory=EyeBaselineConfig)
    narrowed_eyes: NarrowedEyesConfig = Field(default_factory=NarrowedEyesConfig)
    tired_eyes: TiredEyesConfig = Field(default_factory=TiredEyesConfig)
    mouth: MouthConfig = Field(default_factory=MouthConfig)
    yawn: YawnConfig = Field(default_factory=YawnConfig)
    nod: NodConfig = Field(default_factory=NodConfig)
    head_falling: HeadFallingConfig = Field(default_factory=HeadFallingConfig)
    gaze: GazeConfig = Field(default_factory=GazeConfig)
    gaze_zones: GazeZonesConfig = Field(default_factory=GazeZonesConfig)
    distraction: DistractionConfig = Field(default_factory=DistractionConfig)
    face: FaceVisibilityConfig = Field(default_factory=FaceVisibilityConfig)
    phone: PhoneConfig = Field(default_factory=PhoneConfig)
    drowsiness: DrowsinessConfig = Field(default_factory=DrowsinessConfig)
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
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
