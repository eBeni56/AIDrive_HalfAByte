"""Mouth opening: jawOpen blendshape + inner-lip MAR, lightly smoothed, with a coarse state."""

from __future__ import annotations

from dataclasses import dataclass

from driver_monitor.common.enums import MouthState
from driver_monitor.common.filters import Ema
from driver_monitor.config import MouthConfig
from driver_monitor.perception.features import FaceFeatures


@dataclass(slots=True)
class MouthSignal:
    jaw_open: float | None = None
    mar: float | None = None
    state: MouthState = MouthState.UNKNOWN
    state_for_s: float = 0.0


class MouthProcessor:
    def __init__(self, cfg: MouthConfig) -> None:
        self.cfg = cfg
        self._jaw = Ema(cfg.smoothing_tau_s)
        self._mar = Ema(cfg.smoothing_tau_s)
        self._state = MouthState.UNKNOWN
        self._since: float | None = None

    def reset(self) -> None:
        self._jaw.reset()
        self._mar.reset()
        self._state, self._since = MouthState.UNKNOWN, None

    def update(self, face: FaceFeatures | None, t: float) -> MouthSignal:
        if face is None:
            self._jaw.reset()
            self._mar.reset()
            state = MouthState.UNKNOWN
            jaw = mar = None
        else:
            jaw, mar = self._jaw.update(face.jaw_open, t), self._mar.update(face.mar, t)
            c = self.cfg
            if mar >= c.wide_open_mar or jaw >= c.wide_open_jaw:
                state = MouthState.WIDE_OPEN
            elif mar >= c.open_mar or jaw >= c.open_jaw:
                state = MouthState.OPEN
            else:
                state = MouthState.CLOSED
        if state is not self._state or self._since is None:
            self._state, self._since = state, t
        return MouthSignal(jaw, mar, state, t - self._since)
