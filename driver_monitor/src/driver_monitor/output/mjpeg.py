"""Serves the preview image (the same overlay as the window) as an MJPEG stream over HTTP.

The dashboard shows it with  <img src="http://localhost:8090/stream.mjpg">.  The browser can't open the
webcam itself: on Windows only one program can use it, and that is this one.

  GET /stream.mjpg   multipart/x-mixed-replace stream, at most `fps` frames per second

The camera loop only calls `wants_frame()` and `publish()`. A frame is drawn and JPEG-encoded only while at
least one viewer is connected, so the stream costs nothing when nobody watches. A slow viewer just skips
frames (it always gets the newest one); it never slows down the camera loop.
"""

from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import numpy.typing as npt

from driver_monitor.config import CameraStreamConfig

BOUNDARY = "frame"


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second program bind the SAME port while the first still listens (the
    # browser then gets one of them at random). Without it a second driver monitor gets "port in use".
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


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
        self._httpd = _Server((cfg.host, cfg.port), self._handler_class())  # OSError if the port is used
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
        mw = self.cfg.max_width
        small = cv2.resize(img, (mw, round(h * mw / w)), interpolation=cv2.INTER_AREA) if 0 < mw < w else img
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
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
            def log_message(self, format: str, *args: object) -> None:  # quiet console
                pass

            def _headers(self, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

            def do_GET(self) -> None:  # the name is http.server's API
                if self.path.split("?", 1)[0] == "/stream.mjpg":
                    self._stream()
                else:
                    self.send_error(404, "use /stream.mjpg")

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
