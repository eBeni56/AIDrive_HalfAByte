"""Pure geometric features on PIXEL coordinates (normalized ones have different x and y scales)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import numpy.typing as npt

Points = npt.NDArray[np.float32]  # (N, 2) or (N, 3) pixel coordinates


def _dist(a: npt.NDArray[np.float32], b: npt.NDArray[np.float32]) -> float:
    return float(np.linalg.norm(a - b))


def eye_aspect_ratio(pts: Points, idx: Sequence[int]) -> float:
    """EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|). Works on 2D or 3D points."""
    p = pts[list(idx)]
    horizontal = _dist(p[0], p[3])
    if horizontal < 1e-6:
        return 0.0
    return (_dist(p[1], p[5]) + _dist(p[2], p[4])) / (2.0 * horizontal)


def iris_position(pts: Points, corner_a: int, corner_b: int, iris: int) -> tuple[float, float]:
    """(t, s) of the iris centre in the eye.

    t: position along the corner_a -> corner_b axis (0 at corner_a, 1 at corner_b, ~0.5 centred).
    s: perpendicular offset / eye width, positive = below the corner line (image y grows downward).
    """
    a, b, c = pts[corner_a, :2], pts[corner_b, :2], pts[iris, :2]
    ab = b - a
    length = float(np.hypot(ab[0], ab[1]))
    if length < 1e-6:
        return 0.5, 0.0
    t = float(np.dot(c - a, ab) / length**2)
    normal = np.array([-ab[1], ab[0]]) / length  # ab rotated +90 deg = "down" in image coordinates
    s = float(np.dot(c - (a + b) / 2.0, normal) / length)
    return t, s


def matrix_to_euler_deg(matrix: npt.ArrayLike) -> tuple[float, float, float]:
    """(yaw, pitch, roll) in degrees from MediaPipe's facial transformation matrix.

    Convention R = Rz(roll) @ Ry(yaw) @ Rx(pitch). Raw signs (verified against MediaPipe's geometry):
    +yaw = head turned to the subject's left, +pitch = head down, +roll = tilt to the subject's right.
    """
    r = np.asarray(matrix, dtype=np.float64)[:3, :3]
    norms = np.linalg.norm(r, axis=0, keepdims=True)
    r = r / np.where(norms > 1e-9, norms, 1.0)  # remove a possible uniform scale
    yaw = math.degrees(math.asin(float(np.clip(-r[2, 0], -1.0, 1.0))))
    pitch = math.degrees(math.atan2(r[2, 1], r[2, 2]))
    roll = math.degrees(math.atan2(r[1, 0], r[0, 0]))
    return yaw, pitch, roll
