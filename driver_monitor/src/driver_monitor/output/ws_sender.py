"""Streams every driver_state JSON message to a WebSocket server while the camera runs.

The camera loop only calls `send()`, which never blocks: messages go into a small queue and a background
thread delivers them. If the server is not running yet or the connection drops, the thread keeps
reconnecting every `reconnect_s`; meanwhile messages are dropped (the consumer wants the LIVE state, not a
backlog). If sending is slower than the camera, the oldest queued messages are dropped.
Each WebSocket text message is one complete driver_state JSON object (same format as --json / JSON Lines).
From the server only {"type": "recenter_gaze"} is used (at every simulation start: measure the straight-ahead
look again; see `recenter_requests()`); everything else it sends is read and dropped.
"""

from __future__ import annotations

import json
import queue
import threading

from websockets.sync.client import ClientConnection, connect

from driver_monitor.config import WebSocketConfig
from driver_monitor.output.schema import DriverState

QUEUE_SIZE = 30  # messages buffered while sending is slower than the camera (the oldest are dropped)
CONNECT_TIMEOUT_S = 3.0


class WebSocketSender:
    def __init__(self, cfg: WebSocketConfig) -> None:
        self.cfg = cfg
        self._queue: queue.Queue[str] = queue.Queue(maxsize=QUEUE_SIZE)
        self._events: queue.Queue[str] = queue.Queue()
        self._recenter = threading.Event()  # the server asked for a recenter (recenter_gaze)
        self._stop = threading.Event()
        self.connected = False
        self.sent = 0
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name="websocket-sender", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ camera thread
    def send(self, state: DriverState) -> None:
        """Queue one message (non-blocking). Skipped while disconnected."""
        if not self.connected:
            return
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

    def recenter_requests(self) -> bool:
        """True once after the server sent recenter_gaze (a simulation is starting)."""
        if not self._recenter.is_set():
            return False
        self._recenter.clear()
        return True

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)

    # ------------------------------------------------------------------ sender thread
    def _run(self) -> None:
        failing = False
        while not self._stop.is_set():
            try:
                with connect(self.cfg.url, open_timeout=CONNECT_TIMEOUT_S, close_timeout=1.0) as ws:
                    failing = False
                    self._serve(ws)  # handles its own errors; returns when stopped or the link is lost
            except Exception as e:  # server not running, bad address, timeout ...
                if not failing:
                    self._events.put(
                        f"WebSocket: cannot connect to {self.cfg.url} ({_short(e)}) - retrying every "
                        f"{self.cfg.reconnect_s:g} s"
                    )
                    failing = True
            if not self._stop.is_set():
                self._stop.wait(self.cfg.reconnect_s)

    def _serve(self, ws: ClientConnection) -> None:
        while not self._queue.empty():  # never send stale messages from before this connection
            self._queue.get_nowait()
        self.connected = True
        self._events.put(f"WebSocket: connected to {self.cfg.url}, streaming driver_state messages")
        try:
            while not self._stop.is_set():
                if _read_incoming(ws):
                    self._recenter.set()
                try:
                    msg = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                ws.send(msg)
                self.sent += 1
        except Exception as e:
            self._events.put(f"WebSocket: connection to {self.cfg.url} lost ({_short(e)}) - reconnecting")
        finally:
            self.connected = False


def _read_incoming(ws: ClientConnection) -> bool:
    """Read whatever the server sent; True if a recenter_gaze was among it, everything else is dropped.
    Unread messages fill the client's receive buffer; it then stops reading the socket, the server's pongs
    are missed and the connection closes with "keepalive ping timeout"."""
    recenter = False
    while True:
        try:
            raw = ws.recv(timeout=0)
        except TimeoutError:
            return recenter
        recenter = recenter or _is_recenter(raw)


def _is_recenter(raw: str | bytes) -> bool:
    try:
        msg = json.loads(raw)
    except ValueError:
        return False
    return isinstance(msg, dict) and msg.get("type") == "recenter_gaze"


def _short(e: Exception) -> str:
    text = str(e) or type(e).__name__
    return text if len(text) <= 80 else text[:77] + "..."
