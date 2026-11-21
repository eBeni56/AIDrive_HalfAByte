# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

## Run everything together (Docker)

Needs Docker Desktop. From this folder:

```
docker compose up --build
```

| Service | What it does | Address on this PC |
|---|---|---|
| `server` | `server_simulator_lookingdir/`: receives the simulator and the driver monitor, sends `gaze_update` (the driver monitor's gaze zone as a simulator region, 10 Hz) to the simulator | `ws://localhost:8765` |
| `simulator` | `simulator/` served by nginx; its WebSocket goes to `/ws`, which nginx passes to `server` | <http://localhost:8080> |
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

### With the live webcam

Docker Desktop on Windows can't open a USB webcam, so the live driver monitor runs on the host and connects to
the server's published port (its default address in `driver_monitor/config/driver_monitor.yaml` is
`ws://localhost:8765`, which matches `SERVER_PORT`):

```
docker compose up --build server simulator
cd driver_monitor
uv run python -m driver_monitor
```

Look straight ahead until calibration is done (the simulator badge says "Camera calibrating" meanwhile), then the
highlight follows your eyes. If a mirror highlights in the wrong place, look at it and read the gaze angles in the
driver monitor window, then adjust `gaze_zones` in `driver_monitor/config/driver_monitor.yaml`.
