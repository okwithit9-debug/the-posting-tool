"""Browser readers: navigate (read-only) to each native scheduled list,
capture HTML + a screenshot, and hand the HTML to the pure parsers.

BEST-EFFORT: flows and selectors are unverified against live accounts.
Any surprise -> snapshot ``unknown`` (never "empty", never a guess).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .parsers import parse_native_list
from .readonly import ReadOnlyPage


def _shot_path(snap_dir: Path, check: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return snap_dir / "shots" / f"{check}_{ts}.png"


def _finish(ro: ReadOnlyPage, check: str, snap_dir: Path, *, tz, handle: Optional[str],
            html: Optional[str] = None) -> dict:
    html = html if html is not None else ro.html()
    shot = ro.screenshot(_shot_path(snap_dir, check))
    snap = parse_native_list(check, html, now=datetime.now(timezone.utc), tz=tz, expected_handle=handle)
    snap["screenshot"] = shot
    snap["actions"] = list(ro.actions)
    return snap


def read_x(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    try:
        # Load home first so the account chip (handle) is in the DOM.
        ro.goto("https://x.com/home")
        ro.settle(2500)
        ro.goto(urls.get("x") or "https://x.com/compose/post/unsent/scheduled")
        ro.settle()
        ro.scroll(2)
        return _finish(ro, "x", snap_dir, tz=tz, handle=handle)
    finally:
        # Never Escape on X: navigate away from the modal instead.
        try:
            ro.goto("https://x.com/home")
        except Exception:
            ro.leave()


def read_tiktok(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    try:
        # ONLY the Studio Content list; never /upload.
        ro.goto("https://www.tiktok.com/tiktokstudio/content")
        ro.settle(5000)
        ro.scroll(2)
        return _finish(ro, "tiktok", snap_dir, tz=tz, handle=handle)
    finally:
        ro.leave()


def read_instagram(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    try:
        ro.goto(urls.get("instagram") or "https://business.facebook.com/latest/planner")
        ro.settle(5000)
        # Prefer the list view when a toggle is present (navigation only).
        ro.click_nav("List", role="tab") or ro.click_nav("List view")
        ro.settle(2000)
        return _finish(ro, "instagram", snap_dir, tz=tz, handle=handle)
    finally:
        ro.leave()


def read_pinterest(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    h = (handle or "").strip().lstrip("@")
    if not h:
        return {"platform": "pinterest", "status": "unknown", "items": [],
                "note": "no expected Pinterest handle configured"}
    try:
        ro.goto(urls.get("pinterest") or f"https://www.pinterest.com/{h}/_created/")
        ro.settle(4000)
        ro.click_nav("Scheduled Pins")
        ro.settle(2000)
        return _finish(ro, "pinterest", snap_dir, tz=tz, handle=handle)
    finally:
        ro.leave()


def read_threads(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    """Composer -> Drafts -> Scheduled. Only with an EMPTY composer."""
    base = {"platform": "threads", "check": "threads", "items": []}
    ro.goto("https://www.threads.com/")
    ro.settle(3000)
    opened = ro.click_nav("Create", role="button") or ro.click_nav("What's new?")
    if not opened:
        ro.leave()
        return {**base, "status": "unknown", "note": "composer trigger not found"}
    state = ro.eval_readonly("threads_composer_state") or {}
    if not state.get("open"):
        ro.leave()
        return {**base, "status": "unknown", "note": "composer did not open"}
    if state.get("text_len") or state.get("media"):
        # Something is already in the composer. Touch nothing (leaving could
        # save a draft or discard someone's work). Human step.
        return {**base, "status": "blocked",
                "note": "composer is NOT empty; stopped without touching it (human step)"}
    if not (ro.click_nav("Drafts") and (ro.click_nav("Scheduled") or ro.click_nav("Scheduled posts"))):
        ro.goto("https://www.threads.com/")  # composer still empty -> safe to leave
        return {**base, "status": "unknown", "note": "Drafts/Scheduled list not found"}
    ro.settle(1500)
    try:
        return _finish(ro, "threads", snap_dir, tz=tz, handle=handle)
    finally:
        ro.goto("https://www.threads.com/")


def read_youtube_studio(ro: ReadOnlyPage, snap_dir: Path, *, tz, handle, urls) -> dict:
    ro.goto("https://studio.youtube.com/")
    ro.settle(4000)
    m = re.search(r"/channel/(UC[\w-]{10,})", ro.url or "")
    if not m:
        ro.leave()
        return {"platform": "youtube", "check": "youtube_studio", "status": "unknown", "items": [],
                "note": "channel id not found in Studio URL"}
    chan = m.group(1)
    merged: Optional[dict] = None
    try:
        for tab in ("upload", "short"):
            ro.goto(f"https://studio.youtube.com/channel/{chan}/videos/{tab}")
            ro.settle(4000)
            snap = _finish(ro, "youtube_studio", snap_dir, tz=tz, handle=handle or chan)
            if merged is None:
                merged = snap
            elif snap["status"] != "ok" or merged["status"] != "ok":
                merged = {**merged, "status": "unknown", "items": [],
                          "note": f"{merged.get('note')}; {snap.get('note')}"}
            else:
                merged["items"].extend(snap["items"])
        return merged or {"platform": "youtube", "status": "unknown", "items": []}
    finally:
        ro.leave()


READERS = {
    "x": read_x,
    "tiktok": read_tiktok,
    "instagram": read_instagram,
    "pinterest": read_pinterest,
    "threads": read_threads,
    "youtube_studio": read_youtube_studio,
}
