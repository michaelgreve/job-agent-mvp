#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

fail() {
  printf '\nSetup stoppede på grund af en fejl.\n'
  printf 'Kopiér gerne de sidste linjer fra Terminal til den person, der hjælper med fejlen.\n'
}
trap fail ERR

find_python() {
  local candidate
  for candidate in python3.14 python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(not ((3, 11) <= sys.version_info[:2] < (3, 15)))' >/dev/null 2>&1; then
        printf '%s' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  printf 'Jobagenten kræver Python 3.11–3.14.\n'
  printf 'Installér en understøttet Python-version, sørg for at den er på PATH, og kør ./setup.sh igen.\n'
  printf 'På Windows kan du også bruge .\\setup.ps1 i PowerShell.\n'
  exit 1
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import platform; print(platform.python_version())' | tr -d '\r')"
printf 'Bruger %s (%s)\n' "$PYTHON_BIN" "$PYTHON_VERSION"

# A failed installation can leave a half-built environment behind. Recreating
# it is safe because all user data lives in data/, not in .venv/.
if [ -d .venv ]; then
  printf 'Fjerner tidligere virtuelt Python-miljø…\n'
  rm -rf .venv
fi

"$PYTHON_BIN" -m venv .venv
VENV_PYTHON=.venv/bin/python
if [ -f .venv/Scripts/python.exe ]; then
  VENV_PYTHON=.venv/Scripts/python.exe
fi
"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel
"$VENV_PYTHON" -m pip install --prefer-binary -r requirements.txt
"$VENV_PYTHON" -m playwright install chromium
"$VENV_PYTHON" -m pytest -q

touch .setup-complete
trap - ERR
printf '\nSetup færdig og selvtest bestået. Start med ./start.sh\n'
