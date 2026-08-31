#!/usr/bin/env bash
cd "$(dirname "$0")"
if ./setup.sh; then
  printf '\nAlt er klar. Dobbeltklik nu på start.command.\n'
else
  printf '\nSetup blev ikke færdig. Fejlteksten står ovenfor.\n'
fi
read -r -p "Tryk Enter for at lukke…"
