"""Read-only wrapper around a Playwright page.

The wrapped page can ONLY: navigate to allow-listed URLs, wait, scroll,
read HTML/text, take screenshots, and click a short allow-list of
*navigation* labels. It has no keyboard, no typing, no file inputs.
It refuses any click whose label looks like a commit/destructive action
(Schedule, Post, Publish, Share, Discard, Delete, Save, Cancel, ...).
Pages are left by navigating away — never Escape (there is no key API).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

FORBIDDEN_LABEL = re.compile(
    r"\b(schedule|post|post now|publish|publish now|publish immediately|share|share now|"
    r"send|tweet|discard|delete|remove|unschedule|cancel|save|save to drafts|edit|"
    r"reschedule|upload|create pin|now|boost|promote|confirm|continue|ok|yes|reopen|"
    r"log ?out|sign ?out|switch account)\b",
    re.I,
)

# Allowed navigation targets per check.
ALLOWED = {
    "x": [("x.com", re.compile(r"^/(compose/post/unsent/scheduled|home)?/?$"))],
    "tiktok": [("www.tiktok.com", re.compile(r"^/tiktokstudio/content/?$"))],
    "instagram": [("business.facebook.com", re.compile(r"^/latest/(planner|content_calendar|posts)"))],
    "pinterest": [("www.pinterest.com", re.compile(r"^/[\w.-]+/_created/?$|^/$"))],
    "threads": [("www.threads.com", re.compile(r"^/?$"))],
    "youtube_studio": [("studio.youtube.com", re.compile(r"^/(channel/[\w-]+/videos/(upload|short))?/?$"))],
}

# Clickable navigation labels (exact, case-insensitive) per check.
NAV_LABELS = {
    "threads": {"create", "new thread", "what's new?", "drafts", "scheduled", "scheduled posts", "more"},
    "instagram": {"list", "list view", "scheduled"},
    "pinterest": {"scheduled pins", "scheduled"},
    "youtube_studio": {"shorts", "videos"},
    "x": set(),
    "tiktok": set(),
}


class ReadOnlyViolation(RuntimeError):
    pass


def url_allowed(check: str, url: str) -> bool:
    if url == "about:blank":
        return True
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    for host, path_re in ALLOWED.get(check, []):
        if u.hostname == host and path_re.search(u.path or "/"):
            return True
    return False


def label_allowed(check: str, label: str) -> bool:
    lab = " ".join((label or "").split()).lower()
    if not lab:
        return False
    if lab not in NAV_LABELS.get(check, set()):
        return False
    # Nav labels never collide with commit words, but double-check.
    if lab not in {"scheduled", "scheduled posts", "scheduled pins"} and FORBIDDEN_LABEL.search(lab):
        return False
    return True


class ReadOnlyPage:
    def __init__(self, page, check: str):
        self._page = page
        self.check = check
        self.actions: list[str] = []

    @property
    def url(self) -> str:
        return self._page.url

    def goto(self, url: str, *, timeout_ms: int = 45_000) -> None:
        if not url_allowed(self.check, url):
            raise ReadOnlyViolation(f"navigation not allowed for {self.check}: {url}")
        self.actions.append(f"goto {url}")
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def settle(self, ms: int = 3500) -> None:
        try:
            self._page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        self._page.wait_for_timeout(ms)

    def scroll(self, times: int = 3) -> None:
        for _ in range(max(0, times)):
            self._page.mouse.wheel(0, 1600)
            self._page.wait_for_timeout(700)

    def html(self) -> str:
        return self._page.content()

    def text(self) -> str:
        try:
            return self._page.inner_text("body") or ""
        except Exception:
            return ""

    def screenshot(self, path: Path) -> Optional[str]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception:
            return None

    def click_nav(self, label: str, *, role: Optional[str] = None, timeout_ms: int = 4000) -> bool:
        """Click an allow-listed navigation control. Returns False if absent."""
        if not label_allowed(self.check, label):
            raise ReadOnlyViolation(f"click not allowed for {self.check}: {label!r}")
        loc = None
        try:
            if role:
                loc = self._page.get_by_role(role, name=label, exact=True).first
            else:
                loc = self._page.get_by_label(label, exact=True).first
                if not loc.count():
                    loc = self._page.get_by_text(label, exact=True).first
            if not loc.count() or not loc.is_visible(timeout=timeout_ms):
                return False
            # Re-check the element's own accessible text right before clicking.
            seen = (loc.get_attribute("aria-label") or loc.inner_text(timeout=1000) or "").strip()
            if seen and seen.lower() != label.lower() and FORBIDDEN_LABEL.search(seen):
                raise ReadOnlyViolation(f"element text {seen!r} looks like a commit control")
            self.actions.append(f"click {label!r}")
            loc.click(timeout=timeout_ms)
            self._page.wait_for_timeout(900)
            return True
        except ReadOnlyViolation:
            raise
        except Exception:
            return False

    def eval_readonly(self, script_name: str):
        """Run one of a few fixed, side-effect-free scripts."""
        script = READONLY_SCRIPTS[script_name]
        return self._page.evaluate(script)

    def leave(self) -> None:
        """Leave by navigating away. Never Escape."""
        self.actions.append("leave about:blank")
        try:
            self._page.goto("about:blank", timeout=15_000)
        except Exception:
            pass


READONLY_SCRIPTS = {
    # Threads: is the open composer empty? (no text, no attached media)
    "threads_composer_state": """() => {
        const dlg = document.querySelector('[role="dialog"]');
        if (!dlg) return {open: false};
        const boxes = [...dlg.querySelectorAll('[contenteditable="true"], textarea')];
        const text = boxes.map(b => (b.innerText || b.value || '').trim()).join('');
        const media = dlg.querySelectorAll('video, img[src^="blob:"]').length;
        return {open: true, text_len: text.length, media: media};
    }""",
}
