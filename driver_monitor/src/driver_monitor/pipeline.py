"""DriverMonitor: frame in -> DriverState out. Wires perception, calibration, signals and the three detectors.

Per frame:
  1 perception  MediaPipe face landmarks -> EAR, iris, head angles (features); the phone finder looks for a
                phone in the background (~6 times per second)
  2 signals     head (relative to the calibrated straight-ahead) -> eyes (openness, OPEN / CLOSED) ->
                gaze (head + eyes -> zone: road, mirrors, side windows, up, down)
  3 detectors   EYES_CLOSED (eyes), DISTRACTED (gaze zone), PHONE_USE (phone finder)
  4 output      DriverState (summary, quality, calibration, eyes, head, gaze, detections)
Eyes and gaze need the calibration (the driver's own open/closed eye and straight-ahead look), so their
detectors wait for it; the phone needs none and is detected from the first frame.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from driver_monitor.calibration.calibrator import Calibrator
from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.enums import CalibrationStatus, QualityIssue
from driver_monitor.common.filters import Ema
from driver_monitor.config import AppConfig
from driver_monitor.detectors.distraction import DistractionDetector
from driver_monitor.detectors.eye_closure import EyeClosureDetector
from driver_monitor.detectors.phone import PhoneDetector
from driver_monitor.output import builder
from driver_monitor.output.schema import Calibration, DriverState, Quality
from driver_monitor.perception.face_tracker import FaceTracker
from driver_monitor.perception.features import FrameFeatures, extract_features
from driver_monitor.perception.phone_finder import PhoneFinder, PhoneSighting
from driver_monitor.signals.eyes import EyeSignalProcessor
from driver_monitor.signals.gaze import GazeEstimator
from driver_monitor.signals.head import HeadPoseProcessor


@dataclass(slots=True)
class FrameResult:
    """Everything the preview needs for one frame."""

    state: DriverState
    features: FrameFeatures
    phone: PhoneSighting | None = None  # the phone seen in the newest analysed frame (for the overlay)


class DriverMonitor:
    def __init__(self, cfg: AppConfig, base_dir: Path | None = None, calibrate: bool = True) -> None:
        """calibrate=False: population defaults instead of calibrating (--no-calibration)."""
        self.cfg = cfg
        self._base_dir = base_dir
        self.tracker: FaceTracker | None = None  # created on the first camera frame (process_features needs none)
        self.calibrator = Calibrator(cfg.calibration)
        self._want_calibration = calibrate
        self.profile: DriverProfile | None = None if calibrate else DriverProfile.defaults(cfg.calibration)
        self.head_proc = HeadPoseProcessor(cfg.pose)
        self.eye_proc = EyeSignalProcessor(cfg.eyes)
        self.gaze_est = GazeEstimator(cfg.gaze, cfg.gaze_zones)
        self.eyes_closed = EyeClosureDetector(cfg.eye_closure)
        self.distraction = DistractionDetector(cfg.distraction)
        self.phone = PhoneDetector(cfg.phone)
        self.phone_finder: PhoneFinder | None = None  # created lazily with the face tracker
        self._phone_ready = not cfg.phone.enabled  # True once created (or switched off / model missing)
        self._phone_seen: PhoneSighting | None = None
        self.notes: list[str] = []  # one-off messages for the terminal (e.g. a missing optional model)
        self._frame = 0
        self._dt = Ema(1.0)
        self._last_t: float | None = None

    # ------------------------------------------------------------------ control
    def start_calibration(self, t: float) -> None:
        """(Re)calibrate. Resets the eye and gaze detectors: they start fresh afterwards."""
        self.calibrator.start(t)
        self._reset_face()

    def recenter(self, t: float) -> bool:
        """Measure the straight-ahead look again (the server asks at every simulation start: "look at the centre
        of the screen"); the eye baselines are kept. Reported as CALIBRATING meanwhile. Ignored (False) while the
        first calibration is still running."""
        if self.calibrator.active or self.profile is None:
            return False
        self.calibrator.start_recenter(t, self.profile)
        self._reset_face()
        return True

    def _reset_face(self) -> None:
        for part in (self.head_proc, self.eye_proc, self.gaze_est, self.eyes_closed, self.distraction):
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
        t0 = latency_start if latency_start is not None else time.perf_counter()
        if self._frame == 0 and self._want_calibration and not self.calibrator.active:
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
        feats = extract_features(frame_bgr, obs)
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

        calibrating = self.calibrator.active
        if calibrating:
            recentering = self.calibrator.recentering
            done = self.calibrator.update(face, t)
            if done is not None:
                self.profile = done
                self._reset_face()
                calibrating = False
                if recentering:
                    self.notes.append(self.calibrator.message)
        profile = None if calibrating else self.profile

        head = self.head_proc.update(face, profile, t)
        eyes = self.eye_proc.update(face, head, profile, t)
        gaze = self.gaze_est.update(face, head, eyes, profile, t)

        if not calibrating:
            self.eyes_closed.update(eyes, t)
            self.distraction.update(gaze.zone, t)
        self.phone.update(self._phone_seen, t)
        detections = self.eyes_closed.emit() + self.distraction.emit() + self.phone.emit()

        issues: list[QualityIssue] = []
        if calibrating:
            issues.append(QualityIssue.CALIBRATING)
        if eyes.issue is not None:
            issues.append(eyes.issue)
        if feats.brightness < self.cfg.quality.low_light_mean:
            issues.append(QualityIssue.LOW_LIGHT)
        if self._frame > 30 and 0 < fps < self.cfg.quality.low_fps:
            issues.append(QualityIssue.LOW_FPS)

        if calibrating:
            cal = Calibration(
                status=CalibrationStatus.CALIBRATING,
                phase=self.calibrator.phase,
                progress=round(self.calibrator.progress(t), 2),
                message=self.calibrator.message,
            )
        else:
            assert self.profile is not None
            cal = Calibration(
                status=self.profile.status, progress=1.0, message="; ".join(self.profile.notes) or None
            )

        state = DriverState(
            t=round(t, 3),
            frame=self._frame,
            summary=builder.summary(detections),
            quality=Quality(
                fps=round(fps, 1),
                camera_fps=None if camera_fps is None else round(camera_fps, 1),
                latency_ms=round(latency_ms, 1),
                face_detected=face is not None,
                faces_in_view=feats.num_faces,
                issues=issues,
            ),
            calibration=cal,
            eyes=builder.eyes(eyes),
            head=builder.head(head),
            gaze=builder.gaze(gaze),
            detections=builder.detections_out(detections),
        )
        return FrameResult(state, feats, self._phone_seen)
