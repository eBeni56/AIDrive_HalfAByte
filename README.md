# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

## Run everything together (Docker)

Needs Docker Desktop. From this folder:

```
docker compose up --build
```

| Service | What it does | Address on this PC |
|---|---|---|
| `server` | `server_simulator_lookingdir/`: receives the simulator and the driver monitor, sends `gaze_update` to the simulator | `ws://localhost:8765` |
| `simulator` | `simulator/` served by nginx; its WebSocket goes to `/ws`, which nginx passes to `server` | <http://localhost:8080> |
| `model-download` | One-shot: downloads the MediaPipe model into `driver_monitor/models/` if it is missing, then exits | - |
| `driver-monitor` | Starts after `model-download` finished successfully. Replays a recorded video in real time and streams `driver_state` to `server`; exits at the end of the video | - |

Open <http://localhost:8080> in the browser, place objects and press **Start simulation**. The `server` log
shows the simulator messages and every `gaze_update` it sends; the `driver-monitor` log shows its detections.
`docker compose down` stops everything.

**Settings** are in [.env](.env): the host ports (`SERVER_PORT`, `SIMULATOR_PORT`), the replayed video
(`DM_VIDEO`, a file in `driver_monitor/recordings/`) and the server log level. Inside the compose network the
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
