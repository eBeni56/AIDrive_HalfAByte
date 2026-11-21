"""The only module that talks to MediaPipe. Runs Face Landmarker in VIDEO mode on BGR frames."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import numpy.typing as npt

from driver_monitor.config import LandmarkerConfig


@dataclass(slots=True)
class FaceObservation:
    """Raw MediaPipe output for the selected (driver's) face."""

    width: int
    height: int
    pts: npt.NDArray[np.float32]  # (478, 3): x, y in pixels, z scaled like x (z * width)
    blendshapes: dict[str, float]
    matrix: npt.NDArray[np.float64] | None  # 4x4 facial transformation matrix
    num_faces: int  # how many faces MediaPipe returned


class FaceTracker:
    """Wraps `FaceLandmarker` (VIDEO mode: synchronous, strictly increasing timestamps).

    With num_faces > 1 it picks the largest face, which is the driver (closest to the camera).
    """

    def __init__(self, config: LandmarkerConfig, base_dir: Path | None = None) -> None:
        model = Path(config.model_path)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(
                f"Face Landmarker model not found at {model}. Run: uv run python tools/download_model.py"
            )
        vision = mp.tasks.vision
        options = vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=config.num_faces,
            min_face_detection_confidence=config.min_face_detection_confidence,
            min_face_presence_confidence=config.min_face_presence_confidence,
            min_tracking_confidence=config.min_tracking_confidence,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ts_ms = -1

    def process(self, frame_bgr: npt.NDArray[np.uint8], t_s: float) -> FaceObservation | None:
        ts_ms = max(round(t_s * 1000.0), self._last_ts_ms + 1)  # must strictly increase
        self._last_ts_ms = ts_ms
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms
        )
        if not result.face_landmarks:
            return None

        scale = np.array([w, h, w], dtype=np.float32)
        faces = [
            np.array([(p.x, p.y, p.z) for p in lms], dtype=np.float32) * scale
            for lms in result.face_landmarks
        ]
        widths = [float(f[:, 0].max() - f[:, 0].min()) for f in faces]
        i = int(np.argmax(widths))

        blendshapes: dict[str, float] = {}
        if result.face_blendshapes and i < len(result.face_blendshapes):
            blendshapes = {c.category_name: float(c.score) for c in result.face_blendshapes[i]}
        matrix = None
        if result.facial_transformation_matrixes and i < len(result.facial_transformation_matrixes):
            matrix = np.asarray(result.facial_transformation_matrixes[i], dtype=np.float64)
        return FaceObservation(w, h, faces[i], blendshapes, matrix, len(faces))

    def close(self) -> None:
        self._landmarker.close()

    def __enter__(self) -> FaceTracker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
