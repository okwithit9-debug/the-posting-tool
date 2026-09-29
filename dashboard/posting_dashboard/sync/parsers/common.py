"""Shared, defensive native-list parsing.

Every parser returns a snapshot dict (see ``posting_dashboard.snapshots``).
Rules (never guess):

- A sign-in / verify wall -> ``blocked``.
- Expected handle not found on the page -> ``unknown``.
- Rows found but ANY row's time unreadable -> ``unknown``.
- No rows and no positive empty-state marker -> ``unknown``.
- Only then ``ok`` (with items, or empty because an empty marker was seen).

ALL selectors / marker texts here are BEST-EFFORT guesses for UIs that
change often; they were not verified against live accounts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Callable, Optional

from ...ledger import caption_preview
from ...timeparse import parse_human_datetime
from ..html_tree import Node, parse_html

WALL_PATTERNS = [
    re.compile(r"verify\s*it['’]?s\s*you", re.I),
    re.compile(r"confirm\s*it['’]?s\s*you", re.I),
    re.compile(r"verify\s+your\s+identity", re.I),
    re.compile(r"\bcaptcha\b", re.I),
    re.compile(r"suspicious (login|activity)", re.I),
    re.compile(r"\b(log|sign)\s*in to (x|twitter|tiktok|instagram|pinterest|threads|facebook|youtube|continue)\b", re.I),
    re.compile(r"\bsession (has )?expired\b", re.I),
]

BOILERPLATE = re.compile(
    r"\b(will send on|scheduled for|scheduled|publishing on|publishes on|edit|delete|more options|"
    r"views?|likes?|comments?|shares?|public|private|unlisted|draft|reel|video|post|pin)\b",
    re.I,
)


@dataclass
class PlatformSpec:
    platform: str
    # Row containers, tried in order; first selector with hits wins.
    row_selectors: list[Callable[[Node], bool]] = field(default_factory=list)
    # A row counts only if its text matches this (e.g. "Scheduled").
    scheduled_marker: Optional[re.Pattern] = None
    # Optional regex whose group(1) holds the datetime text.
    time_anchor: Optional[re.Pattern] = None
    # Caption containers inside a row (first hit wins).
    caption_selectors: list[Callable[[Node], bool]] = field(default_factory=list)
    # Positive "the list is empty" markers.
    empty_markers: list[re.Pattern] = field(default_factory=list)
    # Link inside a row (href predicate) for "open on platform".
    link_pred: Optional[Callable[[str], bool]] = None
    link_base: str = ""
    # If rows are found but none carry the scheduled marker: False -> unknown.
    trust_empty_when_rows_unscheduled: bool = False


def detect_wall(text: str) -> Optional[str]:
    for pat in WALL_PATTERNS:
        m = pat.search(text or "")
        if m:
            return m.group(0)
    return None


def handle_present(root: Node, html: str, handle: Optional[str]) -> bool:
    h = (handle or "").strip().lstrip("@").lower()
    if not h:
        return False
    low = (html or "").lower()
    return (f"@{h}" in low) or (f"/{h}" in low) or (f'"{h}"' in low) or (f">{h}<" in low) \
        or (f" {h} " in f" {root.text().lower()} ")


def _clean_caption(row_text: str, time_text: str) -> str:
    t = row_text.replace(time_text, " ") if time_text else row_text
    t = BOILERPLATE.sub(" ", t)
    t = re.sub(r"\b\d{1,2}:\d{2}\s*[AP]M\b", " ", t, flags=re.I)
    return " ".join(t.split())


def _row_time(text: str, spec: PlatformSpec, *, now: datetime, tz: tzinfo):
    if spec.time_anchor is not None:
        m = spec.time_anchor.search(text)
        if not m:
            return None, False, ""
        chunk = m.group(1)
        dt, date_only = parse_human_datetime(chunk, now=now, tz=tz)
        return dt, date_only, m.group(0)
    dt, date_only = parse_human_datetime(text, now=now, tz=tz)
    return dt, date_only, ""


def _row_link(row: Node, spec: PlatformSpec) -> Optional[str]:
    if spec.link_pred is None:
        return None
    for n in [row, *row.iter()]:
        href = n.get("href")
        if href and spec.link_pred(href):
            if href.startswith("/"):
                href = spec.link_base.rstrip("/") + href
            return href if href.startswith("https://") else None
    return None


def parse_list(html: str, spec: PlatformSpec, *, now: datetime, tz: tzinfo,
               expected_handle: Optional[str]) -> dict:
    root = parse_html(html)
    body_text = root.text()
    snap = {"platform": spec.platform, "items": [], "status": "unknown", "note": ""}

    wall = detect_wall(body_text)
    if wall:
        snap.update(status="blocked", note=f"wall on page: {wall!r} (pause; human step)")
        return snap
    if not handle_present(root, html, expected_handle):
        snap["note"] = ("expected handle not confirmed on page"
                        if expected_handle else "no expected handle configured")
        return snap

    rows: list[Node] = []
    for sel in spec.row_selectors:
        rows = root.find_all(sel)
        if rows:
            break
    # Drop rows nested inside other rows (keep the outermost).
    row_ids = {id(r) for r in rows}
    rows = [r for r in rows if not any(id(a) in row_ids for a in r.ancestors())]

    sched_rows = []
    for r in rows:
        txt = r.text()
        if not txt:
            continue
        if spec.scheduled_marker is not None and not spec.scheduled_marker.search(txt):
            continue
        sched_rows.append((r, txt))

    if not sched_rows:
        if any(p.search(body_text) for p in spec.empty_markers):
            snap.update(status="ok", note="empty-state marker seen")
            return snap
        if rows and spec.trust_empty_when_rows_unscheduled:
            snap.update(status="ok", note="list recognized; no scheduled rows")
            return snap
        snap["note"] = ("list rows recognized but none marked scheduled"
                        if rows else "scheduled list not recognized")
        return snap

    unreadable = 0
    for r, txt in sched_rows:
        dt, date_only, time_text = _row_time(txt, spec, now=now, tz=tz)
        if dt is None:
            unreadable += 1
            continue
        cap = ""
        for sel in spec.caption_selectors:
            hits = r.find_all(sel)
            if hits:
                cap = hits[0].text()
                break
        if not cap:
            cap = _clean_caption(txt, time_text)
        snap["items"].append({
            "scheduled_for": dt.isoformat(),
            "date_only": bool(date_only),
            "caption": caption_preview(cap),
            "url": _row_link(r, spec),
            "video_id": None,
        })
    if unreadable:
        snap.update(status="unknown", items=[],
                    note=f"{unreadable} scheduled row(s) had an unreadable time; not trusting this read")
        return snap
    snap.update(status="ok", note=f"{len(snap['items'])} scheduled row(s) read")
    return snap
