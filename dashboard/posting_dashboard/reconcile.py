"""Match the tool's own records against native scheduled-list snapshots.

Row statuses shown on the page:

- ``verified``                 in the tool's records AND seen on a fresh ``ok`` native list
- ``missing``                  in the tool's records, time still ahead, NOT on a fresh ``ok`` list
- ``not_tracked``              on the native list, not in the tool's records ("not tracked by the tool")
- ``scheduled_unverified``     in the tool's records; no fresh ``ok`` list to compare with
- history (toggle): ``published`` (time passed), ``failed``, ``skipped``, ``immediate``

Matching key: same platform, time within ``minutes_tolerance`` (same local
day for date-only lists), and caption prefix agreement. If the native item
has no readable caption, a time match counts only when it is the unique
candidate. Nothing is guessed: an ambiguous match stays unverified.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from typing import Optional

VERIFIED = "verified"
MISSING = "missing"
NOT_TRACKED = "not_tracked"
UNVERIFIED = "scheduled_unverified"
PUBLISHED = "published"
FAILED = "failed"
SKIPPED = "skipped"
IMMEDIATE = "immediate"

STATUS_LABELS = {
    VERIFIED: "verified",
    MISSING: "missing",
    NOT_TRACKED: "not tracked by the tool",
    UNVERIFIED: "scheduled (unverified)",
    PUBLISHED: "time passed",
    FAILED: "failed",
    SKIPPED: "skipped",
    IMMEDIATE: "went out immediately",
}


def parse_iso(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def norm_caption(text: Optional[str]) -> str:
    t = (text or "").lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[^\w#@ ]+", " ", t)
    return " ".join(t.split())


def captions_agree(a: Optional[str], b: Optional[str], prefix: int = 40) -> Optional[bool]:
    """True/False when both sides have text; None when either is empty."""
    na, nb = norm_caption(a), norm_caption(b)
    if not na or not nb:
        return None
    pa, pb = na[:prefix], nb[:prefix]
    short = min(len(pa), len(pb))
    if short < 8:
        return pa == pb
    return pa[:short] == pb[:short] or pa in nb or pb in na


def times_agree(a: Optional[datetime], b: Optional[datetime], *, date_only: bool,
                tol_minutes: int, tz: tzinfo) -> bool:
    if a is None or b is None:
        return False
    if date_only:
        return a.astimezone(tz).date() == b.astimezone(tz).date()
    return abs((a - b).total_seconds()) <= tol_minutes * 60


def snapshot_is_fresh(snap: Optional[dict], *, now: datetime, max_age_minutes: int) -> bool:
    if not snap or snap.get("status") != "ok":
        return False
    at = parse_iso(snap.get("checked_at"))
    if at is None:
        return False
    return (now - at) <= timedelta(minutes=max_age_minutes)


def reconcile_platform(tracked: list[dict], snap: Optional[dict], *, now: datetime, tz: tzinfo,
                       tol_minutes: int = 2, prefix: int = 40, max_age_minutes: int = 360,
                       affects_status: bool = True) -> tuple[list[dict], list[dict]]:
    """Annotate tracked rows in place; return (tracked, not_tracked_rows).

    ``tracked`` rows need ``status`` (ledger/receipt status), ``scheduled_for``
    (ISO) and ``caption``.
    """
    fresh = affects_status and snapshot_is_fresh(snap, now=now, max_age_minutes=max_age_minutes)
    native = []
    if fresh:
        for it in snap.get("items") or []:
            native.append({**it, "_dt": parse_iso(it.get("scheduled_for")), "_used": False})

    for row in tracked:
        st = row.get("status")
        dt = parse_iso(row.get("scheduled_for"))
        row["native_match"] = None
        if row.get("went_out_immediately"):
            row["display_status"] = IMMEDIATE
            continue
        if st == "failed":
            row["display_status"] = FAILED
            continue
        if st in ("skipped", "cancelled"):
            row["display_status"] = SKIPPED
            continue
        if st == "success":
            row["display_status"] = PUBLISHED
            continue
        # st == scheduled
        if dt is not None and dt < now:
            row["display_status"] = PUBLISHED
            continue
        if not fresh:
            row["display_status"] = UNVERIFIED
            continue
        # Candidates by time.
        vid = row.get("video_id")
        match = None
        if vid:
            for n in native:
                if not n["_used"] and n.get("video_id") == vid:
                    match = n
                    break
        if match is None:
            cands = [n for n in native if not n["_used"] and times_agree(
                dt, n["_dt"], date_only=bool(n.get("date_only")), tol_minutes=tol_minutes, tz=tz)]
            agreeing = [n for n in cands if captions_agree(row.get("caption"), n.get("caption"), prefix) is True]
            unknown_cap = [n for n in cands if captions_agree(row.get("caption"), n.get("caption"), prefix) is None]
            if len(agreeing) >= 1:
                match = agreeing[0]
            elif len(unknown_cap) == 1 and len(cands) == 1:
                match = unknown_cap[0]
            elif unknown_cap:
                # Several time-matches with no caption to tell them apart.
                row["display_status"] = UNVERIFIED
                row["note"] = "ambiguous native match"
                continue
        if match is not None:
            match["_used"] = True
            row["display_status"] = VERIFIED
            row["native_match"] = {k: v for k, v in match.items() if not k.startswith("_")}
        else:
            row["display_status"] = MISSING

    untracked = []
    if fresh:
        for n in native:
            if n["_used"]:
                continue
            if n["_dt"] is not None and n["_dt"] < now:
                continue
            untracked.append({
                "basename": None,
                "platform": snap.get("platform"),
                "status": "scheduled",
                "scheduled_for": n.get("scheduled_for"),
                "date_only": bool(n.get("date_only")),
                "caption": n.get("caption") or "",
                "url": n.get("url"),
                "video_id": n.get("video_id"),
                "media_path": None,
                "proof_screenshot": snap.get("screenshot"),
                "display_status": NOT_TRACKED,
                "source": "native",
            })
    return tracked, untracked
