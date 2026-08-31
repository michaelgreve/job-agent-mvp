#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then echo "Kør ./setup.sh først"; exit 1; fi
source .venv/bin/activate
python run.py &
PID=$!
sleep 1
python -m webbrowser http://127.0.0.1:8765 || true
wait $PID
