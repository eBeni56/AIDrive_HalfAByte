# Simulator server

WebSocket server with two clients on one port: the driving simulator and the driver camera client.
It reads what both send and tells the simulator, 30 times per second, which region the driver is looking at
(the camera client's gaze zone, translated to the simulator's regions) and what condition the driver is in:
tired, not paying attention, not visible, with the camera client's alerts, drowsiness level and history.

Files: `sim.py` parses the simulator's messages, `camera.py` parses the camera client's messages
(maps its gaze zone to a region and keeps its detections), `alerts.py` words the alerts, `server.py`
routes each message to one of them and sends the answer. Tests: `python -m unittest -v`. A malformed message is logged
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
| `eyes.state` | `OPEN`, `ALMOST_CLOSED`, `CLOSED`, `UNKNOWN` |
| `quality.face_detected` | `false` = no face in the camera |
| `calibration.status` | `CALIBRATING`, `DONE`, `DEFAULTS` |

For the driver's condition (`driver`, see below) also `summary`, `attention`, `drowsiness` (level,
reasons, indicators, 1 / 5 / 15 min history), `eyes`, `head`, `mouth`, `quality`, `calibration` and every
entry of `detections[]`. A detection is an episode with a stable `id` (`"microsleep-3"`): while active it
is in every message (`STARTED`, then `ONGOING`), and it is sent `ENDED` exactly once. The server reads
every camera message, so it never misses an `ENDED` one, even a one-shot `HEAD_NOD` (only ever sent as `ENDED`).

## Output: to the simulator

`GAZE_HZ` (default 30) times per second, to every client that is not the camera client:

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
*when* something is a problem (its timing rules, e.g. looking down counts after 1.2 s, eyes almost closed
after 2 s); the server passes that on and adds a category and a one-line text (`alerts.py`). Example
(looking down at a phone for 1.5 s; the real message has all fields of every block):

```json
"driver": {
  "severity": "WARNING",
  "tired": false,
  "distracted": true,
  "alerts": [
    {"id": "distracted-1", "type": "DISTRACTED", "category": "distracted", "severity": "WARNING",
     "status": "ONGOING", "duration_s": 1.53, "text": "Not paying attention - looking down for 1.5 s",
     "details": {"reason": "LOOKING_DOWN", "zone": "DOWN", "glance_s": 1.53, "...": "..."}}
  ],
  "recent": [],
  "attention": {"state": "DISTRACTED", "zone": "DOWN", "eyes_off_road_s": 1.53, "glance_down_s": 1.53,
                "off_road_last_30s_s": 1.5, "weighted_off_road_30s_s": 1.5, "longest_glance_60s_s": 1.53,
                "glances_60s": 1},
  "drowsiness": {"level": "ALERT", "level_index": 0, "score": 0.0, "peak_level": "ALERT", "warming_up": false,
                 "fatigue_load": 0.0, "reasons": [],
                 "indicators": {"perclos": 0.025, "blink_rate_per_min": 15.0, "long_blinks_60s": 0, "...": "..."},
                 "history": {"last_60s":   {"long_eye_closures": 0, "microsleeps": 0, "closed_eyes_s": 0.0,
                                            "yawns": 0, "head_nods": 0, "head_falls": 0, "narrowed_eyes": 0,
                                            "narrowed_eyes_s": 0.0},
                             "last_5min":  {"...": "..."}, "last_15min": {"...": "..."}}},
  "eyes": {"state": "ALMOST_CLOSED", "state_for_s": 1.4, "openness": 0.46, "openness_ratio": null,
           "normal_learned": true, "trend": "STABLE", "lid_lowered_by_gaze": true},
  "gaze": {"zone": "DOWN", "zone_for_s": 1.5, "direction": "DOWN", "direction_for_s": 1.5,
           "yaw_deg": 6.0, "pitch_deg": -46.0, "on_road": false},
  "head": {"yaw_deg": 6.0, "pitch_deg": -24.5, "roll_deg": 0.0},
  "mouth": {"state": "CLOSED", "state_for_s": 63.9},
  "quality": {"face_detected": true, "face_lost_for_s": 0.0, "eye_metrics_reliable": true, "issues": [],
              "fps": 30.0},
  "calibration": {"status": "DONE", "phase": null, "progress": 1.0, "message": null}
}
```

| Field | Meaning |
|---|---|
| `severity` | Highest severity of the active alerts: `NONE`, `INFO`, `WARNING`, `DANGER`, `CRITICAL`. |
| `tired` | A tiredness alert is active, or `drowsiness.level` is above `ALERT`. |
| `distracted` | Not paying attention: a `DISTRACTED` alert is active (eyes open, but away from the road too long). |
| `alerts` | Active detections, most severe first (equal severity: the camera's order). Each has the camera's `id`, `type`, `severity`, `status`, `duration_s` and `details`, plus `category` and `text`. |
| `recent` | Detections that ended in the last `RECENT_ALERT_S` (5 s), newest first, same fields plus `ended_ago_s`. Catches short one-shot events (head nod, end of a yawn) that the simulator could otherwise miss. |
| `attention`, `drowsiness`, `eyes`, `gaze`, `head`, `mouth`, `quality`, `calibration` | The camera's blocks of the same name (selected fields; `drowsiness.reasons` = the camera's `contributing`, `drowsiness.history` keyed by window). |

Alerts per detection type (`category`, example `text`):

| `type` | `category` | `text` |
|---|---|---|
| `MICROSLEEP` | `tired` | `MICROSLEEP - WAKE UP! Eyes closed 1.8 s` |
| `EYES_CLOSED` | `tired` | `Eyes closed for 0.6 s` |
| `EYES_ALMOST_CLOSED` | `tired` | `Eyes almost closed - you look tired` |
| `EYES_NARROWED` | `tired` | `Eyes narrower than normal - getting tired` |
| `TIRED_EYES` | `tired` | `Tired eyes: heavy eyelids, slow blinks` |
| `YAWN` | `tired` | `Yawning (3 yawns in 5 min)` |
| `HEAD_NOD` | `tired` | `Head nodding off - wake up!` |
| `HEAD_FALLING` | `tired` | `Head falling forward - WAKE UP!` |
| `DROWSY` | `tired` | `Slightly drowsy` / `Drowsy - consider a break` / `Very drowsy - take a break soon` / `Severely drowsy - stop and rest` |
| `DISTRACTED` | `distracted` | `Not paying attention - looking down for 1.5 s` / `... - looking up for 3.2 s` / `... - eyes off the road 10 s in the last 30 s` |
| `FACE_NOT_VISIBLE` | `not_visible` | `Driver not visible - head dropped` |
| anything new | `other` | the type in words (`New thing`) |

## Console output

Every simulator message, and every `gaze_update` whose status, region or alerts (ids and severities)
changed, is printed exactly as received/sent, one JSON per line, so the two directions can be read side by
side. Every alert that starts, changes severity or ends is also printed as one short line:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left", "driver": {...}}
11:06:41 INFO    ALERT STARTED distracted-1 DISTRACTED WARNING: Not paying attention - looking down for 1.5 s
11:06:42 INFO    ALERT CHANGED distracted-1 DISTRACTED WARNING -> DANGER: Not paying attention - looking down for 2.0 s
11:06:43 INFO    ALERT ENDED   distracted-1 DISTRACTED after 2.9s
```

`SENT` is printed only when at least one simulator is connected. Unchanged `gaze_update`s (30 Hz)
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
| `GAZE_HZ` | `30` | `gaze_update` messages per second. |
| `CAMERA_TIMEOUT_S` | `1.0` | No `driver_state` for this long: `no_gaze_client`. |
| `FRONT_SPLIT_DEG` | `7` | Looking at the road, \|yaw\| beyond this: `front_left` / `front_right`. |
| `RECENT_ALERT_S` | `5` | An ended alert stays in `driver.recent` this long. |

In Docker these come from the root `.env`.
