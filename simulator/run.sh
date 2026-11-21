#!/bin/sh
# Sets up .venv (first run only), opens the simulator in the browser, starts the test server.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv ..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q -r requirements.txt
if [ "$(uname)" = "Darwin" ]; then open index.html
elif command -v xdg-open >/dev/null 2>&1; then xdg-open index.html >/dev/null 2>&1 &
else echo "Open index.html in your browser."
fi
exec .venv/bin/python test_server.py
