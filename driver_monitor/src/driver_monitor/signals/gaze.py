"""Gaze = head angle + eye-in-head angle, as a direction label, an intensity and the actual angles.

Eye-in-head yaw comes from the iris position between the eye corners (fused with the eyeLookIn/Out
blendshapes); eye-in-head pitch mostly from eyeLookUp/Down (vertical iris position is weak) - but only
while the eyes are clearly open, because eyeLookDown also rises when the lids close or squint. With
narrowed eyes the vertical eye angle uses the iris position alone.
Both are relative to the neutral values recorded during calibration.

The combined angles are also mapped to a ZONE (road, the three mirrors, left/right side, up, down) - the
mirror positions come from the gaze_zones config (simulator screen layout by default).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.enums import (
    EyeSource,
    EyeState,
    GazeDirection,
    GazeZone,
    Horizontal,
    Intensity,
    Vertical,
)
from driver_monitor.common.filters import OneEuroFilter
from driver_monitor.config import GazeConfig, GazeZonesConfig
from driver_monitor.perception.features import FaceFeatures
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal


@dataclass(slots=True)
class GazeSignal:
    direction: GazeDirection = GazeDirection.UNKNOWN
    horizontal: Horizontal = Horizontal.CENTER
    vertical: Vertical = Vertical.CENTER
    intensity: Intensity = Intensity.NONE
    yaw: float | None = None  # combined, deg: - left / + right
    pitch: float | None = None  # combined, deg: - down / + up
    head_yaw: float | None = None
    head_pitch: float | None = None
    eye_yaw: float | None = None
    eye_pitch: float | None = None
    on_road: bool | None = None
    direction_for_s: float = 0.0
    zone: GazeZone = GazeZone.UNKNOWN
    zone_for_s: float = 0.0


class ZoneClassifier:
    """Gaze angles -> GazeZone. Mirrors first (their boxes may overlap the road box), then the road box,
    then DOWN (anything below the road box: no reason to look there), UP, LEFT_SIDE / RIGHT_SIDE.
    A zone is kept while the gaze stays within `hysteresis_deg` of it (no flicker on the borders)."""

    def __init__(self, gaze_cfg: GazeConfig, zones: GazeZonesConfig) -> None:
        self.g, self.z = gaze_cfg, zones
        self.zone = GazeZone.UNKNOWN

    def reset(self) -> None:
        self.zone = GazeZone.UNKNOWN

    def classify(self, yaw: float, pitch: float, on_road: bool) -> GazeZone:
        g, z, prev = self.g, self.z, self.zone
        hy = z.hysteresis_deg
        mirrors = (
            (GazeZone.REAR_MIRROR, z.rear_mirror),
            (GazeZone.LEFT_MIRROR, z.left_mirror),
            (GazeZone.RIGHT_MIRROR, z.right_mirror),
        )
        zone: GazeZone | None = None
        for name, box in mirrors:  # the current mirror first, with the hysteresis margin
            if name is prev and box.contains(yaw, pitch, hy):
                zone = name
        if zone is None:
            zone = next((name for name, box in mirrors if box.contains(yaw, pitch)), None)
        if zone is None and on_road:
            zone = GazeZone.ROAD
        if zone is None:
            down = g.on_road_pitch_down_deg - (hy if prev is GazeZone.DOWN else 0.0)
            if pitch <= -down:
                zone = GazeZone.DOWN
            elif prev in (GazeZone.DOWN, GazeZone.UP, GazeZone.LEFT_SIDE, GazeZone.RIGHT_SIDE) and (
                abs(yaw) <= g.on_road_yaw_deg and -g.on_road_pitch_down_deg <= pitch <= g.on_road_pitch_up_deg
            ):
                zone = prev  # inside the road box but not back on the road yet (road hysteresis)
            elif pitch / g.on_road_pitch_up_deg >= abs(yaw) / g.on_road_yaw_deg:
                zone = GazeZone.UP
            else:
                zone = GazeZone.LEFT_SIDE if yaw < 0 else GazeZone.RIGHT_SIDE
        self.zone = zone
        return zone


class GazeEstimator:
    def __init__(self, cfg: GazeConfig, zones: GazeZonesConfig | None = None) -> None:
        self.cfg = cfg
        self.zones = ZoneClassifier(cfg, zones or GazeZonesConfig())
        self._zone_since: float | None = None
        self._zone_prev = GazeZone.UNKNOWN
        self._f_yaw = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self._f_pitch = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self._last_eye: tuple[float, float, float] | None = None  # (t, yaw, pitch)
        self._h = Horizontal.CENTER
        self._v = Vertical.CENTER
        self._on_road = True
        self._direction = GazeDirection.UNKNOWN
        self._direction_since: float | None = None

    def reset(self) -> None:
        self._f_yaw.reset()
        self._f_pitch.reset()
        self._last_eye = None
        self._h, self._v, self._on_road = Horizontal.CENTER, Vertical.CENTER, True
        self._direction, self._direction_since = GazeDirection.UNKNOWN, None
        self.zones.reset()
        self._zone_prev, self._zone_since = GazeZone.UNKNOWN, None

    def _set_zone(self, zone: GazeZone, t: float) -> float:
        if zone is not self._zone_prev or self._zone_since is None:
            self._zone_prev, self._zone_since = zone, t
        if zone is GazeZone.UNKNOWN:
            self.zones.reset()
        return t - self._zone_since

    # ------------------------------------------------------------------ eye-in-head angles
    def _eye_angles(
        self, face: FaceFeatures, eyes: EyesSignal, profile: DriverProfile
    ) -> tuple[float, float]:
        c = self.cfg
        dh_l, dh_r = face.left.iris_h - profile.left.iris_h0, face.right.iris_h - profile.right.iris_h0
        dv_l, dv_r = face.left.iris_v - profile.left.iris_v0, face.right.iris_v - profile.right.iris_v0
        if eyes.source is EyeSource.LEFT_ONLY:
            dh, dv = dh_l, dv_l
        elif eyes.source is EyeSource.RIGHT_ONLY:
            dh, dv = dh_r, dv_r
        else:
            dh, dv = 0.5 * (dh_l + dh_r), 0.5 * (dv_l + dv_r)
        # Iris toward the image right / blend_h > 0 both mean "toward the driver's LEFT" = negative yaw.
        yaw = -(
            c.iris_weight_h * c.iris_yaw_gain_deg * dh
            + (1.0 - c.iris_weight_h) * c.blend_yaw_gain_deg * (face.blend_h - profile.blend_h0)
        )
        open_enough = eyes.openness is not None and eyes.openness >= c.blend_pitch_min_openness
        if open_enough:
            pitch = c.iris_weight_v * c.iris_pitch_gain_deg * dv + (
                1.0 - c.iris_weight_v
            ) * c.blend_pitch_gain_deg * (face.blend_v - profile.blend_v0)
        else:  # narrowed lids: eyeLookDown follows the lid, not the eyeball
            pitch = c.iris_pitch_gain_deg * dv
        m = c.max_eye_angle_deg
        return float(np.clip(yaw, -m, m)), float(np.clip(pitch, -m, m))

    # ------------------------------------------------------------------ labels with hysteresis
    def _horizontal(self, yaw: float) -> Horizontal:
        thr, hy = self.cfg.horizontal_deg, self.cfg.direction_hysteresis_deg
        if self._h is Horizontal.LEFT and yaw < -(thr - hy):
            return Horizontal.LEFT
        if self._h is Horizontal.RIGHT and yaw > thr - hy:
            return Horizontal.RIGHT
        if yaw <= -thr:
            return Horizontal.LEFT
        if yaw >= thr:
            return Horizontal.RIGHT
        return Horizontal.CENTER

    def _vertical(self, pitch: float) -> Vertical:
        up, down, hy = self.cfg.up_deg, self.cfg.down_deg, self.cfg.direction_hysteresis_deg
        if self._v is Vertical.UP and pitch > up - hy:
            return Vertical.UP
        if self._v is Vertical.DOWN and pitch < -(down - hy):
            return Vertical.DOWN
        if pitch >= up:
            return Vertical.UP
        if pitch <= -down:
            return Vertical.DOWN
        return Vertical.CENTER

    def _intensity(self, yaw: float, pitch: float) -> Intensity:
        mag = max(abs(yaw), abs(pitch))
        levels = [Intensity.SLIGHT, Intensity.MODERATE, Intensity.STRONG, Intensity.EXTREME]
        result = Intensity.NONE
        for step, level in zip(self.cfg.intensity_steps_deg, levels, strict=False):
            if mag >= step:
                result = level
        return result

    def _road(self, yaw: float, pitch: float) -> bool:
        c = self.cfg
        hy = c.on_road_hysteresis_deg if not self._on_road else 0.0  # harder to come back than to leave
        return (
            abs(yaw) <= c.on_road_yaw_deg - hy
            and -(c.on_road_pitch_down_deg - hy) <= pitch <= c.on_road_pitch_up_deg - hy
        )

    def _set_direction(self, d: GazeDirection, t: float) -> float:
        if d is not self._direction or self._direction_since is None:
            self._direction, self._direction_since = d, t
        return t - self._direction_since

    def update(
        self,
        face: FaceFeatures | None,
        head: HeadSignal,
        eyes: EyesSignal,
        profile: DriverProfile | None,
        t: float,
    ) -> GazeSignal:
        if face is None or profile is None or not head.valid or head.yaw is None or head.pitch is None:
            self._f_yaw.reset()
            self._f_pitch.reset()
            return GazeSignal(
                direction_for_s=self._set_direction(GazeDirection.UNKNOWN, t),
                zone_for_s=self._set_zone(GazeZone.UNKNOWN, t),
            )

        eye_yaw: float | None = None
        eye_pitch: float | None = None
        if eyes.reliable and eyes.state is not EyeState.CLOSED:
            ey, ep = self._eye_angles(face, eyes, profile)
            eye_yaw, eye_pitch = self._f_yaw.update(ey, t), self._f_pitch.update(ep, t)
            self._last_eye = (t, eye_yaw, eye_pitch)
        elif eyes.reliable and eyes.state is EyeState.CLOSED:
            # A blink: keep the last eye angles briefly; a longer closure means "not looking anywhere".
            if self._last_eye is not None and t - self._last_eye[0] <= self.cfg.blink_hold_s:
                eye_yaw, eye_pitch = self._last_eye[1], self._last_eye[2]
            else:
                return GazeSignal(
                    head_yaw=head.yaw,
                    head_pitch=head.pitch,
                    direction_for_s=self._set_direction(GazeDirection.UNKNOWN, t),
                    zone_for_s=self._set_zone(GazeZone.UNKNOWN, t),
                )
        # else: eyes unreliable (head turned far) -> head-only gaze

        yaw = head.yaw + (eye_yaw or 0.0)
        pitch = head.pitch + (eye_pitch or 0.0)
        self._h, self._v = self._horizontal(yaw), self._vertical(pitch)
        self._on_road = self._road(yaw, pitch)
        zone = self.zones.classify(yaw, pitch, self._on_road)
        direction = GazeDirection.combine(self._h, self._v)
        return GazeSignal(
            direction=direction,
            horizontal=self._h,
            vertical=self._v,
            intensity=self._intensity(yaw, pitch),
            yaw=yaw,
            pitch=pitch,
            head_yaw=head.yaw,
            head_pitch=head.pitch,
            eye_yaw=eye_yaw,
            eye_pitch=eye_pitch,
            on_road=self._on_road,
            direction_for_s=self._set_direction(direction, t),
            zone=zone,
            zone_for_s=self._set_zone(zone, t),
        )
