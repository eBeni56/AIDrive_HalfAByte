# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

A camera watches the driver; the system cares about exactly three things:

1. **Where the driver looks** - for every car, bike or person the simulator puts around the car: did the driver
   look at it in time? If yes, nothing happens. If not: "Car to the left of you!" (dashboard + voice).
2. **Not paying attention** - looking up or down (phone, lap, console) instead of at the road, the mirrors or the
   side windows, or a phone in the hand.
3. **Eyes closed** - longer than a blink: "Open your eyes! Pay attention to the road!" after 1 s.

(Tiredness detection was removed on purpose: it was hard to detect reliably and caused false alarms.)

## Run everything with one command

Needs Docker Desktop, a webcam, a microphone and speakers. From this folder:

```
start.bat            # Windows (macOS / Linux: ./start.sh)
start.bat --build    # after a code change: rebuild the images first
```

It starts:

* **server, simulator, dashboard, ollama, copilot** in Docker (`docker compose up`: logs in this terminal, Ctrl+C
  stops them). On the first start `ollama-pull` downloads the language models (`COPILOT_S1_MODEL` /
  `COPILOT_S2_MODEL`, ~1-4 GB) into the `ollama` volume, so later starts are quick. With an NVIDIA GPU
  (`nvidia-smi` found) `compose.gpu.yml` is added and Ollama runs on the GPU; `set OLLAMA_GPU=0` / `1` forces it.
  The safety alerts never wait for the models;
* **the driver monitor** (webcam, MediaPipe) on this PC, in its own minimised window: Docker Desktop can't reach
  a webcam. Its camera image with the markings appears on the dashboard. The first run sets it up by itself:
  with [uv](https://docs.astral.sh/uv/) if installed, otherwise in `driver_monitor/.venv` with Python 3.12
  (`py -3.12`); this takes a few minutes. Close its window (or Ctrl+C in it) to stop it.
  Another camera: `set DM_ARGS=--camera 0` before `start.bat` (`--list-cameras` shows them);
* **the voice client** (`copilot/voice_client.py`: microphone and speakers, which Docker can't reach either) on
  this PC, in its own minimised window. It speaks the copilot's alerts and answers "Hey Jarvis ...". The first run
  sets it up in `copilot/.venv` (uv or Python 3.12, a few minutes). `set VOICE_ARGS=--text` to type instead of
  speaking, `set NO_VOICE=1` to start without it;
* **two browser windows**, the dashboard and the simulator, as soon as they answer.

Look straight ahead until calibration is done (shown on the dashboard), then place objects in the simulator and
press **Start simulation**. Plain `docker compose up --build` starts only the Docker part (no camera, no voice).

| Service | What it does | Address on this PC |
|---|---|---|
| `server` | `server_simulator_lookingdir/`: receives the simulator and the driver monitor, sends `gaze_update` (the driver monitor's gaze zone as a simulator region, 30 Hz) to the simulator, the detections and the "did the driver look?" results to the dashboard, and what to say to the AI copilot | `ws://localhost:8765` |
| `simulator` | `simulator/` served by nginx; its WebSocket goes to `/ws`, which nginx passes to `server` | <http://localhost:8080> |
| `dashboard` | `dashboard/` (React) served by nginx: the driver camera with its markings, the assistant chat and the detections; WebSocket via `/ws`, camera image from the driver monitor at `localhost:8090` | <http://localhost:8081> |
| `ollama` | Runs the copilot's language models; only inside the compose network (`ollama:11434`). Models are kept in the `ollama` volume | - |
| `ollama-pull` | One-shot: downloads `COPILOT_S1_MODEL` / `COPILOT_S2_MODEL` into `ollama` if they are missing (never fails: without them the alerts still work) | - |
| `copilot` | `copilot/ai-server.py`: takes the alerts from `server` and has them spoken, answers the driver's questions with Ollama, sends everything said to the dashboard chat; the voice client connects here | `ws://localhost:8766` |
| `model-download` | Only with `--profile replay`: downloads the MediaPipe model into `driver_monitor/models/` if missing | - |
| `driver-monitor` | Only with `--profile replay`: replays a recorded video from `driver_monitor/recordings/` (not in git) and streams `driver_state` to `server` | - |

`docker compose up --build` starts `server`, `simulator`, `dashboard`, `ollama`, `ollama-pull` and `copilot`; for the
live gaze run the driver monitor on the host with the webcam, and for the voice the voice client (see below). To replay a video instead: `docker compose --profile replay up --build`.

Open <http://localhost:8080> in the browser, place objects and press **Start simulation**. The `server` log
shows the simulator messages, every driver alert that starts / ends (eyes closed, not paying attention), every line
the copilot is told to say, and every change
of the `gaze_update` it sends; the `driver-monitor` log shows its
detections. The highlighted region in the simulator follows the replayed video's driver; "No camera connected"
means no `driver_state` arrived in the last second (the driver monitor is still starting, or the video ended).
`docker compose down` stops everything.

**Settings** are in [.env](.env): the host ports (`SERVER_PORT`, `SIMULATOR_PORT`), the replayed video
(`DM_VIDEO`, a file in `driver_monitor/recordings/`), the server log level and the gaze settings (`GAZE_HZ`,
`CAMERA_TIMEOUT_S`, `FRONT_SPLIT_DEG`, `RECENT_ALERT_S`, see [the server README](server_simulator_lookingdir/README.md#output-1-to-the-simulator-and-the-dashboard)),
when the voice warns (`EYES_CLOSED_VOICE_S`, `EYES_CLOSED_REPEAT_S`, `ATTENTION_REPEAT_S`), and the copilot:
`COPILOT_S1_MODEL` / `COPILOT_S2_MODEL` (the defaults `qwen3:1.7b` + `qwen3:4b-instruct` need a GPU; on a laptop
without one use a small model such as `qwen2.5:1.5b`), `COPILOT_PORT`, `COPILOT_ARGS=--no-llm` (alerts only, no
language model), `OLLAMA_IMAGE`, `OLLAMA_BASE` (another Ollama instead of the container). Inside the compose network the
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
highlight follows your eyes.

### The voice (AI copilot)

`start.bat` does this for you. The copilot (`ai-server.py`) and Ollama run in Docker; the voice client runs on the
host, because it needs the microphone and the speakers:

```
docker compose up --build                     # includes ollama, ollama-pull and copilot (ws://localhost:8766)
cd copilot
.venv\Scripts\python -u voice_client.py --mic   # speaks the alerts (TTS); "Hey Jarvis ..." to ask something
```

The voice client waits for the copilot if it is not up yet. Its packages: `copilot/requirements.txt`,
`speech-recognition/requirements.txt` and `text-to-speech/requirements.txt` (`scripts/setup-voice.bat` installs them
into `copilot/.venv`). Without Docker the copilot also runs on the host: `python -u copilot/ai-server.py` (with a
local Ollama; `--no-llm` for the alerts only, see [copilot/README.md](copilot/README.md)).

The server decides when to warn and what to say (`server_simulator_lookingdir/reactions.py`); everything said
also appears in the dashboard's chat. If a mirror highlights in the wrong place, look at it and read the gaze angles in the
driver monitor window, then set `gaze_zones` in `driver_monitor/config/driver_monitor.yaml` (the defaults are in
`driver_monitor/src/driver_monitor/config.py`; `--dump-config` prints them).
