"""Build the dashboard queue: ledger + receipts + native snapshots."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import PLATFORMS
from .config import Config
from .ledger import caption_preview, current_state, iter_events
from .receipts import receipt_rows
from .reconcile import (FAILED, IMMEDIATE, MISSING, NOT_TRACKED, PUBLISHED, SKIPPED,
                        STATUS_LABELS, UNVERIFIED, VERIFIED, parse_iso, reconcile_platform,
                        snapshot_is_fresh)
from .snapshots import read_all

# Native scheduled-list pages (read-only). Best-effort; Meta and Pinterest
# move these. Override per platform with config "urls".
NATIVE_LIST_URLS = {
    "x": "https://x.com/compose/post/unsent/scheduled",
    "threads": "https://www.threads.com/",
    "tiktok": "https://www.tiktok.com/tiktokstudio/content",
    "instagram": "https://business.facebook.com/latest/planner",
    "pinterest": "https://www.pinterest.com/",
    "youtube": "https://studio.youtube.com/",
}

HISTORY_STATUSES = {PUBLISHED, FAILED, SKIPPED, IMMEDIATE}


def platform_link(platform: str, url: Optional[str], video_id: Optional[str]) -> Optional[str]:
    """'Open on platform' link, only when we actually know one."""
    if url and str(url).startswith("https://"):
        return url
    if platform == "youtube" and video_id:
        return f"https://studio.youtube.com/video/{video_id}/edit"
    return None


def _row_id(r: dict) -> str:
    key = f"{r.get('platform')}|{r.get('basename')}|{r.get('scheduled_for')}|{r.get('caption', '')[:40]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def tracked_rows(cfg: Config) -> list[dict]:
    """Ledger rows win over receipt rows for the same (basename, platform)."""
    rows: dict[tuple[str, str], dict] = {}
    for r in receipt_rows(cfg.runtime_path("posted"), cfg.runtime_path("failed"),
                          cfg.runtime_path("in_flight"), cfg.runtime_path("screenshots")):
        r["source"] = f"receipt:{r.pop('receipt_where', '')}"
        rows[(r["basename"], r["platform"])] = r
    for key, ev in current_state(iter_events(cfg.ledger_path)).items():
        base = rows.get(key, {})
        rows[key] = {
            "basename": ev["basename"],
            "platform": ev["platform"],
            "status": ev.get("event"),
            "scheduled_for": ev.get("scheduled_for") or base.get("scheduled_for"),
            "caption": ev.get("caption_preview") or base.get("caption") or "",
            "media_path": ev.get("media_path") or base.get("media_path"),
            "proof_screenshot": ev.get("proof_screenshot") or base.get("proof_screenshot"),
            "url": ev.get("url") or base.get("url"),
            "video_id": ev.get("video_id") or base.get("video_id"),
            "error": ev.get("error"),
            "went_out_immediately": bool(base.get("went_out_immediately")) and ev.get("event") == "success",
            "recorded_at": ev.get("recorded_at"),
            "source": f"ledger:{ev.get('source') or 'agent'}",
        }
    return list(rows.values())


def build_queue(cfg: Config, *, now: Optional[datetime] = None,
                show_history: Optional[bool] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tz = cfg.tz
    if show_history is None:
        show_history = bool(cfg.raw.get("show_history"))
    win = cfg.raw.get("window") or {}
    start = now - timedelta(hours=float(win.get("past_hours", 24)))
    end = now + timedelta(days=float(win.get("future_days", 14)))
    match = cfg.raw.get("match") or {}
    tol = int(match.get("minutes_tolerance", 2))
    prefix = int(match.get("caption_prefix_chars", 40))
    max_age = int(cfg.raw.get("snapshot_max_age_minutes", 360))

    snaps = read_all(cfg.snapshots_dir)
    # YouTube: API snapshot preferred, Studio snapshot as fallback.
    def snap_for(platform: str) -> Optional[dict]:
        if platform == "youtube":
            api = snaps.get("youtube_api")
            if snapshot_is_fresh(api, now=now, max_age_minutes=max_age):
                return api
            studio = snaps.get("youtube_studio")
            if studio and studio.get("status") not in ("disabled",):
                return studio
            return api or studio
        return snaps.get(platform)

    by_platform: dict[str, list[dict]] = {p: [] for p in PLATFORMS}
    for r in tracked_rows(cfg):
        by_platform.setdefault(r["platform"], []).append(r)

    out_rows: list[dict] = []
    platforms_meta = {}
    for p in PLATFORMS:
        snap = snap_for(p)
        affects = True
        if p == "threads":
            affects = bool(cfg.check_opt("threads", "affects_status", False))
        tracked, untracked = reconcile_platform(
            by_platform.get(p, []), snap, now=now, tz=tz, tol_minutes=tol,
            prefix=prefix, max_age_minutes=max_age, affects_status=affects)
        for r in tracked + untracked:
            dt = parse_iso(r.get("scheduled_for"))
            if dt is not None and not (start <= dt <= end):
                continue
            if dt is None and r["display_status"] not in (FAILED, SKIPPED):
                # A row without a readable time can't be placed; keep failures.
                if r["display_status"] != NOT_TRACKED:
                    continue
            if not show_history and r["display_status"] in HISTORY_STATUSES:
                continue
            r["caption_preview"] = caption_preview(r.get("caption"))
            nm = r.get("native_match") or {}
            r["link"] = (platform_link(p, r.get("url"), r.get("video_id"))
                         or platform_link(p, nm.get("url"), nm.get("video_id")))
            r["status_label"] = STATUS_LABELS.get(r["display_status"], r["display_status"])
            r["id"] = _row_id(r)
            r["local_time"] = dt.astimezone(tz).isoformat() if dt else None
            out_rows.append(r)
        platforms_meta[p] = {
            "native_list_url": (cfg.raw.get("urls") or {}).get(p) or NATIVE_LIST_URLS[p],
            "snapshot_status": (snap or {}).get("status") or "never",
            "snapshot_checked_at": (snap or {}).get("checked_at"),
            "snapshot_note": (snap or {}).get("note"),
            "snapshot_fresh": snapshot_is_fresh(snap, now=now, max_age_minutes=max_age),
            "snapshot_screenshot": (snap or {}).get("screenshot"),
            "affects_status": affects,
        }

    def sort_key(r):
        dt = parse_iso(r.get("scheduled_for"))
        return (dt is None, dt or now, r.get("platform") or "")

    out_rows.sort(key=sort_key)
    counts: dict[str, int] = {}
    for r in out_rows:
        counts[r["display_status"]] = counts.get(r["display_status"], 0) + 1
    # Only expose public fields (no internal keys).
    public = [{k: v for k, v in r.items() if k not in ("caption",)} for r in out_rows]
    return {
        "generated_at": now.isoformat(),
        "timezone": str(cfg.raw.get("timezone")),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "show_history": show_history,
        "rows": public,
        "counts": counts,
        "platforms": platforms_meta,
        "status_labels": STATUS_LABELS,
        "config": cfg.to_public_dict(),
    }
