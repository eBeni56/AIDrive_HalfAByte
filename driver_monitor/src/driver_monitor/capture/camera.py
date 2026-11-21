"""Frame sources returning (BGR frame, timestamp in seconds).

Timestamps: the live camera uses the monotonic clock at the moment the frame was read; a video file
uses the file's own position, so recorded clips replay with correct timing regardless of CPU speed.

Windows note: with the DirectShow backend many USB webcams ignore the MJPG request and stay in raw YUY2,
which USB 2.0 can only carry at ~10 fps in 1280x720. Media Foundation (MSMF) negotiates a compressed format
and delivers 30 fps. backend "auto" therefore tries MSMF first and falls back to DirectShow.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol, cast

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.common.filters import Ema
from driver_monitor.config import CameraConfig

Frame = npt.NDArray[np.uint8]

_BACKENDS = {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}


class FrameSource(Protocol):
    def read(self) -> tuple[Frame, float] | None: ...
    def close(self) -> None: ...
    def describe(self) -> str: ...
    @property
    def fps(self) -> float | None:
        """The measured camera frame rate; None for a video file."""
        ...


def _fourcc(cap: cv2.VideoCapture) -> str:
    code = int(cap.get(cv2.CAP_PROP_FOURCC))
    text = "".join(chr((code >> 8 * i) & 0xFF) for i in range(4))
    return text if text.isalnum() else "native"  # MSMF does not report a readable FourCC


def _open(index: int, backend: str, cfg: CameraConfig) -> cv2.VideoCapture | None:
    cap = cv2.VideoCapture(index, _BACKENDS[backend])
    if not cap.isOpened():
        cap.release()
        return None
    # Size first, then format: DirectShow only accepts MJPG for a resolution it already selected.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
    if backend == "dshow":  # MSMF negotiates a compressed format by itself
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FPS, cfg.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # always process the newest frame
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None
    return cap


class Camera:
    def __init__(self, cfg: CameraConfig) -> None:
        self.cfg = cfg
        order = ["msmf", "dshow"] if cfg.backend == "auto" else [cfg.backend]
        indices = [cfg.index] + [i for i in cfg.fallback_indices if i != cfg.index]
        cap: cv2.VideoCapture | None = None
        self.index, self.backend = cfg.index, order[0]
        for index in indices:
            for backend in order:
                cap = _open(index, backend, cfg)
                if cap is not None:
                    self.index, self.backend = index, backend
                    break
            if cap is not None:
                break
        if cap is None:
            raise RuntimeError(
                f"Cannot open camera {indices} (backend {cfg.backend}). "
                "Run with --list-cameras to see the available cameras, then use --camera N."
            )
        self._cap = cap
        self._interval = Ema(1.0)  # seconds between delivered frames, averaged over ~1 s
        self._last_t: float | None = None
        self._t0 = time.monotonic()

    @property
    def fps(self) -> float | None:
        """Frames per second the camera actually delivers (measured, not the requested value)."""
        return 1.0 / self._interval.value if self._interval.value else None

    def read(self) -> tuple[Frame, float] | None:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        t = time.monotonic() - self._t0
        if self._last_t is not None and t > self._last_t:
            self._interval.update(t - self._last_t, t)
        self._last_t = t
        return cast(Frame, frame), t

    def describe(self) -> str:
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self._cap.get(cv2.CAP_PROP_FPS)
        fallback = (
            f" (camera {self.cfg.index} not available, using fallback)"
            if self.index != self.cfg.index
            else ""
        )
        return (
            f"camera {self.index} ({self.backend}, {_fourcc(self._cap)}) {w}x{h}, "
            f"{fps:.0f} fps requested{fallback}"
        )

    def close(self) -> None:
        self._cap.release()


def list_cameras(cfg: CameraConfig, max_index: int = 5, seconds: float = 1.5) -> list[str]:
    """Probe camera indices 0..max_index-1 and measure what each one really delivers."""
    lines: list[str] = []
    for index in range(max_index):
        found = False
        for backend in ("msmf", "dshow"):
            cap = _open(index, backend, cfg)
            if cap is None:
                continue
            found = True
            frames, brightness = 0, 0.0
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < seconds:
                ok, frame = cap.read()
                if ok and frame is not None:
                    frames += 1
                    brightness = float(frame.mean())
            fps = frames / (time.perf_counter() - t0)
            w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            note = "  (image is black - covered, lid closed or privacy shutter?)" if brightness < 25 else ""
            lines.append(
                f"camera {index} {backend:5s} {w}x{h} {_fourcc(cap):4s} -> {fps:4.1f} fps measured{note}"
            )
            cap.release()
        if not found:
            lines.append(f"camera {index}: not available")
            if index > 0:
                break
    return lines


class VideoFile:
    def __init__(self, path: str | Path, realtime: bool = True) -> None:
        self.path = Path(path)
        self._cap = cv2.VideoCapture(str(self.path))
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video {self.path}")
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._index = 0
        self._realtime = realtime
        self._wall0 = time.monotonic()

    def read(self) -> tuple[Frame, float] | None:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        t = self._index / self._fps
        self._index += 1
        if self._realtime:
            delay = t - (time.monotonic() - self._wall0)
            if delay > 0:
                time.sleep(delay)
        return cast(Frame, frame), t

    @property
    def fps(self) -> float | None:
        """None: a video file has no camera whose rate could be measured."""
        return None

    def describe(self) -> str:
        return f"video {self.path.name} @ {self._fps:.1f} fps"

    def close(self) -> None:
        self._cap.release()
