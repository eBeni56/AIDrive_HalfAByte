"""Minimal test WebSocket server for simulator.html.

Install:  pip install websockets
Run:      python test_server.py
Listens on ws://localhost:8765, prints every received event, replies {"status":"ok"}.
"""
import asyncio
import json

import websockets

HOST, PORT = "localhost", 8765


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
