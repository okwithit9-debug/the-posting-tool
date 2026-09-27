#!/bin/bash
# "Reset Floor" — manual override for queue drift.
# Double-click in Finder. Wipes state.last_scheduled_for so the next dispatch
# starts at now + earliest_offset_minutes instead of being anchored to the
# previous batch's tail.
#
# When to use this: you've noticed new dispatches scheduling posts unusually
# far in the future (e.g. 2-3 days out) because last_scheduled_for has drifted.
# The 24h FLOOR_CAP_HOURS in schedule_batch.py also self-heals this, but this
# is the manual override.

set -u
cd "$(dirname "$0")/../.." || exit 1

echo ""
echo "============================================="
echo "  RESET FLOOR — clear cross-dispatch anchor"
echo "============================================="
echo ""

echo "Current state.json:"
cat state.json 2>/dev/null || echo "  (state.json missing)"
echo ""
echo ""

/usr/bin/python3 schedule_batch.py reset-floor
RC=$?

echo ""
echo "New state.json:"
cat state.json 2>/dev/null || echo "  (state.json missing)"
echo ""
echo ""
echo "============================================="
echo "  EXIT CODE: $RC"
echo "============================================="
echo ""
echo "Press any key to close..."
read -n 1 -s
