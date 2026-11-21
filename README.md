# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

**Did the driver look in time?** A driving simulator puts cars, trucks, bikes and pedestrians around the car. A
webcam watches the driver. When an object enters a zone (ahead, a blind spot, a mirror), the driver has a short
time to look there. The dashboard shows every look and every miss, plus a driver display with the one most
important warning: eyes closed, phone in the hand, eyes off the road, "check the left mirror", ...

## Run it

Needs Docker Desktop and a webcam. From this folder:

```
start.bat            # Windows (macOS / Linux: ./start.sh)
start.bat --build    # after a code change: rebuild the Docker images first
```

It starts:

* **server, simulator, dashboard** in Docker (`docker compose up`: logs in this terminal, Ctrl+C stops them);
* **the driver monitor** on this PC, in its own minimised window, because Docker Desktop can't reach a webcam.
  The first run sets it up by itself (with [uv](https://docs.astral.sh/uv/) if installed, otherwise Python 3.12
  in `driver_monitor/.venv`), which takes a few minutes. Close its window to stop it;
* **two browser windows** as soon as they answer: the dashboard (<http://localhost:8081>) and the simulator
  (<http://localhost:8080>).

Then:

1. **Look straight ahead at the centre of the screen** until the calibration is done (about 9 s: eyes open,
   then closed when asked). The dashboard says "Look straight ahead" meanwhile. Every gaze zone is measured
   from this look.
2. In the simulator, drag objects onto the road and press **Start simulation**.
3. Look where the objects are. The dashboard logs whether you looked in time and shows a summary at the end.

Another camera: `set DM_ARGS=--camera 0` before `start.bat` (`--list-cameras` shows them). To calibrate again,
restart `start.bat` (or run the driver monitor by hand with its window and press `c`, see below).

## How the parts talk

```
                    driver_state                        gaze_update
  driver_monitor ─────────────────►  server  ──────────────────────────►  simulator
     (webcam)                         :8765  ◄──────────────────────────  (browser)
        │                            ▲    │    simulation_started / _tick / _ended
        │                      hello │    │ driver_state, gaze_update, attention_event / _summary,
        │                            │    ▼ simulation started / ended, chat_message
        └── camera image (MJPEG) ──►  dashboard (browser)
```

| From → to | Message | When |
|---|---|---|
| driver monitor → server | `driver_state`: eyes, gaze zone and angles, detections (`EYES_CLOSED`, `DISTRACTED`, `PHONE_USE`) | every camera frame |
| simulator → server | `simulation_started`, `simulation_tick` (every object's position), `simulation_ended` | a run; ticks 10/s |
| server → simulator, dashboard | `gaze_update`: where the driver looks, as one of the 8 regions (or `away`) and a status | 10/s |
| server → dashboard | the latest `driver_state`, `attention_event` / `attention_summary`, the run's start / end, `chat_message` | as they happen |
| dashboard → server | `hello` (role `dashboard`) | once per connection |
| driver monitor → dashboard | the camera image with the markings (MJPEG over HTTP, port 8090) | while the page is open |

Every WebSocket message is one JSON object, all on one port (8765). The exact fields are in each folder's README.

## Folders

| Folder | What it is |
|---|---|
| [driver_monitor/](driver_monitor/README.md) | Python + MediaPipe: webcam → eyes closed, where the driver looks, phone in the hand → `driver_state` |
| [server_simulator_lookingdir/](server_simulator_lookingdir/README.md) | Python WebSocket server: connects everything, turns the gaze zone into a region, checks the attention |
| [simulator/](simulator/README.md) | Browser page (plain HTML/JS): place objects, drive, first-person view with mirrors |
| [dashboard/](dashboard/README.md) | React page: the camera, the Simulation log, the driver display, the assistant chat |
| [scripts/](scripts/README.md) | Helpers of `start.bat` |
| `compose.yml`, `.env` | The Docker services and their settings |

## Settings

* [.env](.env): the ports on this PC (`SERVER_PORT`, `SIMULATOR_PORT`, `DASHBOARD_PORT`), the server settings
  (`SERVER_LOG_LEVEL`, `GAZE_HZ`, `DASHBOARD_HZ`, `CAMERA_TIMEOUT_S`, `FRONT_SPLIT_DEG`) and the replayed video
  (`DM_VIDEO`). docker compose reads it by itself; `start.bat` takes the two page ports from it.
* The driver monitor: [driver_monitor/config/driver_monitor.yaml](driver_monitor/config/driver_monitor.yaml)
  (server address, camera stream); where the mirrors are: `gaze_zones` (see its README).

## Without start.bat

```
docker compose up --build server simulator dashboard     # the Docker part
cd driver_monitor
uv run python -m driver_monitor                          # the webcam on this PC, with a preview window
```

The driver monitor connects to `ws://localhost:8765` (the server's published port). `docker compose down` stops
the Docker part.

**Replaying a video instead of the webcam** (the `replay` profile): put a recording into
`driver_monitor/recordings/` (not in git), set `DM_VIDEO` in `.env`, then
`docker compose --profile replay up --build`. Another video without restarting the rest:
`docker compose run --rm -e DM_VIDEO=other.mp4 driver-monitor`.

## When something is off

| You see | Why / what to do |
|---|---|
| Dashboard: "Camera not connected" | The driver monitor is not running or still starting: look at its window. |
| Dashboard: "No connection to the server" | Docker is not running (yet). |
| Simulator badge: "No gaze data" | No `gaze_update` for 1.5 s: the server is not reachable. |
| A mirror is hard to "hit" | Calibrate looking at the screen centre. The camera image on the dashboard shows the live `gaze ZONE (yaw, pitch)`; adjust `gaze_zones` in the driver monitor's config. |
| "port already in use" | Another program uses it: change the port in `.env`. |
