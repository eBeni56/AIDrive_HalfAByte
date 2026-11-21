# Simulator server

WebSocket server that receives the driving simulator's messages (`simulation_started`,
`simulation_tick` at 10 Hz, `simulation_ended`), validates and parses them, and keeps the current
state in `world` (`server.py`): whether a run is active and the objects visible right now, each
with `object_id`, `type`, `x_px`, `y_px`.

A malformed message is logged as a warning and ignored; the connection stays open. The server
never replies.

## Run

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows (non-MSYS2): .venv\Scripts\pip
.venv/bin/python server.py                       # Windows (non-MSYS2): .venv\Scripts\python
```

Listens on `ws://localhost:8765`. Don't run it together with the simulator's own `test_server.py`
(same port).

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `localhost` | Interface to listen on. Use `0.0.0.0` in Docker. |
| `PORT` | `8765` | Port. The simulator's `WS_URL` must match. |
| `LOG_LEVEL` | `INFO` | `DEBUG` also prints every tick. |
