"""Read The Posting Tool runtime artifacts (receipts, sidecars, media, proof shots).

Receipt shape (``schedule_batch.finalize``)::

    {"basename": ..., "started_at": ..., "finalized_at": ...,
     "platforms": {"x": {"status": "scheduled", "scheduled_for": ISO,
                         "url": ..., "error": ..., ...}, ...}}

Found in ``<posted>/<date>/<basename>.posted.json``, ``<failed>/*.posted.json``
(partial successes live here too) and ``<in_flight>/<basename>.json``.
``*.previous`` backups are ignored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Optional

from . import PLATFORMS
from .ledger import caption_preview, make_event

MEDIA_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".gif")


def _load_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def iter_receipt_files(posted: Optional[Path], failed: Optional[Path],
                       in_flight: Optional[Path]) -> Iterator[tuple[Path, str]]:
    if posted and posted.is_dir():
        for p in sorted(posted.glob("**/*.posted.json")):
            yield p, "posted"
    if failed and failed.is_dir():
        for p in sorted(failed.glob("*.posted.json")):
            yield p, "failed"
    if in_flight and in_flight.is_dir():
        for p in sorted(in_flight.glob("*.json")):
            yield p, "in_flight"


def sidecar_caption(sidecar: Optional[dict], platform: str) -> str:
    """Caption text for one platform from either sidecar contract."""
    if not sidecar:
        return ""
    blocks = []
    caps = sidecar.get("captions")
    if isinstance(caps, dict) and isinstance(caps.get(platform), dict):
        blocks.append(caps[platform])
    dests = sidecar.get("destinations")
    if isinstance(dests, dict) and isinstance(dests.get(platform), dict):
        content = dests[platform].get("content")
        if isinstance(content, dict):
            blocks.append(content)
    for b in blocks:
        for key in ("caption", "title", "description", "text"):
            val = b.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return ""


def find_media(folder: Path, basename: str, sidecar: Optional[dict]) -> Optional[Path]:
    if sidecar:
        media = sidecar.get("media")
        if isinstance(media, dict) and media.get("filename"):
            cand = folder / Path(str(media["filename"])).name
            if cand.is_file():
                return cand
    for ext in MEDIA_EXTS:
        cand = folder / f"{basename}{ext}"
        if cand.is_file():
            return cand
    return None


def find_proof_screenshot(shots_dir: Optional[Path], platform: str, basename: str) -> Optional[Path]:
    """Newest ``<platform>_<basename>_*.png`` (best-effort glob)."""
    if not shots_dir or not shots_dir.is_dir():
        return None
    try:
        hits = list(shots_dir.glob(f"{platform}_{basename}_*.png"))
    except OSError:
        return None
    if not hits:
        return None
    prefer = [h for h in hits if any(k in h.name for k in (
        "scheduled", "native_scheduled_list", "content_list", "submitted"))]
    pool = prefer or hits
    return max(pool, key=lambda h: h.stat().st_mtime)


def receipt_rows(posted: Optional[Path], failed: Optional[Path], in_flight: Optional[Path],
                 screenshots: Optional[Path]) -> list[dict]:
    """Flatten receipts into per-(basename, platform) dicts."""
    rows: list[dict] = []
    for path, where in iter_receipt_files(posted, failed, in_flight):
        data = _load_json(path)
        if not data:
            continue
        name = path.name
        basename = data.get("basename") or (
            name[: -len(".posted.json")] if name.endswith(".posted.json") else path.stem)
        side = _load_json(path.parent / f"{basename}.json") if where != "in_flight" else None
        media = find_media(path.parent, basename, side)
        for platform, entry in (data.get("platforms") or {}).items():
            if platform not in PLATFORMS or not isinstance(entry, dict):
                continue
            status = str(entry.get("status") or "failed")
            immediate = bool(entry.get("scheduling_skipped")) and status == "success"
            shot = entry.get("proof_screenshot")
            shot_path = Path(shot) if shot else find_proof_screenshot(screenshots, platform, basename)
            rows.append({
                "basename": basename,
                "platform": platform,
                "status": status,
                "scheduled_for": entry.get("scheduled_for"),
                "caption": sidecar_caption(side, platform),
                "media_path": str(media) if media else None,
                "proof_screenshot": str(shot_path) if shot_path else None,
                "url": entry.get("url"),
                "video_id": entry.get("video_id"),
                "error": entry.get("error"),
                "went_out_immediately": immediate,
                "recorded_at": entry.get("at") or data.get("finalized_at") or data.get("started_at"),
                "receipt_where": where,
            })
    return rows


def receipt_events(rows: list[dict]) -> Iterator[dict]:
    """Receipt rows -> ledger events (for backfill)."""
    for r in rows:
        status = r["status"] if r["status"] in ("scheduled", "success", "failed", "skipped") else "failed"
        sf = r.get("scheduled_for")
        if status == "scheduled" and not sf:
            status = "failed"
        try:
            yield make_event(
                basename=r["basename"], platform=r["platform"], status=status,
                scheduled_for=sf, caption=caption_preview(r.get("caption")),
                media_path=r.get("media_path"), proof_screenshot=r.get("proof_screenshot"),
                url=r.get("url"), video_id=r.get("video_id"), error=r.get("error"),
                source="receipt-backfill", recorded_at=r.get("recorded_at"),
            )
        except ValueError:
            continue
