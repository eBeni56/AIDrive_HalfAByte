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

from driver_monitor.calibration.profile import DriverProfile, EyeBaseline
from driver_monitor.common.enums import CalibrationPhase, CalibrationStatus
from driver_monitor.config import CalibrationConfig
from driver_monitor.perception.features import FaceFeatures


@dataclass(slots=True)
class _OpenSample:
    yaw: float
    pitch: float
    roll: float
    ear_l: float
    ear_r: float
    blink_l: float
    blink_r: float
    iris_h_l: float
    iris_h_r: float
    iris_v_l: float
    iris_v_r: float
    blend_h: float
    blend_v: float


@dataclass(slots=True)
class _Phase:
    name: CalibrationPhase
    started_t: float
    duration_s: float
    frames: int = 0
    face_frames: int = 0
    open_samples: list[_OpenSample] = field(default_factory=list)
    closed_samples: list[tuple[float, float, float, float]] = field(
        default_factory=list
    )  # ear_l, ear_r, bl_l, bl_r


class Calibrator:
    def __init__(self, cfg: CalibrationConfig) -> None:
        self.cfg = cfg
        self._phase: _Phase | None = None
        self._open_result: list[_OpenSample] = []
        self.profile: DriverProfile | None = None
        self.message = ""
        self.restarts = 0

    @property
    def active(self) -> bool:
        return self._phase is not None

    @property
    def phase(self) -> CalibrationPhase | None:
        return self._phase.name if self._phase else None

    def start(self, t: float) -> None:
        self.profile = None
        self._open_result = []
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.open_phase_s)
        self.message = "Look straight ahead (at the road/screen centre), eyes normally open"

    def progress(self, t: float) -> float:
        if self._phase is None:
            return 1.0 if self.profile else 0.0
        total = self.cfg.open_phase_s + (self.cfg.closed_phase_s if self.cfg.use_closed_phase else 0.0)
        done = (t - self._phase.started_t) + (
            self.cfg.open_phase_s if self._phase.name is CalibrationPhase.CLOSED_EYES else 0.0
        )
        return float(np.clip(done / total, 0.0, 1.0))

    def seconds_left_in_phase(self, t: float) -> float:
        if self._phase is None:
            return 0.0
        return max(0.0, self._phase.duration_s - (t - self._phase.started_t))

    def update(self, face: FaceFeatures | None, t: float) -> DriverProfile | None:
        """Feed one frame. Returns the profile when calibration completes."""
        ph = self._phase
        if ph is None:
            return None
        ph.frames += 1
        if face is not None and face.yaw is not None and face.pitch is not None and face.roll is not None:
            ph.face_frames += 1
            if ph.name is CalibrationPhase.OPEN_EYES:
                ph.open_samples.append(
                    _OpenSample(
                        face.yaw,
                        face.pitch,
                        face.roll,
                        face.left.ear,
                        face.right.ear,
                        face.left.blink,
                        face.right.blink,
                        face.left.iris_h,
                        face.right.iris_h,
                        face.left.iris_v,
                        face.right.iris_v,
                        face.blend_h,
                        face.blend_v,
                    )
                )
            elif t - ph.started_t >= self.cfg.closed_phase_settle_s:
                ph.closed_samples.append((face.left.ear, face.right.ear, face.left.blink, face.right.blink))

        if t - ph.started_t < ph.duration_s:
            return None
        if ph.name is CalibrationPhase.OPEN_EYES:
            return self._finish_open(ph, t)
        return self._finish(ph.closed_samples)

    def _finish_open(self, ph: _Phase, t: float) -> DriverProfile | None:
        valid = ph.face_frames / max(ph.frames, 1)
        if valid < self.cfg.min_valid_fraction or len(ph.open_samples) < 15:
            self._restart(t, f"face visible only {valid:.0%} of the time - stay in view")
            return None
        yaw_std = statistics.pstdev(s.yaw for s in ph.open_samples)
        pitch_std = statistics.pstdev(s.pitch for s in ph.open_samples)
        if max(yaw_std, pitch_std) > self.cfg.max_head_std_deg:
            self._restart(t, "head moved too much - keep still and look straight ahead")
            return None
        self._open_result = ph.open_samples
        if not self.cfg.use_closed_phase:
            return self._finish([])
        self._phase = _Phase(CalibrationPhase.CLOSED_EYES, t, self.cfg.closed_phase_s)
        self.message = "Now CLOSE your eyes (keep facing forward)"
        return None

    def _restart(self, t: float, reason: str) -> None:
        self.restarts += 1
        self._phase = _Phase(CalibrationPhase.OPEN_EYES, t, self.cfg.open_phase_s)
        self.message = f"Calibration restarted: {reason}"
        return None

    def _finish(self, closed: list[tuple[float, float, float, float]]) -> DriverProfile:
        o = self._open_result
        med = statistics.median
        notes: list[str] = []

        def baseline(
            ears: list[float],
            blinks: list[float],
            closed_ears: list[float],
            closed_blinks: list[float],
            iris_h: list[float],
            iris_v: list[float],
            side: str,
        ) -> EyeBaseline:
            open_ear, open_blink = med(ears), med(blinks)
            closed_ear = open_ear * self.cfg.default_closed_ear_ratio
            closed_blink = min(open_blink + self.cfg.default_closed_blink_offset, 0.9)
            if len(closed_ears) >= 10:
                ce = sorted(closed_ears)[: max(len(closed_ears) // 2, 1)]  # most-closed half
                cb = sorted(closed_blinks)[len(closed_blinks) // 2 :]
                c_ear, c_blink = med(ce), med(cb)
                if open_ear - c_ear >= self.cfg.min_ear_margin:
                    closed_ear = c_ear
                else:
                    notes.append(f"{side} eye: closed EAR too close to open - using default ratio")
                if c_blink - open_blink >= self.cfg.min_blink_margin:
                    closed_blink = c_blink
                else:
                    notes.append(f"{side} eye: closed blink score too close to open - using default")
            return EyeBaseline(open_ear, closed_ear, open_blink, closed_blink, med(iris_h), med(iris_v))

        cl = closed
        left = baseline(
            [s.ear_l for s in o],
            [s.blink_l for s in o],
            [c[0] for c in cl],
            [c[2] for c in cl],
            [s.iris_h_l for s in o],
            [s.iris_v_l for s in o],
            "left",
        )
        right = baseline(
            [s.ear_r for s in o],
            [s.blink_r for s in o],
            [c[1] for c in cl],
            [c[3] for c in cl],
            [s.iris_h_r for s in o],
            [s.iris_v_r for s in o],
            "right",
        )
        self.profile = DriverProfile(
            status=CalibrationStatus.DONE,
            neutral_yaw=med(s.yaw for s in o),
            neutral_pitch=med(s.pitch for s in o),
            neutral_roll=med(s.roll for s in o),
            left=left,
            right=right,
            blend_h0=med(s.blend_h for s in o),
            blend_v0=med(s.blend_v for s in o),
            closed_phase_used=len(cl) >= 10,
            notes=notes,
        )
        self._phase = None
        self.message = "Calibration done"
        return self.profile
