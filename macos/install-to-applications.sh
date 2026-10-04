#!/bin/bash
# Build Token Saver.app and install into /Applications (or ~/Applications).
# Safe to re-run after pulling updates.
set -euo pipefail

_SCRIPT="${BASH_SOURCE[0]:-$0}"
if command -v realpath >/dev/null 2>&1; then
  MACOS_DIR="$(dirname "$(realpath "$_SCRIPT")")"
else
  MACOS_DIR="$(cd "$(dirname "$_SCRIPT")" && pwd -P)"
fi
ROOT="$(cd "$MACOS_DIR/.." && pwd -P)"

APP_NAME="Token Saver.app"
DEST_DIR="/Applications"
if [[ ! -w "$DEST_DIR" ]]; then
  DEST_DIR="${HOME}/Applications"
  mkdir -p "$DEST_DIR"
fi
DEST="${DEST_DIR}/${APP_NAME}"

if ! command -v osacompile >/dev/null 2>&1; then
  echo "ERROR: osacompile not found (need macOS with Developer Command Line Tools)."
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/tokensaver-app.XXXXXX")"
cleanup_tmp() { rm -rf "$TMP"; }
trap cleanup_tmp EXIT

echo "Building ${APP_NAME} …"
# -s = stay-open so the app remains in the Dock until Quit
osacompile -s -o "${TMP}/${APP_NAME}" "$MACOS_DIR/TokenSaver.applescript"

RES="${TMP}/${APP_NAME}/Contents/Resources"
chmod +x "$MACOS_DIR/start.sh" "$MACOS_DIR/stop.sh"
cp "$MACOS_DIR/start.sh" "$MACOS_DIR/stop.sh" "$RES/"
chmod +x "$RES/start.sh" "$RES/stop.sh"
printf '%s\n' "$ROOT" >"$RES/ProjectRoot"

# Prefer a readable bundle name in Finder / Dock
PLIST="${TMP}/${APP_NAME}/Contents/Info.plist"
if [[ -f "$PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleName Token Saver" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleName string Token Saver" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Token Saver" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Token Saver" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.universallythinking.tokensaver" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.universallythinking.tokensaver" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 11.0" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 11.0" "$PLIST" 2>/dev/null || true
fi

echo "Installing to ${DEST} …"
rm -rf "$DEST"
ditto "${TMP}/${APP_NAME}" "$DEST"

# Also keep a copy under the repo for reference / reinstall.
REPO_APP="${MACOS_DIR}/${APP_NAME}"
rm -rf "$REPO_APP"
ditto "$DEST" "$REPO_APP"

echo
echo "Installed: ${DEST}"
echo "Project:   ${ROOT}"
echo
echo "Launch:    open -a \"Token Saver\""
echo "       or  open \"${DEST}\""
echo "Quit:      Dock → Quit  (stops proxy + dashboard processes)"
echo
echo "Prefs:     ${ROOT}/.aiproxy_runtime.env  (from start-mac.command Quick start)"
echo "Logs:      ~/Library/Logs/TokenSaver/proxy.log"
echo
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Note: .venv not ready yet — run start-mac.command once before launching the app."
fi
