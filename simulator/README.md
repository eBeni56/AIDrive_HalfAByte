# Driving Scene Simulator

A small browser tool for generating object positions around the car and for running demos.
It is a helper, not the core of the project: it streams positions over a WebSocket to the backend.
Plain HTML/CSS/JS with no build step, so `index.html` also works when opened directly.

## Quick start

Windows: double-click `run.bat`. macOS/Linux: `./run.sh` (or `sh run.sh`). Python 3 is required.
The script creates `.venv`, installs `requirements.txt`, opens `index.html` and starts `test_server.py`.

In the browser, drag icons from the toolbar onto the road (drag again to move, right-click or
double-click to remove). Press **Start simulation**. **Esc** aborts.
The **Speed** button (setup only) cycles 1x → 2x → 0.25x → 0.5x and multiplies only the objects'
speeds; the 10 Hz message rate and all other timing stay the same.

Manual alternative:

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv\Scripts\pip ...
.venv/bin/python test_server.py                # then open index.html
```

## Files

- `index.html`: page markup; loads the CSS and the scripts in order
- `css/style.css`: all styles
- `js/config.js`: tunable constants (WS URL, scale, object types, speeds, timing)
- `js/coords.js`: pixel/meter conversion, lane rule, `mapPx` (the position that is sent)
- `js/gaze.js`: receives `gaze_update`; status badge, region geometry, highlight drawing
- `js/ws.js`: WebSocket client; prints every outgoing message to the browser console
- `js/setup.js`: setup screen: icons, placing/dragging objects (sends nothing)
- `js/render.js`: first-person view, mirrors, minimap, visibility detection
- `js/sim.js`: movement, simulation flow, all outgoing messages; runs on page load
- `test_server.py`: test server that prints every message (port from env var `PORT`, default 8765)
- `requirements.txt`, `run.bat`, `run.sh`: setup and start

## What it sends

The simulator is a WebSocket **client**. It sends JSON text frames to `ws://localhost:8765`. The
only reply it reads is `gaze_update` (see "What it receives"); everything else is ignored. Messages sent while disconnected are lost, and it reconnects every 2 s.
If you change `PORT`, change `WS_URL` in `js/config.js` to match.

Every message is also printed to the browser console (F12 → Console), one JSON per line, exactly
as sent. A message that could not be sent (socket down) appears as a `NOT SENT` warning, and the
connection state is logged when it changes. Every message received from the server is printed
there too, prefixed with `RECEIVED ` and exactly as received, so sent and received lines appear
in order next to each other:

```
{"event":"simulation_tick","timestamp":1763640012.1,"objects":[...]}
RECEIVED {"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
{"event":"simulation_tick","timestamp":1763640012.2,"objects":[...]}
```

The connection itself is not shown on the screen (the gaze badge only says whether gaze data arrives).

Nothing is sent during setup or the 3 s countdown; only the connection is open. There are three
messages: `simulation_started`, then `simulation_tick` 10 times per second, then
`simulation_ended`. `timestamp` is Unix seconds (float) in every one.

### `simulation_started`

Sent once, at GO (after the countdown).

| Field | Why the backend needs it |
|---|---|
| `event` | Message type, used for routing. Without it the message cannot be told apart from the others. |
| `timestamp` | Start of the run. The backend resets its timers and statistics here; without it, state from the previous run leaks into the new one. |

### `simulation_tick`

Sent 10 times per second while driving, starting at GO. One message holds the position of **every**
object placed on the map, in every tick, whether or not the driver can see it right now (in the
windshield, in a mirror, in the blind spot beside the car, or far away). The list is the same set of
objects for the whole run; only their positions change.

| Field | Why the backend needs it |
|---|---|
| `event` | Message type, used for routing. |
| `timestamp` | When this snapshot was taken. The backend pairs it with the gaze samples. |
| `objects` | Every object on the map. Without it the backend does not know what is around the car. |
| `objects[].object_id` | Tells the backend which object this is, so the same object in the next tick is not counted as a new one. |
| `objects[].type` | `car` / `truck` / `bicycle` / `motorcycle` / `pedestrian`. The alert window depends on it (for example, a pedestrian gets a shorter window), and so does the alert text. |
| `objects[].x_px` | Where the object is, sideways: the object's center on the top-down map, in pixels from the ego car's center, positive to the **right**. The backend uses it to decide where the driver has to look. |
| `objects[].y_px` | Where the object is, lengthwise: same map, positive **ahead** of the ego car, negative behind it. |

The position is the object's center on the top-down (setup) map, relative to the center of the
ego car, which never moves. The scale is fixed: one lane is 110 px (3.5 m), so 1 m ≈ 31.4 px; it
does not depend on the window size. Useful values: the road spans `x_px` -165 to +165 (lanes are
split at ±55), and the ego car spans `y_px` -71 to +71.

### `simulation_ended`

Sent when the run is over: 3 s after the last object has finished, or on Esc/Abort while driving.

| Field | Why the backend needs it |
|---|---|
| `event` | Message type, used for routing. |
| `timestamp` | End of the run. The backend stops its pending alert timers (so no late alert arrives after the run) and finalises the statistics. |

### Example

```json
{"event": "simulation_started", "timestamp": 1763640010.0}
{"event": "simulation_tick", "timestamp": 1763640010.0, "objects": [
  {"object_id": "obj_1", "type": "car", "x_px": -110, "y_px": -380},
  {"object_id": "obj_2", "type": "pedestrian", "x_px": 212, "y_px": 405}
]}
{"event": "simulation_tick", "timestamp": 1763640010.1, "objects": [
  {"object_id": "obj_1", "type": "car", "x_px": -110, "y_px": -364},
  {"object_id": "obj_2", "type": "pedestrian", "x_px": 208, "y_px": 405}
]}
{"event": "simulation_ended", "timestamp": 1763640030.0}
```

In the example, `obj_1` is a car behind us in the left lane, overtaking (its `y_px` grows), and
`obj_2` is a pedestrian ahead on the right, crossing to the left (its `x_px` shrinks). The ticks are
shown on several lines for reading; each is sent as one JSON text frame.

## What it receives

One message, `gaze_update`, from the server (display only; the simulator sends nothing in reply).
It shows what the system thinks the driver is looking at. Code: `js/gaze.js`, constants in `js/config.js`.

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok",
 "region": "mirror_left"}
```

| Field | Why the simulator needs it |
|---|---|
| `event` | Must be `gaze_update`; any other message is ignored. |
| `timestamp` | Unix seconds when the gaze was measured. Not used for display. |
| `status` | `ok`, `no_face`, `eyes_closed`, `calibrating` or `no_gaze_client`. Decides the badge text and whether a region is drawn. |
| `region` | Which region to highlight when `status` is `ok`; `null` otherwise. |

Invalid JSON, non-objects, unknown statuses and an `ok` without a known region are ignored (never an
error). Every received frame is printed to the console as `RECEIVED ...` (see "What it sends").
The server currently sends one every second; the 1.5 s timeout leaves room for one late message.

**Regions** (highlighted on the first-person view only; on the setup screen only the badge is shown):
`front_left`, `front_center`, `front_right` (thirds of the open windshield), `side_left`, `side_right`
(side-window glass outside the A-pillars), `mirror_center`, `mirror_left`, `mirror_right` (the mirror
glass), and `away` (no region: an "AWAY" badge appears instead). The highlight is a pulsing translucent
red frame (with a faint red fill) and a "LOOKING: ..." label, drawn as the last layer of the frame.

**Status badge** (bottom-right, visible in setup and in the driving view):

| Badge | Meaning |
|---|---|
| green `Gaze: mirror_left` | `ok`, driver looks at that region |
| amber `Gaze: AWAY` | `ok` with region `away` |
| red `No face detected` / `Eyes closed` | `no_face` / `eyes_closed` |
| blue `Camera calibrating - look straight ahead` | `calibrating` |
| grey `No camera connected` | `no_gaze_client` |
| grey `No gaze data` | nothing received for 1.5 s (`GAZE_TIMEOUT_MS`); any highlight is removed so a dead connection never leaves a stale one |

## Movement in one line

Side-lane vehicles drive ahead (finished 80 m ahead), center-lane vehicles stay parked, and
pedestrians cross the road from the grass on their nearer side.
