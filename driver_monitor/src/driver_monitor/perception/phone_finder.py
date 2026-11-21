"""Finds a mobile phone in the camera image: MediaPipe Object Detector (EfficientDet-Lite0, COCO "cell phone").

The detector is far slower than the face landmarker, so it runs in LIVE_STREAM mode: `submit()` hands a frame to
MediaPipe at most `detect_hz` times per second and returns at once; MediaPipe calls back from its own thread
(and skips frames while it is busy). `latest()` returns the newest result. The camera loop never waits for it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import numpy.typing as npt

from driver_monitor.config import PhoneConfig

PHONE_CATEGORY = "cell phone"


@dataclass(frozen=True, slots=True)
class PhoneSighting:
    """The best "cell phone" in one analysed frame."""

    t: float  # time of the analysed frame (seconds, same clock as the pipeline)
    score: float
    box: tuple[int, int, int, int]  # x, y, width, height in pixels of the RAW (not mirrored) frame


class PhoneFinder:
    def __init__(self, cfg: PhoneConfig, base_dir: Path | None = None) -> None:
        model = Path(cfg.model_path)
        if not model.is_absolute() and base_dir is not None:
            model = base_dir / model
        if not model.exists():
            raise FileNotFoundError(
                f"Object detector model not found at {model}. Run: uv run python tools/download_model.py"
            )
        self.cfg = cfg
        self._lock = threading.Lock()
        self._latest: PhoneSighting | None = None
        self._analysed_t: float | None = None  # time of the newest analysed frame (phone or not)
        self._times: dict[int, float] = {}  # timestamp_ms -> pipeline time, for frames in flight
        self._last_ts_ms = -1
        self._next_t: float | None = None
        vision = mp.tasks.vision
        options = vision.ObjectDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.LIVE_STREAM,
            category_allowlist=[PHONE_CATEGORY],
            score_threshold=cfg.min_score,
            max_results=1,
            result_callback=self._on_result,
        )
        self._detector = vision.ObjectDetector.create_from_options(options)

    def submit(self, frame_bgr: npt.NDArray[np.uint8], t: float) -> None:
        """Hand the frame to the detector if `detect_hz` allows it now (non-blocking)."""
        if self._next_t is not None and t < self._next_t - 1e-3:
            return
        self._next_t = t + 1.0 / max(self.cfg.detect_hz, 0.1)
        ts_ms = max(round(t * 1000.0), self._last_ts_ms + 1)  # must strictly increase
        self._last_ts_ms = ts_ms
        with self._lock:
            self._times[ts_ms] = t
            if len(self._times) > 50:  # results of skipped frames never come back
                for k in sorted(self._times)[:-10]:
                    del self._times[k]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        self._detector.detect_async(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)

    def _on_result(self, result: object, image: object, ts_ms: int) -> None:  # MediaPipe's thread
        best: PhoneSighting | None = None
        with self._lock:
            t = self._times.pop(ts_ms, None)
        if t is None:
            return
        for det in getattr(result, "detections", None) or []:
            for cat in det.categories:
                if cat.category_name == PHONE_CATEGORY and (best is None or cat.score > best.score):
                    b = det.bounding_box
                    best = PhoneSighting(t, float(cat.score), (b.origin_x, b.origin_y, b.width, b.height))
        with self._lock:
            if self._analysed_t is None or t >= self._analysed_t:
                self._analysed_t = t
                self._latest = best

    def latest(self, t: float) -> PhoneSighting | None:
        """The phone in the newest analysed frame, if that frame is at most max_age_s old."""
        with self._lock:
            s = self._latest
        return s if s is not None and t - s.t <= self.cfg.max_age_s else None

    def close(self) -> None:
        self._detector.close()
