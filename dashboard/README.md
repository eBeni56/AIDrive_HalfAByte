# Dashboard

The page for the people watching the drive (a separate page from the simulator). React + TypeScript, built
with Vite.

| Panel | Shows |
|---|---|
| **Camera** (top left) | the driver monitor's own camera image with its markings: eye contours, iris, gaze arrow, phone box, a status panel with the live `gaze ZONE (yaw, pitch)` |
| **Assistant** (bottom left) | the conversation with the voice assistant (a placeholder until that exists) |
| **Simulation** (centre) | a log in plain sentences, see below |
| **Driver** (right) | the driver display: ONE big icon and one short instruction, the most important current problem |

**The Simulation log.** `SIM` (blue): the run started, a new object appeared, an object moved into another zone
(and how long the driver has to look). `CAM` (teal): where the driver looks, when it changes and holds for
0.3 s (only during a run: the server sends `gaze_update` from GO to the end). `OK` (green): the driver looked at an object in time, with the reaction time. `MISSED` (red): did not look
in time. `STATS` at the end: a small table of the run (time looking at the road, at the mirrors, elsewhere, eyes
closed; objects noticed / missed and which; average reaction; how often the eyes were closed, the driver looked up
or down, held a phone). `SYS` (grey): the connection to the server.

**The driver display**, the highest first: eyes closed ("Wake up! Open your eyes") · an object the driver has
not looked at yet ("Look right: a truck is in your right blind spot", with a direction arrow) · phone in the
hand ("Put down your phone") · looking up or down for 1 s ("Eyes on the road") · no face in the camera for 1 s ·
calibrating · no camera / no server · otherwise "All good". A message stays at least 3 s; only a more important
one replaces it sooner.

## What it receives and sends

```
                      driver_state (10/s), gaze_update (10/s, during a run), simulation_started / _ended,
 server :8765  ─────  attention_event, attention_summary, chat_message  ─────────────────────►  dashboard
 driver monitor ────  camera image: http://localhost:8090/stream.mjpg (MJPEG) ───────────────►  dashboard
```

* **WebSocket to the server.** After connecting it sends one message, `{"type": "hello", "role": "dashboard"}`;
  from then on the server forwards everything above (fields: [the server README](../central_point/README.md)).
  It reconnects every 2 s if the server is not running or restarts. Of `driver_state` only the detections,
  `quality.face_detected` and `calibration.status` are used.
* **Camera image.** An `<img>` of the driver monitor's MJPEG stream (`camera_stream` in
  `driver_monitor/config/driver_monitor.yaml`). The browser can't open the webcam itself: the driver monitor
  already uses it. The image is reopened when the driver monitor (re)starts.
* **Chat.** A line appears when any client sends the server
  `{"type": "chat_message", "role": "user" | "assistant", "text": "..."}`, or when code in the page calls
  `addChatMessage(role, text)` from `src/chat.ts` (in the browser console: `window.dashboardChat.add("assistant", "hi")`).
  There is no history: a message sent while the page is closed is not shown later.

In the `STATS` table, the objects noticed / missed and the average reaction come from the server's
`attention_summary`; the shares of looking time and the detection counts are collected by the page itself (from
`gaze_update` and `driver_state`).

## Run

With Docker it is the `dashboard` service of the root `compose.yml` (`start.bat` starts it):
<http://localhost:8081> (`DASHBOARD_PORT`). Its WebSocket goes to `/ws` on the same address (nginx passes it to
the server); the camera image is still read from `localhost:8090`, so the driver monitor must run on this PC (or
the `replay` profile, which publishes 8090).

Without Docker (Node.js 18+; the server and the driver monitor run as usual):

```
cd dashboard
npm install          # first time only
npm run dev          # http://localhost:5173
npm run build        # type-check and write dist/
```

| Build-time variable | Default | Meaning |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8765` (Docker: `/ws`) | the server; a value starting with `/` means this page's own host |
| `VITE_CAMERA_URL` | `http://localhost:8090/stream.mjpg` | the driver monitor's camera stream |

Put them into `dashboard/.env.local` for `npm run dev`.

## Files

| File | What |
|---|---|
| `src/App.tsx` | the layout: camera and chat on the left, the Simulation log, the driver display |
| `src/useServer.ts` | the WebSocket: hello, hands each message to the right place, reconnects |
| `src/narrator.ts` | every sentence of the Simulation log and the end-of-run statistics |
| `src/hud.ts`, `src/components/DriverHud.tsx` | the driver display: which problem is shown (priorities, 3 s hold), its text and icon |
| `src/regions.ts` | the 8 regions in words (log sentences, statistics, driver display) |
| `src/log.ts`, `src/components/Terminal.tsx` | the Simulation log (last 500 lines) and its panel |
| `src/components/CameraPanel.tsx` | the MJPEG image |
| `src/chat.ts`, `src/components/ChatPanel.tsx` | the conversation and its panel |
| `src/types.ts` | the message fields the page reads |
| `src/config.ts`, `src/format.ts` | addresses and timings; number formatting |
