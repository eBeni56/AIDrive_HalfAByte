# Simulator server

WebSocket server for all clients on one port: the driving simulator, the driver camera client
(`driver_monitor`), the dashboard and the AI copilot (`copilot/ai-server.py`). It tells the simulator,
30 times per second, which region the driver is looking at; it tells the dashboard what is wrong; it tells
the copilot what to say.

The server handles exactly **three things about the driver**, nothing else:

1. **Where the driver looks** - the gaze region, and for every car / bike / person around the car: did the
   driver look at it in time (`attention.py`)? Looked in time: nothing happens. Did not look: an alert.
2. **Not paying attention** - `DISTRACTED` (looking up or down - at none of the road, mirrors and side windows -
   for 1 s) and `PHONE_USE` (a phone in the hand).
3. **Eyes closed** - `EYES_CLOSED` (longer than a blink; `DANGER` after 1 s).

Any other detection type the camera client might send (or a `drowsiness` / `mouth` block from an older
version) is dropped on arrival.

Files: `sim.py` parses the simulator's messages, `camera.py` parses the camera client's messages (maps its
gaze zone to a region, keeps the three detections), `alerts.py` words the alerts, `attention.py` decides
whether the driver looked at each simulator object in time, `reactions.py` decides when the copilot speaks
and what, `server.py` routes each message to one of them and sends the answers. Tests:
`python -m unittest -v` (`test_gaze.py`, `test_attention.py`, `test_dashboard.py`, `test_reactions.py`).
A malformed message is logged as a warning and ignored; the connection stays open.

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

Messages have `"type": "driver_state"`, sent about 30 times per second by `driver_monitor`. The full
format is in [driver_monitor/docs/detection-schema.md](../driver_monitor/docs/detection-schema.md). Any
field may be missing, null or of the wrong type (= no data, not an error).

For the gaze region (`status` / `region`) these fields are read:

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
| `eyes.state` | `OPEN`, `CLOSED`, `UNKNOWN` |
| `quality.face_detected` | `false` = no face in the camera |
| `calibration.status` | `CALIBRATING`, `DONE`, `DEFAULTS` |

For the driver's condition (`driver`, see below) also `eyes`, `head`, `gaze`, `quality`, `calibration`
and the entries of `detections[]` whose `type` is `EYES_CLOSED`, `DISTRACTED` or `PHONE_USE`. A detection is
an episode with a stable `id` (`"eyes_closed-3"`): while active it is in every message (`STARTED`, then
`ONGOING`), and it is sent `ENDED` exactly once.

## Output 1: to the simulator (and the dashboard)

`GAZE_HZ` (default 30) times per second, to the simulator and the dashboard:

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left",
 "driver": {...}}
```

`driver` is described in [The driver block](#the-driver-block); it is `null` together with `no_gaze_client`.

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

### The driver block

What the camera client says about the driver, from its latest `driver_state`. The camera client decides
*when* something is a problem (its timing rules, e.g. looking up / down counts after 1 s, closed eyes after
0.5 s); the server passes that on and adds a category and a one-line text (`alerts.py`). Example (looking
down at a phone for 1.5 s):

```json
"driver": {
  "severity": "WARNING",
  "eyes_closed": false,
  "not_paying_attention": true,
  "alerts": [
    {"id": "distracted-1", "type": "DISTRACTED", "category": "not_paying_attention", "severity": "WARNING",
     "status": "ONGOING", "duration_s": 1.53, "text": "Not paying attention - looking down for 1.5 s",
     "details": {"zone": "DOWN", "away_s": 1.53}}
  ],
  "recent": [],
  "eyes": {"state": "OPEN", "state_for_s": 1.4, "openness": 0.46, "source": "BOTH"},
  "gaze": {"zone": "DOWN", "zone_for_s": 1.5, "yaw_deg": 6.0, "pitch_deg": -46.0},
  "head": {"yaw_deg": 6.0, "pitch_deg": -24.5, "roll_deg": 0.0},
  "quality": {"face_detected": true, "faces_in_view": 1, "issues": [], "fps": 30.0},
  "calibration": {"status": "DONE", "phase": null, "progress": 1.0, "message": null}
}
```

| Field | Meaning |
|---|---|
| `severity` | Highest severity of the active alerts: `NONE`, `WARNING`, `DANGER`. |
| `eyes_closed` | An `EYES_CLOSED` alert is active (eyes closed longer than a blink). |
| `not_paying_attention` | A `DISTRACTED` or `PHONE_USE` alert is active. |
| `alerts` | Active detections, most severe first (equal severity: the camera's order). Each has the camera's `id`, `type`, `severity`, `status`, `duration_s` and `details`, plus `category` and `text`. |
| `recent` | Detections that ended in the last `RECENT_ALERT_S` (5 s), newest first, same fields plus `ended_ago_s`. |
| `eyes`, `gaze`, `head`, `quality`, `calibration` | The camera's blocks of the same name (selected fields). |

Alerts per detection type (`category`, example `text`):

| `type` | `category` | `text` |
|---|---|---|
| `EYES_CLOSED` | `eyes_closed` | `Eyes closed for 1.2 s - open your eyes!` |
| `DISTRACTED` | `not_paying_attention` | `Not paying attention - looking down for 1.5 s` / `... - looking up for 3.2 s` |
| `PHONE_USE` | `not_paying_attention` | `Not paying attention - phone in hand for 2.0 s` |

## Attention: did the driver look in time?

`attention.py` puts every object of each `simulation_tick` into one of 8 zones around the ego car (pixels
as sent: `x_px` + right, `y_px` + ahead; 1 m = 31.43 px; lane edge at ±55 px). When an object enters a zone,
the driver has that zone's window to look at it: the gaze region (from the camera, `status` `ok` only) must
equal the zone's name for 0.15 s (`LOOK_DWELL_S`) before the window runs out.

| Zone | Where (`x_px`, `y_px`) | Window |
|---|---|---|
| `front_left` / `front_center` / `front_right` | x < -55 / -55..55 / > 55, 71 < y <= 943 | 0.5 s |
| `side_left` / `side_right` (blind spots) | x < -55 / > 55, -157 <= y <= 71 | 1.0 s |
| `mirror_left` / `mirror_right` | x < -55 / > 55, -943 <= y < -157 | 2.0 s |
| `mirror_center` | -55..55, -943 <= y < -71 | 2.0 s |
| none (no timer) | farther than 943 px (30 m), or overlapping the ego car | - |

Looking already when the object enters counts from the entry. A new zone starts a new window (a running one
is dropped). Zones, windows and the accepted gaze regions are the constants at the top of `attention.py`.
The check runs on every tick and 20 times per second in between.

Sent to the dashboard:

```json
{"event": "attention_event", "timestamp": 1763640012.3, "t_s": 2.31, "kind": "noticed",
 "object_id": "car_1", "type": "car", "zone": "mirror_left", "window_s": 2.0, "reaction_s": 0.42}
{"event": "attention_summary", "timestamp": 1763640060.0, "duration_s": 60.0, "noticed_count": 4,
 "missed_count": 3, "avg_reaction_s": 0.56, "missed": [{"t_s": 5.1, "object_id": "truck_1", "type": "truck", "zone": "front_right"}]}
```

`kind`: `appeared` (first seen in this run), `zone_change`, `noticed` (`reaction_s` set), `missed`.
`zone` / `window_s` are `null` outside every zone. `t_s` is seconds since `simulation_started`. The summary
comes at `simulation_ended`. A `missed` object is also told to the copilot (below).

## Output 2: dashboard

The dashboard (`dashboard/`) sends this once after connecting:

```json
{"type": "hello", "role": "dashboard"}
```

From then on it gets every `gaze_update` (as above), the latest `driver_state` `DASHBOARD_HZ` (default 10)
times per second and only when a new one arrived - cleaned: only the three detections (`summary`
recomputed from them), no other blocks than `summary`, `quality`, `calibration`, `eyes`, `head`, `gaze`,
`detections` - the simulator's `simulation_started` / `simulation_ended` as received, the
attention messages (above), and every `chat_message`.

The voice assistant (any client) puts a line into the dashboard's chat with:

```json
{"type": "chat_message", "role": "assistant", "text": "Car to the left of you!"}
```

`role` is `user` (what the driver said) or `assistant`; anything else is logged as a warning and dropped.
The server keeps no history. A chat client gets no `gaze_update` once it has sent its first `chat_message`.

## Output 3: copilot (what the AI says)

The copilot (`copilot/ai-server.py`) sends this once after connecting:

```json
{"type": "hello", "role": "copilot"}
```

and from then on gets a `copilot_alert` whenever it should speak (`reactions.py` decides, 10 times per
second). The copilot speaks `say` at once; a higher `priority` interrupts a lower one.

```json
{"event": "copilot_alert", "timestamp": 1763640012.3, "kind": "missed_object", "priority": 2,
 "say": "Car to the left of you!", "repeat": false, "detection": null,
 "object": {"object_id": "car_3", "type": "car", "zone": "side_left", "side": "left"}}
```

| `kind` | `priority` | When | `say` |
|---|---|---|---|
| `eyes_closed` | 3 | `EYES_CLOSED` has lasted `EYES_CLOSED_VOICE_S` (1 s; shorter = long blink, dashboard only); again every `EYES_CLOSED_REPEAT_S` (3 s) | `Open your eyes! Pay attention to the road!` |
| `missed_object` | 2 | attention `missed`: the driver did not look at the object in time. Once per object and zone. | `Car to the left of you!`, `Person in front of you!`, `Bike coming up on your right!` ... |
| `not_paying_attention` | 1 | `PHONE_USE` / `DISTRACTED` starts; again every `ATTENTION_REPEAT_S` (5 s) while it lasts; quiet while the eyes-closed line repeats | `Put the phone down! Pay attention to the road!` / `Stop looking down! Pay attention to the road!` (looking up: `Pay attention to the road!`) |

`detection` (`id`, `type`, `severity`, `duration_s`) is set for the camera kinds, `object` (`object_id`,
`type`, `zone`, `side`: `left` / `right` / `ahead` / `behind`) for `missed_object`. The copilot gets no
`gaze_update`. The copilot puts what it said into the dashboard's chat with a `chat_message`.

## Console output

Every simulator message and `chat_message`, and every `gaze_update` whose status, region or alerts (ids and
severities) changed, is printed exactly as received/sent, one JSON per line, so the two directions can be
read side by side. Every alert that starts, changes severity or ends, and every line the copilot is told to
say, is also printed as one short line:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left", "driver": {...}}
11:06:41 INFO    ALERT STARTED distracted-1 DISTRACTED WARNING: Not paying attention - looking down for 1.5 s
11:06:41 INFO    SAY [not_paying_attention] Stop looking down! Pay attention to the road!
11:06:42 INFO    ALERT CHANGED distracted-1 DISTRACTED WARNING -> DANGER: Not paying attention - looking down for 2.0 s
11:06:43 INFO    ALERT ENDED   distracted-1 DISTRACTED after 2.9s
```

`SENT` is printed only when at least one simulator or dashboard is connected. Unchanged `gaze_update`s
(30 Hz) and camera messages (~30 Hz) are printed only with `LOG_LEVEL=DEBUG`. The browser console of the
simulator shows the same pairs (its own messages, plus `RECEIVED` for each `gaze_update`).

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
| `GAZE_HZ` | `30` | `gaze_update` messages per second. |
| `DASHBOARD_HZ` | `10` | `driver_state` messages per second to the dashboard (the camera sends ~30). |
| `CAMERA_TIMEOUT_S` | `1.0` | No `driver_state` for this long: `no_gaze_client`. |
| `FRONT_SPLIT_DEG` | `7` | Looking at the road, \|yaw\| beyond this: `front_left` / `front_right`. |
| `RECENT_ALERT_S` | `5` | An ended alert stays in `driver.recent` this long. |
| `EYES_CLOSED_VOICE_S` | `1.0` | The copilot says "Open your eyes!" once the eyes have been closed this long. |
| `EYES_CLOSED_REPEAT_S` | `3.0` | ... and again every this many seconds while they stay closed. |
| `ATTENTION_REPEAT_S` | `5.0` | The "pay attention" line again every this many seconds while it lasts. |

In Docker these come from the root `.env`.
