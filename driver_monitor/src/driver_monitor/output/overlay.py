"""Draws the camera preview: eye contours, iris, gaze arrow, phone box, status panel, detections.

Inference runs on the raw frame; only the preview is mirrored (like a selfie view), so the landmark
x coordinates are mirrored here when drawing.
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.common.enums import CalibrationStatus, DetectionType, EyeState, GazeZone, Severity
from driver_monitor.output.schema import DriverState
from driver_monitor.output.terminal import describe
from driver_monitor.perception import landmarks as L
from driver_monitor.pipeline import FrameResult

_BGR = {
    Severity.NONE: (90, 200, 90),
    Severity.WARNING: (40, 200, 255),
    Severity.DANGER: (40, 40, 255),
}
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(
    img: npt.NDArray[np.uint8],
    s: str,
    org: tuple[int, int],
    color: tuple[int, int, int],
    scale: float = 0.5,
    thick: int = 1,
) -> None:
    cv2.putText(img, s, org, _FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, s, org, _FONT, scale, color, thick, cv2.LINE_AA)


class OverlayRenderer:
    def __init__(self, mirror: bool = True) -> None:
        self.mirror = mirror

    def render(self, frame: npt.NDArray[np.uint8], res: FrameResult) -> npt.NDArray[np.uint8]:
        img: npt.NDArray[np.uint8] = np.ascontiguousarray(cv2.flip(frame, 1) if self.mirror else frame)
        if not self.mirror:
            img = img.copy()
        h, w = img.shape[:2]
        face = res.features.face
        if face is not None:
            pts = face.pts[:, :2].copy()
            if self.mirror:
                pts[:, 0] = w - 1 - pts[:, 0]
            self._contours(img, pts, res.state)
            self._gaze_arrow(img, pts, res.state)
        self._panel(img, res.state)
        self._phone(img, res, w)  # after the panel: the box stays visible on top of it
        self._detections(img, res.state, w, h)
        return img

    def _contours(self, img: npt.NDArray[np.uint8], pts: npt.NDArray[np.float32], s: DriverState) -> None:
        eye_colors = {EyeState.OPEN: (90, 220, 90), EyeState.CLOSED: (40, 40, 255)}
        eye_color = eye_colors.get(s.eyes.state, (160, 160, 160))
        for contour, color in (
            (L.RIGHT_EYE_CONTOUR, eye_color),
            (L.LEFT_EYE_CONTOUR, eye_color),
            (L.FACE_OVAL, (120, 120, 120)),
        ):
            poly = pts[list(contour)].astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(img, [poly], True, color, 1, cv2.LINE_AA)
        for i in (L.RIGHT_IRIS_CENTER, L.LEFT_IRIS_CENTER):
            cv2.circle(img, (int(pts[i, 0]), int(pts[i, 1])), 2, (255, 255, 0), -1, cv2.LINE_AA)

    def _gaze_arrow(self, img: npt.NDArray[np.uint8], pts: npt.NDArray[np.float32], s: DriverState) -> None:
        g = s.gaze
        if g.yaw_deg is None or g.pitch_deg is None:
            return
        nose = pts[L.NOSE_TIP]
        scale = float(np.linalg.norm(pts[L.RIGHT_EYE_OUTER] - pts[L.LEFT_EYE_OUTER])) * 2.0
        # Driver's right appears on the right of a mirrored preview.
        dx = np.sin(np.radians(g.yaw_deg)) * scale * (1 if self.mirror else -1)
        dy = -np.sin(np.radians(g.pitch_deg)) * scale
        zone = g.zone
        if zone is GazeZone.ROAD:
            color = (90, 220, 90)  # green
        elif zone in (GazeZone.LEFT_MIRROR, GazeZone.REAR_MIRROR, GazeZone.RIGHT_MIRROR):
            color = (230, 180, 60)  # blue: a valid mirror check
        elif zone is GazeZone.DOWN:
            color = (40, 40, 230)  # red: no reason to look down
        else:
            color = (40, 200, 255)  # orange
        cv2.arrowedLine(
            img,
            (int(nose[0]), int(nose[1])),
            (int(nose[0] + dx), int(nose[1] + dy)),
            color,
            2,
            cv2.LINE_AA,
            tipLength=0.2,
        )

    def _phone(self, img: npt.NDArray[np.uint8], res: FrameResult, w: int) -> None:
        """Box around the phone seen in the newest analysed frame; red once PHONE_USE is active."""
        p = res.phone
        if p is None:
            return
        x, y, bw, bh = p.box
        if self.mirror:
            x = w - x - bw
        active = any(d.type is DetectionType.PHONE_USE for d in res.state.active_detections())
        color = (40, 40, 255) if active else (40, 200, 255)
        cv2.rectangle(img, (x, y), (x + bw, y + bh), color, 3, cv2.LINE_AA)
        _text(img, f"PHONE {p.score:.0%}", (x, max(18, y - 8)), color, 0.6, 2)

    def _panel(self, img: npt.NDArray[np.uint8], s: DriverState) -> None:
        e, g, q = s.eyes, s.gaze, s.quality

        def f(x: float | None, fmt: str = "+.0f") -> str:
            return "-" if x is None else format(x, fmt)

        cam = f"cam {q.camera_fps:.0f} fps | " if q.camera_fps is not None else ""
        lines = [f"t {s.t:.1f}s  {cam}loop {q.fps:.0f} fps | proc {q.latency_ms:.0f} ms  faces {q.faces_in_view}"]
        if s.calibration.status is CalibrationStatus.CALIBRATING:
            lines += [f"CALIBRATING {s.calibration.progress:.0%}", s.calibration.message or ""]
        lines += [
            f"eyes {e.state.value} {f(e.openness, '.2f')} ({e.source.value})",
            f"gaze {g.zone.value} ({f(g.yaw_deg)}, {f(g.pitch_deg)})",
        ]
        if q.issues:
            lines.append("issues: " + ", ".join(i.value for i in q.issues))
        y = 22
        overlay = img.copy()
        cv2.rectangle(overlay, (5, 5), (460, 12 + 20 * len(lines)), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)
        for line in lines:
            _text(img, line, (12, y), (235, 235, 235))
            y += 20

    def _detections(self, img: npt.NDArray[np.uint8], s: DriverState, w: int, h: int) -> None:
        active = s.active_detections()
        y = h - 15 - 26 * (len(active) - 1)
        for d in active:
            color = _BGR[d.severity]
            _text(img, f"{d.severity.value} {d.type.value}: {describe(d)}"[:110], (12, y), color, 0.55, 2)
            y += 26
        sev = s.summary.highest_severity
        if sev is Severity.DANGER:
            cv2.rectangle(img, (0, 0), (w - 1, h - 1), _BGR[sev], 8)
