#!/usr/bin/env bash
# Wrap dist/VetCPA.app into a distributable disk image (VetCPA-<version>.dmg).
#
# Run this AFTER scripts/build_macos.sh has produced dist/VetCPA.app.
#
# The accountant receives a single .dmg, double-clicks, drags VetCPA into
# /Applications, ejects the disk image. Standard Mac install UX.

set -euo pipefail

cd "$(dirname "$0")/.."

APP="dist/VetCPA.app"
if [[ ! -d "$APP" ]]; then
  echo "Error: $APP not found. Run ./scripts/build_macos.sh first."
  exit 1
fi

VERSION="$(
  .venv/bin/python -c "import pdf_to_csv; print(pdf_to_csv.__version__)" 2>/dev/null \
    || echo "0.1.1"
)"
DMG="dist/VetCPA-${VERSION}.dmg"
STAGE="dist/dmg-stage"

echo "Staging for DMG..."
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
# Shortcut to /Applications so the user can drag-install.
ln -s /Applications "$STAGE/Applications"

echo "Building $DMG ..."
hdiutil create \
  -volname "VetCPA ${VERSION}" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

rm -rf "$STAGE"

DMG_SIZE="$(du -sh "$DMG" | awk '{print $1}')"
echo
echo "✓ Built $DMG ($DMG_SIZE)"
echo "  Hand off this single file to the accountant — they double-click, drag"
echo "  VetCPA into Applications, eject the disk image, and launch from /Applications."
