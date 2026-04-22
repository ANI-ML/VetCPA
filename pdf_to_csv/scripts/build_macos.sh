#!/usr/bin/env bash
# Build VetCPA.app for macOS using PyInstaller.
#
# Usage:
#     ./scripts/build_macos.sh             # build with models downloaded on first run
#     VETCPA_BUNDLE_MODELS=1 ./scripts/build_macos.sh
#                                          # fully-offline build: bundles ~/.cache/docling
#                                          # (run the app once with internet first to populate)
#
# Requirements: .venv already set up via `make install-dev`.

set -euo pipefail

cd "$(dirname "$0")/.."

VENV="${VENV:-.venv}"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Error: $VENV/bin/python not found. Run 'make install-dev' first."
  exit 1
fi

# PyInstaller is a dev-time dep; install on demand so a plain install doesn't carry it.
if ! "$VENV/bin/python" -c "import PyInstaller" 2>/dev/null; then
  echo "Installing PyInstaller into $VENV ..."
  "$VENV/bin/pip" install --quiet 'pyinstaller>=6.8'
fi

echo "Cleaning previous build output..."
rm -rf build dist

if [[ "${VETCPA_BUNDLE_MODELS:-0}" == "1" ]]; then
  echo "VETCPA_BUNDLE_MODELS=1 — will bundle ~/.cache/docling into the app."
  if [[ ! -d "$HOME/.cache/docling" ]]; then
    echo "Warning: ~/.cache/docling does not exist."
    echo "Run the app once with internet access to populate it, then re-run this script."
    exit 1
  fi
fi

echo "Building VetCPA.app via PyInstaller..."
"$VENV/bin/pyinstaller" VetCPA.spec --clean --noconfirm

if [[ -d "dist/VetCPA.app" ]]; then
  APP_SIZE="$(du -sh dist/VetCPA.app | awk '{print $1}')"
  echo
  echo "✓ Built dist/VetCPA.app ($APP_SIZE)"
  echo "  Launch:  open dist/VetCPA.app"
  echo "  First run opens the default browser to http://127.0.0.1:<port>/"
  if [[ "${VETCPA_BUNDLE_MODELS:-0}" != "1" ]]; then
    echo "  Note: first launch downloads ~2 GB of Docling models from HuggingFace."
    echo "        Subsequent launches are fully offline."
  fi
else
  echo "Build failed — see PyInstaller output above."
  exit 1
fi
