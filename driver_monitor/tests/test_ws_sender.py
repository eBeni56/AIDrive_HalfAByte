"""WebSocketSender against a real local WebSocket server: messages arrive as driver_state JSON, a missing server
never blocks the caller, and the sender reconnects when the server appears."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from websockets.sync.server import Server, ServerConnection, serve

from driver_monitor.config import AppConfig, WebSocketConfig
from driver_monitor.output.schema import DriverState
from driver_monitor.output.ws_sender import WebSocketSender
from driver_monitor.perception.features import FrameFeatures
from driver_monitor.pipeline import DriverMonitor


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return int(s.getsockname()[1])


@contextmanager
def _server(port: int, received: list[dict[str, object]]) -> Iterator[Server]:
    def handler(ws: ServerConnection) -> None:
        for raw in ws:
            received.append(json.loads(raw))

    with serve(handler, "localhost", port) as server:
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        try:
            yield server
        finally:
            server.shutdown()
            th.join(timeout=3)


def _states(n: int) -> list[DriverState]:
    mon = DriverMonitor(AppConfig(), calibrate=False)
    return [
        mon.process_features(FrameFeatures(120.0, None, 0), i / 30).state for i in range(n)
    ]


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_streams_driver_state_json() -> None:
    port, received = _free_port(), []
    with _server(port, received):
        sender = WebSocketSender(WebSocketConfig(url=f"ws://localhost:{port}", reconnect_s=0.1))
        try:
            assert _wait(lambda: sender.connected)
            for s in _states(10):
                sender.send(s)
            assert _wait(lambda: len(received) == 10)
        finally:
            sender.close()
    assert received[0]["type"] == "driver_state" and received[-1]["frame"] == 10
    assert DriverState.model_validate(received[3]).frame == 4  # a complete, valid message
    assert any("connected" in e for e in sender.events())


def test_no_server_never_blocks_and_reconnects_later() -> None:
    port, received = _free_port(), []
    sender = WebSocketSender(WebSocketConfig(url=f"ws://localhost:{port}", reconnect_s=0.1))
    try:
        states = _states(5)
        start = time.perf_counter()
        for s in states:
            sender.send(s)  # nobody listening: dropped, returns at once
        assert time.perf_counter() - start < 0.1
        assert _wait(lambda: any("cannot connect" in e for e in sender.events()))
        with _server(port, received):  # the server starts later: the sender connects by itself
            assert _wait(lambda: sender.connected)
            sender.send(states[-1])
            assert _wait(lambda: len(received) == 1)
    finally:
        sender.close()


def test_messages_from_the_server_do_not_stall_the_connection() -> None:
    # The simulator server sends gaze_update to clients it has not identified yet. Unread, they filled the
    # client's receive buffer, pongs were no longer read and the link dropped with "keepalive ping timeout".
    port, pong = _free_port(), threading.Event()

    def handler(ws: ServerConnection) -> None:
        for i in range(100):
            ws.send(json.dumps({"event": "gaze_update", "n": i}))
        if ws.ping().wait(timeout=3):
            pong.set()
        for _ in ws:
            pass

    with serve(handler, "localhost", port) as server:
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        sender = WebSocketSender(WebSocketConfig(url=f"ws://localhost:{port}", reconnect_s=0.1))
        try:
            assert _wait(lambda: sender.connected)
            assert pong.wait(timeout=5), "the client stopped answering pings"
        finally:
            sender.close()
            server.shutdown()
            th.join(timeout=3)
