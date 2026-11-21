"""Main loop: read frames -> DriverMonitor -> terminal / JSONL / preview window."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2

from driver_monitor.capture.camera import Camera, FrameSource, VideoFile
from driver_monitor.config import AppConfig
from driver_monitor.output.jsonl import JsonlWriter
from driver_monitor.output.overlay import OverlayRenderer
from driver_monitor.output.terminal import TerminalReporter
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
    overlay = OverlayRenderer(mirror=True)
    reporter.line(
        f"driver_monitor: {source.describe()}"
        + (f", writing JSON Lines to {opts.json_path}" if opts.json_path else "")
    )
    if opts.calibrate and cfg.calibration.enabled:
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
            result = monitor.process(
                frame, t, latency_start=started, camera_fps=source.fps if not opts.video else None
            )
            frames += 1
            reporter.report(result.state)
            if writer is not None:
                writer.write(result.state)
            if not opts.show_window:
                continue
            cv2.imshow(WINDOW, overlay.render(frame, result))
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
        if opts.show_window:
            cv2.destroyAllWindows()
    return 0
