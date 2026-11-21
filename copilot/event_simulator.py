"""
Interactive Driving Event Simulator (9 Named Presets -> Live S1/S2 Alerts)
===========================================================================
Connects to copilot/ai-server.py (ws://localhost:8766) and injects realistic
driver_monitor + simulator JSON payloads with broadcast=True so voice_client.py
speaks the urgent System 1 reflex (or proactive System 2 check-in) immediately.

For trying the LLM wording by hand. In the full system the hub server (server_simulator_lookingdir)
decides the alerts itself from the camera and the simulator and sends them to ai-server.py.

Usage (in a 3rd terminal while ai-server.py and voice_client.py are running):
  python -u copilot/event_simulator.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

from websockets.asyncio.client import connect

DEFAULT_WS_URL = "ws://localhost:8766"
DEFAULT_SESSION_ID = "driver-main"

PRESETS: dict[str, dict[str, Any]] = {
    "1": {
        "name": "Eyes Closed (2.4s)",
        "desc": "S1 URGENT | Driver's eyes fully closed for 2.4s at 120 km/h in rain, car ahead",
        "payload": {
            "mode": "urgent",
            "driver": {
                "highest_severity": "DANGER",
                "primary_detection": "EYES_CLOSED",
                "active_detections": ["EYES_CLOSED"],
                "face_detected": True,
                "eyes_state": "CLOSED",
                "eyes_state_for_s": 2.4,
            },
            "surroundings": {
                "objects": [{"object_id": "car-1", "type": "car", "x_px": 0, "y_px": 140}],
            },
            "vehicle": {"speed_kmh": 120, "weather": "rain"},
        },
    },
    "2": {
        "name": "Didn't Look Left — Blind Spot Car",
        "desc": "S1 URGENT | Merging/turning left without checking left mirror, fast car in left blind spot",
        "payload": {
            "mode": "urgent",
            "situation": "Driver did not check left mirror and is drifting left while a car is directly in the left blind spot at 95 km/h.",
            "goal": "Stop the driver from merging left and warn of the vehicle in the left blind spot.",
            "driver": {
                "highest_severity": "DANGER",
                "primary_detection": "DISTRACTED",
                "gaze_region": "front_right",
                "eyes_state": "OPEN",
            },
            "surroundings": {
                "objects": [{"object_id": "car-left-1", "type": "car", "x_px": -90, "y_px": -10}],
                "urgent_hazard": "Car approaching in left blind spot",
            },
            "vehicle": {"speed_kmh": 95},
        },
    },
    "3": {
        "name": "Didn't Look Right — Cyclist in Right Turn",
        "desc": "S1 URGENT | Turning right without looking right while a cyclist is alongside on the right",
        "payload": {
            "mode": "urgent",
            "situation": "Driver did not look right before turning right; a cyclist is directly alongside on the right at 30 km/h.",
            "goal": "Stop the right turn immediately and warn about the cyclist on the right.",
            "driver": {
                "highest_severity": "DANGER",
                "gaze_region": "front_left",
                "eyes_state": "OPEN",
            },
            "surroundings": {
                "objects": [{"object_id": "bike-1", "type": "bicycle", "x_px": 75, "y_px": 15}],
                "urgent_hazard": "Cyclist in right blind spot during right turn",
            },
            "vehicle": {"speed_kmh": 30},
        },
    },
    "4": {
        "name": "Looking Down at Phone (2.5s)",
        "desc": "S1 URGENT | Driver looking DOWN for 2.5s at 90 km/h with slow truck ahead",
        "payload": {
            "mode": "urgent",
            "driver": {
                "highest_severity": "DANGER",
                "primary_detection": "DISTRACTED",
                "active_detections": ["DISTRACTED"],
                "face_detected": True,
                "eyes_state": "OPEN",
                "gaze_zone": "DOWN",
                "eyes_off_road_s": 2.5,
                "glance_down_s": 2.5,
            },
            "surroundings": {
                "objects": [{"object_id": "truck-1", "type": "truck", "x_px": 0, "y_px": 110}],
            },
            "vehicle": {"speed_kmh": 90},
        },
    },
    "5": {
        "name": "Phone in Hand (3.2s)",
        "desc": "S1 URGENT | Driver holding the phone and looking at it at 80 km/h",
        "payload": {
            "mode": "urgent",
            "driver": {
                "highest_severity": "DANGER",
                "primary_detection": "PHONE_USE",
                "active_detections": ["PHONE_USE", "DISTRACTED"],
                "face_detected": True,
                "eyes_state": "OPEN",
                "gaze_zone": "DOWN",
                "eyes_off_road_s": 3.2,
            },
            "vehicle": {"speed_kmh": 80},
        },
    },
    "6": {
        "name": "Pedestrian Ahead While Looking Away",
        "desc": "S1 URGENT | Pedestrian stepping into crosswalk ahead while driver looks right",
        "payload": {
            "mode": "urgent",
            "situation": "Pedestrian crossing directly ahead while driver is looking to the right side at 48 km/h.",
            "goal": "Urgently warn the driver to look forward and brake for the pedestrian.",
            "driver": {
                "highest_severity": "DANGER",
                "primary_detection": "DISTRACTED",
                "gaze_zone": "RIGHT_SIDE",
                "eyes_off_road_s": 2.1,
            },
            "surroundings": {
                "objects": [{"object_id": "ped-1", "type": "pedestrian", "x_px": 10, "y_px": 95}],
                "urgent_hazard": "Pedestrian directly ahead",
            },
            "vehicle": {"speed_kmh": 48},
        },
    },
    "7": {
        "name": "Black Ice / Hydroplaning Hazard",
        "desc": "S1 URGENT | Standing water / black ice at 105 km/h (physics check: no hard braking!)",
        "payload": {
            "mode": "urgent",
            "situation": "Vehicle entering deep standing water and black ice patch at 105 km/h.",
            "goal": "Warn the driver to hold the wheel steady and NOT brake hard.",
            "vehicle": {"speed_kmh": 105, "weather": "heavy rain and ice"},
        },
    },
    "8": {
        "name": "Driver Question (S2)",
        "desc": "S2 INFO   | The driver asks how fast they are going (calm answer; try #1 to interrupt it)",
        "payload": {
            "mode": "info",
            "allow_tools": False,
            "driver": {"face_detected": True, "eyes_state": "OPEN", "gaze_zone": "ROAD"},
            "vehicle": {"speed_kmh": 95, "weather": "clear"},
            "driver_message": "How fast am I going?",
        },
    },
    "9": {
        "name": "Lane-Keep / ADAS Disengaged in Curve",
        "desc": "S1 URGENT | Lane-keep assist disengaged suddenly at 115 km/h",
        "payload": {
            "mode": "urgent",
            "situation": "Lane-keep assist just disengaged mid-curve on highway at 115 km/h.",
            "goal": "Command the driver to grab the steering wheel and take manual control immediately.",
            "vehicle": {"speed_kmh": 115},
        },
    },
}


def print_menu() -> None:
    print(f"\n================ DRIVING EVENT SIMULATOR (1-{len(PRESETS)}) ================")
    for key in PRESETS:
        p = PRESETS[key]
        print(f"  [{key:>2}] {p['name']}")
        print(f"       {p['desc']}")
    print("  [ m] Show menu again | [ q] Quit")
    print("================================================================\n", flush=True)


async def run_simulator(ws_url: str, session_id: str) -> None:
    loop = asyncio.get_running_loop()
    print(f"Connecting to {ws_url} (session={session_id})...", flush=True)
    async with connect(ws_url) as ws:
        print("Connected! Make sure voice_client.py is running to hear automatic alerts.", flush=True)
        print_menu()
        counter = 0
        while True:
            choice = (await loop.run_in_executor(None, input, f"Trigger event [1-{len(PRESETS)}, m, q]> ")).strip().lower()
            if not choice:
                continue
            if choice == "q":
                break
            if choice == "m":
                print_menu()
                continue
            if choice not in PRESETS:
                print(f"Pick a number from 1 to {len(PRESETS)} (or 'm' for menu, 'q' to quit).", flush=True)
                continue

            preset = PRESETS[choice]
            counter += 1
            payload = {
                **preset["payload"],
                "request_id": f"sim-{counter}",
                "session_id": session_id,
                "preset_name": preset["name"],
                "broadcast": True,
            }

            t0 = time.perf_counter()
            await ws.send(json.dumps(payload, ensure_ascii=False))
            reply = json.loads(await ws.recv())
            dt_ms = (time.perf_counter() - t0) * 1000.0
            print(
                f"-> SENT [{choice}] {preset['name']}\n"
                f"<- AI [{str(reply.get('mode', '-')).upper()} | {reply.get('model')} | "
                f"llm={reply.get('latency_ms')}ms | total={dt_ms:.0f}ms]: "
                f"{reply.get('spoken_text')}\n",
                flush=True,
            )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description="Interactive driving event trigger for ai-server.py")
    parser.add_argument("--ws", default=DEFAULT_WS_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION_ID)
    args = parser.parse_args()
    try:
        asyncio.run(run_simulator(args.ws, args.session))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()