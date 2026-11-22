#!/bin/sh
# ONE COMMAND FOR EVERYTHING:  ./start.sh   (after a code change:  ./start.sh --build)
#   - server, simulator, dashboard: Docker ("docker compose up", logs here, Ctrl+C stops everything)
#   - driver monitor (webcam): on this computer in the background (Docker Desktop can't reach a webcam);
#     its camera image with the markings is shown on the dashboard
#   - safety_ai (speaks the warnings and the LLM coach's tips / debrief): on this computer in the background
#   - the dashboard and the simulator open in the browser as soon as they answer
# Extra arguments go to "docker compose up". DM_ARGS goes to the driver monitor, e.g.  DM_ARGS="--camera 0" ./start.sh
cd "$(dirname "$0")" || exit 1
ROOT=$(pwd)

# --- driver monitor: uv if installed (as in driver_monitor/README.md), else a plain venv (first run only)
cd driver_monitor || exit 1
if command -v uv >/dev/null 2>&1; then
  DM_PY="uv run python"
else
  if [ ! -x .venv/bin/python ]; then
    echo "Setting up the driver monitor in driver_monitor/.venv - first run only, takes a few minutes ..."
    PY=$(command -v python3.12 || command -v python3)   # the driver monitor needs Python 3.12
    "$PY" -m venv .venv && .venv/bin/python -m pip install -q --disable-pip-version-check -e . || {
      echo "The driver monitor needs uv (https://docs.astral.sh/uv/) or Python 3.12."; exit 1; }
  fi
  DM_PY=".venv/bin/python"
fi
DM_RUN="$DM_PY -m driver_monitor"
# face_landmarker.task (face) and efficientdet_lite0.tflite (phone): downloads only what is missing
# shellcheck disable=SC2086  # DM_PY is meant to be split into words ("uv run python")
{ [ -f models/face_landmarker.task ] && [ -f models/efficientdet_lite0.tflite ]; } || $DM_PY tools/download_model.py || exit 1
# shellcheck disable=SC2086  # DM_RUN and DM_ARGS are meant to be split into words
$DM_RUN --no-window --scroll $DM_ARGS > "$ROOT/driver_monitor.log" 2>&1 &
DM_PID=$!
cd "$ROOT" || exit 1
echo "Driver monitor running in the background, log: driver_monitor.log"

# --- safety_ai: speaks the warnings and the LLM coach (Docker can't reach the speakers). NO_SAFETY_AI=1 skips it,
#     SAI_ARGS goes to it (e.g. SAI_ARGS="--mute" or "--no-llm" or "--voice male_1")
SAI_PID=
if [ "${NO_SAFETY_AI:-}" != "1" ]; then
  cd safety_ai || exit 1
  if [ ! -x .venv/bin/python ]; then
    echo "Setting up safety_ai in safety_ai/.venv - first run only, takes a minute ..."
    if command -v uv >/dev/null 2>&1; then
      uv venv --python 3.12 .venv -q && uv pip install --python .venv/bin/python -q -r requirements.txt
    else
      PY=$(command -v python3.12 || command -v python3)
      "$PY" -m venv .venv && .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
    fi || { echo "safety_ai could not be set up: running without the voice."; rm -rf .venv; }
  fi
  if [ -x .venv/bin/python ]; then
    # shellcheck disable=SC2086  # SAI_ARGS is meant to be split into words
    .venv/bin/python main.py $SAI_ARGS > "$ROOT/safety_ai.log" 2>&1 &
    SAI_PID=$!
    echo "safety_ai running in the background, log: safety_ai.log"
  fi
  cd "$ROOT" || exit 1
fi
trap 'kill $DM_PID $SAI_PID 2>/dev/null' EXIT INT TERM

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
