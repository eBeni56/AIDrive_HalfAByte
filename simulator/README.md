# Simulator

A browser page for the drive: place cars, trucks, bikes and pedestrians around the ego car, then drive and see
them through the windshield and the three mirrors. It sends the position of every object to the server and shows
where the server says the driver is looking. Plain HTML/CSS/JS, no build step.

## Run

* **With Docker** (`start.bat` in the repository root, or `docker compose up`): <http://localhost:8080>. nginx
  serves the page and passes its WebSocket (`/ws`) to the server.
* **Without Docker**: start the server (`central_point/`, see its README) and open `index.html`
  directly in the browser; it then connects to `ws://localhost:8765` (`WS_URL` in `js/config.js`).

## Use

1. **Setup:** drag an icon from the toolbar onto the road (3 lanes). Drag a placed object to move it;
   right-click or double-click removes it; **Clear all** removes everything. **Speed** (1x → 2x → 0.25x → 0.5x)
   multiplies only the objects' speeds.
2. **Start simulation** (at least one object): "Look at the centre of the screen" and a 5-4-3-2-1 countdown
   with the world frozen (`COUNTDOWN_S`); meanwhile the camera measures the driver's straight-ahead look again
   (the 0,0 of the gaze). Then **GO**. The gaze badge and the highlight are shown only from GO to the end of
   the drive (not in setup, the countdown or after the end).
3. **Driving:** the first-person view (windshield, A-pillars, three mirrors) on the left, a top-down minimap on
   the right. **Esc** / **Abort** stops it.
4. The run ends 3 s after the last object has finished; **Back to setup** keeps the placed objects.

How the objects move: vehicles in a side lane drive ahead (car and motorcycle 5 m/s, truck 3.5, bicycle 2, each
±10 %) and are finished 80 m ahead; vehicles in the centre lane stay parked; pedestrians start on the grass on
their nearer side and cross the road (1.4 m/s).

## What it sends

The simulator is a WebSocket **client**. Nothing is sent during setup. A run is four kinds of messages, every
one with `timestamp` (Unix seconds):

| Message | When | Fields |
|---|---|---|
| `simulation_countdown` | at Start (the server asks the camera for the new straight-ahead look) | `event`, `timestamp` |
| `simulation_started` | at GO | `event`, `timestamp` |
| `simulation_tick` | 10 times per second while driving | `event`, `timestamp`, `objects` |
| `simulation_ended` | at the end, or on Esc / Abort while driving | `event`, `timestamp` |

`objects` holds **every** object on the map in every tick, seen or not (windshield, mirror, blind spot, far away):

| Field | Meaning |
|---|---|
| `object_id` | `obj_1`, `obj_2`, ...: the same object keeps its id for the whole run |
| `type` | `car`, `truck`, `bicycle`, `motorcycle`, `pedestrian` |
| `x_px` | sideways position of the object's centre: pixels from the ego car's centre, + **right** |
| `y_px` | lengthwise: + **ahead** of the ego car, − behind |

The scale is fixed (it does not depend on the window size): a lane is 110 px = 3.5 m, so 1 m ≈ 31.4 px. The road
spans `x_px` −165 … +165 (lanes split at ±55); the ego car spans `y_px` −71 … +71.

```json
{"event": "simulation_countdown", "timestamp": 1763640005.0}
{"event": "simulation_started", "timestamp": 1763640010.0}
{"event": "simulation_tick", "timestamp": 1763640010.1, "objects": [
  {"object_id": "obj_1", "type": "car", "x_px": -110, "y_px": -380},
  {"object_id": "obj_2", "type": "pedestrian", "x_px": 212, "y_px": 405}]}
{"event": "simulation_ended", "timestamp": 1763640030.0}
```

Here `obj_1` is a car behind us in the left lane, overtaking (its `y_px` grows), and `obj_2` a pedestrian ahead
on the right, crossing to the left (its `x_px` shrinks). Messages sent while disconnected are lost; the page
reconnects every 2 s.

## What it receives

Only `gaze_update` from the server (anything else, and anything invalid, is ignored). The server sends it only
during a run, and the page shows it only while driving (GO to the end); at GO and at the end the last gaze is
forgotten, so an old one is never shown:

```json
{"event": "gaze_update", "timestamp": 1763640012.345, "status": "ok", "region": "mirror_left"}
```

| `status` | Badge (bottom right) | First-person view |
|---|---|---|
| `ok` | green `Gaze: mirror_left` (amber `Gaze: AWAY` for region `away`) | the region gets a pulsing red frame and a "LOOKING: ..." label |
| `no_face` / `eyes_closed` | red `No face detected` / `Eyes closed` | no highlight |
| `calibrating` | blue `Camera calibrating - look straight ahead` | no highlight |
| `no_gaze_client` | grey `No camera connected` | no highlight |
| nothing for 1.5 s | grey `No gaze data` | no highlight (a dead connection never leaves an old one) |

The regions on the screen: `front_left`, `front_center`, `front_right` (thirds of the windshield), `side_left`,
`side_right` (the side-window glass outside the A-pillars), `mirror_center`, `mirror_left`, `mirror_right` (the
mirror glass) and `away` (none of them: an "AWAY" label).

## Console (F12)

Every message sent is printed exactly as sent, every received one with `RECEIVED ` in front, so the two
directions can be read side by side. A message that could not be sent appears as a `NOT SENT` warning, and the
connection state is logged when it changes.

## Files

| File | What |
|---|---|
| `index.html`, `css/style.css` | the page and its styles |
| `js/config.js` | the constants: WebSocket address, scale, object types, speeds, timing, gaze display |
| `js/coords.js` | pixels ↔ meters, lanes, `mapPx` (the position that is sent) |
| `js/setup.js` | the setup screen: icons, placing and dragging objects |
| `js/render.js` | the first-person view, the mirrors, the minimap |
| `js/gaze.js` | `gaze_update`: the badge and the highlight |
| `js/ws.js` | the WebSocket client and the console log |
| `js/sim.js` | the movement, the run (countdown → driving → done) and every message sent |
| `Dockerfile`, `nginx.conf` | the Docker image: static files + `/ws` passed to the server |
