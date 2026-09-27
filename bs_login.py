"""
bs_login.py — open the Playwright Chrome profile straight to the Meta
Business Suite composer so the user can interactively log in.

Cookies persist in ~/.posting_tool_chrome (the Posting Tool's dedicated
profile, separate from the user's daily Chrome). Once logged in here, the
schedule_batch.py dispatch will land on the composer fully authenticated.

Usage:
    python3 bs_login.py
    # … log in / dismiss popups in the opened browser window …
    # … then press Return in this terminal to close.
"""

from __future__ import annotations

import json
from pathlib import Path

from platforms._chrome import chrome_session

PROJECT = Path(__file__).resolve().parent
CONFIG = PROJECT / "config.json"


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    asset_id = cfg["instagram"].get("business_suite_asset_id", "")
    business_id = cfg["instagram"].get("business_suite_business_id", "")
    if not asset_id or not business_id:
        print("ERROR: config.json → instagram → business_suite_asset_id / "
              "business_suite_business_id are missing.")
        return 1

    url = (
        "https://business.facebook.com/latest/composer/"
        f"?asset_id={asset_id}"
        f"&business_id={business_id}"
        "&context_ref=HOME"
        "&nav_ref=internal_nav"
        "&ref=biz_web_home_create_post"
    )
    print(f"[bs_login] opening: {url}")
    print("[bs_login] (Chrome will pop open headed — use the persistent profile)")
    print()

    # Force headed regardless of POSTING_TOOL_HEADLESS env so the user can interact.
    with chrome_session(headless=False) as page:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as e:
            print(f"[bs_login] WARN: navigate hit an error ({e}). The browser is still open — log in manually.")

        print("[bs_login] browser is open. Log into Meta / Business Suite.")
        print("[bs_login] Dismiss any 'Save login?' / 'New device' / cookie prompts.")
        print("[bs_login] Once you see 'Create post' with YOUR brand asset pre-selected,")
        print("[bs_login] press Return here to close the browser cleanly.")
        try:
            input("[bs_login] >>> Press Return when done logging in... ")
        except (EOFError, KeyboardInterrupt):
            pass

    print("[bs_login] browser closed. Cookies saved to ~/.posting_tool_chrome.")
    print("[bs_login] You can now run: date > .dispatch_queue/trigger.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
