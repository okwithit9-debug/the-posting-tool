#!/bin/bash
# Retry YouTube-only for any video in failed/ whose YouTube platform receipt
# is status=failed. Other platforms that already succeeded for those videos
# are NOT re-posted.
#
# Use this the morning after a dispatch that hit YouTube's daily upload cap
# (uploadLimitExceeded). Quota resets at ~midnight Pacific.
#
# Spacing: 2.5h between consecutive retries (purely cosmetic for YouTube,
# since publishAt is set per-video; doesn't affect upload pacing).
# Earliest slot: now + 30 minutes.

set -u
cd "$(dirname "$0")/../.." || exit 1

echo ""
echo "============================================="
echo "  RETRY FAILED — YouTube only"
echo "============================================="
echo ""
echo "Videos in failed/ with YouTube=failed:"
ANY=0
for f in failed/*.posted.json; do
    [ -f "$f" ] || continue
    if /usr/bin/python3 -c "
import json, sys
d = json.load(open('$f'))
yt = (d.get('platforms') or {}).get('youtube') or {}
sys.exit(0 if yt.get('status') == 'failed' else 1)
" 2>/dev/null; then
        echo "  $(basename "$f" .posted.json)"
        ANY=1
    fi
done
if [ "$ANY" = "0" ]; then
    echo "  (none — nothing to retry)"
    echo ""
    echo "Press any key to close..."
    read -n 1 -s
    exit 0
fi
echo ""
echo "Running retry-failed --platform youtube ..."
echo ""

/usr/bin/python3 schedule_batch.py retry-failed --platform youtube
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
echo "Press any key to close..."
read -n 1 -s
