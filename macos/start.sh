#!/bin/bash
# Start aiproxy for the Token Saver.app bundle. Invoked on app launch.
set -euo pipefail

RES="$(cd "$(dirname "$0")" && pwd)"
SUPPORT="${HOME}/Library/Application Support/TokenSaver"
LOG_DIR="${HOME}/Library/Logs/TokenSaver"
PIDFILE="${SUPPORT}/proxy.pid"
mkdir -p "$SUPPORT" "$LOG_DIR"

if [[ -f "$RES/ProjectRoot" ]]; then
  ROOT="$(cat "$RES/ProjectRoot" | sed -e 's/[[:space:]]*$//')"
else
  # Fallback: app lives at <repo>/macos/Token Saver.app
  ROOT="$(cd "$RES/../../.." && pwd)"
fi

if [[ ! -d "$ROOT" || ! -f "$ROOT/config.yaml" ]]; then
  osascript -e "display dialog \"Token Saver cannot find the project at:\n${ROOT:-?}\n\nRe-run macos/install-to-applications.sh from the repo.\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  exit 1
fi

VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  osascript -e "display dialog \"Python venv missing.\n\nDouble-click start-mac.command once to install dependencies, then launch Token Saver again.\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  open "$ROOT/start-mac.command" || true
  exit 1
fi

# Stop any previous instance owned by this app / project.
if [[ -x "$RES/stop.sh" ]]; then
  "$RES/stop.sh" || true
fi

# Load quick-start prefs if present (from start-mac.command).
APP=1
MODE=mitm
DRY_RUN=1
PREF_FILE="$ROOT/.aiproxy_runtime.env"
if [[ -f "$PREF_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
      APP=*) APP="${line#APP=}" ;;
      MODE=*) MODE="${line#MODE=}" ;;
      DRY_RUN=*) DRY_RUN="${line#DRY_RUN=}" ;;
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

export AIPROXY_CONFIG="$ROOT/config.yaml"
export PYTHONUNBUFFERED=1

LOG="$LOG_DIR/proxy.log"
echo "---- $(date -u +"%Y-%m-%dT%H:%M:%SZ") start mode=$MODE dry_run=$DRY_RUN root=$ROOT ----" >>"$LOG"

ARGS=( -m aiproxy --mode "$MODE" )
if [[ "$DRY_RUN" -eq 1 ]]; then
  ARGS+=( --dry-run )
fi

# Run under the project venv; record PID for Dock-quit cleanup.
"$VENV_PY" "${ARGS[@]}" >>"$LOG" 2>&1 &
PID=$!
echo "$PID" >"$PIDFILE"
echo "$ROOT" >"${SUPPORT}/project_root"
echo "$PID" >"${SUPPORT}/last_pid"

# Brief wait — if it died immediately, surface the log.
sleep 0.8
if ! kill -0 "$PID" 2>/dev/null; then
  osascript -e "display dialog \"Token Saver failed to start.\n\nSee log:\n$LOG\" buttons {\"OK\"} with icon stop" >/dev/null 2>&1 || true
  open -R "$LOG" || true
  exit 1
fi

# Open dashboard (prefer alias; fall back to direct).
DASH_URL="http://127.0.0.1:8081/"
if [[ -f "$ROOT/config.yaml" ]]; then
  HOST="$(
    grep -E '^dashboard_hostname:' "$ROOT/config.yaml" 2>/dev/null \
      | head -1 \
      | awk -F: '{print $2}' \
      | tr -d ' "'"'"'"' \
      | tr -d '[:space:]'
  )"
  if [[ -n "$HOST" ]]; then
    DASH_URL="http://${HOST}/"
  fi
fi
sleep 0.5
open "$DASH_URL" 2>/dev/null || open "http://127.0.0.1:8081/" 2>/dev/null || true

osascript -e "display notification \"Proxy running · ${DASH_URL}\" with title \"Token Saver\"" >/dev/null 2>&1 || true
exit 0
