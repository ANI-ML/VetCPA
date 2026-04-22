#!/usr/bin/env bash
# VetCPA uninstaller for macOS.
#
# Removes the app, the cached OCR model weights (~2 GB), and the local
# feedback database. Does not touch ~/.cache/huggingface because that cache
# may be shared with other HuggingFace-using apps.
#
# Run from anywhere:
#   curl -fsSL https://raw.githubusercontent.com/ANI-ML/VetCPA/main/pdf_to_csv/scripts/uninstall_macos.sh | bash
# Or:
#   bash uninstall_macos.sh --yes   # skip the confirmation prompt

set -euo pipefail

assume_yes=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) assume_yes=1 ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--yes]

  --yes   Skip the confirmation prompt (for scripted use).

Removes:
  /Applications/VetCPA.app
  ~/Applications/VetCPA.app  (if present)
  ~/Library/Application Support/VetCPA/  (feedback DB)
  ~/.cache/docling/                      (~2 GB of OCR models)
EOF
      exit 0 ;;
  esac
done

# --- What we're going to remove ------------------------------------------
paths=(
  "/Applications/VetCPA.app"
  "$HOME/Applications/VetCPA.app"
  "$HOME/Library/Application Support/VetCPA"
  "$HOME/.cache/docling"
)

# --- Preview to the user -------------------------------------------------
printf '\n\033[1mVetCPA uninstaller — macOS\033[0m\n\n'
printf 'This will permanently remove:\n'
total=0
for p in "${paths[@]}"; do
  if [[ -e "$p" ]]; then
    size=$(du -sh "$p" 2>/dev/null | awk '{print $1}')
    printf '  • %-60s  %s\n' "$p" "$size"
    total=$((total + 1))
  fi
done
if [[ $total -eq 0 ]]; then
  printf '  (nothing — VetCPA doesn'\''t appear to be installed)\n'
  exit 0
fi
printf '\nIt will NOT remove:\n'
printf '  • ~/.cache/huggingface  (shared with other apps that use HF)\n'
printf '  • The .dmg you originally downloaded\n\n'

# --- Confirm -------------------------------------------------------------
if [[ "$assume_yes" -eq 0 ]]; then
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    [Yy]|[Yy][Ee][Ss]) ;;
    *) echo "Cancelled."; exit 0 ;;
  esac
fi

# --- Stop any running instance ------------------------------------------
if pgrep -f "VetCPA.app/Contents/MacOS/VetCPA" >/dev/null 2>&1; then
  echo "Stopping running VetCPA..."
  pkill -f "VetCPA.app/Contents/MacOS/VetCPA" || true
  sleep 1
fi

# --- Remove --------------------------------------------------------------
for p in "${paths[@]}"; do
  if [[ -e "$p" ]]; then
    echo "Removing $p"
    rm -rf "$p"
  fi
done

printf '\n\033[32m✓ VetCPA uninstalled.\033[0m\n'
echo "Reinstall any time from:"
echo "  https://github.com/ANI-ML/VetCPA/releases/latest"
