# driver_monitor

Camera-based driver monitoring for the Half A Byte hackathon project. It watches the driver through a webcam using
MediaPipe Face Landmarker and reports eye closure, microsleep, drowsiness, yawns, head nods and drops, gaze direction
and distraction as a stateful JSON message (`driver_state`) for every frame.

- What it detects, how, which classes/files, the JSON format and many examples: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)
- Scripted scenarios without a camera: `uv run python tools/scenarios.py --list`

## Quick start (Windows)

```powershell
cd driver_monitor
uv sync                                   # creates .venv with all dependencies
uv run python tools/download_model.py     # downloads models/face_landmarker.task (~3.7 MB)
uv run python -m driver_monitor           # opens the camera window and prints detections
```

Useful options:

```powershell
uv run python -m driver_monitor --camera 1             # another camera
uv run python -m driver_monitor --json out.jsonl       # also write every driver_state message as JSON Lines
uv run python -m driver_monitor --video clip.mp4       # run on a recorded video instead of the camera
uv run python -m driver_monitor --no-calibration       # skip calibration (less accurate)
uv run python -m driver_monitor --config my.yaml       # override thresholds
uv run python -m driver_monitor --dump-config          # print all settings with their defaults
uv run python -m driver_monitor --print-schema         # print the JSON Schema of driver_state
```

Keys in the camera window: `q`/`Esc` quit · `c` recalibrate · `j` print the full JSON once per second · `m` toggle mirror.

## Tests

```powershell
uv run pytest
uv run ruff check .
uv run mypy
```
