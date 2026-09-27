#!/bin/bash
# retry_failed_all.command — generic one-click retry of everything in failed/.
#
# Runs schedule_batch.py retry-failed with no platform filter: each clip in
# failed/ gets only its failed platforms re-attempted (succeeded platforms
# are never re-posted), staggered 2.5h apart starting now+30min.
#
# Tees to logs/retry_failed.log; writes .retry_failed_done sentinel (exit
# code) at the end so polling code can detect completion.

set -u
cd "$(dirname "$0")/../.." || exit 1

LOG="logs/retry_failed.log"
SENTINEL=".retry_failed_done"

rm -f "$SENTINEL"

{
    echo "=============================================="
    echo "  RETRY ALL FAILED"
    echo "  $(date)"
    echo "=============================================="

    /usr/bin/python3 schedule_batch.py retry-failed
    RC=$?

    echo "=============================================="
    echo "  RETRY COMPLETE — exit code: $RC"
    echo "=============================================="
    echo "$RC" > "$SENTINEL"
} 2>&1 | tee -a "$LOG"
