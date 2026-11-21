# Simulator server

WebSocket server with two clients on one port: the driving simulator and the driver camera client.
It reads what both send and tells the simulator which region the driver is looking at. For now the
region is a mock: once per second the server sends a random one of the 9 regions.

Files: `sim.py` parses the simulator's messages, `camera.py` parses the camera client's messages,
`server.py` routes each message to one of them and sends the answer. A malformed message is logged
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
 "gaze": {"yaw_deg": -24.3, "pitch_deg": -7.9},
 "eyes": {"state": "OPEN"}, "quality": {"face_detected": true},
 "calibration": {"status": "DONE"}}
```

| Field | Meaning |
|---|---|
| `gaze.yaw_deg` | 0 = straight ahead, negative = left, positive = right |
| `gaze.pitch_deg` | 0 = straight, positive = up, negative = down |
| `eyes.state` | `OPEN`, `ALMOST_CLOSED`, `CLOSED`, `UNKNOWN` |
| `quality.face_detected` | `false` = no face in the camera |
| `calibration.status` | `CALIBRATING`, `DONE`, `DEFAULTS` |

## Output: to the simulator

Once per second, to every client that is not the camera client:

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`timestamp` is server Unix time. `status` is `ok`, `no_face`, `eyes_closed`, `calibrating` or
`no_gaze_client`. `region` is set only when `status` is `ok` (otherwise `null`) and is one of
`front_left`, `front_center`, `front_right`, `side_left`, `side_right`, `mirror_center`,
`mirror_left`, `mirror_right`, `away` (looking at none of the others).

The status comes from the camera client's `driver_state` (no face, eyes closed, calibrating). While
no camera client has reported, the server sends `ok` with a random (mock) region. `no_gaze_client`
is part of the format but not sent yet. The simulator shows "No gaze data" by itself when no
`gaze_update` arrives for 1.5 s.

## Console output

Every simulator message and every `gaze_update` is printed exactly as received/sent, one JSON per
line, so the two directions can be read side by side:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`SENT` is printed only when at least one simulator is connected. Camera messages (~30 Hz) are
printed only with `LOG_LEVEL=DEBUG`. The browser console of the simulator shows the same pairs
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
| `LOG_LEVEL` | `INFO` | `DEBUG` also prints every camera message. |
