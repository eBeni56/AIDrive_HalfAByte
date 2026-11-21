"""Draws the camera preview: eye/lip contours, iris, head axis, gaze arrow, status panel, detections.

Inference runs on the raw frame; only the preview is mirrored (like a selfie view), so the landmark
x coordinates are mirrored here when drawing.
"""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.common.enums import DetectionStatus, Severity
from driver_monitor.output.schema import DriverState
from driver_monitor.output.terminal import describe
from driver_monitor.perception import landmarks as L
from driver_monitor.pipeline import FrameResult

_BGR = {
    Severity.NONE: (90, 200, 90),
    Severity.INFO: (220, 200, 80),
    Severity.WARNING: (40, 200, 255),
    Severity.DANGER: (40, 40, 255),
    Severity.CRITICAL: (0, 0, 255),
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
        self._detections(img, res.state, w, h)
        return img

    def _contours(self, img: npt.NDArray[np.uint8], pts: npt.NDArray[np.float32], s: DriverState) -> None:
        eye_color = {"OPEN": (90, 220, 90), "ALMOST_CLOSED": (40, 200, 255), "CLOSED": (40, 40, 255)}.get(
            s.eyes.state.value, (160, 160, 160)
        )
        for contour, color in (
            (L.RIGHT_EYE_CONTOUR, eye_color),
            (L.LEFT_EYE_CONTOUR, eye_color),
            (L.INNER_LIPS_CONTOUR, (255, 180, 80)),
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
        scale = float(np.linalg.norm(pts[33] - pts[263])) * 2.0
        # Driver's right appears on the right of a mirrored preview.
        dx = np.sin(np.radians(g.yaw_deg)) * scale * (1 if self.mirror else -1)
        dy = -np.sin(np.radians(g.pitch_deg)) * scale
        zone = g.zone.value
        if zone == "ROAD":
            color = (90, 220, 90)  # green
        elif zone.endswith("MIRROR"):
            color = (230, 180, 60)  # blue: a valid mirror check
        elif zone == "DOWN":
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

    def _panel(self, img: npt.NDArray[np.uint8], s: DriverState) -> None:
        e, hd, g, m, dr, a, q = s.eyes, s.head, s.gaze, s.mouth, s.drowsiness, s.attention, s.quality

        def f(x: float | None, fmt: str = "+.0f") -> str:
            return "-" if x is None else format(x, fmt)

        cam = f"cam {q.camera_fps:.0f} fps | " if q.camera_fps is not None else ""
        lines = [
            f"t {s.t:.1f}s  {cam}loop {q.fps:.0f} fps | proc {q.latency_ms:.0f} ms  faces {q.faces_in_view}"
        ]
        if s.calibration.status.value == "CALIBRATING":
            lines += [f"CALIBRATING {s.calibration.progress:.0%}", s.calibration.message or ""]
        lines += [
            f"eyes {e.state.value} {f(e.openness, '.2f')} ({e.source.value}) {e.trend.value}"
            f"  vs normal {f(e.openness_ratio, '.2f')}",
            f"head yaw {f(hd.yaw_deg)} pitch {f(hd.pitch_deg)} roll {f(hd.roll_deg)}",
            f"gaze {g.direction.value} ({f(g.yaw_deg)}, {f(g.pitch_deg)}) zone {g.zone.value}",
            f"mouth {m.state.value} jaw {f(m.jaw_open, '.2f')} mar {f(m.mar, '.2f')}",
            f"attention {a.state.value} off-road {a.eyes_off_road_s:.1f}s (down {a.glance_down_s:.1f}s)"
            f" / 30s: {a.weighted_off_road_30s_s:.1f}s",
            f"drowsy {dr.level.value} {dr.score:.2f}  perclos {f(dr.indicators.perclos, '.2f')}",
            f"blinks/min {f(dr.indicators.blink_rate_per_min, '.0f')}  yawns {dr.indicators.yawns_5min}"
            f"  nods {dr.indicators.head_nods_5min}  microsleeps {dr.indicators.microsleeps_10min}",
        ]
        if q.issues:
            lines.append("issues: " + ", ".join(i.value for i in q.issues))
        y = 22
        overlay = img.copy()
        cv2.rectangle(overlay, (5, 5), (520, 12 + 20 * len(lines)), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, img, 0.55, 0, img)
        for line in lines:
            _text(img, line, (12, y), (235, 235, 235))
            y += 20

    def _detections(self, img: npt.NDArray[np.uint8], s: DriverState, w: int, h: int) -> None:
        active = [d for d in s.detections if d.status is not DetectionStatus.ENDED]
        y = h - 15 - 26 * (len(active) - 1)
        for d in active:
            color = _BGR[d.severity]
            _text(img, f"{d.severity.value} {d.type.value}: {describe(d)}"[:110], (12, y), color, 0.55, 2)
            y += 26
        sev = s.summary.highest_severity
        if sev in (Severity.DANGER, Severity.CRITICAL):
            cv2.rectangle(img, (0, 0), (w - 1, h - 1), _BGR[sev], 8)
