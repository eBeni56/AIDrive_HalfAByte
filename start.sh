#!/bin/sh
# Starts everything with Docker (like "docker compose up") and opens the dashboard and the simulator in the
# browser as soon as they answer. Ctrl+C stops everything, as usual.
# Extra arguments go to "docker compose up", e.g.   ./start.sh --build
cd "$(dirname "$0")" || exit 1

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
exec docker compose up "$@"
