# Dual-Tier In-Car AI Co-Pilot Server (`copilot/ai-server.py`)

WebSocket microservice running two local Ollama models concurrently with the benchmark-winning **`P8_Production_Master`** prompts (audited across 992 turns on RTX 4060 8 GB).

## Runtime Configuration (Benchmark-Verified)

| Tier | Mode | Model | `num_ctx` | `temperature` | `num_predict` | `think` | Purpose |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **System 1** | `urgent` | `qwen3:1.7b` | `512` | `0.00` | `25` | `False` | Split-second spoken safety reflex (~110 ms, 0/30 critical fails) |
| **System 2** | `info` | `qwen3:4b-instruct` | `4096` | `0.45` | `95` | `False` | Calm, warm advisory check-ins & multi-turn driver Q&A (~227 ms TTFC) |

Both tiers run on **independent workers**: an `urgent` hazard never waits behind an in-flight `info` reply, and every `urgent` alert is logged into System 2's session memory (`S1 -> S2 handoff`).

Model names can be changed with `COPILOT_S1_MODEL` / `COPILOT_S2_MODEL` (for example `qwen2.5:1.5b` for both on a laptop
without a GPU: `qwen3:4b-instruct` on CPU takes 15-50 s to answer).

## Safety alerts from the hub server (the voice of the system)

`ai-server.py` also connects to the central server (`server_simulator_lookingdir/server.py`, `ws://localhost:8765`,
env `HUB_URL` or `--hub`) with `{"type": "hello", "role": "copilot"}`. The hub decides **when** to warn and **what**
to say (`server_simulator_lookingdir/reactions.py`), for exactly three things:

| `kind` | `priority` | Example line |
| :--- | :---: | :--- |
| `eyes_closed` | 3 | `Open your eyes! Pay attention to the road!` (after 1 s closed, again every 3 s) |
| `missed_object` | 2 | `Car to the left of you!` / `Person in front of you!` (only when the driver did NOT look in time) |
| `not_paying_attention` | 1 | `Put the phone down! Pay attention to the road!` / `Stop looking down! Pay attention to the road!` |

Each `copilot_alert` is spoken **at once** (fixed line, no LLM wait) by every registered `voice_client.py`; a more urgent
line interrupts a less urgent one (conversation is priority 0). Every new line is also sent back to the hub as
`{"type": "chat_message", "role": "assistant", "text": ...}` and shows up in the dashboard's chat, as do the driver's
spoken questions (`role: "user"`) and the answers. The pushed message to the voice clients:

```json
{"event": "copilot_response", "pushed": true, "mode": "urgent", "alert_kind": "missed_object", "priority": 2,
 "repeat": false, "spoken_text": "Car to the left of you!", "model": null, "latency_ms": 0.0}
```

Without Ollama (or to keep the laptop free): `python -u copilot/ai-server.py --no-llm` still speaks every hub alert;
driver questions then get a fixed answer. `--no-hub` runs the LLM part alone.

---

## Quick Start

With Docker (what `start.bat` / `start.sh` do): `docker compose up --build` in the repository root starts Ollama
(`ollama`), downloads the two models on the first start (`ollama-pull`, models kept in the `ollama` volume) and runs
this server as `copilot` on `ws://localhost:8766`, connected to the hub at `ws://server:8765`. The voice client stays
on the host (microphone, speakers): `copilot\.venv\Scripts\python -u voice_client.py --mic` (set up by
`scripts/setup-voice.bat`). The models are warmed up in the background and tried again every 10 s until Ollama has
them, so the hub's safety alerts work from the first second.

Without Docker:

1. Make sure Ollama is running locally with both models pulled:
   ```bash
   ollama pull qwen3:1.7b
   ollama pull qwen3:4b-instruct
   ```
2. Start the Co-Pilot WebSocket server (default `ws://localhost:8766`, so it does not collide with `server_simulator_lookingdir/server.py` on `8765`):
   ```bash
   python copilot/ai-server.py
   ```
   Optional environment variables: `COPILOT_HOST`, `COPILOT_PORT`, `OLLAMA_BASE`, `HUB_URL`, `COPILOT_S1_MODEL`,
   `COPILOT_S2_MODEL`, `LOG_LEVEL`. Options: `--no-llm`, `--hub URL`, `--no-hub`.
3. Start the voice: `python -u copilot/voice_client.py --mic` (or `--text` to type instead of speaking).

---

## Incoming JSON Protocol (Orchestrator -> `ai-server.py`)

Send one JSON object per WebSocket text frame.

### Routing (`mode`)
* `"urgent"` — Force System 1 (`qwen3:1.7b`, short alarm).
* `"info"` — Force System 2 (`qwen3:4b-instruct`, calm spoken check-in or answer).
* `"auto"` *(default)* — Automatically routes to `"urgent"` when `highest_severity` is `DANGER`/`CRITICAL`, an acute detection (`EYES_CLOSED`, `PHONE_USE`) is active, or `surroundings.urgent_hazard` is set; otherwise routes to `"info"`.

### Field Reference

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `request_id` | `string` | No | Correlation ID echoed back unchanged in the response. |
| `session_id` | `string` | No | Conversation session key (default `"default"`). |
| `mode` | `"urgent" \| "info" \| "auto"` | No | Tier selector (default `"auto"`). |
| `reset_history` | `bool` | No | Clears System 2 conversation history before answering. |
| `driver` | `object` | No | Normalized state from `driver_monitor` (`highest_severity`, `primary_detection`, `active_detections`, `face_detected`, `camera_status`, `eyes_state`, `eyes_state_for_s`, `gaze_zone`, `gaze_region`, `eyes_off_road_s`). |
| `driver_state` | `object` | No | Alternative: pass the raw `driver_state` JSON from `driver_monitor` directly; `ai-server.py` extracts the relevant fields automatically. |
| `surroundings` | `object` | No | Simulator state: `objects` (`[{object_id, type, x_px, y_px}]` matching `sim.py`), optional `urgent_hazard` string, optional `environment` string. |
| `vehicle` | `object` | No | Optional telemetry: `speed_kmh`, `speed_limit_kmh`, `gear`, `weather`, `local_time`, `battery_pct`, `nearest_rest_stop_km`, `nearest_charger_km`, `outside_temp_c`, `verified_news` (`list[str]`). Omit unknown fields—never send guesses. |
| `driver_message` | `string` | No | Spoken driver question/message (STT output) for System 2. |
| `situation` / `goal` | `string` | No | Optional explicit overrides (mainly for custom System 1 scenarios or proactive System 2 goals). |

---

### Example 1: Urgent Reflex (`mode: "urgent"` or `"auto"`)

**Send:**
```json
{
  "request_id": "urg-001",
  "mode": "auto",
  "driver": {
    "highest_severity": "DANGER",
    "primary_detection": "EYES_CLOSED",
    "active_detections": ["EYES_CLOSED"],
    "face_detected": true,
    "eyes_state": "CLOSED",
    "eyes_state_for_s": 2.4
  },
  "surroundings": {
    "objects": [{"object_id": "car-1", "type": "car", "x_px": 0, "y_px": 180}]
  },
  "vehicle": {
    "speed_kmh": 120,
    "weather": "rain"
  }
}
```

**Receive:**
```json
{
  "event": "copilot_response",
  "request_id": "urg-001",
  "session_id": "default",
  "mode": "urgent",
  "priority": "high",
  "model": "qwen3:1.7b",
  "spoken_text": "Open your eyes! Eyes on the road!",
  "latency_ms": 108.4,
  "ttft_ms": 43.2,
  "ttfc_ms": 61.5,
  "timestamp": 1763732520.123
}
```

### Example 2: Proactive Info Check-In (`mode: "info"`)

**Send:**
```json
{
  "request_id": "info-002",
  "mode": "info",
  "driver": {
    "face_detected": true,
    "eyes_state": "OPEN",
    "gaze_region": "front_center"
  },
  "vehicle": {
    "speed_kmh": 105,
    "weather": "clear"
  },
  "goal": "Briefly tell the driver the road ahead is clear and to keep the eyes on it."
}
```

### Example 3: Driver Question + Simulator Blind-Spot / Degraded Camera

**Send:**
```json
{
  "request_id": "q-003",
  "mode": "info",
  "driver": {
    "face_detected": false,
    "camera_status": "BLOCKED"
  },
  "vehicle": {
    "speed_kmh": 118,
    "weather": "clear"
  },
  "driver_message": "Can you see me? Am I looking at the road?"
}
```

---

## Outgoing JSON Protocol (`ai-server.py` -> Orchestrator / TTS)

| Field | Type | Description |
| :--- | :--- | :--- |
| `event` | `"copilot_response" \| "copilot_error"` | Message type. |
| `request_id` | `string \| null` | Echoed from the request. |
| `session_id` | `string` | Session ID used for S2 history & S1->S2 handoff. |
| `mode` | `"urgent" \| "info"` | Which tier handled the request. |
| `priority` | `"high" \| "normal"` | TTS queue hint: `"high"` can interrupt an ongoing `"normal"` playback. |
| `model` | `string` | Exact Ollama model that generated the text. |
| `spoken_text` | `string` | Sanitized, TTS-ready English speech (no em-dashes, no raw `km/h` / `%` / `°C`, no emojis). |
| `latency_ms` / `ttft_ms` / `ttfc_ms` | `float` | Live generation timing metrics in milliseconds. |

---

## Quick Local Smoke Test (Python)

Run while `python copilot/ai-server.py` is active:

```python
import asyncio, json, websockets

async def test():
    async with websockets.connect("ws://localhost:8766") as ws:
        await ws.send(json.dumps({
            "request_id": "t1",
            "mode": "urgent",
            "driver": {"primary_detection": "EYES_CLOSED", "eyes_state": "CLOSED", "eyes_state_for_s": 2.1},
            "vehicle": {"speed_kmh": 110, "weather": "rain"},
        }))
        await ws.send(json.dumps({
            "request_id": "t2",
            "mode": "info",
            "driver": {"face_detected": True, "eyes_state": "OPEN"},
            "vehicle": {"speed_kmh": 95, "battery_pct": 64, "weather": "clear"},
            "driver_message": "How fast am I going and how much battery is left?",
        }))
        print(await ws.recv())  # URGENT returns first (~110 ms)
        print(await ws.recv())  # INFO returns right after (~500 ms)

asyncio.run(test())
```