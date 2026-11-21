"""Quick test & interactive client for copilot/ai-server.py (ws://localhost:8766)."""

import asyncio
import json
import time
from websockets.asyncio.client import connect

URL = "ws://localhost:8766"


async def run_automated_checks(ws) -> None:
    print("\n=== 1. PARHUZAMOS TESZT (INFO + URGENT egyszerre) ===")
    t0 = time.perf_counter()

    # Eloszor a lassabb INFO-t kuldjuk, kozvetlenul utana a gyors URGENT-et
    await ws.send(json.dumps({
        "request_id": "par-info",
        "session_id": "demo",
        "mode": "info",
        "driver": {"face_detected": True, "eyes_state": "OPEN"},
        "vehicle": {"speed_kmh": 110, "battery_pct": 64, "weather": "rain"},
        "driver_message": "How fast am I going and how much battery do I have left?",
    }))
    await ws.send(json.dumps({
        "request_id": "par-urgent",
        "session_id": "demo",
        "mode": "auto",  # auto-routing -> EYES_CLOSED miatt urgent lesz
        "driver": {
            "highest_severity": "DANGER",
            "primary_detection": "EYES_CLOSED",
            "active_detections": ["EYES_CLOSED"],
            "eyes_state": "CLOSED",
            "eyes_state_for_s": 2.3,
        },
        "surroundings": {
            "objects": [{"object_id": "car-1", "type": "car", "x_px": 0, "y_px": 150}]
        },
        "vehicle": {"speed_kmh": 120, "weather": "rain"},
    }))

    for _ in range(2):
        reply = json.loads(await ws.recv())
        dt_ms = (time.perf_counter() - t0) * 1000
        print(
            f"[{dt_ms:6.0f} ms] mode={reply['mode']:6s} | model={reply['model']:17s} | "
            f"llm={reply['latency_ms']}ms | TTS: {reply['spoken_text']}"
        )

    print("\n=== 2. S1 -> S2 HANDOFF TESZT (emlekszik-e a csukott szemre) ===")
    await ws.send(json.dumps({
        "request_id": "handoff-1",
        "session_id": "demo",
        "mode": "info",
        "driver": {"face_detected": True, "eyes_state": "OPEN"},
        "vehicle": {"speed_kmh": 100, "weather": "rain"},
        "driver_message": "Sorry, I closed my eyes for a second. Why did you shout?",
    }))
    reply = json.loads(await ws.recv())
    print(f"mode={reply['mode']} | llm={reply['latency_ms']}ms | TTS: {reply['spoken_text']}")


async def run_interactive(ws) -> None:
    print("\n=== 3. INTERAKTIV MOD ===")
    print("Irj be egy uzenetet az S2-nek, vagy '!' elejevel S1 veszhelyzetet (pl: !black ice ahead).")
    print("Kilepes: q\n")
    loop = asyncio.get_running_loop()
    i = 1
    while True:
        text = (await loop.run_in_executor(None, input, "Te> ")).strip()
        if not text or text.lower() == "q":
            break
        if text.startswith("!"):
            payload = {
                "request_id": f"live-{i}",
                "session_id": "demo",
                "mode": "urgent",
                "situation": text[1:].strip(),
                "goal": "Give the immediate safe driving reflex.",
            }
        else:
            payload = {
                "request_id": f"live-{i}",
                "session_id": "demo",
                "mode": "info",
                "driver": {"face_detected": True, "eyes_state": "OPEN"},
                "vehicle": {"speed_kmh": 95, "battery_pct": 58, "weather": "clear"},
                "driver_message": text,
            }
        i += 1
        await ws.send(json.dumps(payload))
        reply = json.loads(await ws.recv())
        print(f"AI [{reply['mode'].upper()} | {reply['latency_ms']}ms]: {reply['spoken_text']}\n")


async def main() -> None:
    async with connect(URL) as ws:
        await run_automated_checks(ws)
        await run_interactive(ws)


if __name__ == "__main__":
    asyncio.run(main())