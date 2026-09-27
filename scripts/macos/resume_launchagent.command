#!/bin/bash
# Double-click to RE-ENABLE the optional Python-backup LaunchAgent.

set -u
PLIST=~/Library/LaunchAgents/com.example.posting-tool.plist
LABEL="com.example.posting-tool"

echo "[resume_launchagent] loading LaunchAgent: $LABEL"
launchctl load -w "$PLIST" 2>&1 || true

if launchctl list | grep -q "$LABEL"; then
    echo "[resume_launchagent] confirmed: $LABEL is loaded."
else
    echo "[resume_launchagent] WARNING: $LABEL not in launchctl list. Try again."
fi
echo ""
echo "Press any key to close..."
read -n 1 -s
