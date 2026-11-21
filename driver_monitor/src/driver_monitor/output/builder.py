"""Converts the internal signals/state/detections of one frame into the `DriverState` JSON model."""

from __future__ import annotations

from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.enums import (
    AttentionState,
    CalibrationPhase,
    CalibrationStatus,
    DetectionStatus,
    QualityIssue,
    Severity,
)
from driver_monitor.detectors.base import Detection
from driver_monitor.output.schema import (
    Attention,
    Calibration,
    DetectionOut,
    Drowsiness,
    EyeReadingOut,
    Eyes,
    Gaze,
    Head,
    HeadAngles,
    HistoryWindow,
    Indicators,
    LastBlink,
    Mouth,
    PerEye,
    Quality,
    Summary,
)
from driver_monitor.signals.eyes import EyeReading, EyesSignal
from driver_monitor.signals.gaze import GazeSignal
from driver_monitor.signals.head import HeadSignal
from driver_monitor.signals.mouth import MouthSignal
from driver_monitor.state.attention import AttentionSnapshot
from driver_monitor.state.drowsiness import DrowsinessState
from driver_monitor.state.eye_stats import EyeStats
from driver_monitor.state.fatigue import FatigueSnapshot


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def summary(detections: list[Detection]) -> Summary:
    active = [d for d in detections if d.status is not DetectionStatus.ENDED]
    if not active:
        return Summary()
    top = max(active, key=lambda d: (d.severity.rank, d.started_t))
    return Summary(
        highest_severity=Severity.max(*(d.severity for d in active)),
        primary=top.type,
        active=[d.type for d in active],
    )


def detections_out(detections: list[Detection]) -> list[DetectionOut]:
    out = [
        DetectionOut(
            id=d.id,
            type=d.type,
            severity=d.severity,
            status=d.status,
            started_t=round(d.started_t, 3),
            ended_t=_r(d.ended_t, 3),
            duration_s=round(d.duration_s, 2),
            details=d.details,
        )
        for d in detections
    ]
    order = {
        s: i for i, s in enumerate((Severity.CRITICAL, Severity.DANGER, Severity.WARNING, Severity.INFO))
    }
    return sorted(out, key=lambda d: (d.status is DetectionStatus.ENDED, order.get(d.severity, 9)))


def quality(
    fps: float,
    latency_ms: float,
    face_detected: bool,
    faces: int,
    lost_for: float,
    eyes: EyesSignal | None,
    issues: list[QualityIssue],
    camera_fps: float | None = None,
) -> Quality:
    return Quality(
        fps=round(fps, 1),
        camera_fps=None if camera_fps is None else round(camera_fps, 1),
        latency_ms=round(latency_ms, 1),
        face_detected=face_detected,
        faces_in_view=faces,
        face_lost_for_s=round(lost_for, 2),
        eye_metrics_reliable=bool(eyes and eyes.reliable),
        issues=issues,
    )


def calibration(
    status: CalibrationStatus,
    phase: CalibrationPhase | None,
    progress: float,
    message: str | None,
    profile: DriverProfile | None,
) -> Calibration:
    neutral = open_ear = None
    if profile is not None:
        neutral = HeadAngles(
            yaw_deg=_r(profile.neutral_yaw, 1),
            pitch_deg=_r(profile.neutral_pitch, 1),
            roll_deg=_r(profile.neutral_roll, 1),
        )
        open_ear = PerEye(left=round(profile.left.open_ear, 3), right=round(profile.right.open_ear, 3))
    return Calibration(
        status=status,
        phase=phase,
        progress=round(progress, 2),
        message=message,
        neutral_head=neutral,
        open_ear=open_ear,
    )


def _eye(e: EyeReading | None) -> EyeReadingOut | None:
    if e is None:
        return None
    return EyeReadingOut(
        openness=_r(e.openness), ear=round(e.ear, 3), blink_score=round(e.blink, 2), state=e.state
    )


def eyes(
    e: EyesSignal,
    last_blink: tuple[float, float] | None,
    normal_openness: float = 1.0,
    normal_learned: bool = False,
    openness_ratio: float | None = None,
) -> Eyes:
    lb = (
        LastBlink(ended_t=round(last_blink[0], 3), duration_ms=round(last_blink[1] * 1000.0))
        if last_blink
        else None
    )
    return Eyes(
        left=_eye(e.left),
        right=_eye(e.right),
        openness=_r(e.openness),
        state=e.state,
        state_for_s=round(e.state_for_s, 2),
        trend=e.trend,
        openness_velocity_per_s=round(e.velocity, 2),
        source=e.source,
        lid_lowered_by_gaze=e.lid_lowered_by_gaze,
        last_blink=lb,
        normal_openness=round(normal_openness, 2),
        normal_learned=normal_learned,
        openness_ratio=_r(openness_ratio),
    )


def head(h: HeadSignal) -> Head:
    return Head(
        yaw_deg=_r(h.yaw, 1),
        pitch_deg=_r(h.pitch, 1),
        roll_deg=_r(h.roll, 1),
        yaw_velocity_dps=_r(h.yaw_velocity, 1),
        pitch_velocity_dps=_r(h.pitch_velocity, 1),
        roll_velocity_dps=_r(h.roll_velocity, 1),
    )


def gaze(g: GazeSignal) -> Gaze:
    return Gaze(
        direction=g.direction,
        horizontal=g.horizontal,
        vertical=g.vertical,
        intensity=g.intensity,
        yaw_deg=_r(g.yaw, 1),
        pitch_deg=_r(g.pitch, 1),
        head_yaw_deg=_r(g.head_yaw, 1),
        head_pitch_deg=_r(g.head_pitch, 1),
        eye_yaw_deg=_r(g.eye_yaw, 1),
        eye_pitch_deg=_r(g.eye_pitch, 1),
        on_road=g.on_road,
        direction_for_s=round(g.direction_for_s, 2),
        zone=g.zone,
        zone_for_s=round(g.zone_for_s, 2),
    )


def mouth(m: MouthSignal) -> Mouth:
    return Mouth(jaw_open=_r(m.jaw_open), mar=_r(m.mar), state=m.state, state_for_s=round(m.state_for_s, 2))


def attention(a: AttentionSnapshot, distracted: bool) -> Attention:
    state = AttentionState.DISTRACTED if distracted and a.state is not AttentionState.UNKNOWN else a.state
    return Attention(
        state=state,
        zone=a.zone,
        eyes_off_road_s=round(a.eyes_off_road_s, 2),
        glance_down_s=round(a.glance_down_s, 2),
        off_road_last_30s_s=round(a.off_road_window_s, 1),
        weighted_off_road_30s_s=round(a.weighted_off_road_s, 1),
        longest_glance_60s_s=round(a.longest_glance_s, 2),
        glances_60s=a.glances,
    )


def drowsiness(
    s: DrowsinessState,
    e: EyeStats,
    fatigue: FatigueSnapshot,
    yawns: int,
    nods: int,
    falls: int,
    microsleeps: int,
) -> Drowsiness:
    return Drowsiness(
        level=s.level,
        level_index=s.level.number,
        score=round(s.score, 2),
        peak_level=s.peak_level,
        warming_up=s.warming_up,
        contributing=s.contributing,
        fatigue_load=round(fatigue.load, 2),
        history=[
            HistoryWindow(
                window_s=w.window_s,
                long_eye_closures=w.long_eye_closures,
                microsleeps=w.microsleeps,
                closed_eyes_s=round(w.closed_eyes_s, 2),
                yawns=w.yawns,
                head_nods=w.head_nods,
                head_falls=w.head_falls,
                narrowed_eyes=w.narrowed_eyes,
                narrowed_eyes_s=round(w.narrowed_eyes_s, 1),
            )
            for w in fatigue.windows
        ],
        indicators=Indicators(
            perclos=_r(e.perclos, 3),
            perclos_window_s=round(min(e.coverage_s, 60.0), 1),
            blink_rate_per_min=_r(e.blink_rate_per_min, 1),
            mean_blink_duration_ms=_r(e.mean_blink_duration_ms, 0),
            long_blinks_60s=e.long_blinks,
            mean_openness_60s=_r(e.mean_openness),
            mean_openness_ratio_60s=_r(e.mean_openness_ratio),
            yawns_5min=yawns,
            head_nods_5min=nods,
            head_falls_10min=falls,
            microsleeps_10min=microsleeps,
        ),
    )
