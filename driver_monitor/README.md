# driver_monitor

Camera-based driver monitoring for the Half A Byte hackathon project. It watches the driver through a webcam using
MediaPipe Face Landmarker and reports eye closure, microsleep, drowsiness, yawns, head nods and drops, gaze direction
and distraction as a stateful JSON message (`driver_state`) for every frame.

- What it detects, how, which classes/files, the JSON format and many examples: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)
- The `driver_state` JSON field by field (for the simulator / consumers): [docs/detection-schema.md](docs/detection-schema.md),
  machine-readable JSON Schema: [docs/driver_state.schema.json](docs/driver_state.schema.json)
- Scripted scenarios without a camera: `uv run python tools/scenarios.py --list`

## Quick start (Windows)

```powershell
cd driver_monitor
uv sync                                   # creates .venv with all dependencies
uv run python tools/download_model.py     # downloads models/face_landmarker.task (~3.7 MB)
uv run python -m driver_monitor           # opens the camera window and prints detections
```

While it runs, every `driver_state` JSON message is also **streamed to a WebSocket** (one text message per
processed frame). The address is in [config/driver_monitor.yaml](config/driver_monitor.yaml) (`websocket.url`,
default `ws://localhost:8765`), which is loaded automatically. If no server is listening, the monitor keeps
running and reconnects every 2 s. To see the stream without the simulator, start the test receiver in a second
terminal:

```powershell
uv run python tools/ws_receiver.py          # listens on ws://localhost:8765, prints one summary line per second
uv run python tools/ws_receiver.py --full   # prints every JSON message
```

Useful options:

```powershell
uv run python -m driver_monitor --ws ws://192.168.1.20:9000/driver   # stream to another address (overrides the config)
uv run python -m driver_monitor --no-ws                              # do not stream
uv run python -m driver_monitor --list-cameras         # show the cameras and the FPS each really delivers
uv run python -m driver_monitor --camera 0             # pick a camera (default 1 = external USB camera, falls back to 0)
uv run python -m driver_monitor --json out.jsonl       # also write every driver_state message as JSON Lines
uv run python -m driver_monitor --video clip.mp4       # run on a recorded video instead of the camera
uv run python -m driver_monitor --no-calibration       # skip calibration (less accurate)
uv run python -m driver_monitor --config my.yaml       # use another settings file instead of config/driver_monitor.yaml
uv run python -m driver_monitor --dump-config          # print all settings with their defaults
uv run python -m driver_monitor --print-schema         # print the JSON Schema of driver_state
```

To run it together with the simulator and the server (Docker Compose, or the live camera on the host against the
server in Docker), see the [repository README](../README.md#run-everything-together-docker).

Keys in the camera window: `q`/`Esc` quit · `c` recalibrate · `j` print the full JSON once per second · `m` toggle mirror.

## Tests

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```

`tests/test_recordings.py` replays real webcam recordings from `recordings/` (git-ignored); cache a new
one with `uv run python tools/cache_recording.py recordings/clip.mp4` (`--screen` for a screen capture of
the preview window). See docs/IMPLEMENTATION.md section 12.
