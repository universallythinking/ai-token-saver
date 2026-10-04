#!/bin/bash
# Double-clickable: build Token Saver.app → /Applications (or ~/Applications),
# and refresh "Install to Applications.app" in this project folder (with icon).
set -euo pipefail

_SCRIPT="${BASH_SOURCE[0]:-$0}"
if command -v realpath >/dev/null 2>&1; then
  ROOT="$(dirname "$(realpath "$_SCRIPT")")"
else
  ROOT="$(cd "$(dirname "$_SCRIPT")" && pwd -P)"
fi
cd "$ROOT"

MACOS_DIR="$ROOT/macos"
APP_NAME="Token Saver.app"
INSTALLER_NAME="Install to Applications.app"

DEST_DIR="/Applications"
if [[ ! -w "$DEST_DIR" ]]; then
  DEST_DIR="${HOME}/Applications"
  mkdir -p "$DEST_DIR"
fi
DEST="${DEST_DIR}/${APP_NAME}"

if ! command -v osacompile >/dev/null 2>&1; then
  echo "ERROR: osacompile not found (need macOS with Developer Command Line Tools)."
  echo "Install via: xcode-select --install"
  exit 1
fi

_apply_bundle_icon() {
  local app="$1"
  local res="${app}/Contents/Resources"
  local plist="${app}/Contents/Info.plist"
  [[ -d "$res" && -f "$plist" ]] || return 0
  if [[ -f "$MACOS_DIR/AppIcon.icns" ]]; then
    cp "$MACOS_DIR/AppIcon.icns" "$res/applet.icns"
    cp "$MACOS_DIR/AppIcon.icns" "$res/AppIcon.icns"
  fi
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet" "$plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string applet" "$plist" 2>/dev/null || true
  # osacompile's Assets.car + CFBundleIconName wins over .icns — remove both.
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$plist" 2>/dev/null || true
  rm -f "$res/Assets.car"
  # Bump version so Launch Services / Dock treat this as a new icon binding.
  local ver
  ver="$(date -u +"%Y%m%d%H%M%S")"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${ver}" "$plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${ver}" "$plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 1.0.${ver}" "$plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 1.0.${ver}" "$plist" 2>/dev/null || true
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
  fi
}

_refresh_icon_cache() {
  local app="$1"
  [[ -d "$app" ]] || return 0
  local lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  touch "$app" "$app/Contents/Info.plist" 2>/dev/null || true
  if [[ -x "$lsregister" ]]; then
    # Drop stale registration, then re-register (helps Dock keep old icons across machines).
    "$lsregister" -u "$app" >/dev/null 2>&1 || true
    "$lsregister" -f "$app" >/dev/null 2>&1 || true
  fi
  # Clear Finder/Dock icon services cache for this user (safe; regenerates on next paint).
  rm -rf "${HOME}/Library/Caches/com.apple.iconservices.store" 2>/dev/null || true
  find "${HOME}/Library/Caches" -maxdepth 1 -name 'com.apple.iconservices*' -exec rm -rf {} + 2>/dev/null || true
  killall Dock >/dev/null 2>&1 || true
  killall Finder >/dev/null 2>&1 || true
}

_set_finder_icon() {
  # Custom Finder icon for this .command (resource fork; local only).
  local target="$1"
  local icns="$MACOS_DIR/AppIcon.icns"
  [[ -f "$icns" && -e "$target" ]] || return 0
  osascript -l JavaScript <<EOF >/dev/null 2>&1 || true
ObjC.import("AppKit");
var img = $.NSImage.alloc.initWithContentsOfFile("$(printf '%s' "$icns" | sed 's/\\/\\\\/g')");
if (img) {
  $.NSWorkspace.sharedWorkspace.setIconForFileOptions(img, "$(printf '%s' "$target" | sed 's/\\/\\\\/g')", 0);
}
EOF
}

echo "========================================"
echo "  Token Saver — install to Applications"
echo "========================================"
echo "Project: $ROOT"
echo

TMP="$(mktemp -d "${TMPDIR:-/tmp}/tokensaver-app.XXXXXX")"
cleanup_tmp() { rm -rf "$TMP"; }
trap cleanup_tmp EXIT

echo "Building ${APP_NAME} …"
osacompile -s -o "${TMP}/${APP_NAME}" "$MACOS_DIR/TokenSaver.applescript"

RES="${TMP}/${APP_NAME}/Contents/Resources"
chmod +x "$MACOS_DIR/start.sh" "$MACOS_DIR/stop.sh" "$MACOS_DIR/open-dashboard.sh"
cp "$MACOS_DIR/start.sh" "$MACOS_DIR/stop.sh" "$MACOS_DIR/open-dashboard.sh" "$RES/"
chmod +x "$RES/start.sh" "$RES/stop.sh" "$RES/open-dashboard.sh"
printf '%s\n' "$ROOT" >"$RES/ProjectRoot"

PLIST="${TMP}/${APP_NAME}/Contents/Info.plist"
APP_TMP="${TMP}/${APP_NAME}"
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
_apply_bundle_icon "$APP_TMP"

echo "Installing to ${DEST} …"
# Quit running copy so Dock drops the old icon binding.
osascript -e 'tell application "Token Saver" to quit' >/dev/null 2>&1 || true
sleep 0.3
rm -rf "$DEST"
ditto "$APP_TMP" "$DEST"

# Repo copy for reference (machine-local ProjectRoot — gitignored).
REPO_APP="${MACOS_DIR}/${APP_NAME}"
rm -rf "$REPO_APP"
ditto "$DEST" "$REPO_APP"

echo "Building ${INSTALLER_NAME} (project root) …"
INSTALLER_TMP="${TMP}/${INSTALLER_NAME}"
osacompile -o "$INSTALLER_TMP" "$MACOS_DIR/InstallToApplications.applescript"
IPLIST="${INSTALLER_TMP}/Contents/Info.plist"
if [[ -f "$IPLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleName Install to Applications" "$IPLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleName string Install to Applications" "$IPLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Install to Applications" "$IPLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Install to Applications" "$IPLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.universallythinking.tokensaver.install" "$IPLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.universallythinking.tokensaver.install" "$IPLIST" 2>/dev/null || true
fi
_apply_bundle_icon "$INSTALLER_TMP"
INSTALLER_DEST="${ROOT}/${INSTALLER_NAME}"
rm -rf "$INSTALLER_DEST"
ditto "$INSTALLER_TMP" "$INSTALLER_DEST"

_set_finder_icon "$ROOT/install-to-applications.command"
_set_finder_icon "$INSTALLER_DEST"

echo "Refreshing Dock / Finder icon cache …"
_refresh_icon_cache "$DEST"
_refresh_icon_cache "$INSTALLER_DEST"

echo
echo "Installed:  ${DEST}"
echo "Installer:  ${INSTALLER_DEST}"
echo "Project:    ${ROOT}"
echo
echo "Launch:     open -a \"Token Saver\""
echo "        or  open \"${DEST}\""
echo "Dock click: focuses the open dashboard tab (no relaunch)"
echo "Quit:       Dock → Quit  (stops proxy + dashboard)"
echo
echo "Prefs:      ${ROOT}/.aiproxy_runtime.env  (from start-mac.command)"
echo "Logs:       ~/Library/Logs/TokenSaver/proxy.log"
echo
echo "Other Mac:  git pull, then re-run this installer (Dock does not update from git alone)."
echo
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Note: .venv not ready yet — double-click start-mac.command once, then open Token Saver."
fi

# Keep Terminal window readable when double-clicked.
if [[ -t 0 ]]; then
  :
elif [[ "${TERM_PROGRAM:-}" == "Apple_Terminal" || -n "${TERM:-}" ]]; then
  echo
  read -r -p "Press Return to close…" _ || true
fi
