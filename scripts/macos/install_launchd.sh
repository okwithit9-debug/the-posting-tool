#!/bin/bash
# install_launchd.sh — optional Python-backup LaunchAgent (not the primary path).
#
# Copies launchd/com.example.posting-tool.plist into ~/Library/LaunchAgents/
# after substituting this repo's path. There is no shipped brand, Mac path,
# or account handle.

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PLIST_NAME="com.example.posting-tool.plist"
PLIST_SRC="$PROJECT_DIR/launchd/$PLIST_NAME"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LABEL="com.example.posting-tool"

if [ ! -f "$PLIST_SRC" ]; then
    echo "ERROR: $PLIST_SRC not found"
    exit 1
fi

mkdir -p "$PROJECT_DIR/.dispatch_queue"
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

if launchctl list | grep -q "$LABEL"; then
    echo "Unloading previous LaunchAgent..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Substitute this checkout's path. The template ships __PROJECT_DIR__.
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DEST"

echo "Installing $PLIST_NAME -> $PLIST_DEST"
echo "Loading LaunchAgent..."
launchctl load "$PLIST_DEST"

echo
echo "Installed. The LaunchAgent will:"
echo "  - Run schedule_batch.py dispatch when a file is added to:"
echo "      $PROJECT_DIR/.dispatch_queue/"
echo "  - Also fire on the example six-hour interval in the plist"
echo "    (not a required default — edit after install)"
echo
echo "Trigger a run with:"
echo "  date > '$PROJECT_DIR/.dispatch_queue/trigger.txt'"
echo
echo "Tail logs:"
echo "  tail -F '$PROJECT_DIR/logs/launchd.out.log' '$PROJECT_DIR/logs/launchd.err.log'"
