#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

fail() {
  printf '\nSetup stoppede på grund af en fejl.\n'
  printf 'Kopiér gerne de sidste linjer fra Terminal til den person, der hjælper med fejlen.\n'
}
trap fail ERR

find_python() {
  local candidate version major minor
  for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
      major="${version%%.*}"
      minor="${version#*.}"
      if [ "$major" = "3" ] && [ "$minor" -ge 11 ] && [ "$minor" -le 14 ]; then
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
  printf 'Installér en understøttet Python-version og kør setup.command igen.\n'
  exit 1
fi

PYTHON_VERSION="$($PYTHON_BIN -c 'import platform; print(platform.python_version())')"
printf 'Bruger %s (%s)\n' "$PYTHON_BIN" "$PYTHON_VERSION"

# A failed installation can leave a half-built environment behind. Recreating
# it is safe because all user data lives in data/, not in .venv/.
if [ -d .venv ]; then
  printf 'Fjerner tidligere virtuelt Python-miljø…\n'
  rm -rf .venv
fi

"$PYTHON_BIN" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --prefer-binary -r requirements.txt
python -m playwright install chromium
python -m pytest -q

touch .setup-complete
trap - ERR
printf '\nSetup færdig og selvtest bestået. Start med ./start.sh\n'
