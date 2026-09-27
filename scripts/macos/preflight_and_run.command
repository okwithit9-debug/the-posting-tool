#!/bin/bash
# Optional example launcher. ZERO brand / handle / Chrome-profile defaults.
# Copy profile.example.json → profile.json and fill the placeholders, then
# run scripts/macos/posting_tool_go.command or this script. schedule_batch.py reads config.

set -u
cd "$(dirname "$0")/../.." || exit 1

if [ ! -f profile.json ]; then
  echo "Missing profile.json."
  echo "Copy profile.example.json to profile.json and fill YOUR_HANDLE,"
  echo "yourdomain.com, and the Chrome profile name you already opened."
  echo "This launcher will not invent a brand, handle, or Chrome profile."
  echo "Press any key to close..."
  read -n 1 -s
  exit 2
fi

echo ""
echo "============================================="
echo "  POSTING TOOL — profile.json / env only"
echo "============================================="
echo ""

/usr/bin/python3 schedule_batch.py preflight
PF=$?
if [ "$PF" != "0" ]; then
  echo "Preflight refused the batch. Do not Post now."
  echo "Press any key to close..."
  read -n 1 -s
  exit "$PF"
fi

echo ""
echo "Running dispatch..."
echo ""
/usr/bin/python3 schedule_batch.py dispatch
RC=$?
echo ""
echo "DISPATCH COMPLETE — exit code: $RC"
echo "Press any key to close..."
read -n 1 -s
exit "$RC"
