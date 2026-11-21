# Dashboard

A separate page from the simulator: the driver camera with the driver monitor's markings (top left), the
conversation with the voice assistant (top right) and what the driver monitor detects (bottom).
React + TypeScript, built with Vite.

```
                 driver_state JSON                        driver_state (10/s), gaze_update, chat_message
driver_monitor ─────────────────────► server :8765 ───────────────────────────────────────► dashboard
      │                                    ▲
      │ camera preview (MJPEG)             │ chat_message (voice assistant)
      └─────────── http://localhost:8090/stream.mjpg ──────────────────────────────────────► dashboard
```

* **Camera:** the driver monitor's own preview (eye and lip contours, iris, gaze arrow, status panel,
  detections), served by the driver monitor as MJPEG (`camera_stream` in
  `driver_monitor/config/driver_monitor.yaml`, default `http://localhost:8090/stream.mjpg`). The browser can't
  open the webcam itself: the driver monitor already uses it. Frames are only encoded while a page watches.
* **Data:** the dashboard connects to the server and sends `{"type": "hello", "role": "dashboard"}`. The
  server then forwards the latest `driver_state` (`DASHBOARD_HZ`, default 10/s) and every `chat_message`, and
  `gaze_update` as to the simulator. The simulator never gets `driver_state`.
* **Chat:** only a place for the conversation for now. Messages appear when any client sends the server
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
| `src/App.tsx` | Layout; decides whether the camera data is fresh (no `driver_state` for 1.5 s = "No camera data") |
| `src/useServer.ts` | WebSocket: hello, messages, reconnect every 2 s |
| `src/components/CameraPanel.tsx` | The MJPEG image; reopens it when the driver monitor (re)starts |
| `src/components/ChatPanel.tsx`, `src/chat.ts` | Conversation placeholder and its message store |
| `src/components/StatusPanel.tsx` | Cards: drowsiness, attention, gaze, eyes and head, active detections |
| `src/components/Header.tsx` | Server, calibration, face and fps status |
| `src/types.ts` | The `driver_state` fields used (full reference: `driver_monitor/docs/detection-schema.md`) |
| `src/format.ts` | Number formatting and the colour of each state |
