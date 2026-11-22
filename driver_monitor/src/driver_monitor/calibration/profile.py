"""The calibrated baseline of one driver. Every relative measurement is computed against it."""

from __future__ import annotations

from dataclasses import dataclass, field

from driver_monitor.common.enums import CalibrationStatus
from driver_monitor.config import CalibrationConfig

MAX_CLOSED_BLINK = 0.9  # the eyeBlink blendshape of a closed eye, at most


def default_closed(cfg: CalibrationConfig, open_ear: float, open_blink: float) -> tuple[float, float]:
    """(EAR, eyeBlink) of a closed eye, estimated from the open eye when there is no usable closed sample."""
    return open_ear * cfg.default_closed_ear_ratio, min(open_blink + cfg.default_closed_blink_offset, MAX_CLOSED_BLINK)


@dataclass(slots=True)
class EyeBaseline:
    open_ear: float
    closed_ear: float
    open_blink: float
    closed_blink: float
    iris_h0: float = 0.5  # neutral iris position along the eye axis
    iris_v0: float = 0.0


@dataclass(slots=True)
class DriverProfile:
    status: CalibrationStatus
    neutral_yaw: float
    neutral_pitch: float
    neutral_roll: float
    left: EyeBaseline
    right: EyeBaseline
    blend_h0: float = 0.0
    blend_v0: float = 0.0
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def defaults(cfg: CalibrationConfig) -> DriverProfile:
        """Population defaults (--no-calibration): the camera axis is straight ahead."""

        def eye() -> EyeBaseline:
            closed_ear, closed_blink = default_closed(cfg, cfg.default_open_ear, cfg.default_open_blink)
            return EyeBaseline(cfg.default_open_ear, closed_ear, cfg.default_open_blink, closed_blink)

        return DriverProfile(
            CalibrationStatus.DEFAULTS,
            0.0,
            0.0,
            0.0,
            eye(),
            eye(),
            notes=["population defaults - run calibration for accurate results"],
        )
