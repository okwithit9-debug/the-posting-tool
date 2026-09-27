#!/bin/bash
# Double-click this file in Finder to open the Posting Tool's Playwright
# Chrome profile right at the Meta Business Suite composer.
#
# What this does:
#   1. cds to the Posting Tool project
#   2. Runs bs_login.py — opens Playwright Chrome (using the dedicated
#      ~/.posting_tool_chrome profile, separate from your daily Chrome)
#      and navigates to business.facebook.com/latest/composer/?asset_id=...
#   3. The browser stays open until you press Return in this Terminal
#      window.
#   4. Log into Meta / Business Suite in the window. Dismiss any "Save
#      login info?" or "New device, confirm it's you" prompts. The session
#      persists in ~/.posting_tool_chrome — no need to log in again next run.
#   5. When the composer is fully loaded and you can see "Create post" with
#      YOUR brand asset pre-selected in the Post to dropdown, switch back
#      to this Terminal window and press Return to close cleanly.

set -eu
cd "$(dirname "$0")/../.." || exit 1

echo "[bs_login] launching Playwright Chrome → Meta Business Suite composer..."
echo "[bs_login] (separate profile from your normal Chrome — ~/.posting_tool_chrome)"
echo ""

/usr/bin/python3 bs_login.py

echo ""
echo "[bs_login] done. Press any key to close this window..."
read -n 1 -s
