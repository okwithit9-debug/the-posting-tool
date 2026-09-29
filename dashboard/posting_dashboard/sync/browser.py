"""Open ONE browser tab for the read-only checks. Attach-only.

Connects over CDP to a Chrome that YOU already opened (signed in to your
accounts) with ``--remote-debugging-port``, opens one extra tab, and closes
only that tab. It never launches Chrome, never opens a second profile, and
never closes your windows. No CDP URL, or nothing listening, means every
browser check is reported ``blocked``.
"""

from __future__ import annotations

import contextlib
from typing import Iterator


class BrowserUnavailable(RuntimeError):
    pass


@contextlib.contextmanager
def open_page(cdp_url: str) -> Iterator:
    if not cdp_url:
        raise BrowserUnavailable(
            "no CDP URL configured (set browser.cdp_url or profile.json chrome.cdp)")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable("playwright not installed") from exc
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:  # noqa: BLE001
            raise BrowserUnavailable(
                f"could not attach over CDP at {cdp_url}: {type(exc).__name__}") from exc
        ctx = browser.contexts[0] if browser.contexts else None
        if ctx is None:
            raise BrowserUnavailable("attached Chrome has no browser context")
        page = ctx.new_page()
        try:
            yield page
        finally:
            try:
                page.close()
            except Exception:
                pass
