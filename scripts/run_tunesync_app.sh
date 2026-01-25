#!/usr/bin/env bash
set -euo pipefail

# Resolve repo root (this script lives in scripts/)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="$REPO_ROOT/.venv/bin/python"
LOG_FILE="/tmp/tunesync_app.log"
PID_FILE="/tmp/tunesync_app.pid"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: Python venv not found/executable at $PY" >&2
  echo "Run: python3 setup.py (or create .venv manually)" >&2
  exit 1
fi

# Stop any existing instance (PID file first; then fall back to pkill)
if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.2
  fi
  rm -f "$PID_FILE" || true
fi

pkill -f -- "-m tunesync_app" 2>/dev/null || true

# Start detached; use -u for unbuffered logs
# shellcheck disable=SC2091
nohup "$PY" -u -m tunesync_app >"$LOG_FILE" 2>&1 &
new_pid=$!
echo "$new_pid" > "$PID_FILE"

# Verify it actually started
sleep 0.3
if ! kill -0 "$new_pid" 2>/dev/null; then
  echo "ERROR: TuneSync failed to start (pid=$new_pid)." >&2
  if [[ -f "$LOG_FILE" ]]; then
    echo "--- Last 80 lines of $LOG_FILE ---" >&2
    tail -80 "$LOG_FILE" >&2 || true
  fi
  exit 2
fi

echo "TuneSync started (pid=$new_pid). Logs: $LOG_FILE"
