#!/bin/bash
# Stop all Token Saver / aiproxy processes for this project. Invoked on Dock quit.
set -uo pipefail

RES="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="${HOME}/Library/Application Support/TokenSaver"
PIDFILE="${SUPPORT}/proxy.pid"

ROOT=""
if [[ -f "$RES/ProjectRoot" ]]; then
  ROOT="$(cat "$RES/ProjectRoot" | sed -e 's/[[:space:]]*$//')"
elif [[ -f "${SUPPORT}/project_root" ]]; then
  ROOT="$(cat "${SUPPORT}/project_root" | sed -e 's/[[:space:]]*$//')"
fi

_kill_pid() {
  local pid="$1"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 0
  kill -TERM "$pid" 2>/dev/null || true
  # Children (if any)
  pkill -TERM -P "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.15
  done
  kill -KILL "$pid" 2>/dev/null || true
  pkill -KILL -P "$pid" 2>/dev/null || true
}

if [[ -f "$PIDFILE" ]]; then
  _kill_pid "$(cat "$PIDFILE" 2>/dev/null | tr -d '[:space:]')"
  rm -f "$PIDFILE"
fi

# Belt-and-suspenders: anything still running from this project's venv / config.
if [[ -n "$ROOT" ]]; then
  # python -m aiproxy for this checkout
  pkill -TERM -f "${ROOT}/.venv/bin/python -m aiproxy" 2>/dev/null || true
  pkill -TERM -f "${ROOT}/.venv/bin/python3 -m aiproxy" 2>/dev/null || true
  # mitmdump addon path for this checkout
  pkill -TERM -f "mitmdump.*${ROOT}/aiproxy/mitm_entry" 2>/dev/null || true
  pkill -TERM -f "AIPROXY_CONFIG=${ROOT}/config.yaml" 2>/dev/null || true
  sleep 0.25
  pkill -KILL -f "${ROOT}/.venv/bin/python -m aiproxy" 2>/dev/null || true
  pkill -KILL -f "${ROOT}/.venv/bin/python3 -m aiproxy" 2>/dev/null || true
  pkill -KILL -f "mitmdump.*${ROOT}/aiproxy/mitm_entry" 2>/dev/null || true
fi

# Generic leftovers on default ports from aiproxy label (best-effort, loopback only).
# Do NOT touch the tokensaver.local LaunchDaemon (port_alias) — that stays installed.

if [[ "${STOP_QUIET:-0}" != "1" ]]; then
  osascript -e 'display notification "Proxy stopped" with title "Token Saver"' >/dev/null 2>&1 || true
fi
exit 0
