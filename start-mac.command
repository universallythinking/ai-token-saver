#!/bin/bash
# Double-clickable macOS installer / launcher for aiproxy.
# Opens Terminal, installs deps if needed, asks Cursor vs Claude, starts proxy.

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

clear 2>/dev/null || true
echo "========================================"
echo "  aiproxy — install & start (macOS)"
echo "========================================"
echo "Project: $ROOT"
echo

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

# --- Questions ---
echo "Which app are you connecting?"
echo "  1) Cursor  (default models — MITM HTTPS proxy)"
echo "  2) Claude Code  (ANTHROPIC_BASE_URL — no CA needed)"
echo "  3) Both  (MITM — Cursor + Claude via HTTPS_PROXY)"
echo
read -r -p "Choice [1/2/3] (default 1): " APP
APP="${APP:-1}"

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

CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
PROXY_URL="http://127.0.0.1:8080"
DASH="http://127.0.0.1:8081/"

MODE="mitm"
case "$APP" in
  2)
    MODE="anthropic"
    ;;
  3)
    MODE="mitm"
    ;;
  *)
    MODE="mitm"
    APP=1
    ;;
esac

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
  read -r -p "Trust mitmproxy CA now (sudo, once)? [y/N]: " TRUST
  TRUST="$(echo "${TRUST:-n}" | tr '[:upper:]' '[:lower:]')"
  # Ensure CA exists by briefly noting first mitm run creates it — create via mitmdump cert if missing
  if [[ ! -f "$CA" ]]; then
    echo "CA not found yet — starting a one-shot mitm to generate it …"
    # mitmproxy creates certs on first listen; use --version / short run via python -c if needed
    "$VENV_PY" -c "
from pathlib import Path
from mitmproxy.certs import CertStore
p = Path.home()/'.mitmproxy'
p.mkdir(parents=True, exist_ok=True)
CertStore.from_store(str(p), 'mitmproxy', 2048)
print('CA ready at', p/'mitmproxy-ca-cert.pem')
" || true
  fi
  if [[ "$TRUST" == "y" || "$TRUST" == "yes" ]]; then
    if [[ -f "$CA" ]]; then
      sudo security add-trusted-cert -d -r trustRoot \
        -k /Library/Keychains/System.keychain \
        "$CA" && echo "CA trusted." || echo "Trust step failed — see README."
    else
      echo "CA still missing at $CA — start proxy once, then re-run trust."
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

echo "----------------------------------------"
echo "Starting aiproxy (mode=$MODE) …"
echo "Dashboard: $DASH"
echo "Ctrl+C to stop."
echo "========================================"
echo

# Avoid empty-array + set -u on macOS bash 3.2 ("unbound variable").
if [[ "$DRY_RUN" -eq 1 ]]; then
  exec "$VENV_PY" -m aiproxy --mode "$MODE" --dry-run
else
  exec "$VENV_PY" -m aiproxy --mode "$MODE"
fi
