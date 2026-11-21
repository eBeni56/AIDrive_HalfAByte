"""Minimal test WebSocket server for the simulator (index.html).

Install:  pip install -r requirements.txt
Run:      python test_server.py
Listens on ws://localhost:8765, prints every received event, replies {"status":"ok"}.
The port can be changed with the PORT environment variable; WS_URL in js/config.js must then match.
"""
import asyncio
import json
import os

import websockets

HOST, PORT = "localhost", int(os.environ.get("PORT", "8765"))


async def handler(ws):
    print("client connected")
    try:
        async for message in ws:
            try:
                print(json.dumps(json.loads(message)))
            except json.JSONDecodeError:
                print("non-JSON message:", message)
            await ws.send(json.dumps({"status": "ok"}))
    except websockets.ConnectionClosed:
        pass
    print("client disconnected")


async def main():
    async with websockets.serve(handler, HOST, PORT):
        print(f"listening on ws://{HOST}:{PORT}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
