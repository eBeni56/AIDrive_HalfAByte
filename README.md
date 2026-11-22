# AIDrive_HalfAByte
Porsche AI Drive Hackathon -- Cluj-Napoca, November 2026 -- Team: Half A Byte

**Did the driver look in time?** A driving simulator puts cars, trucks, bikes and pedestrians around the car. A
webcam watches the driver. When an object enters a zone (ahead, a blind spot, a mirror), the driver has a short
time to look there. The dashboard shows every look and every miss, plus a driver display with the one most
important warning: eyes closed, phone in the hand, eyes off the road, "check the left mirror", ...

**The car talks, and an AI coaches the driver.** Every warning is also spoken ("Car in your right blind spot!").
An LLM, **Azure OpenAI `gpt-4.1-mini`**, acts as a driving instructor: during the drive it gives a short spoken
tip when a pattern shows up (two misses on the right, eyes closing again), and at the end of the drive it analyses
the full statistics of the run and tells the driver what went wrong, how to do it better and what improved since
the previous drives. The warnings themselves are decided by fixed rules, so they are instant and work offline; the
LLM does what needs judgement and words. See [The AI](#the-ai-an-llm-driving-instructor-azure-gpt-41-mini).

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
* **safety_ai** (the voice and the LLM coach) on this PC, in its own minimised window "safety ai", because Docker
  can't reach the speakers. Set up on the first run in `safety_ai/.venv`. The LLM needs the Azure key in
  `safety_ai/.env` (see [The AI](#the-ai-an-llm-driving-instructor-azure-gpt-41-mini)); without it only the
  warnings are spoken. `set NO_SAFETY_AI=1` skips it, `set SAI_ARGS=--mute` prints instead of speaking,
  `set SAI_ARGS=--no-llm` speaks the warnings only;
* **two browser windows** as soon as they answer: the dashboard (<http://localhost:8081>) and the simulator
  (<http://localhost:8080>).

Then:

1. **Look straight ahead at the centre of the screen** until the first calibration is done (about 9 s: eyes
   open, then closed when asked). The dashboard says "Look straight ahead" meanwhile. It measures your open and
   closed eyes and a first straight-ahead look.
2. **When the simulator page loads**: "Calibration: look at the number" and a 5-4-3-2-1 countdown over the
   setup screen. Look at the number: it is where the road ahead is while driving. Meanwhile the camera
   measures your straight-ahead look: the 0,0 of every gaze zone. Once per page load; the **Calibrate** button
   in the setup menu does it again (e.g. after you moved your seat); Esc skips it.
3. In the simulator, drag objects onto the road and press **Start simulation**: the driving view appears,
   frozen, with **"Press SPACE to start"**. No calibration here.
4. **SPACE = GO**: only now do the eye tracking of the drive, the warnings and the coach start. Look where the
   objects are. The dashboard logs whether you looked in time; a miss, closed eyes, the phone or looking down
   is warned at once (spoken and on the driver display). In a calm moment the coach may give a tip. Where you
   look is shown (the simulator's highlight, the dashboard's `CAM` lines) only from GO to the end of the drive;
   a blink does not hide it. The dashboard's camera image runs all the time.
5. The simulation ends when every object has driven off the minimap, and in any case after 40 s (an analysis
   needs at least 20 s). With it no new data is sent any more: no warnings are spoken (a tip already started is
   finished), the dashboard's log, chat and driver display keep the drive's data and stand still, and the AI
   gets nothing new. About 2-4 s later the AI's analysis of the drive is read out (about half a minute) and
   appears in the dashboard's chat.
6. **Back to setup** (a new simulation) stops the voice at once, also in the middle of the analysis (it stays
   in the chat). Drive again: the next analysis compares the two.

Another camera: `set DM_ARGS=--camera 0` before `start.bat` (`--list-cameras` shows them). To calibrate the
eyes again, restart `start.bat` (or run the driver monitor by hand with its window and press `c`, see below);
the straight-ahead look alone: the simulator's **Calibrate** button.

## The AI: an LLM driving instructor (Azure, gpt-4.1-mini)

| What | Done by | Why there |
|---|---|---|
| **Warnings** ("Car in your right blind spot!", "Put the phone down!") | central_point, fixed rules | must come within a fraction of a second, always be right and work offline |
| **Live tips** during the drive | the LLM | seeing the pattern behind several events and saying it helpfully needs judgement and language |
| **End-of-drive analysis** | the LLM | turning dozens of numbers into "what went wrong, why it matters, what to do" is an instructor's job |
| **Speaking** all of it | Phonon text-to-speech, offline on this PC | in safety_ai |

**The model.** `gpt-4.1-mini`, deployed in Microsoft Foundry (Azure OpenAI), called by
[safety_ai/coach_llm.py](safety_ai/coach_llm.py) over the Responses API with the resource's API key. Each answer
is forced into a strict JSON schema, so it can always be read and spoken. Chosen because it is fast (a tip ~1 s,
an analysis 2-4 s), cheap (~$0.0003 per tip, ~$0.001 per analysis: ~900-1,500 tokens in, ~200-400 out) and
follows the rules well. Also tried: small local models (Ollama, up to 1.5B) were too slow and too weak on a
laptop; `gpt-5-mini` was not better on our reports and took 5-35 s.

**How it works.**

1. **central_point measures** (no AI): during a drive it keeps a run log ([central_point/runlog.py](central_point/runlog.py))
   of every road user that needed a look (noticed in time with the reaction, or missed with its distance and
   where the driver looked instead), every eyes-closed / phone / looking-down episode with its length, the gaze
   time on the road and each mirror, the mirror checks, and the warnings spoken.
2. **central_point decides when to ask** ([central_point/coach.py](central_point/coach.py)): a tip when a
   pattern shows up (2 warnings of the same kind or 3 in total, or a streak of 6 noticed in time for praise), at
   most every 40 s and only in a calm moment (no warning, no open look window). An analysis at the end of each
   drive of 20 s or more. It sends a `coach_request` with a **report: plain facts, no conclusions**.
3. **safety_ai asks the LLM** ([safety_ai/coach_llm.py](safety_ai/coach_llm.py)) with an instructor prompt:
   use only facts from the report, never guess causes, rank problems by danger (eyes closed > a missed
   pedestrian > phone > blind spots > mirror habits), give a concrete fix for each, compare with the previous
   drives, suggest a break when the eyes close more than once, say the analysis is less reliable when the camera
   lost the face. Answers:
   * tip: `{"speak", "focus", "say"}` (at most ~20 words; `speak: false` when there is no real pattern);
   * analysis: `{"verdict", "say", "mistakes": [{"problem", "evidence", "fix"}], "went_well", "next_focus"}`
     (`say`: ~100 words, about 35 s spoken).
4. **safety_ai speaks it** and sends it back (`coach_result`); central_point shows it in the dashboard's chat
   (the analysis plus "Drive: needs work. Next drive: ...") and remembers the tips for the next reports. A tip is
   dropped if a warning came in while the LLM was writing it: a tip never talks over a hazard.

An analysis, from a drive where the driver never looked right (report and answer shortened):

```
report:  Road users that needed a look: 10. Noticed in time 5 (average reaction 0.47 s), missed 5.
         By side: left 4 of 4 noticed, right 0 of 4, ahead 1 of 2.
         - 1:10 a car in the right blind spot, 3 m away; gaze during the look window: road 1.0 s
         Gaze shares: road 82 %, left mirror 11 %, rear mirror 1 %, right mirror 0 %, ...
spoken:  "You did well to keep your eyes open and your phone down throughout the drive. However, missing five
         road users, especially on the right side, is a concern. You did not check your right mirror at all,
         which led to missing four vehicles on that side. ... For your next drive, focus on checking your right
         mirror regularly, about every five to eight seconds."
```

**Setup.** `safety_ai/.env` (not in git, never share it) holds `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY` and
`AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini`; details in [safety_ai/README.md](safety_ai/README.md). The "safety ai"
window shows `LLM coach ready (gpt-4.1-mini)` at start. Without the key, without internet or with `--no-llm`
everything else works: the warnings are still spoken, there are just no tips and no analysis.

## How the parts talk

```
                    driver_state                           gaze_update
  driver_monitor ─────────────────►  central_point  ──────────────────────────►  simulator
     (webcam)    ◄─────────────────      :8765      ◄──────────────────────────  (browser)
        │           recenter_gaze    ▲    │   ▲  │    calibration_started, simulation_started / _tick / _ended / _reset
        │                      hello │    │   │  │ warning, coach_request
        │                            │    │   │  ▼
        │                            │    │   safety_ai (this PC) ──► speakers;  ──► Azure gpt-4.1-mini (tips, analysis)
        │                            │    │      coach_result
        │                            │    ▼ driver_state, gaze_update, attention_event / _summary,
        │                            │      simulation started / ended, chat_message (warnings, tips, analysis)
        └── camera image (MJPEG) ──►  dashboard (browser)
```

| From → to | Message | When |
|---|---|---|
| driver monitor → server | `driver_state`: eyes, gaze zone and angles, detections (`EYES_CLOSED`, `DISTRACTED`, `PHONE_USE`) | every camera frame |
| simulator → server | `calibration_started` (page loaded / Calibrate: "look at the number"), `simulation_started` (at GO), `simulation_tick` (every object's position), `simulation_ended`, `simulation_reset` (back to setup after a run) | a run; ticks 10/s |
| server → driver monitor | `recenter_gaze`: measure the straight-ahead look again | at `calibration_started` |
| server → simulator, dashboard | `gaze_update`: where the driver looks, as one of the 8 regions (or `away`) and a status | 10/s, only during a run (GO to the end) |
| server → dashboard | the latest `driver_state`, `attention_event` / `attention_summary`, the run's start / end, `chat_message` | as they happen; `driver_state` only during a run |
| server → safety_ai | `simulation_started` / `simulation_ended` (at the end: no more warnings, the tips are finished, then only the analysis), `simulation_reset` (stop talking at once) | a run |
| dashboard → server | `hello` (role `dashboard`) | once per connection |
| server → safety_ai | `warning` (the line to speak, a priority), `coach_request` (the facts, for a tip or the end-of-drive analysis) | only between `simulation_started` and `simulation_ended`: a miss / driver problem; a pattern; at the end the analysis |
| safety_ai → server | `hello` (role `safety_ai`), `coach_result` (the LLM's tip / analysis; shown in the dashboard's chat) | per request |
| safety_ai → Azure | the report and the instructor prompt (HTTPS, Responses API); back: the answer as JSON | per request |
| driver monitor → dashboard | the camera image with the markings (MJPEG over HTTP, port 8090) | all the time while the page is open |

Every WebSocket message is one JSON object, all on one port (8765). The exact fields are in each folder's README.

## Folders

| Folder | What it is |
|---|---|
| [driver_monitor/](driver_monitor/README.md) | Python + MediaPipe: webcam → eyes closed, where the driver looks, phone in the hand → `driver_state` |
| [central_point/](central_point/README.md) | Python WebSocket server: connects everything, turns the gaze zone into a region, checks the attention, decides the warnings, logs the run's statistics and decides when the coach speaks |
| [safety_ai/](safety_ai/README.md) | Python, on this PC: speaks the warnings (Phonon text-to-speech) and the LLM coach's tips and end-of-drive analysis (Azure `gpt-4.1-mini`) |
| [simulator/](simulator/README.md) | Browser page (plain HTML/JS): place objects, drive, first-person view with mirrors |
| [dashboard/](dashboard/README.md) | React page: the camera, the Simulation log, the driver display, the assistant chat |
| [scripts/](scripts/README.md) | Helpers of `start.bat` |
| `compose.yml`, `.env` | The Docker services and their settings |

## Settings

* [.env](.env): the ports on this PC (`SERVER_PORT`, `SIMULATOR_PORT`, `DASHBOARD_PORT`), the server settings
  (`SERVER_LOG_LEVEL`, `GAZE_HZ`, `DASHBOARD_HZ`, `CAMERA_TIMEOUT_S`) and the replayed video
  (`DM_VIDEO`). docker compose reads it by itself; `start.bat` takes the two page ports from it.
* The driver monitor: [driver_monitor/config/driver_monitor.yaml](driver_monitor/config/driver_monitor.yaml)
  (server address, camera stream); where the mirrors are: `gaze_zones` (see its README).
* The LLM: `safety_ai/.env` (Azure endpoint, key, deployment; not in git). The voice: `SAI_ARGS=--voice male_1`
  (`female_1` default, `female_2`, `male_1`, `male_2`).

## Without start.bat

```
docker compose up --build server simulator dashboard     # the Docker part
cd driver_monitor
uv run python -m driver_monitor                          # the webcam on this PC, with a preview window
cd ..\safety_ai
.venv\Scripts\python main.py                             # the voice + LLM coach (set up once by start.bat)
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
| No gaze highlight in the simulator while driving | No `gaze_update` for 1.5 s (the server is not reachable), or the camera sees no face / closed eyes: the dashboard's driver display says which. (Before GO and after the end nothing is shown, on purpose.) |
| A mirror is hard to "hit" | Calibrate looking at the screen centre. The camera image on the dashboard shows the live `gaze ZONE (yaw, pitch)`; adjust `gaze_zones` in the driver monitor's config. |
| "port already in use" | Another program uses it: change the port in `.env`. |
| No voice at all | Look at the "safety ai" window: it must say `connected to central_point`. Speakers muted, or `SAI_ARGS=--mute` set? |
| Warnings are spoken, but no tips / no analysis | The window says `NOT reachable` or `no LLM coach`: check `safety_ai/.env` and the internet; `SAI_ARGS=--no-llm` still set? No analysis also after a drive shorter than 20 s. |
| What exactly did the LLM get and answer? | `docker compose logs server` shows every `COACH REQUEST` (the report) and `COACH ...` (the answer). |
