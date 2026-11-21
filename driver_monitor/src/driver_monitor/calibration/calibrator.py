"""Two-phase calibration at the start of a session.

Phase 1 OPEN_EYES  (~6 s): "look straight ahead, eyes normally open" -> neutral head pose, open-eye EAR
                           and blink score (medians, so natural blinks are ignored), neutral gaze.
Phase 2 CLOSED_EYES (~3 s): "close your eyes" -> closed-eye EAR and blink score. If the values are not
                           clearly different from the open ones (driver didn't close them), fall back
                           to default ratios for that eye.
The open phase restarts if the face was missing too often or the head moved too much.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import numpy as np

from driver_monitor.calibration.profile import DriverProfile, EyeBaseline, default_closed
from driver_monitor.common.enums import CalibrationPhase, CalibrationStatus
from driver_monitor.config import CalibrationConfig
from driver_monitor.perception.features import EyeFeatures, FaceFeatures

MIN_OPEN_SAMPLES = 15  # frames with a face needed in the open phase
MIN_CLOSED_SAMPLES = 10  # frames with a face needed in the closed phase, else the default closed eye is used


@dataclass(slots=True, frozen=True)
class _Sample:
    """One frame with a face: its head angles (never None here) and its features."""

    yaw: float
    pitch: float
    roll: float
    face: FaceFeatures


@dataclass(slots=True)
class _Phase:
    name: CalibrationPhase
    started_t: float
    duration_s: float
    frames: int = 0
    samples: list[_Sample] = field(default_factory=list)  # frames with a face (closed phase: after settling)
    face_frames: int = 0


class Calibrator:
    def __init__(self, cfg: CalibrationConfig) -> None:
        self.cfg = cfg
        self._phase: _Phase | None = None
        self._open: list[_Sample] = []  # the accepted open phase
        self.message = ""

    @property
    def active(self) -> bool:
        return self._phase is not None

    @property
    def phase(self) -> CalibrationPhase | None:
        return self._phase.name if self._phase else None

    def start(self, t: float) -> None:
        self._open = []
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.open_phase_s)
        self.message = "Look straight ahead (at the road/screen centre), eyes normally open"

    def progress(self, t: float) -> float:
        """How much of the calibration is done, 0..1."""
        if self._phase is None:
            return 1.0
        total = self.cfg.open_phase_s + (self.cfg.closed_phase_s if self.cfg.use_closed_phase else 0.0)
        done = (t - self._phase.started_t) + (
            self.cfg.open_phase_s if self._phase.name is CalibrationPhase.CLOSED_EYES else 0.0
        )
        return float(np.clip(done / total, 0.0, 1.0))

    def update(self, face: FaceFeatures | None, t: float) -> DriverProfile | None:
        """Feed one frame. Returns the profile when calibration completes."""
        ph = self._phase
        if ph is None:
            return None
        ph.frames += 1
        if face is not None and face.yaw is not None and face.pitch is not None and face.roll is not None:
            ph.face_frames += 1
            settled = ph.name is CalibrationPhase.OPEN_EYES or t - ph.started_t >= self.cfg.closed_phase_settle_s
            if settled:
                ph.samples.append(_Sample(face.yaw, face.pitch, face.roll, face))

        if t - ph.started_t < ph.duration_s:
            return None
        if ph.name is CalibrationPhase.OPEN_EYES:
            return self._finish_open(ph, t)
        return self._finish(ph.samples)

    def _finish_open(self, ph: _Phase, t: float) -> DriverProfile | None:
        valid = ph.face_frames / max(ph.frames, 1)
        if valid < self.cfg.min_valid_fraction or len(ph.samples) < MIN_OPEN_SAMPLES:
            self._restart(t, f"face visible only {valid:.0%} of the time - stay in view")
            return None
        yaw_std = statistics.pstdev(s.yaw for s in ph.samples)
        pitch_std = statistics.pstdev(s.pitch for s in ph.samples)
        if max(yaw_std, pitch_std) > self.cfg.max_head_std_deg:
            self._restart(t, "head moved too much - keep still and look straight ahead")
            return None
        self._open = ph.samples
        if not self.cfg.use_closed_phase:
            return self._finish([])
        self._phase = _Phase(CalibrationPhase.CLOSED_EYES, t, self.cfg.closed_phase_s)
        self.message = "Now CLOSE your eyes (keep facing forward)"
        return None

    def _restart(self, t: float, reason: str) -> None:
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.open_phase_s)
        self.message = f"Calibration restarted: {reason}"

    def _eye_baseline(
        self, open_eyes: list[EyeFeatures], closed_eyes: list[EyeFeatures], side: str, notes: list[str]
    ) -> EyeBaseline:
        med = statistics.median
        open_ear, open_blink = med(e.ear for e in open_eyes), med(e.blink for e in open_eyes)
        closed_ear, closed_blink = default_closed(self.cfg, open_ear, open_blink)
        if len(closed_eyes) >= MIN_CLOSED_SAMPLES:
            ears = sorted(e.ear for e in closed_eyes)
            blinks = sorted(e.blink for e in closed_eyes)
            c_ear = med(ears[: max(len(ears) // 2, 1)])  # the most-closed half
            c_blink = med(blinks[len(blinks) // 2 :])
            if open_ear - c_ear >= self.cfg.min_ear_margin:
                closed_ear = c_ear
            else:
                notes.append(f"{side} eye: closed EAR too close to open - using default ratio")
            if c_blink - open_blink >= self.cfg.min_blink_margin:
                closed_blink = c_blink
            else:
                notes.append(f"{side} eye: closed blink score too close to open - using default")
        iris_h0 = med(e.iris_h for e in open_eyes)
        iris_v0 = med(e.iris_v for e in open_eyes)
        return EyeBaseline(open_ear, closed_ear, open_blink, closed_blink, iris_h0, iris_v0)

    def _finish(self, closed: list[_Sample]) -> DriverProfile:
        o, med = self._open, statistics.median
        notes: list[str] = []
        left = self._eye_baseline([s.face.left for s in o], [s.face.left for s in closed], "left", notes)
        right = self._eye_baseline([s.face.right for s in o], [s.face.right for s in closed], "right", notes)
        profile = DriverProfile(
            status=CalibrationStatus.DONE,
            neutral_yaw=med(s.yaw for s in o),
            neutral_pitch=med(s.pitch for s in o),
            neutral_roll=med(s.roll for s in o),
            left=left,
            right=right,
            blend_h0=med(s.face.blend_h for s in o),
            blend_v0=med(s.face.blend_v for s in o),
            notes=notes,
        )
        self._phase = None
        self.message = "Calibration done"
        return profile
