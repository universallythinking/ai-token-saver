#!/bin/bash
# Start aiproxy for the Token Saver.app bundle. Invoked on app launch.
# Does NOT kill a healthy existing proxy (avoids breaking Cursor mid-session).
set -euo pipefail

RES="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="${HOME}/Library/Application Support/TokenSaver"
LOG_DIR="${HOME}/Library/Logs/TokenSaver"
PIDFILE="${SUPPORT}/proxy.pid"
mkdir -p "$SUPPORT" "$LOG_DIR"

if [[ -f "$RES/ProjectRoot" ]]; then
  ROOT="$(cat "$RES/ProjectRoot" | sed -e 's/[[:space:]]*$//')"
else
  ROOT="$(cd "$RES/../../.." && pwd)"
fi

if [[ ! -d "$ROOT" || ! -f "$ROOT/config.yaml" ]]; then
  osascript -e "display dialog \"Token Saver cannot find the project at:\n${ROOT:-?}\n\nDouble-click Install to Applications (or install-to-applications.command) in the project folder.\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  exit 1
fi

cd "$ROOT"

DIRECT_URL="http://127.0.0.1:8081/"
SETUP_URL="http://127.0.0.1:8081/?setup=1"
OPEN_DASH="${RES}/open-dashboard.sh"

_open_dashboard() {
  local mode="${1:-focus}" # focus = reuse tab; setup = first-launch URL
  # Always prefer loopback:8081 — tokensaver.local needs the :80 alias service.
  if [[ "$mode" == "focus" && -x "$OPEN_DASH" ]]; then
    # Reuse matching tab; open-dashboard opens a new tab if none match.
    TOKENSAVER_DASH_URL="$DIRECT_URL" bash "$OPEN_DASH" || true
  elif [[ "$mode" == "setup" ]]; then
    # Cold start: reuse tab if present, else open setup URL.
    if [[ -x "$OPEN_DASH" ]]; then
      TOKENSAVER_DASH_URL="$SETUP_URL" bash "$OPEN_DASH" || open "$SETUP_URL" 2>/dev/null || true
    else
      open "$SETUP_URL" 2>/dev/null || open "$DIRECT_URL" 2>/dev/null || true
    fi
  else
    if [[ -x "$OPEN_DASH" ]]; then
      TOKENSAVER_DASH_URL="$DIRECT_URL" bash "$OPEN_DASH" || true
    else
      open "$DIRECT_URL" 2>/dev/null || true
    fi
  fi
}

# If proxy/dashboard already healthy, focus existing tab — do not restart/kill.
if curl -fsS -m 1 "http://127.0.0.1:8081/api/stats" >/dev/null 2>&1; then
  _open_dashboard focus
  exit 0
fi

VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  osascript -e "display dialog \"Python venv missing.\n\nDouble-click start-mac.command once to install dependencies, then launch Token Saver again.\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  open "$ROOT/start-mac.command" || true
  exit 1
fi

# Only stop a prior instance we own if dashboard is down (stale pid / crashed).
if [[ -x "$RES/stop.sh" && -f "$PIDFILE" ]]; then
  STOP_QUIET=1 "$RES/stop.sh" || true
fi

APP=1
MODE=mitm
DRY_RUN=1
SKIP_SETUP=0
PREF_FILE="$ROOT/.aiproxy_runtime.env"
SESSION_FILE="$ROOT/.aiproxy_session.env"
_LOAD_FILE=""
if [[ -f "$SESSION_FILE" ]]; then
  _LOAD_FILE="$SESSION_FILE"
elif [[ -f "$PREF_FILE" ]]; then
  _LOAD_FILE="$PREF_FILE"
fi
if [[ -n "$_LOAD_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
      APP=*) APP="${line#APP=}" ;;
      MODE=*) MODE="${line#MODE=}" ;;
      DRY_RUN=*) DRY_RUN="${line#DRY_RUN=}" ;;
      SKIP_SETUP=*) SKIP_SETUP="${line#SKIP_SETUP=}" ;;
    esac
  done <"$_LOAD_FILE"
fi
# Prefer persistent quick-start flag for skipping the setup modal.
if [[ -f "$PREF_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      SKIP_SETUP=*) SKIP_SETUP="${line#SKIP_SETUP=}" ;;
    esac
  done <"$PREF_FILE"
fi
case "$APP" in
  2) MODE=anthropic ;;
  3) MODE=mitm ;;
  *) ;;
esac
case "$DRY_RUN" in
  0|1) ;;
  *) DRY_RUN=1 ;;
esac
case "$SKIP_SETUP" in
  0|1) ;;
  *) SKIP_SETUP=0 ;;
esac
# Only skip setup when saved prefs exist.
if [[ "$SKIP_SETUP" == "1" && ! -f "$PREF_FILE" ]]; then
  SKIP_SETUP=0
fi

export AIPROXY_CONFIG="$ROOT/config.yaml"
export PYTHONUNBUFFERED=1
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

LOG="$LOG_DIR/proxy.log"
echo "---- $(date -u +"%Y-%m-%dT%H:%M:%SZ") start mode=$MODE dry_run=$DRY_RUN root=$ROOT cwd=$(pwd) ----" >>"$LOG"

ARGS=( -m aiproxy --mode "$MODE" -c "$ROOT/config.yaml" )
if [[ "$DRY_RUN" -eq 1 ]]; then
  ARGS+=( --dry-run )
fi

"$VENV_PY" "${ARGS[@]}" >>"$LOG" 2>&1 &
PID=$!
echo "$PID" >"$PIDFILE"
echo "$ROOT" >"${SUPPORT}/project_root"
echo "$PID" >"${SUPPORT}/last_pid"

ok=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  sleep 0.4
  if ! kill -0 "$PID" 2>/dev/null; then
    osascript -e "display dialog \"Token Saver failed to start.\n\nSee log:\n$LOG\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
    open -R "$LOG" || true
    exit 1
  fi
  if curl -fsS -m 1 "http://127.0.0.1:8081/api/stats" >/dev/null 2>&1; then
    ok=1
    break
  fi
done
if [[ "$ok" -ne 1 ]]; then
  osascript -e "display dialog \"Token Saver started but dashboard is not responding on :8081.\n\nSee log:\n$LOG\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  open -R "$LOG" || true
  exit 1
fi

# Cold start: setup wizard unless user chose "Don't show this again".
if [[ "$SKIP_SETUP" == "1" ]]; then
  _open_dashboard focus
else
  _open_dashboard setup
fi
exit 0
