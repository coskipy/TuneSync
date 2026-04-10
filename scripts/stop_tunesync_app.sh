#!/usr/bin/env bash
set -euo pipefail

PID_FILE="/tmp/tunesync_app.pid"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" || true)"
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 0.2
  fi
  rm -f "$PID_FILE" || true
fi

pkill -f -- "-m tunesync_app" 2>/dev/null || true

echo "TuneSync stopped."
