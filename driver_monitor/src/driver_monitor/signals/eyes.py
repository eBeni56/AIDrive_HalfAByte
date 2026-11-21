"""Eye openness (EAR + blendshape fusion, calibrated per driver) and eye state with hysteresis.

openness: 1.0 = this driver's normal open eye, 0.0 = this driver's closed eye.
Guards: pose reliability (head turned / pitched too far), near-eye selection when the head is turned,
smile / cheek squint.
Looking down (head pitched down) lowers the lids with open eyes (openness ~0.3-0.5). It never vetoes a
closure; instead EAR gets more weight (the eyeBlink blendshape rises when looking down) and the CLOSED
threshold is stricter. The eyeLookDown blendshape is NOT used: it rises when the eyes close.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from driver_monitor.calibration.profile import DriverProfile, EyeBaseline
from driver_monitor.common.enums import EyeSource, EyeState, EyeTrend
from driver_monitor.common.filters import Differentiator, Ema
from driver_monitor.config import EyesConfig
from driver_monitor.perception.features import EyeFeatures, FaceFeatures
from driver_monitor.signals.head import HeadSignal


@dataclass(slots=True)
class EyeReading:
    openness: float
    ear: float
    blink: float
    state: EyeState


@dataclass(slots=True)
class EyesSignal:
    reliable: bool
    state: EyeState
    state_for_s: float
    openness: float | None = None  # smoothed, combined over the used eye(s)
    velocity: float = 0.0  # d openness / dt (1/s)
    trend: EyeTrend = EyeTrend.STABLE
    source: EyeSource = EyeSource.BOTH
    left: EyeReading | None = None
    right: EyeReading | None = None
    iris_visibility: float | None = None
    lid_lowered_by_gaze: bool = False
    smiling: bool = False
    looking_down: bool = False
    unreliable_reason: str | None = None  # "HEAD_TURNED_TOO_FAR" | "FACE_TOO_SMALL" | "NO_FACE" | ...


def _openness(eye: EyeFeatures, base: EyeBaseline, cfg: EyesConfig, looking_down: bool = False) -> float:
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
        self._vel = Differentiator(cfg.velocity_tau_s)
        self._state = EyeState.UNKNOWN
        self._state_since: float | None = None

    def reset(self) -> None:
        self._ema.reset()
        self._vel.reset()
        self._state = EyeState.UNKNOWN
        self._state_since = None

    def _simple_state(self, o: float) -> EyeState:
        if o < self.cfg.closed_enter:
            return EyeState.CLOSED
        if o < self.cfg.almost_enter:
            return EyeState.ALMOST_CLOSED
        return EyeState.OPEN

    def _hysteresis_state(self, o: float, looking_down: bool) -> EyeState:
        c = self.cfg
        prev = self._state
        enter, exit_ = (
            (c.closed_enter_looking_down, c.closed_exit_looking_down)
            if looking_down
            else (c.closed_enter, c.closed_exit)
        )
        if o < (exit_ if prev is EyeState.CLOSED else enter):
            return EyeState.CLOSED
        almost_thr = c.almost_exit if prev in (EyeState.ALMOST_CLOSED, EyeState.CLOSED) else c.almost_enter
        if o < almost_thr:
            return EyeState.ALMOST_CLOSED
        return EyeState.OPEN

    def _set_state(self, state: EyeState, t: float) -> float:
        if state is not self._state or self._state_since is None:
            self._state, self._state_since = state, t
        return t - self._state_since

    def update(
        self, face: FaceFeatures | None, head: HeadSignal, profile: DriverProfile | None, t: float
    ) -> EyesSignal:
        c = self.cfg
        if face is None or profile is None:
            self._ema.reset()
            self._vel.reset()
            dur = self._set_state(EyeState.UNKNOWN, t)
            return EyesSignal(False, EyeState.UNKNOWN, dur, unreliable_reason="NO_FACE")

        yaw = head.yaw if head.valid and head.yaw is not None else 0.0
        pitch = head.pitch if head.valid and head.pitch is not None else 0.0
        looking_down = pitch <= c.look_down_head_pitch_deg
        left = EyeReading(
            _openness(face.left, profile.left, c, looking_down), face.left.ear, face.left.blink, EyeState.OPEN
        )
        right = EyeReading(
            _openness(face.right, profile.right, c, looking_down),
            face.right.ear,
            face.right.blink,
            EyeState.OPEN,
        )
        left.state, right.state = self._simple_state(left.openness), self._simple_state(right.openness)

        # Turning to the driver's LEFT shows the camera the RIGHT side of the face: the right eye stays usable.
        if yaw <= -c.single_eye_yaw_deg:
            source, raw, iris_vis = EyeSource.RIGHT_ONLY, right.openness, face.right.iris_visibility
        elif yaw >= c.single_eye_yaw_deg:
            source, raw, iris_vis = EyeSource.LEFT_ONLY, left.openness, face.left.iris_visibility
        else:
            source = EyeSource.BOTH
            raw = 0.5 * (left.openness + right.openness)
            iris_vis = 0.5 * (face.left.iris_visibility + face.right.iris_visibility)

        reason = None
        if (
            abs(yaw) > c.unreliable_yaw_deg
            or -pitch > c.unreliable_pitch_down_deg
            or pitch > c.unreliable_pitch_up_deg
        ):
            reason = "HEAD_TURNED_TOO_FAR"
        elif face.interocular_px < c.min_interocular_px:
            reason = "FACE_TOO_SMALL"
        elif face.partially_out:
            reason = "FACE_PARTIALLY_OUT_OF_FRAME"

        smiling = face.smile > c.smile_threshold or face.cheek_squint > c.cheek_squint_threshold

        if reason is not None:
            self._ema.reset()
            self._vel.reset()
            dur = self._set_state(EyeState.UNKNOWN, t)
            return EyesSignal(
                False,
                EyeState.UNKNOWN,
                dur,
                None,
                0.0,
                EyeTrend.STABLE,
                source,
                left,
                right,
                iris_vis,
                looking_down,
                smiling,
                looking_down,
                reason,
            )

        openness = self._ema.update(raw, t)
        velocity = self._vel.update(openness, t)
        if velocity <= -c.trend_velocity_per_s:
            trend = EyeTrend.CLOSING
        elif velocity >= c.trend_velocity_per_s:
            trend = EyeTrend.OPENING
        else:
            trend = EyeTrend.STABLE
        state = self._hysteresis_state(openness, looking_down)
        dur = self._set_state(state, t)
        # Lids lowered by a downward look: looking down and narrowed, but not (clearly) closed.
        lid_lowered = looking_down and state is not EyeState.CLOSED and openness < c.almost_exit
        return EyesSignal(
            True,
            state,
            dur,
            openness,
            velocity,
            trend,
            source,
            left,
            right,
            iris_vis,
            lid_lowered,
            smiling,
            looking_down,
            None,
        )
