# Dashboard

A separate page from the simulator. Two large console-like logs, in plain sentences, plus a small camera
image and a small chat. React + TypeScript, built with Vite.

| Panel | Shows |
|---|---|
| **Simulation** (centre) | `SIM` (purple) input from the simulation: started, a new object appeared, an object moved into another zone (and how long the driver has to look), the end with a summary. `CAM` (cyan): where the driver looks, when it changes and holds for 0.3 s. `OK` (green): the driver looked at an object in time, with the reaction time. `MISSED` (red): did not look in time. `STATS` at the end: a small table of the run (share of time looking at the road, at the mirrors, elsewhere, eyes closed; objects noticed / missed and which; average reaction; how many times the eyes were closed, the driver looked up / down, held a phone), collected by the dashboard in `src/narrator.ts`. |
| **Driver** (right) | A driver display: ONE big icon and one short instruction, the most important current problem. Highest first: eyes closed ("Wake up! Open your eyes"), an object of the simulation the driver has not looked at yet ("Look right – a truck is in your right blind spot", with a direction arrow), phone in the hand ("Put down your phone"), looking at none of the 8 regions (up / down) for 1 s ("Eyes on the road"), no face in the camera for 1 s, calibrating, no camera, otherwise "All good". A message stays at least 3 s; only a more important one replaces it sooner. Rules and texts: `src/hud.ts`. |
| Camera (top left) | The driver monitor's own preview with its markings (eye and lip contours, iris, gaze arrow). |
| Assistant (bottom left) | Placeholder for the conversation with the voice assistant. |

A phone in the hand is the driver monitor's `PHONE_USE` detection (MediaPipe Object Detector); the camera image
shows a box around the phone.

```
                 driver_state JSON                        driver_state (10/s), gaze_update, chat_message,
driver_monitor ─────────────────────► server :8765 ───── simulation_started/_ended, attention_event/_summary ──► dashboard
      │                                 ▲      ▲
      │ camera preview (MJPEG)          │      └─ simulator (simulation_tick: object positions)
      └─────────── http://localhost:8090/stream.mjpg ─────────────────────────────────────────────────────────► dashboard
```

* **Data:** the dashboard connects to the server and sends `{"type": "hello", "role": "dashboard"}`. The
  server then forwards the latest `driver_state` (`DASHBOARD_HZ`, default 10/s), the simulation's start and end,
  its own `attention_event` / `attention_summary` (which zone each object is in, and whether the driver looked
  at it in time: the rules are in [the server README](../server_simulator_lookingdir/README.md#attention-did-the-driver-look-in-time)),
  every `gaze_update` and every `chat_message`. The simulator gets none of these, only `gaze_update`.
* **Camera:** served by the driver monitor as MJPEG (`camera_stream` in
  `driver_monitor/config/driver_monitor.yaml`). The browser can't open the webcam itself: the driver monitor
  already uses it. Frames are only encoded while a page watches.
* **Chat:** messages appear when any client sends the server
  `{"type": "chat_message", "role": "user" | "assistant", "text": "..."}`, or when code in the page calls
  `addChatMessage(role, text)` from `src/chat.ts` (`window.dashboardChat.add("assistant", "hi")` in the
  browser console). There is no history: a message sent while the page is closed is not shown later.

## Run

Needs Node.js 18+. The server and the driver monitor run as usual (see the root README).

```
cd dashboard
npm install          # first time only
npm run dev          # http://localhost:5173
```

With Docker it is the `dashboard` service of `compose.yml`: <http://localhost:8081> (`DASHBOARD_PORT`).
Its WebSocket goes to `/ws` on the same address (nginx passes it to the server); the camera stream is still read
from `localhost:8090`, so run the live driver monitor on this PC (or the `replay` profile, which publishes 8090).

| Build-time variable | Default | Meaning |
|---|---|---|
| `VITE_WS_URL` | `ws://localhost:8765` (Docker: `/ws`) | The server. A value starting with `/` means this page's own host. |
| `VITE_CAMERA_URL` | `http://localhost:8090/stream.mjpg` | The driver monitor's camera stream. |

Put them in `dashboard/.env.local` for `npm run dev`. `npm run build` type-checks and writes `dist/`.

## Files

| File | What |
|---|---|
| `src/App.tsx` | Layout: camera and chat on the left, the two terminals |
| `src/useServer.ts` | WebSocket: hello, routes each message to the narrator, reconnects every 2 s |
| `src/narrator.ts` | Every sentence of the Simulation log and the end-of-run statistics |
| `src/hud.ts`, `src/components/DriverHud.tsx` | The driver display: which problem is shown (priorities, 3 s hold), its text and icon |
| `src/log.ts` | The Simulation log (last 500 lines) |
| `src/components/Terminal.tsx` | A log panel; scrolls by itself while you are at the bottom |
| `src/components/CameraPanel.tsx` | The MJPEG image; reopens it when the driver monitor (re)starts |
| `src/components/ChatPanel.tsx`, `src/chat.ts` | Conversation placeholder and its message store |
| `src/types.ts` | The message fields used (full `driver_state` reference: `driver_monitor/docs/detection-schema.md`) |
