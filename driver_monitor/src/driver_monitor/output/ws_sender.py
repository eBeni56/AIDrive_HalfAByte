"""Streams every driver_state JSON message to a WebSocket server while the camera runs.

The camera loop only calls `send()`, which never blocks: messages go into a small queue and a background
thread delivers them. If the server is not running yet or the connection drops, the thread keeps
reconnecting every `reconnect_s`; meanwhile messages are dropped (the consumer wants the LIVE state, not a
backlog). If sending is slower than the camera, the oldest queued messages are dropped.
Each WebSocket text message is one complete driver_state JSON object (same format as --json / JSON Lines).
"""

from __future__ import annotations

import queue
import threading

from websockets.sync.client import ClientConnection, connect

from driver_monitor.config import WebSocketConfig
from driver_monitor.output.schema import DriverState


class WebSocketSender:
    def __init__(self, cfg: WebSocketConfig) -> None:
        self.cfg = cfg
        self.url = cfg.url
        self._queue: queue.Queue[str] = queue.Queue(maxsize=max(1, cfg.queue_size))
        self._events: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self.connected = False
        self.sent = 0
        self.dropped = 0
        self._next_t: float | None = None  # rate limit: earliest time of the next message
        self._thread = threading.Thread(target=self._run, name="websocket-sender", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ camera thread
    def send(self, state: DriverState, t: float) -> None:
        """Queue one message (non-blocking). Skipped while disconnected or above max_rate_hz."""
        if not self.connected:
            return
        if self.cfg.max_rate_hz > 0:  # send on a fixed time grid: max_rate_hz on average, no bursts
            interval = 1.0 / self.cfg.max_rate_hz
            if self._next_t is not None and t < self._next_t - 1e-3:
                return
            self._next_t = (
                t + interval if self._next_t is None else max(self._next_t + interval, t + interval / 2)
            )
        msg = state.model_dump_json()
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            try:
                self._queue.get_nowait()  # drop the oldest: the newest state matters most
                self.dropped += 1
            except queue.Empty:
                pass
            self._queue.put_nowait(msg)

    def events(self) -> list[str]:
        """Status messages from the sender thread (connected, lost, ...) for the terminal."""
        out: list[str] = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------ sender thread
    def _run(self) -> None:
        failing = False
        while not self._stop.is_set():
            try:
                with connect(self.url, open_timeout=self.cfg.connect_timeout_s, close_timeout=1.0) as ws:
                    failing = False
                    self._serve(ws)  # handles its own errors; returns when stopped or the link is lost
            except Exception as e:  # server not running, bad address, timeout ...
                if not failing:
                    self._events.put(
                        f"WebSocket: cannot connect to {self.url} ({_short(e)}) - retrying every "
                        f"{self.cfg.reconnect_s:g} s"
                    )
                    failing = True
            if not self._stop.is_set():
                self._stop.wait(self.cfg.reconnect_s)

    def _serve(self, ws: ClientConnection) -> None:
        while not self._queue.empty():  # never send stale messages from before this connection
            self._queue.get_nowait()
        self.connected = True
        self._events.put(f"WebSocket: connected to {self.url}, streaming driver_state messages")
        try:
            while not self._stop.is_set():
                try:
                    msg = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                ws.send(msg)
                self.sent += 1
        except Exception as e:
            self._events.put(f"WebSocket: connection to {self.url} lost ({_short(e)}) - reconnecting")
        finally:
            self.connected = False


def _short(e: Exception) -> str:
    text = str(e) or type(e).__name__
    return text if len(text) <= 80 else text[:77] + "..."
