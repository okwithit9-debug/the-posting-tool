"""Per-platform native scheduled-list parsers (BEST-EFFORT selectors).

None of these selectors were verified against a live account. They are
kept as data so a live check can update them in one place. When they do
not match, the parser returns ``unknown`` and the dashboard shows
"scheduled (unverified)" — it never flags a post "missing" from a page it
could not read.
"""

from __future__ import annotations

import re
from datetime import datetime, tzinfo
from typing import Optional

from ..html_tree import attr_contains, attr_eq, tag_is
from .common import PlatformSpec, parse_list

_SCHED = re.compile(r"\bscheduled\b", re.I)

SPECS: dict[str, PlatformSpec] = {
    # x.com/compose/post/unsent/scheduled — modal list, rows say "Will send on …".
    "x": PlatformSpec(
        platform="x",
        row_selectors=[attr_eq("data-testid", "cellInnerDiv"),
                       attr_contains("data-testid", "unsentpost"),
                       attr_eq("role", "listitem")],
        scheduled_marker=re.compile(r"will send on", re.I),
        time_anchor=re.compile(r"will send on\s+(.{6,60}?\d{1,2}:\d{2}(?:\s*[ap]\.?\s?m\.?)?)", re.I),
        caption_selectors=[attr_eq("data-testid", "tweetText")],
        empty_markers=[re.compile(r"(haven.?t|don.?t have any|no)\s+scheduled\s+(any\s+)?(posts|tweets)", re.I),
                       re.compile(r"nothing scheduled", re.I)],
    ),
    # tiktok.com/tiktokstudio/content — post table; scheduled rows carry "Scheduled".
    "tiktok": PlatformSpec(
        platform="tiktok",
        row_selectors=[attr_contains("data-tt", "postitem"),
                       attr_contains("data-e2e", "post-item"),
                       attr_contains("class", "post-item"),
                       attr_eq("role", "row")],
        scheduled_marker=_SCHED,
        time_anchor=None,
        caption_selectors=[attr_contains("data-tt", "title"),
                           attr_contains("data-e2e", "post-title"),
                           attr_contains("class", "post-title")],
        empty_markers=[re.compile(r"no (scheduled )?(posts|content|videos) yet", re.I),
                       re.compile(r"you haven.?t (posted|scheduled)", re.I)],
        link_pred=lambda h: "/video/" in h,
        link_base="https://www.tiktok.com",
    ),
    # business.facebook.com/latest/planner (list view) — rows with "Scheduled".
    "instagram": PlatformSpec(
        platform="instagram",
        row_selectors=[attr_contains("data-testid", "planner-post"),
                       attr_eq("role", "row"),
                       attr_eq("role", "listitem")],
        scheduled_marker=_SCHED,
        caption_selectors=[attr_contains("data-testid", "post-text")],
        empty_markers=[re.compile(r"no scheduled (posts|content|reels)", re.I),
                       re.compile(r"nothing (is )?scheduled", re.I)],
    ),
    # pinterest.com/<handle>/_created/ — "Scheduled Pins" section.
    "pinterest": PlatformSpec(
        platform="pinterest",
        row_selectors=[attr_contains("data-test-id", "scheduled-pin"),
                       attr_contains("data-test-id", "pinwrapper"),
                       attr_eq("role", "listitem")],
        scheduled_marker=re.compile(r"\b(scheduled|publishing on|publishes on|will publish)\b", re.I),
        time_anchor=re.compile(r"(?:scheduled for|publishing on|publishes on|will publish on|scheduled)\s*:?\s*(.{3,40})", re.I),
        caption_selectors=[attr_contains("data-test-id", "pin-title")],
        empty_markers=[re.compile(r"no scheduled pins", re.I)],
        link_pred=lambda h: h.startswith("/pin/") or "pinterest.com/pin/" in h,
        link_base="https://www.pinterest.com",
    ),
    # Threads composer -> Drafts -> Scheduled list (only with an EMPTY composer).
    "threads": PlatformSpec(
        platform="threads",
        row_selectors=[attr_contains("data-testid", "scheduled"),
                       attr_eq("role", "listitem")],
        scheduled_marker=None,
        time_anchor=re.compile(r"(?:scheduled for|scheduled)?\s*((?:today|tomorrow|mon|tue|wed|thu|fri|sat|sun|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[^|]{2,40}?\d{1,2}:\d{2}\s*[ap]\.?\s?m\.?)", re.I),
        empty_markers=[re.compile(r"no scheduled (threads|posts)", re.I),
                       re.compile(r"nothing scheduled", re.I)],
    ),
    # studio.youtube.com/channel/<id>/videos/{upload,short} — fallback only.
    "youtube_studio": PlatformSpec(
        platform="youtube",
        row_selectors=[tag_is("ytcp-video-row"), attr_eq("role", "row")],
        scheduled_marker=_SCHED,
        caption_selectors=[attr_eq("id", "video-title")],
        empty_markers=[re.compile(r"no content available", re.I)],
        link_pred=lambda h: "/video/" in h and "/edit" in h,
        link_base="https://studio.youtube.com",
    ),
}


def parse_native_list(check: str, html: str, *, now: datetime, tz: tzinfo,
                      expected_handle: Optional[str]) -> dict:
    spec = SPECS[check]
    snap = parse_list(html, spec, now=now, tz=tz, expected_handle=expected_handle)
    snap["check"] = check
    if check == "youtube_studio":
        for it in snap["items"]:
            m = re.search(r"/video/([\w-]{6,})/edit", it.get("url") or "")
            if m:
                it["video_id"] = m.group(1)
    return snap
