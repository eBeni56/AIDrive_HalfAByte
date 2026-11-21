#!/bin/sh
# ONE COMMAND FOR EVERYTHING:  ./start.sh   (after a code change:  ./start.sh --build)
#   - server, simulator, dashboard: Docker ("docker compose up", logs here, Ctrl+C stops everything)
#   - driver monitor (webcam): on this computer in the background (Docker Desktop can't reach a webcam);
#     its camera image with the markings is shown on the dashboard
#   - the dashboard and the simulator open in the browser as soon as they answer
# Extra arguments go to "docker compose up". DM_ARGS goes to the driver monitor, e.g.  DM_ARGS="--camera 0" ./start.sh
cd "$(dirname "$0")" || exit 1
ROOT=$(pwd)

# --- driver monitor: uv if installed (as in driver_monitor/README.md), else a plain venv (first run only)
cd driver_monitor || exit 1
if command -v uv >/dev/null 2>&1; then
  DM_RUN="uv run python -m driver_monitor"
  { [ -f models/face_landmarker.task ] && [ -f models/efficientdet_lite0.tflite ]; } || uv run python tools/download_model.py || exit 1
else
  if [ ! -x .venv/bin/python ]; then
    echo "Setting up the driver monitor in driver_monitor/.venv - first run only, takes a few minutes ..."
    python3 -m venv .venv && .venv/bin/python -m pip install -q --disable-pip-version-check -e . || {
      echo "The driver monitor needs uv (https://docs.astral.sh/uv/) or Python 3.9 - 3.12."; exit 1; }
  fi
  DM_RUN=".venv/bin/python -m driver_monitor"
  { [ -f models/face_landmarker.task ] && [ -f models/efficientdet_lite0.tflite ]; } || .venv/bin/python tools/download_model.py || exit 1
fi
# shellcheck disable=SC2086  # DM_RUN and DM_ARGS are meant to be split into words
$DM_RUN --no-window --scroll $DM_ARGS > "$ROOT/driver_monitor.log" 2>&1 &
DM_PID=$!
cd "$ROOT" || exit 1
echo "Driver monitor running in the background, log: driver_monitor.log"
trap 'kill $DM_PID 2>/dev/null' EXIT INT TERM

# --- open the pages once they answer
port() {  # environment variable, else the root .env, else the default
  eval "v=\${$1:-}"
  [ -z "$v" ] && [ -f .env ] && v=$(sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p" .env | head -n 1)
  echo "${v:-$2}"
}
DASHBOARD_URL="http://localhost:$(port DASHBOARD_PORT 8081)/"
SIMULATOR_URL="http://localhost:$(port SIMULATOR_PORT 8080)/"

open_pages() {
  for url in "$DASHBOARD_URL" "$SIMULATOR_URL"; do
    i=0
    until curl -fs -o /dev/null "$url"; do
      i=$((i + 1))
      if [ "$i" -ge 360 ]; then echo "open-pages: $url did not answer, open it by hand."; return; fi
      sleep 0.5
    done
  done
  for url in "$DASHBOARD_URL" "$SIMULATOR_URL"; do
    if [ "$(uname)" = "Darwin" ]; then open "$url"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1
    else echo "Open $url in your browser."
    fi
  done
}
open_pages &

docker compose up "$@"
