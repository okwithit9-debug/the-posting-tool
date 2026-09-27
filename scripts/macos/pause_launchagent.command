#!/bin/bash
# Double-click to STOP the optional Python-backup LaunchAgent.
# Re-enable later with resume_launchagent.command or install_launchd.sh.

set -u

PLIST=~/Library/LaunchAgents/com.example.posting-tool.plist
LABEL="com.example.posting-tool"

echo "[pause_launchagent] unloading LaunchAgent: $LABEL"
launchctl unload "$PLIST" 2>&1 || true

if launchctl list | grep -q "$LABEL"; then
    echo "[pause_launchagent] WARNING: $LABEL still in launchctl list. Try again."
else
    echo "[pause_launchagent] confirmed: $LABEL is no longer loaded."
fi
echo ""
echo "[pause_launchagent] LaunchAgent paused."
echo "[pause_launchagent] Resume with resume_launchagent.command."
echo ""
echo "Press any key to close..."
read -n 1 -s
