"""The calibrated baseline of one driver. Every relative measurement is computed against it."""

from __future__ import annotations

from dataclasses import dataclass, field

from driver_monitor.common.enums import CalibrationStatus
from driver_monitor.config import CalibrationConfig


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
    closed_phase_used: bool = False
    notes: list[str] = field(default_factory=list)

    @staticmethod
    def defaults(cfg: CalibrationConfig) -> DriverProfile:
        def eye() -> EyeBaseline:
            return EyeBaseline(
                open_ear=cfg.default_open_ear,
                closed_ear=cfg.default_open_ear * cfg.default_closed_ear_ratio,
                open_blink=cfg.default_open_blink,
                closed_blink=min(cfg.default_open_blink + cfg.default_closed_blink_offset, 0.9),
            )

        return DriverProfile(
            CalibrationStatus.DEFAULTS,
            0.0,
            0.0,
            0.0,
            eye(),
            eye(),
            notes=["population defaults - run calibration for accurate results"],
        )
