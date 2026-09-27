"""
setup_playwright_logins.py — first-run "warmup" for the Playwright Chrome
profile.

Sign in to each platform inside the dedicated Playwright profile once. The
platforms often show first-time popups on a new browser profile:

  - "Save your login info?"             → click "Not now" / dismiss
  - "Turn on notifications?"            → click "Not now"
  - "Looks like you're using a new device, confirm it's you" → confirm
  - 2FA prompt the first visit          → handle as normal
  - Cookie banner / consent prompt      → reject or accept
  - "Welcome to TikTok Studio" intro    → close
  - "Tour our new pin-builder"          → skip

These dismissals get saved to the profile, so once you do them once, future
dispatches run straight to the upload UI without interruption.

Run this BEFORE your first real dispatch:

    python3 setup_playwright_logins.py

The Playwright Chrome window opens. For each platform:
  1. The script navigates to its composer / main upload URL.
  2. YOU dismiss any popups, sign 2FA prompts, etc.
  3. YOU confirm you can see the actual composer (where you'd type a caption /
     drop a video).
  4. Press Enter in the terminal to advance to the next platform.

Total time: ~5 platforms × ~30 sec = ~3 minutes.
"""

from __future__ import annotations

import sys
from platforms._chrome import chrome_session, PROFILE_DIR

# Land on each platform's actual upload/compose page so the user knows what
# the dispatch will see when it arrives.
PLATFORMS = [
    ("X — composer",
        "https://x.com/compose/post"),
    ("Threads — feed (composer opens via the pencil icon)",
        "https://www.threads.com/"),
    ("TikTok — Studio upload",
        "https://www.tiktok.com/tiktokstudio/upload"),
    ("Instagram — homepage (Reels composer is the + button in left sidebar)",
        "https://www.instagram.com/"),
    ("Pinterest — pin-builder",
        "https://www.pinterest.com/pin-builder/"),
    # YouTube Studio is a fallback target — the dispatch normally uploads via
    # the Data API, but switches to driving studio.youtube.com when the API
    # returns uploadLimitExceeded. Cookies for the Studio login live here in
    # the Playwright profile so the fallback can run unattended.
    ("YouTube Studio — channel dashboard",
        "https://studio.youtube.com/"),
]


def main() -> int:
    print(f"Profile dir: {PROFILE_DIR}")
    print()
    print("This warms up the Playwright profile by walking through each")
    print("platform's upload UI. For each one:")
    print("  1. The Chrome window will navigate to the upload page.")
    print("  2. Dismiss any first-time popups (Save login? Notifications?")
    print("     Welcome screens, cookie banners, 2FA prompts).")
    print("  3. Confirm you can see the actual composer.")
    print("  4. Press Enter here to move to the next platform.")
    print()
    print("If any platform shows a login screen instead of the composer,")
    print("sign in right there in this window — the session is saved to")
    print("the dedicated profile for future runs.")
    print()
    input("Press Enter to start... ")

    # One Chrome session that stays open across all navigations so popup-
    # dismissed state persists between platforms.
    with chrome_session(headless=False) as page:
        for label, url in PLATFORMS:
            print()
            print(f"--- {label} ---")
            print(f"Navigating to {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                print(f"  navigation warning: {e}")
            input("Dismiss popups, confirm composer is visible, press Enter to continue... ")
        print()
        print("All platforms warmed up. Closing Chrome.")

    print(f"Done. Profile at {PROFILE_DIR} should now be popup-free for the next dispatch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
