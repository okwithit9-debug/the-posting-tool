#!/bin/bash
# Double-click this file in Finder to kill the in-flight dispatch.
# It will:
#   1. SIGTERM the schedule_batch.py process (lets it finish whatever Playwright
#      step it's mid-call on, then exits cleanly without starting the next platform).
#   2. If that doesn't take within 8s, SIGKILL it.
#   3. Clean up any orphan Playwright/Chromium processes spawned by the dispatch.
#
# After running this, fix your code, then run retry-failed via:
#   date > .dispatch_queue/trigger.txt
# (the LaunchAgent's next dispatch will only re-run videos that didn't finalize all-ok).

set -u

echo "[kill_dispatch] looking for schedule_batch.py dispatch process..."
PIDS=$(pgrep -f "schedule_batch.py dispatch" || true)

if [ -z "$PIDS" ]; then
    echo "[kill_dispatch] no schedule_batch.py dispatch process is running. Nothing to kill."
else
    echo "[kill_dispatch] found PID(s): $PIDS"
    echo "[kill_dispatch] sending SIGTERM..."
    kill $PIDS 2>/dev/null || true

    # Wait up to 8s for graceful exit.
    for i in 1 2 3 4 5 6 7 8; do
        sleep 1
        STILL=$(pgrep -f "schedule_batch.py dispatch" || true)
        if [ -z "$STILL" ]; then
            echo "[kill_dispatch] process exited cleanly after ${i}s."
            break
        fi
    done

    STILL=$(pgrep -f "schedule_batch.py dispatch" || true)
    if [ -n "$STILL" ]; then
        echo "[kill_dispatch] still alive after 8s — sending SIGKILL to $STILL"
        kill -9 $STILL 2>/dev/null || true
    fi
fi

# Mop up orphan Playwright Chromium spawned by the dispatch.
echo "[kill_dispatch] cleaning up Playwright Chromium orphans..."
pkill -f "posting_tool_chrome" 2>/dev/null || true
sleep 1

# Show what's still around so the user can sanity-check.
echo ""
echo "[kill_dispatch] remaining schedule_batch / playwright processes (should be empty):"
pgrep -fl "schedule_batch.py|posting_tool_chrome" || echo "  (none)"

echo ""
echo "[kill_dispatch] done. You can close this window."
echo "Press any key to exit..."
read -n 1 -s
