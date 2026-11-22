"""Head pose relative to the calibrated neutral pose, One-Euro smoothed."""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.filters import OneEuroFilter
from driver_monitor.config import PoseConfig
from driver_monitor.perception.features import FaceFeatures


@dataclass(slots=True)
class HeadSignal:
    """All three angles are None when there is no face (or no pose) in the frame."""

    yaw: float | None = None  # deg, relative to neutral: - left / + right
    pitch: float | None = None  # - down / + up
    roll: float | None = None  # - left shoulder / + right shoulder


class HeadPoseProcessor:
    _RESET_GAP_S = 0.5  # after a tracking gap longer than this, restart the filters

    def __init__(self, cfg: PoseConfig) -> None:
        self.cfg = cfg
        self._filters = [OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta) for _ in range(3)]
        self._last_t: float | None = None

    def reset(self) -> None:
        for f in self._filters:
            f.reset()
        self._last_t = None

    def update(self, face: FaceFeatures | None, profile: DriverProfile | None, t: float) -> HeadSignal:
        if face is None or face.yaw is None or face.pitch is None or face.roll is None or profile is None:
            return HeadSignal()
        if self._last_t is not None and t - self._last_t > self._RESET_GAP_S:
            for f in self._filters:
                f.reset()
        self._last_t = t
        rel = (
            face.yaw - profile.neutral_yaw,
            face.pitch - profile.neutral_pitch,
            face.roll - profile.neutral_roll,
        )
        yaw, pitch, roll = (f.update(v, t) for f, v in zip(self._filters, rel, strict=True))
        return HeadSignal(yaw, pitch, roll)
