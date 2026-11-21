"""Turns a raw FaceObservation into a flat set of per-frame features (no state, no thresholds)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.config import PoseConfig
from driver_monitor.perception import landmarks as L
from driver_monitor.perception.face_tracker import FaceObservation
from driver_monitor.perception.geometry import eye_aspect_ratio, iris_position, matrix_to_euler_deg


@dataclass(slots=True)
class EyeFeatures:
    ear: float  # 3D EAR (less sensitive to head yaw than 2D)
    blink: float  # MediaPipe eyeBlink* blendshape 0..1
    iris_h: float  # iris position along the eye axis, 0 image-left corner .. 1 image-right corner
    iris_v: float  # perpendicular offset / eye width, positive = up


@dataclass(slots=True)
class FaceFeatures:
    """Everything measured on one frame where a face was found. Angles use the SCHEMA convention
    (+yaw right, +pitch up, +roll toward the right shoulder) but are NOT yet relative to neutral."""

    left: EyeFeatures  # driver's left eye
    right: EyeFeatures  # driver's right eye
    blend_h: float  # blendshape gaze, > 0 = toward the driver's LEFT, roughly -1..1
    blend_v: float  # > 0 = up
    yaw: float | None
    pitch: float | None
    roll: float | None
    interocular_px: float
    partially_out: bool
    pts: npt.NDArray[np.float32]  # (478, 3) pixels, for drawing


@dataclass(slots=True)
class FrameFeatures:
    t: float
    width: int
    height: int
    brightness: float  # mean gray level 0..255
    face: FaceFeatures | None
    num_faces: int


def frame_brightness(frame_bgr: npt.NDArray[np.uint8]) -> float:
    small = cv2.resize(frame_bgr, (64, 36), interpolation=cv2.INTER_AREA)
    return float(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).mean())


class FeatureExtractor:
    def __init__(self, pose: PoseConfig) -> None:
        self.pose = pose

    def extract(
        self, frame_bgr: npt.NDArray[np.uint8], obs: FaceObservation | None, t: float
    ) -> FrameFeatures:
        h, w = frame_bgr.shape[:2]
        brightness = frame_brightness(frame_bgr)
        if obs is None:
            return FrameFeatures(t, w, h, brightness, None, 0)
        return FrameFeatures(t, w, h, brightness, self.face_features(obs), obs.num_faces)

    def face_features(self, obs: FaceObservation) -> FaceFeatures:
        """Features of one observation (also used to replay cached MediaPipe output)."""
        pts, bs = obs.pts, obs.blendshapes
        g = bs.get

        def eye(
            ear_idx: tuple[int, ...],
            corners: tuple[int, int],
            iris: int,
            blink_name: str,
        ) -> EyeFeatures:
            t_axis, s_perp = iris_position(pts, corners[0], corners[1], iris)
            return EyeFeatures(
                ear=eye_aspect_ratio(pts, ear_idx),
                blink=g(blink_name, 0.0),
                iris_h=t_axis,
                iris_v=-s_perp,
            )

        right = eye(
            L.RIGHT_EYE_EAR,
            L.RIGHT_EYE_CORNERS,
            L.RIGHT_IRIS_CENTER,
            "eyeBlinkRight",
        )
        left = eye(
            L.LEFT_EYE_EAR,
            L.LEFT_EYE_CORNERS,
            L.LEFT_IRIS_CENTER,
            "eyeBlinkLeft",
        )

        # Gaze toward the driver's left = right eye looks "in" (to the nose) + left eye looks "out".
        blend_h = 0.5 * (
            (g("eyeLookInRight", 0.0) + g("eyeLookOutLeft", 0.0))
            - (g("eyeLookOutRight", 0.0) + g("eyeLookInLeft", 0.0))
        )
        look_up = 0.5 * (g("eyeLookUpLeft", 0.0) + g("eyeLookUpRight", 0.0))
        look_down = 0.5 * (g("eyeLookDownLeft", 0.0) + g("eyeLookDownRight", 0.0))

        yaw = pitch = roll = None
        if obs.matrix is not None:
            raw_yaw, raw_pitch, raw_roll = matrix_to_euler_deg(obs.matrix)
            yaw = self.pose.yaw_sign * raw_yaw
            pitch = self.pose.pitch_sign * raw_pitch
            roll = self.pose.roll_sign * raw_roll

        partially_out = bool(
            pts[:, 0].min() < 0 or pts[:, 1].min() < 0 or pts[:, 0].max() > obs.width or pts[:, 1].max() > obs.height
        )
        return FaceFeatures(
            left=left,
            right=right,
            blend_h=blend_h,
            blend_v=look_up - look_down,
            yaw=yaw,
            pitch=pitch,
            roll=roll,
            interocular_px=float(np.linalg.norm(pts[33, :2] - pts[263, :2])),
            partially_out=partially_out,
            pts=pts,
        )
