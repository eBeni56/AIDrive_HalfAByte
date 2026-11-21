"""DriverMonitor: frame in -> DriverState out. Wires perception, calibration, signals, state and detectors.

Per frame, in this order (later steps may use earlier results):
  1 features   MediaPipe landmarks/blendshapes/matrix -> EAR, MAR, iris, head angles ...
  2 signals    head (relative, smoothed) -> eyes (openness, state) -> gaze (head + eyes) -> mouth
  3 detectors  eye closure/microsleep -> yawn -> eyes almost closed -> personal eye baseline + eyes
               narrowed -> head nod -> head falling
               -> attention + distraction -> face visibility -> phone in hand -> eye stats + tired eyes
               -> fatigue ledger (events of this frame) -> drowsiness
  4 output     DriverState (summary, quality, calibration, signals, windowed state, detections)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from driver_monitor.calibration.calibrator import Calibrator
from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.enums import CalibrationStatus, QualityIssue
from driver_monitor.common.filters import Ema
from driver_monitor.config import AppConfig
from driver_monitor.detectors.almost_closed import EyesAlmostClosedDetector
from driver_monitor.detectors.base import Detection
from driver_monitor.detectors.distraction import DistractionDetector
from driver_monitor.detectors.drowsy import DrowsyDetector
from driver_monitor.detectors.eye_closure import EyeClosureDetector
from driver_monitor.detectors.face_visibility import FaceVisibilityDetector
from driver_monitor.detectors.head_falling import HeadFallingDetector
from driver_monitor.detectors.head_nod import HeadNodDetector
from driver_monitor.detectors.narrowed import EyesNarrowedDetector
from driver_monitor.detectors.phone import PhoneDetector
from driver_monitor.detectors.tired_eyes import TiredEyesDetector
from driver_monitor.detectors.yawn import YawnDetector
from driver_monitor.output import builder
from driver_monitor.output.schema import DriverState
from driver_monitor.perception.face_tracker import FaceTracker
from driver_monitor.perception.features import FeatureExtractor, FrameFeatures
from driver_monitor.perception.phone_finder import PhoneFinder, PhoneSighting
from driver_monitor.signals.eyes import EyeSignalProcessor, EyesSignal
from driver_monitor.signals.gaze import GazeEstimator, GazeSignal
from driver_monitor.signals.head import HeadPoseProcessor, HeadSignal
from driver_monitor.signals.mouth import MouthProcessor, MouthSignal
from driver_monitor.state.attention import AttentionTracker
from driver_monitor.state.drowsiness import DrowsinessEstimator, DrowsinessInputs
from driver_monitor.state.eye_baseline import EyeBaseline
from driver_monitor.state.eye_stats import EyeStatistics
from driver_monitor.state.fatigue import FatigueLedger


@dataclass(slots=True)
class FrameResult:
    """Everything the UI needs for one frame."""

    state: DriverState
    features: FrameFeatures
    head: HeadSignal
    eyes: EyesSignal
    gaze: GazeSignal
    mouth: MouthSignal
    phone: PhoneSighting | None = None  # the phone seen in the newest analysed frame (for the overlay)


class DriverMonitor:
    def __init__(
        self,
        cfg: AppConfig,
        base_dir: Path | None = None,
        tracker: FaceTracker | None = None,
        calibrate: bool = True,
    ) -> None:
        self.cfg = cfg
        self._base_dir = base_dir
        self.tracker = tracker  # created lazily on the first camera frame (process_features needs none)
        self.features = FeatureExtractor(cfg.pose)
        self.calibrator = Calibrator(cfg.calibration)
        self.profile: DriverProfile | None = None
        self._want_calibration = calibrate and cfg.calibration.enabled
        if not self._want_calibration:
            self.profile = DriverProfile.defaults(cfg.calibration)
        # signals
        self.head_proc = HeadPoseProcessor(cfg.pose)
        self.eye_proc = EyeSignalProcessor(cfg.eyes)
        self.gaze_est = GazeEstimator(cfg.gaze, cfg.gaze_zones)
        self.mouth_proc = MouthProcessor(cfg.mouth)
        # state + detectors
        d = cfg.drowsiness
        self.closure = EyeClosureDetector(cfg.eye_closure, d.microsleep_window_s)
        self.yawn = YawnDetector(cfg.yawn, d.yawn_window_s)
        self.almost = EyesAlmostClosedDetector(cfg.almost_closed, cfg.eyes, cfg.eye_closure)
        self.baseline = EyeBaseline(cfg.eye_baseline, cfg.narrowed_eyes)
        self.narrowed = EyesNarrowedDetector(cfg.narrowed_eyes)
        self.nod = HeadNodDetector(cfg.nod)
        self.falling = HeadFallingDetector(cfg.head_falling)
        self.attention = AttentionTracker(cfg.distraction)
        self.distraction = DistractionDetector(cfg.distraction)
        self.face_vis = FaceVisibilityDetector(cfg.face)
        self.phone = PhoneDetector(cfg.phone)
        self.phone_finder: PhoneFinder | None = None  # created lazily with the face tracker
        self._phone_ready = not cfg.phone.enabled  # True once created (or switched off / model missing)
        self._phone_seen: PhoneSighting | None = None
        self.notes: list[str] = []  # one-off messages for the terminal (e.g. a missing optional model)
        self.eye_stats = EyeStatistics(d, cfg.eyes, cfg.eye_closure)
        self.tired = TiredEyesDetector(cfg.tired_eyes)
        self.fatigue = FatigueLedger(d)
        self.drowsiness = DrowsinessEstimator(d)
        self.drowsy = DrowsyDetector()
        self._frame = 0
        self._dt = Ema(1.0)
        self._last_t: float | None = None

    # ------------------------------------------------------------------ control
    def start_calibration(self, t: float) -> None:
        """(Re)calibrate. Resets all detectors and windows: the session starts fresh afterwards."""
        self.calibrator.start(t)
        self._reset_all()

    def _reset_all(self) -> None:
        for part in (
            self.head_proc,
            self.eye_proc,
            self.gaze_est,
            self.mouth_proc,
            self.closure,
            self.yawn,
            self.almost,
            self.baseline,
            self.narrowed,
            self.nod,
            self.falling,
            self.attention,
            self.distraction,
            self.face_vis,
            self.phone,
            self.eye_stats,
            self.tired,
            self.fatigue,
            self.drowsiness,
            self.drowsy,
        ):
            part.reset()

    def close(self) -> None:
        if self.tracker is not None:
            self.tracker.close()
        if self.phone_finder is not None:
            self.phone_finder.close()

    # ------------------------------------------------------------------ per frame
    def process(
        self,
        frame_bgr: npt.NDArray[np.uint8],
        t: float,
        latency_start: float | None = None,
        camera_fps: float | None = None,
    ) -> FrameResult:
        import time

        t0 = latency_start if latency_start is not None else time.perf_counter()
        if (
            self._frame == 0
            and self._want_calibration
            and self.profile is None
            and not self.calibrator.active
        ):
            self.start_calibration(t)
        if self.tracker is None:
            self.tracker = FaceTracker(self.cfg.landmarker, self._base_dir)
        if not self._phone_ready:
            self._phone_ready = True
            try:
                self.phone_finder = PhoneFinder(self.cfg.phone, self._base_dir)
            except FileNotFoundError as e:  # optional: everything else works without it
                self.notes.append(f"Phone detection off: {e}")
        if self.phone_finder is not None:
            self.phone_finder.submit(frame_bgr, t)  # non-blocking, at most detect_hz
            self._phone_seen = self.phone_finder.latest(t)
        obs = self.tracker.process(frame_bgr, t)
        feats = self.features.extract(frame_bgr, obs, t)
        return self.process_features(feats, t, (time.perf_counter() - t0) * 1000.0, camera_fps)

    def process_features(
        self, feats: FrameFeatures, t: float, latency_ms: float = 0.0, camera_fps: float | None = None
    ) -> FrameResult:
        self._frame += 1
        if self._last_t is not None and t > self._last_t:
            self._dt.update(t - self._last_t, t)
        self._last_t = t
        fps = 1.0 / self._dt.value if self._dt.value else 0.0
        face = feats.face
        cfg = self.cfg

        calibrating = self.calibrator.active
        if calibrating:
            done = self.calibrator.update(face, t)
            if done is not None:
                self.profile = done
                self._reset_all()
                calibrating = False
        profile = None if calibrating else self.profile

        head = self.head_proc.update(face, profile, t)
        eyes = self.eye_proc.update(face, head, profile, t)
        gaze = self.gaze_est.update(face, head, eyes, profile, t)
        mouth = self.mouth_proc.update(face, t)

        detections: list[Detection] = []
        if not calibrating:
            falling_before = self.falling.active
            self.closure.update(eyes, head, falling_before, t)
            self.yawn.update(mouth, eyes, head.pitch if head.valid else None, t)
            self.almost.update(eyes, self.yawn.active, t)
            ratio = self.baseline.update(eyes, head, self.yawn.active, t)
            self.narrowed.update(ratio, self.baseline.value, self.baseline.learned, t)
            self.nod.update(head, eyes, t)
            self.falling.update(
                head,
                eyes,
                self.closure.closed_since,
                self.head_proc.history,
                self.closure.microsleep.is_active,
                t,
            )
            att = self.attention.update(gaze, t)
            self.distraction.update(att, t)
            self.face_vis.update(face, eyes.state, self.falling.active, self.head_proc.history, t)
            self.phone.update(self._phone_seen, t)
            self.eye_stats.update(eyes, t)
            stats = self.eye_stats.stats(self.closure, t, self.baseline.value)
            self.tired.update(stats, t)
            for det in (
                self.closure,
                self.yawn,
                self.almost,
                self.narrowed,
                self.nod,
                self.falling,
                self.distraction,
                self.face_vis,
                self.phone,
                self.tired,
            ):
                detections.extend(det.emit())
            # this frame's events (microsleeps, nods, falls, yawns) -> decaying fatigue load
            self.fatigue.ingest(detections, t)
            fatigue = self.fatigue.snapshot(t)
            narrowed = self.narrowed.active
            dstate = self.drowsiness.update(
                DrowsinessInputs(
                    stats,
                    fatigue,
                    self.baseline.ratio if narrowed else None,
                    self.narrowed.duration_s if narrowed else 0.0,
                ),
                t,
            )
            self.drowsy.update(dstate, t)
            detections.extend(self.drowsy.emit())
        else:
            att = self.attention.update(GazeSignal(), t)
            stats = self.eye_stats.stats(self.closure, t, self.baseline.value)
            fatigue = self.fatigue.snapshot(t)
            dstate = self.drowsiness.update(DrowsinessInputs(stats, fatigue), t)

        issues: list[QualityIssue] = []
        if calibrating:
            issues.append(QualityIssue.CALIBRATING)
        if eyes.unreliable_reason in {i.value for i in QualityIssue}:
            issues.append(QualityIssue(eyes.unreliable_reason))
        if feats.brightness < cfg.quality.low_light_mean:
            issues.append(QualityIssue.LOW_LIGHT)
        if self._frame > 30 and 0 < fps < cfg.quality.low_fps:
            issues.append(QualityIssue.LOW_FPS)

        if calibrating:
            cal = builder.calibration(
                CalibrationStatus.CALIBRATING,
                self.calibrator.phase,
                self.calibrator.progress(t),
                self.calibrator.message,
                None,
            )
        else:
            assert self.profile is not None
            cal = builder.calibration(
                self.profile.status, None, 1.0, "; ".join(self.profile.notes) or None, self.profile
            )

        state = DriverState(
            t=round(t, 3),
            frame=self._frame,
            summary=builder.summary(detections),
            quality=builder.quality(
                fps,
                latency_ms,
                face is not None,
                feats.num_faces,
                self.face_vis.lost_for(t) if face is None else 0.0,
                eyes,
                issues,
                camera_fps,
            ),
            calibration=cal,
            eyes=builder.eyes(
                eyes,
                self.closure.last_blink,
                self.baseline.value,
                self.baseline.learned,
                self.baseline.ratio,
            ),
            head=builder.head(head),
            gaze=builder.gaze(gaze),
            mouth=builder.mouth(mouth),
            attention=builder.attention(att, self.distraction.active),
            drowsiness=builder.drowsiness(
                dstate,
                stats,
                fatigue,
                self.yawn.count(t),
                self.nod.count(t),
                self.falling.count(t),
                self.closure.microsleeps_in_window(t),
            ),
            detections=builder.detections_out(detections),
        )
        return FrameResult(state, feats, head, eyes, gaze, mouth, self._phone_seen)
