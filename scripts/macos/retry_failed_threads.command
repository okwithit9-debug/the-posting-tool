#!/bin/bash
# Retry Threads-only for any video in failed/ whose Threads platform receipt
# is status=failed. Other platforms (YT/TikTok/IG/Pinterest) that already
# succeeded for those videos are NOT re-posted.
#
# Created 2026-05-05 after fixing pick_date_threads_style in _pickers.py
# (the role="gridcell" anchor patch). Use this once after the patch lands
# to clear the backlog of 7 failed Threads receipts; after that, regular
# dispatch should handle Threads inline.
#
# Spacing: 2.5h between consecutive retries.
# Earliest slot: now + 30 minutes.

set -u
cd "$(dirname "$0")/../.." || exit 1

echo ""
echo "============================================="
echo "  RETRY FAILED — Threads only"
echo "============================================="
echo ""
echo "Videos in failed/ with Threads=failed:"
for f in failed/*.posted.json; do
    [ -f "$f" ] || continue
    if /usr/bin/python3 -c "
import json, sys
d = json.load(open('$f'))
th = (d.get('platforms') or {}).get('threads') or {}
sys.exit(0 if th.get('status') == 'failed' else 1)
" 2>/dev/null; then
        echo "  $(basename "$f" .posted.json)"
    fi
done
echo ""
echo "Running retry-failed --platform threads ..."
echo ""

/usr/bin/python3 schedule_batch.py retry-failed --platform threads
RC=$?

echo ""
echo "============================================="
echo "  RETRY COMPLETE — exit code: $RC"
echo "============================================="
echo ""

TODAY=$(date +%Y-%m-%d)
echo "posted/$TODAY/ (recent):"
ls -t posted/$TODAY/*.posted.json 2>/dev/null | head -10 | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "failed/ (still failing):"
ls failed/*.posted.json 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "Latest Threads screenshots (look for *_scheduled or *_schedule_flow_failed):"
ls -t logs/screenshots/threads_*.png 2>/dev/null | head -8 | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "Press any key to close..."
read -n 1 -s
