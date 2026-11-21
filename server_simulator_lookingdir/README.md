# Server

The WebSocket server that connects everything, on one port (default 8765): the **simulator**, the **driver
monitor** (camera client), the **dashboard** and any **chat** client (the voice assistant). It

* tells the simulator and the dashboard, 10 times per second, **where the driver looks**: the driver monitor's gaze
  zone translated to one of the simulator's 8 regions (`gaze_update`);
* decides for every simulator object **whether the driver looked at it in time** (`attention_event`, and an
  `attention_summary` at the end of a run);
* forwards to the dashboard the latest `driver_state`, the run's start and end and every `chat_message`.

Every message is one JSON object. Who sent it is read from the message itself; a malformed one is logged as a
warning and ignored (the connection stays open).

| File | What |
|---|---|
| `server.py` | routes each message, the three send loops (gaze, dashboard, attention), the gaze decision |
| `sim.py` | parses the simulator's messages, keeps the current objects |
| `camera.py` | reads the driver monitor's messages, maps the gaze zone to a region |
| `attention.py` | did the driver look at each object in time? (no networking, no clock: easy to test) |
| `test_*.py` | the tests: `python -m unittest -v` |

## What it receives

**From the simulator** (messages with an `event`; every one has a numeric `timestamp`):

```json
{"event": "simulation_started", "timestamp": 1763640000.0}
{"event": "simulation_tick", "timestamp": 1763640000.1,
 "objects": [{"object_id": "car1", "type": "car", "x_px": 40, "y_px": 120}]}
{"event": "simulation_ended", "timestamp": 1763640060.0}
```

`simulation_tick` comes 10 times per second with every object on the map. `type` is `car`, `truck`, `bicycle`,
`motorcycle` or `pedestrian`; `x_px` is + right of the ego car, `y_px` + ahead (1 m = 31.43 px).

**From the driver monitor** (`"type": "driver_state"`, ~30 per second; full reference in
[driver_monitor/docs/detection-schema.md](../driver_monitor/docs/detection-schema.md)). Only these fields are
read; any of them may be missing or null, which just means "no data":

| Field | Used for |
|---|---|
| `gaze.zone` | the region: `ROAD`, `LEFT_MIRROR`, `REAR_MIRROR`, `RIGHT_MIRROR`, `LEFT_SIDE`, `RIGHT_SIDE`, `UP`, `DOWN`, `UNKNOWN` |
| `gaze.yaw_deg` | splits `ROAD` into front left / centre / right (− left, + right) |
| `eyes.state` | `CLOSED` → status `eyes_closed` |
| `quality.face_detected` | `false` → status `no_face` |
| `calibration.status` | `CALIBRATING` → status `calibrating` |

**From the dashboard**, once after connecting: `{"type": "hello", "role": "dashboard"}`.

**From a chat client** (the voice assistant): `{"type": "chat_message", "role": "assistant", "text": "..."}`.
`role` is `user` (what the driver said) or `assistant`; anything else is dropped with a warning.

## What it sends

**`gaze_update`**, `GAZE_HZ` (10) times per second, to every client except the driver monitor and chat clients
(the simulator sends nothing before GO, so it can't be recognised earlier; it gets the message anyway):

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`timestamp` is the server's Unix time. `region` is set only when `status` is `ok`. Decided from the latest
`driver_state`, the first matching row wins:

| The driver monitor says | `status` | `region` |
|---|---|---|
| nothing yet, or nothing for `CAMERA_TIMEOUT_S` (1 s) | `no_gaze_client` | `null` |
| `quality.face_detected: false` | `no_face` | `null` |
| `eyes.state: CLOSED` | `eyes_closed` | `null` |
| `calibration.status: CALIBRATING` | `calibrating` | `null` |
| `ROAD`, yaw < −7° / within ±7° / > +7° (`FRONT_SPLIT_DEG`) | `ok` | `front_left` / `front_center` / `front_right` |
| `LEFT_MIRROR` / `REAR_MIRROR` / `RIGHT_MIRROR` | `ok` | `mirror_left` / `mirror_center` / `mirror_right` |
| `LEFT_SIDE` / `RIGHT_SIDE` | `ok` | `side_left` / `side_right` |
| `UP`, `DOWN`, `UNKNOWN` or missing | `ok` | `away` |

**To the dashboard only:**
* the latest `driver_state` exactly as received, `DASHBOARD_HZ` (10) times per second, only when a new one came;
* `simulation_started` / `simulation_ended` as received (the ticks are not forwarded);
* the attention messages (below) and every valid `chat_message`.

## Attention: did the driver look in time?

Every object of each tick is in one of 8 zones around the ego car, or in none. When an object enters a zone, the
driver has that zone's window to look there: the gaze region (status `ok` only) must equal the zone's name for
0.15 s (`LOOK_DWELL_S`) before the window runs out. Looking already when it enters counts from the entry. A new
zone starts a new window (a running one is dropped). It is checked on every tick and 20 times per second.

| Zone | Where (`x_px`, `y_px`) | Window |
|---|---|---|
| `front_left` / `front_center` / `front_right` | x < −55 / −55 … 55 / > 55, 71 < y ≤ 943 | 0.5 s |
| `side_left` / `side_right` (the blind spots) | x < −55 / > 55, −157 ≤ y ≤ 71 | 1.0 s |
| `mirror_left` / `mirror_right` | x < −55 / > 55, −943 ≤ y < −157 | 2.0 s |
| `mirror_center` | −55 … 55, −943 ≤ y < −71 | 2.0 s |
| none (no timer) | farther than 943 px (30 m), or overlapping the ego car | – |

```json
{"event": "attention_event", "timestamp": 1763640012.3, "t_s": 2.31, "kind": "noticed",
 "object_id": "car_1", "type": "car", "zone": "mirror_left", "window_s": 2.0, "reaction_s": 0.42}
{"event": "attention_summary", "timestamp": 1763640060.0, "duration_s": 60.0, "noticed_count": 4,
 "missed_count": 3, "avg_reaction_s": 0.56,
 "missed": [{"t_s": 5.1, "object_id": "truck_1", "type": "truck", "zone": "front_right"}]}
```

`kind`: `appeared` (first seen in this run), `zone_change`, `noticed` (with `reaction_s`), `missed`. `zone` and
`window_s` are `null` outside every zone; `t_s` is seconds since `simulation_started`. The summary comes at
`simulation_ended` (also when the run is aborted). Zones, windows and the accepted gaze regions are the constants
at the top of `attention.py`.

## Console

Every simulator and chat message is printed exactly as received, and every `gaze_update` whose status or region
changed as sent, one JSON per line:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

The attention decisions are logged too (`car_1 car in mirror_left noticed in 0.42 s`, a summary at the end).
With `LOG_LEVEL=DEBUG` also every unchanged `gaze_update` and every camera message.

## Run

With Docker it is the `server` service of the root `compose.yml` (`start.bat` starts it). By hand:

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python server.py                       # Windows: .venv\Scripts\python
```

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `localhost` (Docker: `0.0.0.0`) | interface to listen on |
| `PORT` | `8765` | port (in Docker the host port is `SERVER_PORT` in the root `.env`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` also prints every `gaze_update` and every camera message |
| `GAZE_HZ` | `10` | `gaze_update` messages per second |
| `DASHBOARD_HZ` | `10` | `driver_state` messages per second to the dashboard (the camera sends ~30) |
| `CAMERA_TIMEOUT_S` | `1.0` | no `driver_state` for this long: `no_gaze_client` |
| `FRONT_SPLIT_DEG` | `7` | looking at the road, \|yaw\| beyond this: `front_left` / `front_right` |

In Docker these come from the root `.env`.
