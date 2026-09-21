#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then echo "Kør ./setup.sh først"; exit 1; fi
VENV_PYTHON=.venv/bin/python
if [ -f .venv/Scripts/python.exe ]; then
  VENV_PYTHON=.venv/Scripts/python.exe
fi
"$VENV_PYTHON" run.py &
PID=$!
sleep 1
"$VENV_PYTHON" -m webbrowser http://127.0.0.1:8765 || true
wait $PID
