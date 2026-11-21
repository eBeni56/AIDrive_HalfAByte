"""Gaze = head angle + eye-in-head angle, mapped to a ZONE: where the driver looks.

Eye-in-head yaw comes from the iris position between the eye corners (fused with the eyeLookIn/Out
blendshapes); eye-in-head pitch mostly from eyeLookUp/Down (vertical iris position is weak) - but only
while the eyes are clearly open, because eyeLookDown also rises when the lids close or squint. With
narrowed eyes the vertical eye angle uses the iris position alone.
Both are relative to the neutral values recorded during calibration.

The combined angles are mapped to a ZONE (road, the three mirrors, left/right side window, up, down) - the
mirror positions come from the gaze_zones config (simulator screen layout by default).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from driver_monitor.calibration.profile import DriverProfile
from driver_monitor.common.enums import EyeSource, EyeState, GazeZone
from driver_monitor.common.filters import OneEuroFilter
from driver_monitor.config import GazeConfig, GazeZonesConfig
from driver_monitor.perception.features import FaceFeatures
from driver_monitor.signals.eyes import EyesSignal
from driver_monitor.signals.head import HeadSignal


@dataclass(slots=True)
class GazeSignal:
    yaw: float | None = None  # head + eyes, deg: - left / + right
    pitch: float | None = None  # head + eyes, deg: - down / + up
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
        self._on_road = True

    def reset(self) -> None:
        self._f_yaw.reset()
        self._f_pitch.reset()
        self._last_eye = None
        self._on_road = True
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

    # ------------------------------------------------------------------ road box with hysteresis
    def _road(self, yaw: float, pitch: float) -> bool:
        c = self.cfg
        hy = c.on_road_hysteresis_deg if not self._on_road else 0.0  # harder to come back than to leave
        return (
            abs(yaw) <= c.on_road_yaw_deg - hy
            and -(c.on_road_pitch_down_deg - hy) <= pitch <= c.on_road_pitch_up_deg - hy
        )

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
            return GazeSignal(zone_for_s=self._set_zone(GazeZone.UNKNOWN, t))

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
                return GazeSignal(zone_for_s=self._set_zone(GazeZone.UNKNOWN, t))
        # else: eyes unreliable (head turned far) -> head-only gaze

        yaw = head.yaw + (eye_yaw or 0.0)
        pitch = head.pitch + (eye_pitch or 0.0)
        self._on_road = self._road(yaw, pitch)
        zone = self.zones.classify(yaw, pitch, self._on_road)
        return GazeSignal(yaw, pitch, zone, self._set_zone(zone, t))
