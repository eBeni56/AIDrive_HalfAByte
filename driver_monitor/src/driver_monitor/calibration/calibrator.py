"""Two-phase calibration at the start of a session.

Phase 1 OPEN_EYES  (~6 s): "look straight ahead, eyes normally open" -> neutral head pose, open-eye EAR
                           and blink score (medians, so natural blinks are ignored), neutral gaze.
Phase 2 CLOSED_EYES (~3 s): "close your eyes" -> closed-eye EAR and blink score. If the values are not
                           clearly different from the open ones (driver didn't close them), fall back
                           to default ratios for that eye.
The open phase restarts if the face was missing too often or the head moved too much.

Recenter (start_recenter, at every simulation start): one open phase of recenter_s, the first recenter_settle_s
ignored -> only the straight-ahead look is measured again (neutral head pose, iris and gaze blendshapes); the
eye baselines of the profile are kept. No restart (the simulation starts anyway): if the face was missing too
often or the head moved too much, the previous straight-ahead is kept.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace

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
    settle_s: float = 0.0  # samples are collected only after this (reaction time)
    frames: int = 0
    samples: list[_Sample] = field(default_factory=list)  # frames with a face (closed phase: after settling)
    face_frames: int = 0


class Calibrator:
    def __init__(self, cfg: CalibrationConfig) -> None:
        self.cfg = cfg
        self._phase: _Phase | None = None
        self._open: list[_Sample] = []  # the accepted open phase
        self._base: DriverProfile | None = None  # while recentering: the profile whose straight-ahead is replaced
        self.message = ""

    @property
    def active(self) -> bool:
        return self._phase is not None

    @property
    def recentering(self) -> bool:
        return self._base is not None

    @property
    def phase(self) -> CalibrationPhase | None:
        return self._phase.name if self._phase else None

    def start(self, t: float) -> None:
        self._open = []
        self._base = None
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.open_phase_s)
        self.message = "Look straight ahead (at the road/screen centre), eyes normally open"

    def start_recenter(self, t: float, base: DriverProfile) -> None:
        """Measure only the straight-ahead look again; `base` keeps everything else."""
        self._base = base
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.recenter_s, self.cfg.recenter_settle_s)
        self.message = "Look at the centre of the screen"

    def progress(self, t: float) -> float:
        """How much of the calibration is done, 0..1."""
        if self._phase is None:
            return 1.0
        if self._base is not None:
            return float(np.clip((t - self._phase.started_t) / self.cfg.recenter_s, 0.0, 1.0))
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
            if t - ph.started_t >= ph.settle_s:
                ph.samples.append(_Sample(face.yaw, face.pitch, face.roll, face))

        if t - ph.started_t < ph.duration_s:
            return None
        if self._base is not None:
            return self._finish_recenter(ph)
        if ph.name is CalibrationPhase.OPEN_EYES:
            return self._finish_open(ph, t)
        return self._finish(ph.samples)

    def _finish_recenter(self, ph: _Phase) -> DriverProfile:
        base, self._base, self._phase = self._base, None, None
        assert base is not None
        valid = ph.face_frames / max(ph.frames, 1)
        reason = None
        if valid < self.cfg.min_valid_fraction or len(ph.samples) < MIN_OPEN_SAMPLES:
            reason = f"face visible only {valid:.0%} of the time"
        elif max(statistics.pstdev(s.yaw for s in ph.samples),
                 statistics.pstdev(s.pitch for s in ph.samples)) > self.cfg.max_head_std_deg:
            reason = "head moved too much"
        if reason is not None:
            self.message = f"Straight-ahead NOT updated ({reason}): the previous one is kept"
            return base
        o, med = ph.samples, statistics.median

        def eye(b: EyeBaseline, eyes: list[EyeFeatures]) -> EyeBaseline:
            return replace(b, iris_h0=med(e.iris_h for e in eyes), iris_v0=med(e.iris_v for e in eyes))

        self.message = "Straight-ahead updated (look at the centre of the screen)"
        return replace(
            base,
            neutral_yaw=med(s.yaw for s in o),
            neutral_pitch=med(s.pitch for s in o),
            neutral_roll=med(s.roll for s in o),
            left=eye(base.left, [s.face.left for s in o]),
            right=eye(base.right, [s.face.right for s in o]),
            blend_h0=med(s.face.blend_h for s in o),
            blend_v0=med(s.face.blend_v for s in o),
            notes=list(base.notes),
        )

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
        self._phase = _Phase(CalibrationPhase.CLOSED_EYES, t, self.cfg.closed_phase_s, self.cfg.closed_phase_settle_s)
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
