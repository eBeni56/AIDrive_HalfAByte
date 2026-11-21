"""A tiny WebSocket server that receives the driver_state stream - to check the connection without the simulator.

Usage (in a second terminal, before or after starting the monitor):
  uv run python tools/ws_receiver.py                 # listens on ws://localhost:8765 (the default url)
  uv run python tools/ws_receiver.py --port 9000
  uv run python tools/ws_receiver.py --full          # print every complete JSON message

Without --full it prints one summary line per second: messages/s, time, eyes, gaze zone, active detections.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time

from websockets.sync.server import ServerConnection, serve


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--full", action="store_true", help="print every message")
    args = p.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # show each line at once, also when piped to a file

    def handler(ws: ServerConnection) -> None:
        print(f"client connected: {ws.remote_address}")
        n, last = 0, time.monotonic()
        for raw in ws:
            msg = json.loads(raw)
            n += 1
            if args.full:
                print(json.dumps(msg, indent=2))
                continue
            now = time.monotonic()
            if now - last >= 1.0:
                active = ", ".join(msg.get("summary", {}).get("active", [])) or "-"
                print(
                    f"{n / (now - last):5.1f} msg/s | t {msg.get('t')} | eyes {msg.get('eyes', {}).get('state')}"
                    f" | gaze {msg.get('gaze', {}).get('zone')} | active: {active}"
                )
                n, last = 0, now
        print("client disconnected")

    with serve(handler, args.host, args.port) as server:
        print(f"listening on ws://{args.host}:{args.port} (Ctrl+C to stop)")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
