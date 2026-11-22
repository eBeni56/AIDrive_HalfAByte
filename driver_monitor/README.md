# driver_monitor

Watches the driver through a webcam (MediaPipe, on the CPU) and reports, for every camera frame, three things:

| What | How | In the message |
|---|---|---|
| **Where the driver looks** | head pose + eye direction → a zone: the road, one of the three mirrors, a side window, up, down | `gaze.zone`; `DISTRACTED` after 1 s up or down |
| **Eyes closed** | eye openness against this driver's own open and closed eye (calibration) | `eyes.state`; `EYES_CLOSED` from 0.5 s, `DANGER` from 1 s |
| **Phone in the hand** | MediaPipe object detector ("cell phone"), ~6 times per second in the background | `PHONE_USE` |

## What it needs, what it sends

**Needs**
* a webcam: camera 1 (an external USB camera) by default, camera 0 if that one is missing;
* the two model files in `models/` (`tools/download_model.py` downloads them);
* Python 3.12 (with [uv](https://docs.astral.sh/uv/), or the `.venv` that `start.bat` creates).

**Sends**
* **`driver_state`**: one JSON message per processed frame (~30 per second), as a WebSocket text message to
  `websocket.url` (default `ws://localhost:8765`, the server). The monitor is the *client*: it reconnects every
  2 s and never waits for the server; while disconnected the messages are dropped (the server wants the live
  state). Every field is described in [docs/detection-schema.md](docs/detection-schema.md); machine-readable:
  [docs/driver_state.schema.json](docs/driver_state.schema.json).
* **The camera image** with the markings (eye contours, iris, gaze arrow, phone box, status panel), as an MJPEG
  stream for the dashboard: `http://localhost:8090/stream.mjpg`. A frame is only encoded while a page watches.
* **The terminal**: a status line and one line whenever a detection starts, changes or ends.

It **receives** only `{"type": "recenter_gaze"}` from the server, when the simulator calibrates (its page loads,
or its Calibrate button): it then measures
the straight-ahead look again (see [Calibration](#calibration-the-first-9-s)). Everything else the server sends
is read and dropped.

A shortened `driver_state` while looking down at a phone:

```json
{"type": "driver_state", "schema_version": 2, "t": 12.4, "frame": 371,
 "eyes": {"state": "OPEN", "state_for_s": 9.1, "openness": 0.46, "source": "BOTH"},
 "gaze": {"zone": "DOWN", "zone_for_s": 1.7, "yaw_deg": 0.0, "pitch_deg": -46.0},
 "detections": [{"id": "distracted-1", "type": "DISTRACTED", "severity": "WARNING", "status": "ONGOING",
                 "started_t": 10.7, "ended_t": null, "duration_s": 1.7, "details": {"zone": "DOWN", "away_s": 1.7}}]}
```

## Run

`start.bat` in the repository root does all of this. By hand:

```powershell
cd driver_monitor
uv sync                                   # creates .venv with all dependencies
uv run python tools/download_model.py     # the face and the phone model into models/
uv run python -m driver_monitor           # camera window + terminal; streams to the server
```

| Option | |
|---|---|
| `--camera N`, `--list-cameras` | pick a camera / show the cameras and the FPS each really delivers |
| `--no-window`, `--scroll` | no preview window / status lines instead of one updating line |
| `--no-ws`, `--ws URL` | do not stream / stream to another address |
| `--no-stream` | no MJPEG camera stream |
| `--video clip.mp4 [--fast]` | a recorded video instead of the camera |
| `--json out.jsonl`, `--print-json` | every message into a JSON Lines file / the full JSON once per second |
| `--no-calibration` | skip the calibration (average values, less accurate) |
| `--config my.yaml`, `--dump-config`, `--print-schema` | another settings file / print every setting / the JSON Schema |

Keys in the preview window: `q` / `Esc` quit · `c` calibrate again · `j` print the JSON · `m` mirror the image.

## Calibration (the first ~9 s)

1. **~6 s, eyes open, looking straight ahead at the centre of the screen.** This look is the zero point of
   every angle (head and eyes); it also gives this driver's normal open eye.
2. **~3 s, eyes closed** (when asked): this driver's closed eye.

It starts again if the face was missing too often or the head moved too much. Look at the screen centre: if you
look elsewhere, every zone shifts. Without calibration (`--no-calibration`) the camera axis counts as straight
ahead and average eyes are used. The phone detection needs no calibration and works from the first frame.

**When the simulator calibrates** (its page loads, or its Calibrate button: "look at the number", a 5 s
countdown; not at each simulation start) the server sends `recenter_gaze`, and only the straight-ahead look is
measured again: 4 s (`calibration.recenter_s`), the
first 1 s ignored while the driver turns to the centre (`recenter_settle_s`). The eye baselines stay. Meanwhile
`calibration.status` is `CALIBRATING` (phase `OPEN_EYES`, message "Look at the centre of the screen"). It is
not repeated by itself: if the face was missing too often or the head moved too much, the previous straight-ahead is kept
(the terminal says which). While the first calibration is still running the request is skipped.

## How it works

```
camera → perception   MediaPipe face landmarks → eye openness (EAR), iris position, head angles; phone finder
       → signals      head angles relative to the calibration → eye openness and OPEN / CLOSED →
                      gaze angles (head + eyes) → zone
       → detectors    EYES_CLOSED, DISTRACTED, PHONE_USE: episodes with an id, STARTED → ONGOING → ENDED
       → output       driver_state (WebSocket, JSON Lines), terminal, preview / MJPEG
```

The zones, for the simulator in full screen, from the middle outwards on each side (angles in degrees from
the calibrated straight-ahead look, yaw − left / + right, pitch − down / + up):

| Zone | Where |
|---|---|
| `ROAD` | \|yaw\| ≤ 20, pitch −15 … +15 (the whole windshield: the server's `front`) |
| `LEFT_MIRROR`, `RIGHT_MIRROR` | yaw 20 … 60, at any height up to +30 (looking "leftish" / "rightish", head turned up to ~45° and more) |
| `LEFT_SIDE`, `RIGHT_SIDE` | yaw beyond 60: head turned fully to the side (the side window in the simulator) |
| `REAR_MIRROR` | top centre: yaw −12 … +12, pitch +8 … +25 |
| `DOWN`, `UP` | below / above the road box: nothing to look at (`DISTRACTED` after 1 s) |
| `UNKNOWN` | no face, or the eyes closed longer than a blink |

Every border has 3° hysteresis, so the zone does not flicker. The preview's panel shows the live
`gaze ZONE (yaw, pitch)`: if a mirror is hard to hit, look at it, read the angles and adjust `gaze_zones`.

## Settings

[config/driver_monitor.yaml](config/driver_monitor.yaml) is loaded at start; only the keys written there override
the defaults, and an unknown key is an error. Every setting and threshold, with a comment, is in
[src/driver_monitor/config.py](src/driver_monitor/config.py); `--dump-config` prints them all. In Docker
[config/driver_monitor.docker.yaml](config/driver_monitor.docker.yaml) is used (the server is `ws://server:8765`).

## Folders

| Folder / file | What is in it |
|---|---|
| `src/driver_monitor/app.py`, `__main__.py` | the main loop and the command line |
| `src/driver_monitor/pipeline.py` | `DriverMonitor`: one frame in, one `driver_state` out |
| `capture/` | the camera (Windows: Media Foundation first, DirectShow as fallback) or a video file |
| `perception/` | MediaPipe: face landmarks, per-frame features, the phone finder |
| `calibration/` | the two calibration phases and the driver's profile |
| `signals/` | head pose, eyes (openness, OPEN / CLOSED), gaze angles and zone |
| `detectors/` | `eye_closure.py`, `distraction.py`, `phone.py`; episodes: `base.py` |
| `output/` | the JSON model (`schema.py`), terminal, preview overlay, WebSocket sender, MJPEG stream |
| `config/`, `docs/` | settings files; the `driver_state` reference |
| `tools/` | `download_model.py`, `scenarios.py` (scripted drivers without a camera), `ws_receiver.py` (a test server) |
| `tests/` | the tests; `models/`, `recordings/`: downloaded models and videos (not in git) |

## Tests and tools

```powershell
uv run pytest                              # detectors, zones, scenarios, schema file, WebSocket sender
uv run ruff check .
uv run mypy
uv run python tools/scenarios.py --list    # scripted scenarios through the real pipeline
uv run python tools/ws_receiver.py         # receive the stream without the server: one line per second
```

`docs/driver_state.schema.json` must match the code (a test checks it); after a change to `output/schema.py`
regenerate it with `uv run python -m driver_monitor --print-schema > docs/driver_state.schema.json`.
