"""
platforms/_chrome.py — shared Playwright Chrome helper.

Every Playwright-driven platform (X, TikTok, IG, Threads, Pinterest) opens
Chrome through `chrome_session()`. It launches a real Chrome (not headless
chromium) with a *dedicated persistent profile* so logins stick across runs,
and applies playwright-stealth to mask common automation fingerprints.

Why a dedicated profile (not the user's daily Chrome):
  - macOS Chrome locks ~/Library/Application Support/Google/Chrome/Default
    while running, so Playwright would have to wait for Chrome to be closed.
  - A separate profile means the posting tool can run while the user keeps
    using their normal browser.

First-run UX:
  - Profile lives at ~/.posting_tool_chrome
  - First time you launch it, no logins exist — run setup_playwright_logins.py
    once, log into all 6 platforms in the window that opens, then close it.
  - All subsequent runs use the saved cookies.

Usage:
    from platforms._chrome import chrome_session

    with chrome_session() as page:
        page.goto("https://x.com/compose/post")
        ...
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

PROFILE_DIR = Path.home() / ".posting_tool_chrome"


# A common modern Chrome on macOS user agent. Stealth handles most of the
# fingerprint masking, but we override UA explicitly to avoid the
# "HeadlessChrome" string when Playwright's default user-agent is in play.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)



def _browser_timezone() -> str:
    """Timezone for the Playwright context: env, then profile.json, then UTC."""
    name = (os.environ.get("POSTING_TOOL_TIMEZONE") or "").strip()
    if not name:
        try:
            from platforms._safety_locks import load_user_profile
            name = (load_user_profile().timezone_name or "").strip()
        except Exception:
            name = ""
    return name or "UTC"

def clear_and_type(page, locator, text: str, delay: int = 10) -> None:
    """Focus a field, wipe whatever's in it (including filename auto-populates
    from set_input_files), then type fresh text.

    Why this exists: TikTok and a few other platforms auto-populate the caption
    field with the uploaded video's filename (e.g.
    'example_clip_2026-04-25'). Without an explicit clear, our
    `type()` would *append* the real caption after the filename. This helper
    makes sure the field is empty before we type.

    Uses ControlOrMeta+A (Cmd on macOS, Ctrl on Linux/Windows) so it works
    across platforms. For plain <input>/<textarea>, we additionally call
    locator.fill("") which is a stronger reset for native inputs.
    """
    locator.click()
    # First try the input-style fill (works on <input>/<textarea>).
    try:
        locator.fill("")
    except Exception:
        # contenteditable or unusual element — fall back to keyboard select-all.
        pass
    # Belt-and-suspenders for contenteditable elements.
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.press("Backspace")
    locator.type(text, delay=delay)


class ChromeAttachError(RuntimeError):
    """Could not attach to the already-open Chrome profile named in config."""


def _read_attached_profile(context) -> tuple[str, str]:
    """Best-effort Profile Path from chrome://version. No brand defaults."""
    from platforms._safety_locks import load_user_profile

    profile = load_user_profile()
    directory = profile.chrome_profile_directory
    name = profile.chrome_profile_name
    page = None
    try:
        page = context.new_page()
        page.goto("chrome://version", wait_until="domcontentloaded", timeout=8_000)
        text = page.inner_text("body") or ""
        for line in text.splitlines():
            if "profile path" in line.lower():
                path = line.split(":", 1)[-1].strip() if ":" in line else line
                tail = Path(path).name
                if tail:
                    directory = tail
        # Profile *name* (the label in Chrome's profile picker) is not always
        # on chrome://version. Keep the configured name unless the page
        # clearly shows a different profile-path directory.
    except Exception:
        # chrome://version is not always readable over CDP. Config values stay.
        pass
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
    return directory, name


@contextmanager
def chrome_session(
    *,
    headless: Optional[bool] = None,
    viewport: tuple[int, int] = (1280, 900),
    profile_dir: Path = PROFILE_DIR,
) -> Iterator:
    """Context-manage a Playwright Chrome page tied to a persistent profile.

    Yields a `page` ready to navigate. The browser is closed on exit.

    Attach-only path (profile.json chrome.attach_only or
    POSTING_TOOL_ATTACH_CHROME): connect to the already-open Chrome profile
    named in config via CDP. Never launch a second Chrome /
    TPT-Content-Manager-Chrome. The user's window is not closed on exit.
    There is no default Chrome profile name.

    Args:
      headless: if None, reads POSTING_TOOL_HEADLESS env var (default False).
                Run headed for first runs (you can watch); headless once stable.
      viewport: browser window size.
      profile_dir: where Chrome stores cookies/local-storage. Default:
                   ~/.posting_tool_chrome. Ignored on the attach-only path.
    """
    from playwright.sync_api import sync_playwright

    from platforms._safety_locks import (
        chrome_launch_decision,
        pause_on_wall,
        verify_attached_chrome_profile,
    )

    if headless is None:
        headless = os.environ.get("POSTING_TOOL_HEADLESS", "0") in ("1", "true", "yes")

    decision = chrome_launch_decision(would_launch_named=None)
    if not decision.ok:
        err = decision.error
        raise ChromeAttachError((err.message if err else "Chrome attach refused") +
                                ((" " + err.human_step) if err and err.human_step else ""))

    if decision.mode == "attach_cdp":
        # Never launch. Attach only. Do not close Chrome on exit.
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp(decision.cdp_url)
            except Exception as exc:
                wall = pause_on_wall(
                    "chrome_crash_reopen",
                    detail=(
                        f"Could not attach to already-open Chrome at {decision.cdp_url} "
                        f"({type(exc).__name__}: {exc}). Do not click Reopen. Do not "
                        "launch TPT-Content-Manager-Chrome."
                    ),
                )
                raise ChromeAttachError(wall.message + " " + (wall.human_step or "")) from exc
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            directory, name = _read_attached_profile(context)
            mismatch = verify_attached_chrome_profile(
                profile_directory=directory, profile_name=name
            )
            if mismatch:
                raise ChromeAttachError(mismatch.message + " " + (mismatch.human_step or ""))
            if context.pages:
                page = context.pages[0]
            else:
                page = context.new_page()
            try:
                yield page
            finally:
                # Detach only. Closing the context would quit the user's Chrome.
                pass
        return

    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # launch_persistent_context: persistent cookies/storage across runs.
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",  # Use the user's installed Chrome, not Playwright's Chromium.
            headless=headless,
            viewport={"width": viewport[0], "height": viewport[1]},
            user_agent=DEFAULT_UA,
            args=[
                "--disable-blink-features=AutomationControlled",
                # Belt-and-suspenders for the automation infobar.
                "--no-default-browser-check",
                "--no-first-run",
            ],
            # `--enable-automation` is the flag that draws the
            # "Chrome is being controlled by automated test software" banner.
            # Excluding it from the default args removes the banner entirely.
            ignore_default_args=["--enable-automation"],
            # Locale/timezone matter for some bot-detection signals.
            locale="en-US",
            timezone_id=_browser_timezone(),
        )

        # Mask the navigator.webdriver flag too. Some sites read this directly.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        # Apply stealth tweaks (navigator.webdriver=false, etc.).
        try:
            from playwright_stealth import stealth_sync  # type: ignore
            for page in context.pages:
                stealth_sync(page)
            # Hook future pages too:
            context.on("page", lambda pg: stealth_sync(pg))
        except ImportError:
            # Not fatal — most platforms work without stealth, TikTok/IG may not.
            pass

        # Reuse the pre-existing about:blank page rather than opening a fresh one
        # so we don't end up with extra tabs.
        if context.pages:
            page = context.pages[0]
        else:
            page = context.new_page()

        try:
            yield page
        finally:
            try:
                context.close()
            except Exception:
                pass
