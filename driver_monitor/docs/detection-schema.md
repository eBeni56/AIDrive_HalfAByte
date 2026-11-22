# `driver_state` JSON — schema reference (schema_version 2)

The message the driver monitor sends for **every processed camera frame** (~30 per second). It carries what the
project needs and nothing else: **where the driver looks** (`gaze.zone`), **whether the eyes are open**
(`eyes.state`) and three detections: **`EYES_CLOSED`**, **`DISTRACTED`** and **`PHONE_USE`**.

* **Source of truth:** the pydantic model [output/schema.py](../src/driver_monitor/output/schema.py).
* **Machine-readable JSON Schema:** [driver_state.schema.json](driver_state.schema.json). It is generated from the
  model and kept in sync by a test. Regenerate it with
  `uv run python -m driver_monitor --print-schema > docs/driver_state.schema.json`.

## Transport

| Channel | How |
|---|---|
| **WebSocket** (live) | One text message = one `driver_state` object. The monitor is the *client*: it connects to `websocket.url` from [config/driver_monitor.yaml](../config/driver_monitor.yaml) (default `ws://localhost:8765`) and reconnects every 2 s. Test receiver: `uv run python tools/ws_receiver.py`. |
| **JSON Lines file** | `--json session.jsonl`: every message, one per line. |
| **Terminal** | `j` in the window / `--print-json`: the full JSON once per second. |

## Conventions

* Times are seconds on the monitor's own clock (`t`, `started_t`, …), and durations are seconds.
* Angles are degrees **relative to the calibrated straight-ahead look**: yaw `-` left / `+` right, pitch `-` down /
  `+` up.
* `openness`: `1` = this driver's normal open eye, `0` = closed (both measured during calibration).
* Any value that cannot be measured (no face, head turned too far) is `null` or `UNKNOWN`. It is never an error.

## Fields

| Block | Field | Meaning |
|---|---|---|
| — | `type`, `schema_version`, `t`, `frame` | `"driver_state"`, `2`, time, frame counter |
| `summary` | `highest_severity` | `NONE` / `WARNING` / `DANGER` of the detections going on now |
| | `active` | their types |
| `quality` | `fps`, `camera_fps`, `latency_ms` | processing speed |
| | `face_detected`, `faces_in_view` | is a face in the image (the largest one is the driver) |
| | `issues` | `CALIBRATING`, `HEAD_TURNED_TOO_FAR`, `FACE_TOO_SMALL`, `FACE_PARTIALLY_OUT_OF_FRAME`, `LOW_LIGHT`, `LOW_FPS` |
| `calibration` | `status` | `CALIBRATING` (the first ~9 s: look straight ahead, then close the eyes), `DONE`, `DEFAULTS` (`--no-calibration`) |
| | `phase`, `progress`, `message` | while calibrating: `OPEN_EYES` / `CLOSED_EYES`, 0..1, the instruction |
| `eyes` | `state` | `OPEN`, `CLOSED`, `UNKNOWN` (no face / head turned too far) |
| | `state_for_s`, `openness`, `source` | how long in this state, 0..1, `BOTH` / `LEFT_ONLY` / `RIGHT_ONLY` (one eye when the head is turned) |
| `head` | `yaw_deg`, `pitch_deg`, `roll_deg` | head pose |
| `gaze` | `zone` | where the driver looks, see below |
| | `zone_for_s` | how long in this zone |
| | `yaw_deg`, `pitch_deg` | head + eyes combined |
| `detections[]` | see below | |

### Gaze zones

| `gaze.zone` | Simulator region (server) | |
|---|---|---|
| `ROAD` | `front_left` / `front_center` / `front_right` (split by `gaze.yaw_deg`) | fine |
| `LEFT_MIRROR`, `REAR_MIRROR`, `RIGHT_MIRROR` | `mirror_left`, `mirror_center`, `mirror_right` | fine |
| `LEFT_SIDE`, `RIGHT_SIDE` | `side_left`, `side_right` (head turned far: side window / blind spot) | fine |
| `UP`, `DOWN` | `away` | none of the 8 regions → `DISTRACTED` after 1 s |
| `UNKNOWN` | `away` (the server reports `no_face` / `eyes_closed`) | no face, or the eyes closed longer than a blink |

The mirror boxes are in `gaze_zones` in the config. The defaults are for the simulator in full screen. From the
middle outwards, per side:
* the road (|yaw| ≤ 20°): `ROAD` (the server splits it into front-left / centre / front-right);
* the side mirror: yaw 20°…55° at any height (lower screen corner up to eye level);
* the side window: beyond 55° (`LEFT_SIDE` / `RIGHT_SIDE`);
* the rear-view mirror is the top centre; looking down in the middle is `DOWN`.

The road box is set by `gaze.on_road_*`.

## Detections

Each detection is an **episode** with a stable `id` (e.g. `eyes_closed-3`). Its first message has
`status: STARTED`, the next ones `ONGOING`, and exactly one message has `ENDED`. `started_t` is when the problem
began (not when the threshold was crossed). `ended_t` is when it was over, and `duration_s` is the length.
Detections that are going on come first, the most severe first.

| `type` | Starts | `WARNING` → `DANGER` | Ends | `details` |
|---|---|---|---|---|
| `EYES_CLOSED` | eyes `CLOSED` for 0.5 s (a blink is shorter) | after 1 s (microsleep) | eyes open for 0.2 s, or no face for 1 s | `closed_for_s` |
| `DISTRACTED` | gaze `UP` or `DOWN` (none of the 8 regions) for 1 s | after 2.5 s | back on the road / a mirror / a side window for 0.5 s | `zone` (`UP` / `DOWN`), `away_s` |
| `PHONE_USE` | a phone seen for 0.4 s (MediaPipe Object Detector, COCO "cell phone", ~6 checks/s) | after 3 s | no phone seen for 1 s | `score`, `max_score`, `box` {x, y, w, h} (raw frame pixels), `seen_for_s` |

`EYES_CLOSED` and `DISTRACTED` need the calibration, so they start after it. `PHONE_USE` works from the first frame.
Every threshold is in [config.py](../src/driver_monitor/config.py) (`eye_closure`, `distraction`, `phone`) and
can be overridden in the YAML file.

## Example

Looking down at a phone for 1.7 s. The message was generated by the real pipeline:
`uv run python tools/scenarios.py look_down`.

```json
{
  "type": "driver_state", "schema_version": 2, "t": 2.8, "frame": 85,
  "summary": {"highest_severity": "WARNING", "active": ["DISTRACTED"]},
  "quality": {"fps": 30.0, "camera_fps": null, "latency_ms": 12.0, "face_detected": true, "faces_in_view": 1, "issues": []},
  "calibration": {"status": "DEFAULTS", "phase": null, "progress": 1.0,
                  "message": "population defaults - run calibration for accurate results"},
  "eyes": {"state": "OPEN", "state_for_s": 2.8, "openness": 0.46, "source": "BOTH"},
  "head": {"yaw_deg": 0.0, "pitch_deg": -24.5, "roll_deg": 0.0},
  "gaze": {"zone": "DOWN", "zone_for_s": 1.7, "yaw_deg": 0.0, "pitch_deg": -46.0},
  "detections": [
    {"id": "distracted-1", "type": "DISTRACTED", "severity": "WARNING", "status": "ONGOING",
     "started_t": 1.1, "ended_t": null, "duration_s": 1.7, "details": {"zone": "DOWN", "away_s": 1.7}}
  ]
}
```
