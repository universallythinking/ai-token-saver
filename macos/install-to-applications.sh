#!/bin/bash
# Back-compat shim — installer lives at the project root.
set -euo pipefail
_SCRIPT="${BASH_SOURCE[0]:-$0}"
if command -v realpath >/dev/null 2>&1; then
  MACOS_DIR="$(dirname "$(realpath "$_SCRIPT")")"
else
  MACOS_DIR="$(cd "$(dirname "$_SCRIPT")" && pwd -P)"
fi
ROOT="$(cd "$MACOS_DIR/.." && pwd -P)"
exec bash "$ROOT/install-to-applications.command" "$@"
