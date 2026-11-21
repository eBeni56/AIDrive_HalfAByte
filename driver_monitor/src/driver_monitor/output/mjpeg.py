"""Serves the preview image (the same overlay as the window) as an MJPEG stream over HTTP.

The dashboard shows it with  <img src="http://localhost:8090/stream.mjpg">.  The browser can't open the
webcam itself: on Windows only one program can use it, and that is this one.

  GET /stream.mjpg   multipart/x-mixed-replace stream, at most `fps` frames per second
  GET /frame.jpg     the latest frame once

The camera loop only calls `wants_frame()` and `publish()`. A frame is drawn and JPEG-encoded only while at
least one viewer is connected, so the stream costs nothing when nobody watches. A slow viewer just skips
frames (it always gets the newest one); it never slows down the camera loop.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.config import CameraStreamConfig

BOUNDARY = "frame"


class MjpegServer:
    def __init__(self, cfg: CameraStreamConfig) -> None:
        self.cfg = cfg
        self.url = f"http://{cfg.host}:{cfg.port}/stream.mjpg"
        self._cond = threading.Condition()
        self._jpeg: bytes | None = None
        self._seq = 0  # increases with every published frame
        self._viewers = 0
        self._stop = threading.Event()
        self._next_t: float | None = None
        self._httpd = ThreadingHTTPServer((cfg.host, cfg.port), self._handler_class())  # OSError if port used
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="mjpeg-server", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ camera thread
    def wants_frame(self, t: float) -> bool:
        """True if a frame should be published now: someone is watching and `fps` allows it."""
        if self._viewers == 0 or self._stop.is_set():
            self._next_t = None
            return False
        if self._next_t is not None and t < self._next_t - 1e-3:
            return False
        interval = 1.0 / max(self.cfg.fps, 0.1)
        self._next_t = t + interval if self._next_t is None else max(self._next_t + interval, t + interval / 2)
        return True

    def publish(self, img: npt.NDArray[np.uint8]) -> None:
        h, w = img.shape[:2]
        if self.cfg.max_width > 0 and w > self.cfg.max_width:
            img = cv2.resize(img, (self.cfg.max_width, round(h * self.cfg.max_width / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
        if not ok:
            return
        with self._cond:
            self._jpeg = buf.tobytes()
            self._seq += 1
            self._cond.notify_all()

    def close(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._httpd.shutdown()
        self._httpd.server_close()

    # ------------------------------------------------------------------ HTTP threads
    def _next_jpeg(self, after_seq: int) -> tuple[int, bytes] | None:
        """Wait for a frame newer than `after_seq`. None when stopping (or nothing for a while)."""
        with self._cond:
            self._cond.wait_for(lambda: self._stop.is_set() or self._seq > after_seq, timeout=5.0)
            if self._stop.is_set() or self._jpeg is None or self._seq <= after_seq:
                return None
            return self._seq, self._jpeg

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - quiet console
                pass

            def _headers(self, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                path = self.path.split("?", 1)[0]
                if path == "/stream.mjpg":
                    self._stream()
                elif path == "/frame.jpg":
                    self._frame()
                else:
                    self.send_error(404, "use /stream.mjpg or /frame.jpg")

            def _frame(self) -> None:
                with server._cond:
                    server._viewers += 1
                try:
                    got = server._next_jpeg(0)
                finally:
                    with server._cond:
                        server._viewers -= 1
                if got is None:
                    self.send_error(503, "no frame yet")
                    return
                self._headers("image/jpeg")
                self.wfile.write(got[1])

            def _stream(self) -> None:
                self._headers(f"multipart/x-mixed-replace; boundary={BOUNDARY}")
                with server._cond:
                    server._viewers += 1
                seq = 0
                try:
                    while not server._stop.is_set():
                        got = server._next_jpeg(seq)
                        if got is None:
                            continue  # no new frame for a while (camera stalled): keep the viewer
                        seq, jpeg = got
                        self.wfile.write(
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
                        )
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # the browser closed the page
                finally:
                    with server._cond:
                        server._viewers -= 1

        return Handler
