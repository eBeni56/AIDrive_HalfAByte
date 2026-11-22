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

# zones the road hysteresis keeps while the gaze is just inside the road box
_OFF_ROAD = (GazeZone.DOWN, GazeZone.UP, GazeZone.LEFT_SIDE, GazeZone.RIGHT_SIDE)


@dataclass(slots=True)
class GazeSignal:
    yaw: float | None = None  # head + eyes, deg: - left / + right
    pitch: float | None = None  # head + eyes, deg: - down / + up
    zone: GazeZone = GazeZone.UNKNOWN
    zone_for_s: float = 0.0


class ZoneClassifier:
    """Gaze angles -> GazeZone. Mirrors first (their boxes may overlap the road box), then the road box,
    then DOWN (anything below the road box: no reason to look there), UP, LEFT_SIDE / RIGHT_SIDE.
    Every border has the same hysteresis (gaze_zones.hysteresis_deg), so nothing flickers on it:
      a mirror is kept while the gaze is within hysteresis_deg of its box;
      the road box is that much smaller while the gaze is off the road (harder to come back than to leave),
      and inside that band the previous off-road zone is kept;
      DOWN is kept up to hysteresis_deg above its border."""

    def __init__(self, gaze_cfg: GazeConfig, zones: GazeZonesConfig) -> None:
        self.g, self.z = gaze_cfg, zones
        self.zone = GazeZone.UNKNOWN
        self._on_road = True

    def reset(self) -> None:
        self.zone = GazeZone.UNKNOWN
        self._on_road = True

    def forget_zone(self) -> None:
        """No gaze (no face, eyes closed): the next zone is decided afresh; the road state is kept."""
        self.zone = GazeZone.UNKNOWN

    def _in_road_box(self, yaw: float, pitch: float, shrink: float = 0.0) -> bool:
        g = self.g
        return (
            abs(yaw) <= g.on_road_yaw_deg - shrink
            and -(g.on_road_pitch_down_deg - shrink) <= pitch <= g.on_road_pitch_up_deg - shrink
        )

    def classify(self, yaw: float, pitch: float) -> GazeZone:
        g, z, prev = self.g, self.z, self.zone
        hy = z.hysteresis_deg
        self._on_road = on_road = self._in_road_box(yaw, pitch, 0.0 if self._on_road else hy)
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
            elif prev in _OFF_ROAD and self._in_road_box(yaw, pitch):
                zone = prev  # inside the road box but not back on the road yet (road hysteresis)
            elif pitch / g.on_road_pitch_up_deg >= abs(yaw) / g.on_road_yaw_deg:
                zone = GazeZone.UP
            else:
                zone = GazeZone.LEFT_SIDE if yaw < 0 else GazeZone.RIGHT_SIDE
        self.zone = zone
        return zone


class GazeEstimator:
    def __init__(self, cfg: GazeConfig, zones: GazeZonesConfig) -> None:
        self.cfg = cfg
        self.zones = ZoneClassifier(cfg, zones)
        self._zone_since: float | None = None
        self._zone_prev = GazeZone.UNKNOWN
        self._f_yaw = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self._f_pitch = OneEuroFilter(cfg.filter_min_cutoff, cfg.filter_beta)
        self._last_eye: tuple[float, float, float] | None = None  # (t, yaw, pitch)
        self._last_seen: tuple[float, float, GazeZone] | None = None  # face visible: (t, yaw, zone), for side_hold

    def reset(self) -> None:
        self._f_yaw.reset()
        self._f_pitch.reset()
        self._last_eye = None
        self.zones.reset()
        self._zone_prev, self._zone_since = GazeZone.UNKNOWN, None

    def _set_zone(self, zone: GazeZone, t: float) -> float:
        if zone is not self._zone_prev or self._zone_since is None:
            self._zone_prev, self._zone_since = zone, t
        if zone is GazeZone.UNKNOWN:
            self.zones.forget_zone()
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

    def update(
        self,
        face: FaceFeatures | None,
        head: HeadSignal,
        eyes: EyesSignal,
        profile: DriverProfile | None,
        t: float,
    ) -> GazeSignal:
        if face is None or profile is None or head.yaw is None or head.pitch is None:
            self._f_yaw.reset()
            self._f_pitch.reset()
            side = self._turned_out_of_view(t) if profile is not None else None
            if side is not None:  # head turned fully to the side: looking out of that side window
                self.zones.zone = side  # coming back into view, the side zone keeps its hysteresis
                return GazeSignal(zone=side, zone_for_s=self._set_zone(side, t))
            self._last_seen = None
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
        zone = self.zones.classify(yaw, pitch)
        self._last_seen = (t, yaw, zone)
        return GazeSignal(yaw, pitch, zone, self._set_zone(zone, t))

    def _turned_out_of_view(self, t: float) -> GazeZone | None:
        """The face was just lost: LEFT_SIDE / RIGHT_SIDE if it was last seen turned that far to the side (the
        webcam loses a fully turned head), for at most side_hold_s; else None."""
        if self._last_seen is None:
            return None
        seen_t, yaw, zone = self._last_seen
        if t - seen_t > self.cfg.side_hold_s:
            return None
        if zone in (GazeZone.LEFT_SIDE, GazeZone.RIGHT_SIDE):
            return zone
        if abs(yaw) >= self.cfg.side_hold_min_yaw_deg:
            return GazeZone.LEFT_SIDE if yaw < 0 else GazeZone.RIGHT_SIDE
        return None
