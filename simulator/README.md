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
- `js/ws.js`: WebSocket client; prints every outgoing message to the browser console
- `js/setup.js`: setup screen: icons, placing/dragging objects (sends nothing)
- `js/render.js`: first-person view, mirrors, minimap, visibility detection
- `js/sim.js`: movement, simulation flow, all outgoing messages; runs on page load
- `test_server.py`: test server that prints every message (port from env var `PORT`, default 8765)
- `requirements.txt`, `run.bat`, `run.sh`: setup and start

## What it sends

The simulator is a WebSocket **client**. It sends JSON text frames to `ws://localhost:8765` and
ignores replies. Messages sent while disconnected are lost, and it reconnects every 2 s.
If you change `PORT`, change `WS_URL` in `js/config.js` to match.

Every message is also printed to the browser console (F12 → Console), one JSON per line, exactly
as sent. A message that could not be sent (socket down) appears as a `NOT SENT` warning, and the
connection state is logged when it changes. Nothing about the connection is shown on the screen.

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

Sent 10 times per second while driving, starting at GO. One message holds a list of every object
that is visible right now (in the windshield or any mirror). An object that is not visible is not
in the list; the list can be empty.

| Field | Why the backend needs it |
|---|---|
| `event` | Message type, used for routing. |
| `timestamp` | When this snapshot was taken. The backend pairs it with the gaze samples; the first tick an object appears in is the moment it became visible, which starts the reaction-time clock and the alert window. |
| `objects` | The list of visible objects. Without it the backend does not know what is around the car. |
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

## Movement in one line

Side-lane vehicles drive ahead (finished 80 m ahead), center-lane vehicles stay parked, and
pedestrians cross the road from the grass on their nearer side.
