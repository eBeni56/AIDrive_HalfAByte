# Simulator server

WebSocket server with two clients on one port: the driving simulator and the driver camera client.
It reads what both send and tells the simulator which region the driver is looking at: the camera
client's gaze zone, translated to the simulator's regions, 10 times per second.

Files: `sim.py` parses the simulator's messages, `camera.py` parses the camera client's messages
(and maps its gaze zone to a region), `server.py` routes each message to one of them and sends the
answer. Tests: `python -m unittest -v`. A malformed message is logged
as a warning and ignored; the connection stays open.

## Input 1: simulator

Messages have an `event` field. All have a numeric `timestamp`.

```json
{"event": "simulation_started", "timestamp": 1763640000.0}
{"event": "simulation_tick", "timestamp": 1763640000.1,
 "objects": [{"object_id": "car1", "type": "car", "x_px": 40, "y_px": 120}]}
{"event": "simulation_ended", "timestamp": 1763640060.0}
```

`simulation_tick` comes at 10 Hz with the position of every object on the map, visible to the
driver or not (the same objects in every tick of a run). `type` is one of `car`,
`truck`, `bicycle`, `motorcycle`, `pedestrian`; `x_px` is + right of the ego car, `y_px` is + ahead.

## Input 2: driver camera client

Messages have `"type": "driver_state"`, sent about 30 times per second. Only these fields are read,
the rest is ignored. Any field may be missing or null (= no data, not an error).

```json
{"type": "driver_state", "t": 990.21,
 "gaze": {"yaw_deg": -24.3, "pitch_deg": -7.9, "zone": "LEFT_MIRROR"},
 "eyes": {"state": "OPEN"}, "quality": {"face_detected": true},
 "calibration": {"status": "DONE"}}
```

| Field | Meaning |
|---|---|
| `gaze.yaw_deg` | 0 = straight ahead, negative = left, positive = right |
| `gaze.pitch_deg` | 0 = straight, positive = up, negative = down |
| `gaze.zone` | `ROAD`, `REAR_MIRROR`, `LEFT_MIRROR`, `RIGHT_MIRROR`, `LEFT_SIDE`, `RIGHT_SIDE`, `UP`, `DOWN`, `UNKNOWN` |
| `eyes.state` | `OPEN`, `ALMOST_CLOSED`, `CLOSED`, `UNKNOWN` |
| `quality.face_detected` | `false` = no face in the camera |
| `calibration.status` | `CALIBRATING`, `DONE`, `DEFAULTS` |

## Output: to the simulator

`GAZE_HZ` (default 10) times per second, to every client that is not the camera client:

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`timestamp` is server Unix time. `status` is `ok`, `no_face`, `eyes_closed`, `calibrating` or
`no_gaze_client`. `region` is set only when `status` is `ok` (otherwise `null`) and is one of
`front_left`, `front_center`, `front_right`, `side_left`, `side_right`, `mirror_center`,
`mirror_left`, `mirror_right`, `away` (looking at none of the others).

How it is decided from the latest `driver_state`, first match wins:

| Camera client says | `status` | `region` |
|---|---|---|
| nothing yet, or nothing for `CAMERA_TIMEOUT_S` (1 s) | `no_gaze_client` | `null` |
| `quality.face_detected: false` | `no_face` | `null` |
| `eyes.state: CLOSED` | `eyes_closed` | `null` |
| `calibration.status: CALIBRATING` | `calibrating` | `null` |
| `gaze.zone: ROAD`, `gaze.yaw_deg` < -`FRONT_SPLIT_DEG` (7) / within / > +7 | `ok` | `front_left` / `front_center` / `front_right` |
| `gaze.zone: LEFT_MIRROR` / `REAR_MIRROR` / `RIGHT_MIRROR` | `ok` | `mirror_left` / `mirror_center` / `mirror_right` |
| `gaze.zone: LEFT_SIDE` / `RIGHT_SIDE` | `ok` | `side_left` / `side_right` |
| `gaze.zone: UP`, `DOWN`, `UNKNOWN` or missing | `ok` | `away` |

Where the mirrors and the road are (in gaze angles) is set in the camera client, `gaze_zones` and
`gaze.on_road_*` in `driver_monitor/config/driver_monitor.yaml`; its window overlay shows the live
angles and zone. The simulator shows "No gaze data" by itself when no `gaze_update` arrives for 1.5 s.

## Console output

Every simulator message, and every `gaze_update` whose status or region changed, is printed exactly
as received/sent, one JSON per line, so the two directions can be read side by side:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`SENT` is printed only when at least one simulator is connected. Unchanged `gaze_update`s (10 Hz)
and camera messages (~30 Hz) are printed only with `LOG_LEVEL=DEBUG`. The browser console of the simulator shows the same pairs
(its own messages, plus `RECEIVED` for each `gaze_update`).

## Run

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows (non-MSYS2): .venv\Scripts\pip
.venv/bin/python server.py                       # Windows (non-MSYS2): .venv\Scripts\python
```

Listens on `ws://localhost:8765`. Don't run it together with the simulator's own `test_server.py`
(same port).

With Docker it runs as the `server` service of `compose.yml` in the repository root (`docker compose up`),
on `0.0.0.0:8765`; the host port is `SERVER_PORT` in the root `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `localhost` | Interface to listen on. Use `0.0.0.0` in Docker. |
| `PORT` | `8765` | Port. The simulator's `WS_URL` must match. |
| `LOG_LEVEL` | `INFO` | `DEBUG` also prints every `gaze_update` and every camera message. |
| `GAZE_HZ` | `10` | `gaze_update` messages per second. |
| `CAMERA_TIMEOUT_S` | `1.0` | No `driver_state` for this long: `no_gaze_client`. |
| `FRONT_SPLIT_DEG` | `7` | Looking at the road, \|yaw\| beyond this: `front_left` / `front_right`. |

In Docker these come from the root `.env`.
