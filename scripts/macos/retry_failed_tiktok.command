#!/bin/bash
# Retry TikTok-only for any video in failed/ whose TikTok platform receipt
# is status=failed. Other platforms (YT/X/Threads/IG/Pinterest) that
# already succeeded for those videos are NOT re-posted.
#
# Spacing: 2.5h between consecutive retries.
# Earliest slot: now + 30 minutes.
# Receipt's `displayed_schedule` field will show the actual time TikTok
# accepted; if it differs from the planned slot by >5min, the new picker
# code returns `failed` instead of silently submitting at the wrong time.

set -u
cd "$(dirname "$0")/../.." || exit 1

echo ""
echo "============================================="
echo "  RETRY FAILED — TikTok only"
echo "============================================="
echo ""
echo "Videos in failed/ with TikTok=failed:"
for f in failed/*.posted.json; do
    [ -f "$f" ] || continue
    if /usr/bin/python3 -c "
import json, sys
d = json.load(open('$f'))
tt = (d.get('platforms') or {}).get('tiktok') or {}
sys.exit(0 if tt.get('status') == 'failed' else 1)
" 2>/dev/null; then
        echo "  $(basename "$f" .posted.json)"
    fi
done
echo ""
echo "Running retry-failed --platform tiktok ..."
echo ""

/usr/bin/python3 schedule_batch.py retry-failed --platform tiktok
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
echo "Latest TikTok screenshots (look for *_schedule_verified or *_schedule_inputs_unfillable):"
ls -t logs/screenshots/tiktok_*.png 2>/dev/null | head -8 | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "Press any key to close..."
read -n 1 -s
