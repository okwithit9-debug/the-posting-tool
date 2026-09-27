#!/bin/bash
# "Posting Tool Go" — manual dispatch trigger.
# Double-click in Finder. Runs schedule_batch.py dispatch on everything
# in inbox/ across YouTube + X + Threads + TikTok + Instagram + Pinterest.
# Reddit is auto-skipped (topics.json paused_platforms).
#
# Reads profile.json / env for handle, CTA host, and Chrome attach.
# There are no shipped brand defaults. Native Schedule only.
#
# Spacing: 2.5h between consecutive videos in this batch.
# Cross-dispatch floor: respects state.last_scheduled_for so this batch
# doesn't cluster against a previous batch.
# Earliest slot: now + 30 minutes.
#
# Native Schedule only. Missing Schedule is a wall — never Post now.

set -u
cd "$(dirname "$0")/../.." || exit 1

echo ""
echo "============================================="
echo "  POSTING TOOL GO — manual dispatch"
echo "============================================="
echo ""
echo "Videos in inbox/ at start:"
ls inbox/*.mp4 2>/dev/null | xargs -n1 basename | sed 's/^/  /'
N=$(ls inbox/*.mp4 2>/dev/null | wc -l | tr -d ' ')
if [ "$N" = "0" ]; then
    echo "  (none)"
    echo ""
    echo "Nothing to dispatch. Drop video + sidecar files into inbox/ first."
    echo ""
    echo "Press any key to close..."
    read -n 1 -s
    exit 0
fi

echo ""
echo "Running dispatch..."
echo ""

/usr/bin/python3 schedule_batch.py dispatch
RC=$?

echo ""
echo "============================================="
echo "  DISPATCH COMPLETE — exit code: $RC"
echo "============================================="
echo ""

TODAY=$(date +%Y-%m-%d)
echo "posted/$TODAY/:"
ls posted/$TODAY/*.posted.json 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "failed/ (today):"
ls failed/*.posted.json 2>/dev/null | grep "$TODAY" | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "inbox/ remaining:"
ls inbox/*.mp4 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/^/  /' || echo "  (none)"
echo ""
echo "Tip: any video in failed/ can be retried with:"
echo "  python3 schedule_batch.py retry-failed --video <basename>"
echo "  (only re-runs platforms that failed; doesn't repost the ones that succeeded)"
echo ""
echo "Press any key to close..."
read -n 1 -s
