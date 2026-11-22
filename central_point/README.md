# central_point

The WebSocket hub that connects everything, on one port (default 8765): the **simulator**, the **driver
monitor** (camera client), the **dashboard** and **safety_ai** (the voice and the LLM coach). It

* tells the simulator and the dashboard, 10 times per second, **where the driver looks**: the driver monitor's gaze
  zone translated to one of the simulator's 8 regions (`gaze_update`);
* decides for every road user that needs a look **whether the driver looked at it in time** (`attention_event`,
  and an `attention_summary` at the end of a run);
* decides the **warnings** (deterministic, fixed lines): a road user that was not looked at in time, eyes closed,
  phone in the hand, looking up / down. safety_ai speaks them, the dashboard's chat shows them;
* keeps a **log of the run** and asks safety_ai's LLM for a **driving tip** when a pattern shows up, and for a
  **debrief** when the run ends (`coach_request`); the answers go to the dashboard's chat;
* forwards to the dashboard the latest `driver_state`, the run's start and end and every `chat_message`.

Every message is one JSON object. Who sent it is read from the message itself; a malformed one is logged as a
warning and ignored (the connection stays open).

| File | What |
|---|---|
| `server.py` | routes each message, the send loops (gaze, dashboard, 20 Hz tick), the gaze decision |
| `sim.py` | parses the simulator's messages, keeps the current objects |
| `camera.py` | reads the driver monitor's messages, maps the gaze zone to a region |
| `attention.py` | does a road user need a look, and did the driver look in time? |
| `alerts.py` | the warnings and their spoken lines |
| `runlog.py` | what happened in this run (and the last 3 runs), written as text for the LLM |
| `coach.py` | when to ask the LLM for a tip or a debrief |
| `test_*.py` | the tests: `python -m unittest -v` (not yet updated for the files above) |

`attention.py`, `alerts.py`, `runlog.py` and `coach.py` have no networking and no clock (every call gets `now`).

## What it receives

**From the simulator** (messages with an `event`; every one has a numeric `timestamp`):

```json
{"event": "calibration_started", "timestamp": 1763639900.0}
{"event": "simulation_started", "timestamp": 1763640000.0}
{"event": "simulation_tick", "timestamp": 1763640000.1,
 "objects": [{"object_id": "car1", "type": "car", "x_px": 40, "y_px": 120}]}
{"event": "simulation_ended", "timestamp": 1763640060.0}
{"event": "simulation_reset", "timestamp": 1763640075.0}
```

`calibration_started` comes when the simulator page loads (and with its Calibrate button), not at a simulation
start: the driver looks at the screen centre, the server asks the driver monitor for the new straight-ahead
look (`recenter_gaze`, below). `simulation_reset` comes when the simulator goes back to setup after a run (a new
simulation is prepared): it is passed to safety_ai, which stops talking at once. Neither starts or ends a run.
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
| `detections[]` (`EYES_CLOSED`, `PHONE_USE`, `DISTRACTED`: `id`, `status`, `duration_s`, `details.zone`) | the driver warnings and the run log |

**From the dashboard**, once after connecting: `{"type": "hello", "role": "dashboard"}`.

**From safety_ai**: once after connecting `{"type": "hello", "role": "safety_ai"}`; then a `coach_result` for every
`coach_request` it answered:

```json
{"type": "coach_result", "request_id": "tip-1", "kind": "tip", "speak": true, "focus": "right side",
 "say": "You missed two cars on the right side. Check your right mirror and blind spot more often."}
{"type": "coach_result", "request_id": "debrief-2", "kind": "debrief", "verdict": "needs work", "say": "...",
 "mistakes": [{"problem": "...", "evidence": "...", "fix": "..."}], "went_well": ["..."], "next_focus": "..."}
```

`say` goes to the dashboard's chat; a tip's `say` is also remembered for the next reports ("tips already given").
A debrief adds a second chat line "Drive: <verdict>. Next drive: <next_focus>"; its mistakes are logged.

**From any client**: `{"type": "chat_message", "role": "assistant", "text": "..."}` puts a line into the dashboard's
chat. `role` is `user` or `assistant`; anything else is dropped with a warning.

## What it sends

**`gaze_update`**, `GAZE_HZ` (10) times per second, to every client except the driver monitor, safety_ai and chat
clients, **only during a run** (from `simulation_started` to `simulation_ended`). Before GO the straight-ahead
look is not set yet (the countdown sets it), and after the end the drive is over, so nothing is sent then:

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

`timestamp` is the server's Unix time. `region` is set only when `status` is `ok`. Decided from the latest
`driver_state`, the first matching row wins. A blink is not "eyes closed": during it (up to 0.5 s, the driver
monitor's `blink_hold_s`) the driver monitor keeps the last gaze zone, so the region stays and a look is not
broken:

| The driver monitor says | `status` | `region` |
|---|---|---|
| nothing yet, or nothing for `CAMERA_TIMEOUT_S` (1 s) | `no_gaze_client` | `null` |
| `quality.face_detected: false` | `no_face` | `null` |
| `eyes.state: CLOSED` and `gaze.zone` `UNKNOWN` or missing (closed longer than a blink) | `eyes_closed` | `null` |
| `calibration.status: CALIBRATING` | `calibrating` | `null` |
| `ROAD`, yaw < −7° / within ±7° / > +7° (`FRONT_SPLIT_DEG`) | `ok` | `front_left` / `front_center` / `front_right` |
| `LEFT_MIRROR` / `REAR_MIRROR` / `RIGHT_MIRROR` | `ok` | `mirror_left` / `mirror_center` / `mirror_right` |
| `LEFT_SIDE` / `RIGHT_SIDE` | `ok` | `side_left` / `side_right` |
| `UP`, `DOWN`, `UNKNOWN` or missing | `ok` | `away` |

**To the dashboard only:**
* the latest `driver_state` exactly as received, `DASHBOARD_HZ` (10) times per second, only when a new one came,
  only during a run;
* a dashboard that connects during a run first gets that run's `simulation_started`;
* `simulation_started` / `simulation_ended` as received (the ticks are not forwarded);
* the attention messages (below) and every valid `chat_message`;
* every warning (not its repeats), tip and debrief as a chat line: `{"type": "chat_message", "role": "assistant", "text": ...}`.

**To the driver monitor only:** at `calibration_started`, `{"type": "recenter_gaze", "timestamp": ...}`: it
measures the straight-ahead look (the 0,0 of every gaze angle) again, reported as `calibration.status:
CALIBRATING` meanwhile (no `gaze_update` is sent then: that is only during a run).

**To safety_ai only:** every `warning` (also the repeats) and every `coach_request` (below), only during a run
(from `simulation_started` to `simulation_ended`): nothing before GO, and nothing that could interrupt the
end-of-drive analysis. Also `simulation_started` / `simulation_ended` as received: at the end safety_ai speaks no
more warnings, finishes the tips it already has, then speaks only the debrief (requested right after). And
`simulation_reset`: it stops talking at once (also in the middle of the debrief). A tip
asked for during the run whose `coach_result` comes back after the end is still shown in the chat.

**Outside a run** (before GO, after `simulation_ended`) the server sends nothing new but the end-of-drive debrief,
its chat lines and a tip still being finished; the driver monitor keeps streaming `driver_state` to the server
(so the camera data is fresh at GO), but it goes nowhere.

## Attention: did the driver look in time?

Every object of each tick is in one of 8 zones around the ego car, or in none. When an object enters a zone **and
needs a look there**, the driver has that zone's window to look there: the gaze region (status `ok` only) must equal
the zone's name for 0.15 s (`LOOK_DWELL_S`) before the window runs out. Looking already when it enters counts from
the entry. A new zone starts a new window (a running one is dropped). It is checked on every tick and 20 times per
second.

**Needs a look**: always in a blind spot (`side_*`) and in our lane ahead (`front_center`); in every other zone only
while it is getting closer, to the car or sideways to our lane (by at least 0.1 m over the last 0.4 s of ticks).
So an overtaking car coming up behind us gets a window, the same car ahead of us and pulling away does not; a
pedestrian walking toward our lane does, one walking away after crossing does not. An object seen for the first time
in such a zone is decided (and its `appeared` sent) after 0.4 s of positions. The decision is made once per zone.

| Zone | Where (`x_px`, `y_px`) | Window |
|---|---|---|
| `front_left` / `front_center` / `front_right` | x < −55 / −55 … 55 / > 55, 71 < y ≤ 943 | 0.5 s |
| `side_left` / `side_right` (the blind spots) | x < −55 / > 55, −157 ≤ y ≤ 71 | 1.0 s |
| `mirror_left` / `mirror_right` | x < −55 / > 55, −943 ≤ y < −157 | 2.0 s |
| `mirror_center` | −55 … 55, −943 ≤ y < −71 | 2.0 s |
| none (no timer) | farther than 943 px (30 m), or overlapping the ego car | – |

```json
{"event": "attention_event", "timestamp": 1763640012.3, "t_s": 2.31, "kind": "noticed",
 "object_id": "car_1", "type": "car", "zone": "mirror_left", "window_s": 2.0, "reaction_s": 0.42, "distance_m": 15.9}
{"event": "attention_summary", "timestamp": 1763640060.0, "duration_s": 60.0, "noticed_count": 4,
 "missed_count": 3, "avg_reaction_s": 0.56,
 "missed": [{"t_s": 5.1, "object_id": "truck_1", "type": "truck", "zone": "front_right"}]}
```

`kind`: `appeared` (first seen in this run), `zone_change`, `noticed` (with `reaction_s`), `missed`. `zone` is `null`
outside every zone; `window_s` is `null` when there is no window (no zone, or no look needed); `distance_m` is from
the ego car's centre; `t_s` is seconds since `simulation_started`. The summary comes at `simulation_ended` (also
when the run is aborted). Zones, windows, the accepted gaze regions and the "needs a look" settings are the
constants at the top of `attention.py`.

## Warnings (`alerts.py`)

| `kind` | When | Spoken line (`say`) | `priority` |
|---|---|---|---|
| `eyes_closed` | `EYES_CLOSED` has lasted `EYES_CLOSED_VOICE_S` (1 s; shorter = long blink); again every `EYES_CLOSED_REPEAT_S` (3 s) | `Open your eyes! Watch the road!` | 3 |
| `road_user` | an attention `missed`: once per road user and zone | `Car coming up on your left!`, `Truck in your right blind spot!`, `Pedestrian ahead of you!` ... | 2 |
| `phone` | `PHONE_USE` starts; again every `ATTENTION_REPEAT_S` (5 s) | `Put the phone down! Watch the road!` | 1 |
| `looking_away` | `DISTRACTED` starts; again every 5 s | `Stop looking down! Watch the road!` / `Eyes back on the road!` | 1 |

Phone and looking away stay quiet while the eyes-closed line is being repeated. No driver warnings when the driver
monitor has sent nothing for 1 s, and none outside a run (before GO or after `simulation_ended`).

```json
{"type": "warning", "timestamp": 1763640012.3, "t_s": 3.58, "kind": "road_user", "category": "right", "priority": 2,
 "repeat": false, "say": "Car in your right blind spot!",
 "object": {"object_id": "car_3", "type": "car", "zone": "side_right", "distance_m": 3.5}, "detection": null}
```

`category` (`left`, `right`, `ahead`, `behind`, `eyes_closed`, `phone`, `looking_away`) is what the coach counts;
`detection` (`id`, `type`, `duration_s`) is set for the driver warnings.

## The LLM coach (`runlog.py`, `coach.py`)

central_point decides **when** to ask and writes **what happened** (facts only); safety_ai's LLM finds the pattern
and writes the tip. A tip is asked for when all of these hold:

* **a pattern**: since the last tip, 2 warnings of the same `category` or 3 warnings in total; or praise: 6 road
  users noticed in time in a row and no warning for 45 s;
* **40 s** since the last tip;
* **a calm moment**: no driver problem active, no road user's look window open, the last warning 3 s ago (so it has
  been spoken). No calm moment within 20 s: the tip is dropped (the debrief still covers it).

A **debrief** is asked for at `simulation_ended` when the run lasted at least 20 s and something happened.

```json
{"type": "coach_request", "kind": "tip", "id": "tip-1", "t_s": 6.6,
 "report": "Drive time: 0:07.\nSo far in this drive:\n- 0:02 missed: a car coming up in the right mirror (not looked at within 2.0 s)\n..."}
```

A tip's report: the events since the last tip (the newest 12: road users noticed / missed, eyes closed, phone,
looked up / down with their lengths), one line of totals for the drive so far (road users, reaction, gaze shares,
counts) and the tips already given. A debrief's report: the full statistics of the run (road users noticed /
missed by side and by type; reaction times, also per half of the drive; every miss with its distance and where
the driver looked during its look window; eyes closed / phone / looked down with count, total and longest and
each episode; gaze time shares per mirror; mirror checks per minute and the longest stretch without one; the
warnings spoken; the tips given) and one line per previous run (the last 3, kept in memory) for progress.

## Console

Every simulator and chat message is printed exactly as received, and every `gaze_update` whose status or region
changed as sent, one JSON per line:

```
11:06:40 INFO    RECEIVED {"event":"simulation_tick","timestamp":1763640012.1,"objects":[]}
11:06:40 INFO    SENT {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

The attention decisions are logged too (`car_1 car in mirror_left noticed in 0.42 s`, a summary at the end), every
warning (`WARNING p2 road_user: Car in your right blind spot!`), every coach request with its full report and every
tip / debrief that came back. With `LOG_LEVEL=DEBUG` also every unchanged `gaze_update`, every camera message and
the repeated warnings.

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
| `EYES_CLOSED_VOICE_S` | `1.0` | eyes closed this long: the first eyes-closed warning |
| `EYES_CLOSED_REPEAT_S` | `3.0` | ... repeated this often while they stay closed |
| `ATTENTION_REPEAT_S` | `5.0` | phone / looking away warning repeated this often while it lasts |

In Docker these come from the root `.env`.
