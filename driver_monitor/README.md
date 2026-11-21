# driver_monitor

Camera-based driver monitoring for the Half A Byte hackathon project. It watches the driver through a webcam
(MediaPipe) and sends one JSON message (`driver_state`) per frame with exactly three things:

| What | How | In the message |
|---|---|---|
| **Where the driver looks** | head pose + iris position → a zone: road, the three mirrors, the side windows, up, down | `gaze.zone` (the server turns it into the simulator's 8 regions); `DISTRACTED` when it is up / down (none of the 8) for 1 s |
| **Eyes closed** | eye openness against the driver's own open / closed eye (calibration) | `eyes.state`; `EYES_CLOSED` from 0.5 s, `DANGER` from 1 s |
| **Phone in the hand** | MediaPipe Object Detector (EfficientDet-Lite0, "cell phone") in the background | `PHONE_USE` |

- The `driver_state` JSON field by field: [docs/detection-schema.md](docs/detection-schema.md), machine-readable
  JSON Schema: [docs/driver_state.schema.json](docs/driver_state.schema.json)
- Scripted scenarios without a camera: `uv run python tools/scenarios.py --list`

| Folder | What is in it |
|---|---|
| `perception/` | MediaPipe: face landmarks (`face_tracker.py`), per-frame features (`features.py`), the phone finder |
| `calibration/` | the first ~9 s: straight-ahead look, open and closed eye of this driver |
| `signals/` | head pose, eye openness and state, gaze angles and zone |
| `detectors/` | `eye_closure.py`, `distraction.py`, `phone.py` (episodes: `base.py`) |
| `output/` | the JSON model (`schema.py`), terminal, preview overlay, WebSocket sender, MJPEG stream |

## Quick start (Windows)

```powershell
cd driver_monitor
uv sync                                   # creates .venv with all dependencies
uv run python tools/download_model.py     # downloads the face and the phone model into models/
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

The phone detector (`models/efficientdet_lite0.tflite`, settings: `phone` in the config) runs in the background
~6 times per second and never slows down the face pipeline; without the model file it is switched off with a
message. It needs no calibration, so it works from the first frame. The preview draws a box around the phone.

It also serves the preview window's image (with all the markings) as an MJPEG stream for the dashboard, at
`http://localhost:8090/stream.mjpg` (`camera_stream` in the config, `--no-stream` turns it off). A frame is only
encoded while a page is watching, so it costs nothing otherwise.

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
