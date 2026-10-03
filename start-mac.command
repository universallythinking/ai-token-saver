#!/bin/bash
# Double-clickable macOS installer / launcher for aiproxy.
# Opens Terminal, installs deps if needed, asks Cursor vs Claude, starts proxy.
# Option 4 = quick start from saved preferences (skip setup prompts).

set -euo pipefail

# Resolve this script's directory (works for double-click .command, symlinks, relative paths).
# Never hardcode a home/username path — always derive from the script location.
_SCRIPT="${BASH_SOURCE[0]:-$0}"
if command -v realpath >/dev/null 2>&1; then
  ROOT="$(dirname "$(realpath "$_SCRIPT")")"
else
  ROOT="$(cd "$(dirname "$_SCRIPT")" && pwd -P)"
fi
cd "$ROOT"

PREF_FILE="$ROOT/.aiproxy_runtime.env"
CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
PROXY_URL="http://127.0.0.1:8080"
DASH="http://127.0.0.1:8081/"

clear 2>/dev/null || true
echo "========================================"
echo "  aiproxy — install & start (macOS)"
echo "========================================"
echo "Project: $ROOT"
echo

# --- helpers ---
_ca_exists() {
  [[ -f "$CA" ]]
}

_ensure_ca() {
  if _ca_exists; then
    return 0
  fi
  echo "CA not found — generating mitmproxy cert …"
  "$VENV_PY" -c "
from pathlib import Path
from mitmproxy.certs import CertStore
p = Path.home()/'.mitmproxy'
p.mkdir(parents=True, exist_ok=True)
CertStore.from_store(str(p), 'mitmproxy', 2048)
print('CA ready at', p/'mitmproxy-ca-cert.pem')
" || true
}

# True if mitmproxy CA is in a macOS keychain (system or login).
_ca_trusted() {
  _ca_exists || return 1
  if ! command -v security >/dev/null 2>&1; then
    return 1
  fi
  # Prefer fingerprint match against installed certs named mitmproxy.
  local fp
  fp="$(openssl x509 -in "$CA" -noout -fingerprint -sha256 2>/dev/null | sed 's/^.*=//;s/://g' | tr '[:lower:]' '[:upper:]')"
  if [[ -n "$fp" ]]; then
    if security find-certificate -a -c mitmproxy -Z 2>/dev/null \
      | tr -d ':' \
      | tr '[:lower:]' '[:upper:]' \
      | grep -q "$fp"; then
      return 0
    fi
  fi
  # Fallback: any keychain entry named mitmproxy
  security find-certificate -c mitmproxy -a >/dev/null 2>&1
}

_ca_status_label() {
  if ! _ca_exists; then
    echo "CA missing"
  elif _ca_trusted; then
    echo "CA trusted ✓"
  else
    echo "CA not trusted yet"
  fi
}

_app_label() {
  case "${1:-1}" in
    2) echo "Claude Code" ;;
    3) echo "Cursor + Claude" ;;
    *) echo "Cursor" ;;
  esac
}

_dry_label() {
  if [[ "${1:-1}" -eq 1 ]]; then
    echo "dry-run"
  else
    echo "live strip"
  fi
}

_save_prefs() {
  cat >"$PREF_FILE" <<EOF
# aiproxy launcher preferences (local — not committed)
APP=$APP
MODE=$MODE
DRY_RUN=$DRY_RUN
SAVED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
  echo "Saved quick-start settings → $PREF_FILE"
}

_load_prefs() {
  # shellcheck disable=SC1090
  APP=1
  MODE=mitm
  DRY_RUN=1
  # Prefer sourcing known keys only
  if [[ ! -f "$PREF_FILE" ]]; then
    return 1
  fi
  # shellcheck disable=SC1090
  # Parse KEY=VALUE lines (ignore comments / blanks)
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      ''|\#*) continue ;;
      APP=*) APP="${line#APP=}" ;;
      MODE=*) MODE="${line#MODE=}" ;;
      DRY_RUN=*) DRY_RUN="${line#DRY_RUN=}" ;;
    esac
  done <"$PREF_FILE"
  case "$APP" in
    2) MODE=anthropic ;;
    3) MODE=mitm ;;
    *) APP=1; MODE=mitm ;;
  esac
  case "$DRY_RUN" in
    0|1) ;;
    *) DRY_RUN=1 ;;
  esac
  return 0
}

_mode_from_app() {
  case "$APP" in
    2) MODE=anthropic ;;
    3) MODE=mitm ;;
    *) APP=1; MODE=mitm ;;
  esac
}

_start_proxy() {
  echo "----------------------------------------"
  echo "Starting aiproxy (mode=$MODE) …"
  echo "Dashboard: $DASH"
  if [[ "$MODE" != "anthropic" ]]; then
    echo "CA: $(_ca_status_label)"
    if _ca_exists; then
      echo "Relaunch Cursor with:"
      echo "  export NODE_EXTRA_CA_CERTS=\"$CA\""
      echo "  open -a Cursor"
    fi
  fi
  echo "Ctrl+C to stop."
  echo "========================================"
  echo

  # Avoid empty-array + set -u on macOS bash 3.2 ("unbound variable").
  if [[ "$DRY_RUN" -eq 1 ]]; then
    exec "$VENV_PY" -m aiproxy --mode "$MODE" --dry-run
  else
    exec "$VENV_PY" -m aiproxy --mode "$MODE"
  fi
}

# --- Python / venv ---
if command -v python3 >/dev/null 2>&1; then
  BOOT=python3
elif command -v python >/dev/null 2>&1; then
  BOOT=python
else
  echo "ERROR: Python 3 not found. Install from https://www.python.org/downloads/ then re-run."
  read -r -p "Press Enter to close…"
  exit 1
fi

VENV_DIR="$ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"

_venv_ok() {
  # Path must exist and the interpreter must actually run (catches broken
  # symlinks / venvs whose base Python was upgraded or removed).
  [[ -e "$VENV_PY" ]] && "$VENV_PY" -c "import sys" >/dev/null 2>&1
}

if ! _venv_ok; then
  if [[ -e "$VENV_DIR" ]]; then
    echo "Existing .venv is missing or broken — recreating with $BOOT …"
    rm -rf "$VENV_DIR"
  else
    echo "Creating virtualenv with $BOOT …"
  fi
  "$BOOT" -m venv "$VENV_DIR"
  VENV_PY="$VENV_DIR/bin/python"
fi

if ! _venv_ok; then
  echo "ERROR: Could not create a working venv at:"
  echo "  $VENV_PY"
  echo "Tried boot interpreter: $BOOT ($("$BOOT" -c 'import sys; print(sys.executable)' 2>/dev/null || echo '?'))"
  read -r -p "Press Enter to close…"
  exit 1
fi

echo "Installing / updating dependencies …"
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r "$ROOT/requirements.txt"
"$VENV_PY" -m pip install -e "$ROOT" >/dev/null
echo "Deps OK."
echo

HAS_PREFS=0
if [[ -f "$PREF_FILE" ]]; then
  HAS_PREFS=1
fi

# Probe CA status early (generate if missing so trust check is meaningful for mitm users).
_ensure_ca >/dev/null 2>&1 || true
CA_LABEL="$(_ca_status_label)"

# --- Questions ---
echo "Which app are you connecting?"
echo "  1) Cursor  (default models — MITM HTTPS proxy)"
echo "  2) Claude Code  (ANTHROPIC_BASE_URL — no CA needed)"
echo "  3) Both  (MITM — Cursor + Claude via HTTPS_PROXY)"
if [[ "$HAS_PREFS" -eq 1 ]]; then
  # Show saved summary without clobbering globals yet
  _saved_app="$(grep '^APP=' "$PREF_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  _saved_dry="$(grep '^DRY_RUN=' "$PREF_FILE" 2>/dev/null | head -1 | cut -d= -f2-)"
  _saved_app="${_saved_app:-1}"
  _saved_dry="${_saved_dry:-1}"
  echo "  4) Quick start  ($(_app_label "$_saved_app") · $(_dry_label "$_saved_dry") · $CA_LABEL)"
  DEFAULT_CHOICE=4
else
  echo "  4) Quick start  (save settings after setup — none saved yet)"
  DEFAULT_CHOICE=1
fi
echo
echo "Certificate: $CA_LABEL"
echo
read -r -p "Choice [1/2/3/4] (default $DEFAULT_CHOICE): " CHOICE
CHOICE="${CHOICE:-$DEFAULT_CHOICE}"

# --- Quick start ---
if [[ "$CHOICE" == "4" ]]; then
  if [[ "$HAS_PREFS" -ne 1 ]]; then
    echo
    echo "No saved settings yet. Pick 1–3 once, then choose Y when asked to save."
    read -r -p "Press Enter to continue with setup (Cursor)…"
    CHOICE=1
  else
    _load_prefs
    echo
    echo "→ Quick start: $(_app_label "$APP") · $(_dry_label "$DRY_RUN") · mode=$MODE · $CA_LABEL"
    if [[ "$MODE" != "anthropic" ]]; then
      _ensure_ca
      if ! _ca_trusted; then
        echo
        echo "CA is not trusted in Keychain yet."
        read -r -p "Trust mitmproxy CA now (sudo, once)? [Y/n]: " TRUST
        TRUST="$(echo "${TRUST:-Y}" | tr '[:upper:]' '[:lower:]')"
        if [[ "$TRUST" != "n" && "$TRUST" != "no" ]]; then
          if [[ -f "$CA" ]]; then
            sudo security add-trusted-cert -d -r trustRoot \
              -k /Library/Keychains/System.keychain \
              "$CA" && echo "CA trusted." || echo "Trust step failed — see README."
          fi
        fi
      fi
    fi
    _start_proxy
  fi
fi

# --- Interactive setup (1–3) ---
case "$CHOICE" in
  2) APP=2 ;;
  3) APP=3 ;;
  *) APP=1 ;;
esac
_mode_from_app

echo
read -r -p "Dry-run first? (log savings, do not rewrite bodies) [Y/n]: " DRY
DRY="$(echo "${DRY:-Y}" | tr '[:upper:]' '[:lower:]')"
DRY_RUN=0
if [[ "$DRY" != "n" && "$DRY" != "no" ]]; then
  DRY_RUN=1
  echo "→ dry-run ON"
else
  echo "→ dry-run OFF (will strip when rules match)"
fi

echo
echo "----------------------------------------"
if [[ "$MODE" == "anthropic" ]]; then
  echo "Mode: anthropic reverse proxy"
  echo "Start Claude in another terminal with:"
  echo "  export ANTHROPIC_BASE_URL=\"$PROXY_URL\""
  echo "  claude"
  echo "  /status   # confirm base URL"
  echo
  read -r -p "Write ANTHROPIC_BASE_URL into ~/.claude/settings.json? [y/N]: " WRITE_CLAUDE
  WRITE_CLAUDE="$(echo "${WRITE_CLAUDE:-n}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$WRITE_CLAUDE" == "y" || "$WRITE_CLAUDE" == "yes" ]]; then
    mkdir -p "$HOME/.claude"
    SETTINGS="$HOME/.claude/settings.json"
    if [[ -f "$SETTINGS" ]]; then
      echo "Existing $SETTINGS — leave it unchanged and set the env var manually,"
      echo "or merge \"ANTHROPIC_BASE_URL\": \"$PROXY_URL\" under env yourself."
    else
      cat >"$SETTINGS" <<EOF
{
  "env": {
    "ANTHROPIC_BASE_URL": "$PROXY_URL"
  }
}
EOF
      echo "Wrote $SETTINGS"
    fi
  fi
else
  echo "Mode: mitm (Cursor default models)"
  echo "Dashboard: $DASH"
  echo "Certificate: $(_ca_status_label)"
  echo
  echo "Cursor settings.json needs:"
  cat <<EOF
  {
    "http.proxy": "$PROXY_URL",
    "http.proxySupport": "override",
    "http.proxyStrictSSL": false,
    "cursor.general.disableHttp2": true
  }
EOF
  echo
  echo "Open User Settings (JSON): Cmd+Shift+P → “Open User Settings (JSON)”"
  echo
  _ensure_ca
  if _ca_trusted; then
    echo "CA already trusted in Keychain — skipping trust prompt."
  else
    read -r -p "Trust mitmproxy CA now (sudo, once)? [y/N]: " TRUST
    TRUST="$(echo "${TRUST:-n}" | tr '[:upper:]' '[:lower:]')"
    if [[ "$TRUST" == "y" || "$TRUST" == "yes" ]]; then
      if [[ -f "$CA" ]]; then
        sudo security add-trusted-cert -d -r trustRoot \
          -k /Library/Keychains/System.keychain \
          "$CA" && echo "CA trusted." || echo "Trust step failed — see README."
      else
        echo "CA still missing at $CA — start proxy once, then re-run trust."
      fi
    fi
  fi
  echo
  echo "After proxy starts: fully quit Cursor (Cmd+Q), then:"
  echo "  export NODE_EXTRA_CA_CERTS=\"$CA\""
  echo "  open -a Cursor"
  if [[ "$APP" == "3" ]]; then
    echo
    echo "For Claude Code in another terminal:"
    echo "  export HTTPS_PROXY=\"$PROXY_URL\""
    echo "  export HTTP_PROXY=\"$PROXY_URL\""
    echo "  export NODE_EXTRA_CA_CERTS=\"$CA\""
    echo "  claude"
  fi
fi

echo
read -r -p "Save these settings for Quick start (option 4) next time? [Y/n]: " SAVE
SAVE="$(echo "${SAVE:-Y}" | tr '[:upper:]' '[:lower:]')"
if [[ "$SAVE" != "n" && "$SAVE" != "no" ]]; then
  _save_prefs
fi

_start_proxy
