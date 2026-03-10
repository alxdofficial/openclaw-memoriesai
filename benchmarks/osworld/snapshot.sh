#!/bin/bash
# Save a named snapshot of current benchmark results
# Usage: ./snapshot.sh "v2-finding-fixes"  or  ./snapshot.sh (auto-names with timestamp)

BASE="/home/alex/OSWorld/results/pyautogui/screenshot"
SNAP_DIR="$BASE/_snapshots"
NAME="${1:-$(date +%Y%m%d-%H%M)}"
DEST="$SNAP_DIR/$NAME"

if [ -d "$DEST" ]; then
  echo "Snapshot '$NAME' already exists. Delete it first or pick a different name."
  exit 1
fi

mkdir -p "$DEST"
# Copy all detm-* dirs (skip _snapshots)
for d in "$BASE"/detm-*; do
  [ -d "$d" ] && cp -r "$d" "$DEST/"
done

echo "Snapshot saved: $DEST"
echo "Contents:"
ls "$DEST"
