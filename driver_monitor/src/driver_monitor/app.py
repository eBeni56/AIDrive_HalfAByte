"""Main loop: read frames -> DriverMonitor -> terminal / JSONL / WebSocket / preview window / MJPEG stream."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2

from driver_monitor.capture.camera import Camera, FrameSource, VideoFile
from driver_monitor.config import AppConfig
from driver_monitor.output.jsonl import JsonlWriter
from driver_monitor.output.mjpeg import MjpegServer
from driver_monitor.output.overlay import OverlayRenderer
from driver_monitor.output.terminal import TerminalReporter
from driver_monitor.output.ws_sender import WebSocketSender
from driver_monitor.pipeline import DriverMonitor

PROJECT_DIR = Path(__file__).resolve().parents[2]  # driver_monitor/ (contains models/, config/)
WINDOW = "driver_monitor  [q quit | c recalibrate | j json | m mirror]"


@dataclass(slots=True)
class RunOptions:
    video: str | None = None
    json_path: str | None = None
    show_window: bool = True
    calibrate: bool = True
    print_json: bool = False
    realtime_video: bool = True


def run(cfg: AppConfig, opts: RunOptions) -> int:
    source: FrameSource = VideoFile(opts.video, opts.realtime_video) if opts.video else Camera(cfg.camera)
    monitor = DriverMonitor(cfg, base_dir=PROJECT_DIR, calibrate=opts.calibrate)
    reporter = TerminalReporter(cfg.output)
    reporter.print_json = opts.print_json
    writer = JsonlWriter(opts.json_path) if opts.json_path else None
    sender = WebSocketSender(cfg.websocket) if cfg.websocket.enabled else None
    overlay = OverlayRenderer()
    stream: MjpegServer | None = None
    if cfg.camera_stream.enabled:
        try:
            stream = MjpegServer(cfg.camera_stream)
        except OSError as e:  # most often: the port is already used
            reporter.line(f"Camera stream disabled: port {cfg.camera_stream.port}: {e}")
    reporter.line(
        f"driver_monitor: {source.describe()}"
        + (f", writing JSON Lines to {opts.json_path}" if opts.json_path else "")
        + (f", streaming JSON to {cfg.websocket.url}" if sender else "")
        + (f", camera stream at {stream.url}" if stream else "")
    )
    if opts.calibrate:
        reporter.line(
            "Calibration starts now: sit normally, look straight ahead; close your eyes when asked."
        )
    frames = 0
    try:
        while True:
            item = source.read()
            if item is None:
                if opts.video:
                    reporter.line("End of video.")
                    break
                reporter.line("Camera returned no frame - retrying...")
                time.sleep(0.2)
                continue
            frame, t = item
            started = time.perf_counter()
            result = monitor.process(frame, t, latency_start=started, camera_fps=source.fps)
            frames += 1
            while monitor.notes:
                reporter.line(monitor.notes.pop(0))
            reporter.report(result.state)
            if writer is not None:
                writer.write(result.state)
            if sender is not None:
                sender.send(result.state)
                for msg in sender.events():
                    reporter.line(msg)
            to_stream = stream if stream is not None and stream.wants_frame(t) else None
            if not opts.show_window and to_stream is None:
                continue
            preview = overlay.render(frame, result)
            if to_stream is not None:
                to_stream.publish(preview)
            if not opts.show_window:
                continue
            cv2.imshow(WINDOW, preview)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("c"):
                monitor.start_calibration(t)
                reporter.line("Recalibrating (all detectors reset).")
            elif key == ord("j"):
                reporter.print_json = not reporter.print_json
            elif key == ord("m"):
                overlay.mirror = not overlay.mirror
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        pass
    finally:
        reporter.close()
        reporter.line(f"Stopped after {frames} frames.")
        source.close()
        monitor.close()
        if writer is not None:
            writer.close()
        if sender is not None:
            sender.close()
            reporter.line(f"WebSocket: {sender.sent} messages sent, {sender.dropped} dropped.")
        if stream is not None:
            stream.close()
        if opts.show_window:
            cv2.destroyAllWindows()
    return 0
