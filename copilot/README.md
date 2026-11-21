# Dual-Tier In-Car AI Co-Pilot Server (`copilot/ai-server.py`)

WebSocket microservice running two local Ollama models concurrently with the benchmark-winning **`P8_Production_Master`** prompts (audited across 992 turns on RTX 4060 8 GB).

## Runtime Configuration (Benchmark-Verified)

| Tier | Mode | Model | `num_ctx` | `temperature` | `num_predict` | `think` | Purpose |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **System 1** | `urgent` | `qwen3:1.7b` | `512` | `0.00` | `25` | `False` | Split-second spoken safety reflex (~110 ms, 0/30 critical fails) |
| **System 2** | `info` | `qwen3:4b-instruct` | `1024` | `0.45` | `95` | `False` | Calm, warm advisory check-ins & multi-turn driver Q&A (~227 ms TTFC) |

Both tiers run on **independent workers**: an `urgent` hazard never waits behind an in-flight `info` reply, and every `urgent` alert is logged into System 2's session memory (`S1 -> S2 handoff`).

---

## Quick Start

1. Make sure Ollama is running locally with both models pulled:
   ```bash
   ollama pull qwen3:1.7b
   ollama pull qwen3:4b-instruct
   ```
2. Start the Co-Pilot WebSocket server (default `ws://localhost:8766`, so it does not collide with `server_simulator_lookingdir/server.py` on `8765`):
   ```bash
   python copilot/ai-server.py
   ```
   Optional environment variables: `COPILOT_HOST`, `COPILOT_PORT`, `OLLAMA_BASE`, `LOG_LEVEL`.

---

## Incoming JSON Protocol (Orchestrator -> `ai-server.py`)

Send one JSON object per WebSocket text frame.

### Routing (`mode`)
* `"urgent"` — Force System 1 (`qwen3:1.7b`, short alarm).
* `"info"` — Force System 2 (`qwen3:4b-instruct`, calm spoken check-in or answer).
* `"auto"` *(default)* — Automatically routes to `"urgent"` when `highest_severity` is `DANGER`/`CRITICAL`, an acute detection (`MICROSLEEP`, `HEAD_FALLING`, `EYES_CLOSED`, `HEAD_NOD`) is active, or `surroundings.urgent_hazard` is set; otherwise routes to `"info"`.

### Field Reference

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `request_id` | `string` | No | Correlation ID echoed back unchanged in the response. |
| `session_id` | `string` | No | Conversation session key (default `"default"`). |
| `mode` | `"urgent" \| "info" \| "auto"` | No | Tier selector (default `"auto"`). |
| `reset_history` | `bool` | No | Clears System 2 conversation history before answering. |
| `driver` | `object` | No | Normalized state from `driver_monitor` (`highest_severity`, `primary_detection`, `active_detections`, `face_detected`, `camera_status`, `eyes_state`, `eyes_state_for_s`, `gaze_zone`, `gaze_region`, `eyes_off_road_s`, `drowsiness_level`). |
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
    "highest_severity": "CRITICAL",
    "primary_detection": "MICROSLEEP",
    "active_detections": ["MICROSLEEP"],
    "face_detected": true,
    "eyes_state": "CLOSED",
    "eyes_state_for_s": 2.4,
    "drowsiness_level": "DROWSY"
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
  "spoken_text": "Wake up! Eyes on the road now!",
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
    "drowsiness_level": "SLIGHTLY_DROWSY",
    "eyes_state": "OPEN",
    "gaze_region": "front_center",
    "active_detections": ["YAWN"]
  },
  "vehicle": {
    "speed_kmh": 105,
    "weather": "clear",
    "nearest_rest_stop_km": 4.2
  },
  "goal": "Gently check in after a yawn and mention the rest stop ahead."
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
  "driver_message": "Look at my face, do I look wide awake?"
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
            "driver": {"primary_detection": "MICROSLEEP", "eyes_state": "CLOSED", "eyes_state_for_s": 2.1},
            "vehicle": {"speed_kmh": 110, "weather": "rain"},
        }))
        await ws.send(json.dumps({
            "request_id": "t2",
            "mode": "info",
            "driver": {"face_detected": True, "drowsiness_level": "SLIGHTLY_DROWSY"},
            "vehicle": {"speed_kmh": 95, "battery_pct": 64, "weather": "clear"},
            "driver_message": "How fast am I going and how much battery is left?",
        }))
        print(await ws.recv())  # URGENT returns first (~110 ms)
        print(await ws.recv())  # INFO returns right after (~500 ms)

asyncio.run(test())
```