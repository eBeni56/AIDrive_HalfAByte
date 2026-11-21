# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

## Run everything with one command

Needs Docker Desktop and a webcam. From this folder:

```
start.bat            # Windows (macOS / Linux: ./start.sh)
start.bat --build    # after a code change: rebuild the images first
```

It starts:

* **server, simulator, dashboard** in Docker (`docker compose up`: logs in this terminal, Ctrl+C stops them);
* **the driver monitor** (webcam, MediaPipe) on this PC, in its own minimised window: Docker Desktop can't reach
  a webcam. Its camera image with the markings appears on the dashboard. The first run sets it up by itself:
  with [uv](https://docs.astral.sh/uv/) if installed, otherwise in `driver_monitor/.venv` with Python 3.12
  (`py -3.12`); this takes a few minutes. Close its window (or Ctrl+C in it) to stop it.
  Another camera: `set DM_ARGS=--camera 0` before `start.bat` (`--list-cameras` shows them);
* **two browser windows**, the dashboard and the simulator, as soon as they answer.

Look straight ahead until calibration is done (shown on the dashboard), then place objects in the simulator and
press **Start simulation**. Plain `docker compose up --build` starts only the Docker part.

| Service | What it does | Address on this PC |
|---|---|---|
| `server` | `server_simulator_lookingdir/`: receives the simulator and the driver monitor, sends `gaze_update` (the driver monitor's gaze zone as a simulator region, 10 Hz) to the simulator | `ws://localhost:8765` |
| `simulator` | `simulator/` served by nginx; its WebSocket goes to `/ws`, which nginx passes to `server` | <http://localhost:8080> |
| `dashboard` | `dashboard/` (React) served by nginx: the driver camera with its markings, the assistant chat and the detections; WebSocket via `/ws`, camera image from the driver monitor at `localhost:8090` | <http://localhost:8081> |
| `model-download` | Only with `--profile replay`: downloads the MediaPipe model into `driver_monitor/models/` if missing | - |
| `driver-monitor` | Only with `--profile replay`: replays a recorded video from `driver_monitor/recordings/` (not in git) and streams `driver_state` to `server` | - |

`docker compose up --build` starts only `server` and `simulator`; for the live gaze run the driver monitor on the
host with the webcam (see below). To replay a video instead: `docker compose --profile replay up --build`.

Open <http://localhost:8080> in the browser, place objects and press **Start simulation**. The `server` log
shows the simulator messages and every change of the `gaze_update` it sends; the `driver-monitor` log shows its
detections. The highlighted region in the simulator follows the replayed video's driver; "No camera connected"
means no `driver_state` arrived in the last second (the driver monitor is still starting, or the video ended).
`docker compose down` stops everything.

**Settings** are in [.env](.env): the host ports (`SERVER_PORT`, `SIMULATOR_PORT`), the replayed video
(`DM_VIDEO`, a file in `driver_monitor/recordings/`), the server log level and the gaze settings (`GAZE_HZ`,
`CAMERA_TIMEOUT_S`, `FRONT_SPLIT_DEG`, see [the server README](server_simulator_lookingdir/README.md#output-to-the-simulator)). Inside the compose network the
services always use `server:8765`; the driver monitor gets that address from
[driver_monitor/config/driver_monitor.docker.yaml](driver_monitor/config/driver_monitor.docker.yaml).

Replay another video without restarting the rest: `docker compose run --rm -e DM_VIDEO=recording-12.mp4 driver-monitor`
(or change `DM_VIDEO` in `.env` and `docker compose up -d driver-monitor`).

### The live webcam by hand

`start.bat` does this for you. The driver monitor runs on the host and connects to the server's published port (its default address in `driver_monitor/config/driver_monitor.yaml` is
`ws://localhost:8765`, which matches `SERVER_PORT`):

```
docker compose up --build server simulator dashboard
cd driver_monitor
uv run python -m driver_monitor
```

The dashboard (<http://localhost:8081>) then shows the camera with the driver monitor's markings (streamed from
`localhost:8090`), the detections and the assistant chat. Without Docker: `cd dashboard`, `npm install`,
`npm run dev` (<http://localhost:5173>), see [dashboard/README.md](dashboard/README.md).

Look straight ahead until calibration is done (the simulator badge says "Camera calibrating" meanwhile), then the
highlight follows your eyes. If a mirror highlights in the wrong place, look at it and read the gaze angles in the
driver monitor window, then adjust `gaze_zones` in `driver_monitor/config/driver_monitor.yaml`.
