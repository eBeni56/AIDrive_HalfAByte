#!/bin/sh
# ONE COMMAND FOR EVERYTHING:  ./start.sh   (after a code change:  ./start.sh --build)
#   - server, simulator, dashboard, ollama, ollama-pull (downloads the language models on the first start) and
#     copilot (the AI): Docker ("docker compose up", logs here, Ctrl+C stops everything). When an NVIDIA GPU is
#     found (nvidia-smi), compose.gpu.yml is added so Ollama runs on it; OLLAMA_GPU=0 / 1 forces it off / on.
#   - driver monitor (webcam): on this computer in the background (Docker Desktop can't reach a webcam);
#     its camera image with the markings is shown on the dashboard
#   - voice client (microphone + speakers): on this computer in the background, log voice_client.log; it speaks
#     the copilot's alerts and answers "Hey Jarvis ...". The first start sets it up in copilot/.venv.
#     VOICE_ARGS goes to it (default --mic), NO_VOICE=1 skips it.
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

# --- voice client: copilot/.venv (first run only); without it the dashboard still shows every alert
VC_PID=
if [ "${NO_VOICE:-}" != "1" ]; then
  cd copilot || exit 1
  if [ ! -x .venv/bin/python ]; then
    echo "Setting up the voice client in copilot/.venv - first run only, takes a few minutes ..."
    REQS="-r requirements.txt -r ../speech-recognition/requirements.txt -r ../text-to-speech/requirements.txt"
    # shellcheck disable=SC2086  # REQS is meant to be split into words
    if command -v uv >/dev/null 2>&1; then
      uv venv -q --python 3.12 .venv && uv pip install -q --python .venv/bin/python $REQS
    else
      python3 -m venv .venv && .venv/bin/python -m pip install -q --disable-pip-version-check $REQS
    fi || { echo "The voice client could not be set up; going on without the voice."; rm -rf .venv; }
  fi
  if [ -x .venv/bin/python ]; then
    # shellcheck disable=SC2086  # VOICE_ARGS is meant to be split into words
    .venv/bin/python -u voice_client.py ${VOICE_ARGS:---mic} > "$ROOT/voice_client.log" 2>&1 &
    VC_PID=$!
    echo "Voice client running in the background, log: voice_client.log"
  fi
  cd "$ROOT" || exit 1
fi
trap 'kill $DM_PID $VC_PID 2>/dev/null' EXIT INT TERM

# --- Ollama on the NVIDIA GPU if there is one
if [ -z "${OLLAMA_GPU:-}" ]; then
  if command -v nvidia-smi >/dev/null 2>&1; then OLLAMA_GPU=1; else OLLAMA_GPU=0; fi
fi
COMPOSE_FILES="-f compose.yml"
if [ "$OLLAMA_GPU" = "1" ]; then
  COMPOSE_FILES="-f compose.yml -f compose.gpu.yml"
  echo "Ollama runs on the NVIDIA GPU."
else
  echo "Ollama runs on the CPU: answers to questions are slow - the safety alerts are not affected."
fi

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

# shellcheck disable=SC2086  # COMPOSE_FILES is meant to be split into words
docker compose $COMPOSE_FILES up "$@"
