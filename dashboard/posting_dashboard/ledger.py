"""Append-only scheduling ledger (``logs/ledger.jsonl``, gitignored).

One JSON object per line. The latest event per ``(basename, platform)``
wins. Written by ``python3 -m posting_dashboard record ...`` (REQUIRED after
every successful native Schedule; see docs/PLAYBOOK.md) and by
``python3 -m posting_dashboard backfill`` (seeds from posted/ and failed/ receipts).

Only a caption *preview* (first 200 chars) is stored; the full caption
stays in the pack sidecar.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from . import PLATFORMS

LEDGER_VERSION = 1
CAPTION_PREVIEW_CHARS = 200
STATUSES = ("scheduled", "success", "failed", "skipped", "cancelled")


def caption_preview(text: Optional[str], limit: int = CAPTION_PREVIEW_CHARS) -> str:
    t = " ".join(str(text or "").split())
    return t[:limit]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_iso(value: Optional[str]) -> Optional[str]:
    """Validate an ISO-8601 datetime. Naive values are rejected (no guessing)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 datetime: {value!r}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"scheduled time needs a UTC offset, got naive {value!r}")
    return dt.isoformat()


def make_event(
    *,
    basename: str,
    platform: str,
    status: str,
    scheduled_for: Optional[str] = None,
    caption: Optional[str] = None,
    media_path: Optional[str] = None,
    proof_screenshot: Optional[str] = None,
    url: Optional[str] = None,
    video_id: Optional[str] = None,
    error: Optional[str] = None,
    source: str = "agent",
    recorded_at: Optional[str] = None,
) -> dict:
    platform = (platform or "").strip().lower()
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {', '.join(PLATFORMS)}; got {platform!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}; got {status!r}")
    if not basename or "/" in basename or "\\" in basename:
        raise ValueError("basename must be a bare pack basename (no path)")
    if status == "scheduled" and not scheduled_for:
        raise ValueError("status=scheduled requires --scheduled-for")
    ev = {
        "v": LEDGER_VERSION,
        "event": status,
        "basename": basename,
        "platform": platform,
        "scheduled_for": normalize_iso(scheduled_for),
        "caption_preview": caption_preview(caption),
        "media_path": media_path or None,
        "proof_screenshot": proof_screenshot or None,
        "url": url or None,
        "video_id": video_id or None,
        "error": (error or None),
        "source": source,
        "recorded_at": recorded_at or _now_iso(),
    }
    return ev


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def iter_events(path: Path) -> Iterator[dict]:
    """Yield valid events; silently skip corrupt/partial lines."""
    if not path.is_file():
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("platform") not in PLATFORMS or not ev.get("basename"):
                continue
            yield ev


def current_state(events: Iterable[dict]) -> dict[tuple[str, str], dict]:
    """Fold events: latest ``recorded_at`` per (basename, platform) wins.

    Ties keep file order (later line wins).
    """
    out: dict[tuple[str, str], dict] = {}
    for ev in events:
        key = (ev["basename"], ev["platform"])
        prev = out.get(key)
        if prev is None or str(ev.get("recorded_at") or "") >= str(prev.get("recorded_at") or ""):
            out[key] = ev
    return out


def _dedupe_key(ev: dict) -> tuple:
    return (ev.get("basename"), ev.get("platform"), ev.get("event"), ev.get("scheduled_for"))


def backfill(path: Path, receipt_events: Iterable[dict]) -> int:
    """Append receipt-derived events that the ledger does not have yet.

    Idempotent: running twice appends nothing the second time.
    """
    existing = {_dedupe_key(e) for e in iter_events(path)}
    n = 0
    for ev in receipt_events:
        k = _dedupe_key(ev)
        if k in existing:
            continue
        append_event(path, ev)
        existing.add(k)
        n += 1
    return n
