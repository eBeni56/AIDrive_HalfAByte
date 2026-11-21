# scripts

Helpers of `start.bat` (Windows) in the repository root. You don't need to run them yourself; `start.sh`
(macOS / Linux) does the same steps inline.

| Script | What it does |
|---|---|
| `setup-driver-monitor.bat` | Prepares the driver monitor on this PC and sets `DM_RUN`, the command `start.bat` starts it with. Uses `uv` if it is installed; otherwise creates `driver_monitor\.venv` with Python 3.12 (`py -3.12`) on the first run (a few minutes). Downloads the two model files into `driver_monitor\models\` if one is missing. Exit code 1 (with instructions) when neither uv nor Python 3.12 is found. |
| `open-pages.bat` | Waits until the dashboard and the simulator answer (up to 3 minutes: the first Docker start can take a while), then opens each in its own browser window (Chrome or Edge with `--new-window`; any other default browser opens them as tabs). |

`open-pages.bat` takes the ports from the environment variables `DASHBOARD_PORT` / `SIMULATOR_PORT`, else from the
root `.env`, else the defaults 8081 / 8080. With `PRINT_ONLY=1` it only prints what it would open (for testing).

`start.bat` itself: stops a driver monitor left over from an earlier start (only one program can use the
camera), runs `setup-driver-monitor.bat`, starts the driver monitor minimised in a window titled
"driver monitor" (with `DM_ARGS`, e.g. `set DM_ARGS=--camera 0`), starts `open-pages.bat` in the background, then
`docker compose up` with its own arguments (e.g. `--build`). When Docker stops, the driver monitor is stopped too.
