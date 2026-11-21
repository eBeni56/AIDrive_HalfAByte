"""Eye openness (EAR + blendshape fusion, calibrated per driver) and eye state (OPEN / CLOSED) with hysteresis.

openness: 1.0 = this driver's normal open eye, 0.0 = this driver's closed eye.
Guards: pose reliability (head turned / pitched too far, face too small or cut off), near-eye selection when
the head is turned.
Looking down (head pitched down) lowers the lids with open eyes (openness ~0.3-0.5). It never vetoes a
closure; instead EAR gets more weight (the eyeBlink blendshape rises when looking down) and the CLOSED
threshold is stricter. The eyeLookDown blendshape is NOT used: it rises when the eyes close.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from driver_monitor.calibration.profile import DriverProfile, EyeBaseline
from driver_monitor.common.enums import EyeSource, EyeState
from driver_monitor.common.filters import Ema
from driver_monitor.config import EyesConfig
from driver_monitor.perception.features import EyeFeatures, FaceFeatures
from driver_monitor.signals.head import HeadSignal


@dataclass(slots=True)
class EyesSignal:
    reliable: bool
    state: EyeState
    state_for_s: float
    openness: float | None = None  # smoothed, combined over the used eye(s)
    source: EyeSource = EyeSource.BOTH
    unreliable_reason: str | None = None  # "HEAD_TURNED_TOO_FAR" | "FACE_TOO_SMALL" | "NO_FACE" | ...


def _openness(eye: EyeFeatures, base: EyeBaseline, cfg: EyesConfig, looking_down: bool) -> float:
    ear_span = max(base.open_ear - base.closed_ear, 1e-3)
    o_ear = (eye.ear - base.closed_ear) / ear_span
    blink_span = max(base.closed_blink - base.open_blink, 1e-3)
    o_blink = 1.0 - (eye.blink - base.open_blink) / blink_span
    if looking_down:
        w_ear = cfg.looking_down_ear_weight
        w_blink = 1.0 - w_ear
    else:
        w_ear, w_blink = cfg.ear_weight, cfg.blink_weight
    fused = (w_ear * o_ear + w_blink * o_blink) / max(w_ear + w_blink, 1e-6)
    return float(np.clip(fused, 0.0, cfg.max_openness))


class EyeSignalProcessor:
    def __init__(self, cfg: EyesConfig) -> None:
        self.cfg = cfg
        self._ema = Ema(cfg.smoothing_tau_s)
        self._state = EyeState.UNKNOWN
        self._state_since: float | None = None

    def reset(self) -> None:
        self._ema.reset()
        self._state = EyeState.UNKNOWN
        self._state_since = None

    def _set_state(self, state: EyeState, t: float) -> float:
        if state is not self._state or self._state_since is None:
            self._state, self._state_since = state, t
        return t - self._state_since

    def _unknown(self, reason: str, t: float, source: EyeSource = EyeSource.BOTH) -> EyesSignal:
        self._ema.reset()
        return EyesSignal(False, EyeState.UNKNOWN, self._set_state(EyeState.UNKNOWN, t), None, source, reason)

    def update(
        self, face: FaceFeatures | None, head: HeadSignal, profile: DriverProfile | None, t: float
    ) -> EyesSignal:
        c = self.cfg
        if face is None or profile is None:
            return self._unknown("NO_FACE", t)

        yaw = head.yaw if head.valid and head.yaw is not None else 0.0
        pitch = head.pitch if head.valid and head.pitch is not None else 0.0
        looking_down = pitch <= c.look_down_head_pitch_deg
        left = _openness(face.left, profile.left, c, looking_down)
        right = _openness(face.right, profile.right, c, looking_down)

        # Turning to the driver's LEFT shows the camera the RIGHT side of the face: the right eye stays usable.
        if yaw <= -c.single_eye_yaw_deg:
            source, raw = EyeSource.RIGHT_ONLY, right
        elif yaw >= c.single_eye_yaw_deg:
            source, raw = EyeSource.LEFT_ONLY, left
        else:
            source, raw = EyeSource.BOTH, 0.5 * (left + right)

        if abs(yaw) > c.unreliable_yaw_deg or -pitch > c.unreliable_pitch_down_deg or pitch > c.unreliable_pitch_up_deg:
            return self._unknown("HEAD_TURNED_TOO_FAR", t, source)
        if face.interocular_px < c.min_interocular_px:
            return self._unknown("FACE_TOO_SMALL", t, source)
        if face.partially_out:
            return self._unknown("FACE_PARTIALLY_OUT_OF_FRAME", t, source)

        openness = self._ema.update(raw, t)
        enter, exit_ = (
            (c.closed_enter_looking_down, c.closed_exit_looking_down)
            if looking_down
            else (c.closed_enter, c.closed_exit)
        )
        closed = openness < (exit_ if self._state is EyeState.CLOSED else enter)
        state = EyeState.CLOSED if closed else EyeState.OPEN
        return EyesSignal(True, state, self._set_state(state, t), openness, source, None)
