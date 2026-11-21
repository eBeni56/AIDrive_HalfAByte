"""Command line entry point:  uv run python -m driver_monitor [options]"""

from __future__ import annotations

import argparse
import json
import os
import sys


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="driver_monitor", description="Webcam driver monitoring (MediaPipe).")
    p.add_argument(
        "--config",
        help="YAML file overriding the defaults (default: config/driver_monitor.yaml if it exists; see --dump-config)",
    )
    p.add_argument(
        "--ws", dest="ws_url", help="WebSocket address to stream the JSON to (overrides the config)"
    )
    p.add_argument("--no-ws", action="store_true", help="do not stream the JSON to the WebSocket")
    p.add_argument(
        "--no-stream", action="store_true", help="do not serve the camera preview as MJPEG (for the dashboard)"
    )
    p.add_argument(
        "--camera", type=int, help="camera index (default 1 = external USB camera, falls back to 0)"
    )
    p.add_argument(
        "--list-cameras", action="store_true", help="probe the cameras, show their real FPS and exit"
    )
    p.add_argument("--width", type=int, help="capture width (default 1280)")
    p.add_argument("--height", type=int, help="capture height (default 720)")
    p.add_argument(
        "--backend", choices=["auto", "dshow", "msmf", "any"], help="OpenCV capture backend (default auto)"
    )
    p.add_argument("--video", help="process a video file instead of the camera")
    p.add_argument("--fast", action="store_true", help="with --video: process as fast as possible")
    p.add_argument("--json", dest="json_path", help="write every driver_state message to this .jsonl file")
    p.add_argument("--print-json", action="store_true", help="print the full JSON once per second")
    p.add_argument("--no-window", action="store_true", help="no preview window (terminal only)")
    p.add_argument("--no-calibration", action="store_true", help="skip calibration, use population defaults")
    p.add_argument("--scroll", action="store_true", help="print status lines instead of one updating line")
    p.add_argument("--dump-config", action="store_true", help="print all settings (YAML) and exit")
    p.add_argument("--print-schema", action="store_true", help="print the driver_state JSON Schema and exit")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    os.environ.setdefault("GLOG_minloglevel", "2")  # quieter MediaPipe/TFLite logs
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from driver_monitor.config import DEFAULT_CONFIG_FILE, dump_config, load_config

    config_path = args.config or (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None)
    cfg = load_config(config_path)
    if args.ws_url:
        cfg.websocket.url = args.ws_url
        cfg.websocket.enabled = True
    if args.no_ws:
        cfg.websocket.enabled = False
    if args.no_stream:
        cfg.camera_stream.enabled = False
    if args.camera is not None:
        cfg.camera.index = args.camera
        cfg.camera.fallback_indices = []  # an explicit choice must not silently open another camera
    if args.width:
        cfg.camera.width = args.width
    if args.height:
        cfg.camera.height = args.height
    if args.backend:
        cfg.camera.backend = args.backend
    if args.scroll:
        cfg.output.status_in_place = False

    if args.dump_config:
        print(dump_config(cfg))
        return 0
    if args.list_cameras:
        from driver_monitor.capture.camera import list_cameras

        print("Probing cameras (a few seconds each)...")
        for line in list_cameras(cfg.camera):
            print("  " + line)
        print("Use the one you want with --camera N (MSMF is used automatically when it is faster).")
        return 0
    if args.print_schema:
        from driver_monitor.output.schema import DriverState

        print(json.dumps(DriverState.model_json_schema(), indent=2))
        return 0

    from driver_monitor.app import RunOptions, run

    return run(
        cfg,
        RunOptions(
            video=args.video,
            json_path=args.json_path,
            show_window=not args.no_window,
            calibrate=not args.no_calibration,
            print_json=args.print_json,
            realtime_video=not args.fast,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
