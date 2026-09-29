"""YouTube Data API read of the scheduled queue (official, read-only).

channels.list(mine) -> uploads playlist -> playlistItems.list -> videos.list
(part=snippet,status). Scheduled = privacyStatus "private" with a future
``status.publishAt``. ~1 quota unit per call. Reuses the posting tool's
existing OAuth token (config.json ``youtube.token_file``); never starts an
interactive OAuth flow. Missing token -> snapshot ``unknown`` ("not configured").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..ledger import caption_preview
from ..reconcile import parse_iso

MAX_PLAYLIST_PAGES = 4  # 200 most recent uploads


def items_from_videos(videos: list[dict], *, now: datetime) -> list[dict]:
    out = []
    for v in videos or []:
        status = v.get("status") or {}
        if status.get("privacyStatus") != "private":
            continue
        pub = parse_iso(status.get("publishAt"))
        if pub is None or pub <= now:
            continue
        vid = v.get("id")
        title = (v.get("snippet") or {}).get("title") or ""
        out.append({
            "scheduled_for": pub.isoformat(),
            "date_only": False,
            "caption": caption_preview(title),
            "url": f"https://studio.youtube.com/video/{vid}/edit" if vid else None,
            "video_id": vid,
        })
    return out


def _load_credentials(posting_tool_root: Path):
    cfg_path = posting_tool_root / "config.json"
    if not cfg_path.is_file():
        return None, "config.json not found (YouTube not configured)"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "config.json unreadable"
    token_rel = ((cfg.get("youtube") or {}).get("token_file")) or ""
    if not token_rel:
        return None, "no youtube.token_file in config.json (YouTube not configured)"
    token = Path(token_rel)
    if not token.is_absolute():
        token = posting_tool_root / token
    if not token.is_file():
        return None, "YouTube token file not found (run setup_youtube_auth.py)"
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None, "google-auth not installed"
    try:
        creds = Credentials.from_authorized_user_file(str(token))
        if not creds.valid and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds.valid:
            return None, "YouTube token invalid (re-run setup_youtube_auth.py)"
    except Exception as exc:  # noqa: BLE001
        return None, f"YouTube token error: {type(exc).__name__}"
    return creds, ""


def read_scheduled(posting_tool_root: Path, *, now: Optional[datetime] = None, service=None) -> dict:
    now = now or datetime.now(timezone.utc)
    snap = {"platform": "youtube", "check": "youtube_api", "status": "unknown", "items": [], "note": ""}
    if service is None:
        creds, why = _load_credentials(posting_tool_root)
        if creds is None:
            snap["note"] = why
            return snap
        try:
            from googleapiclient.discovery import build
        except ImportError:
            snap["note"] = "google-api-python-client not installed"
            return snap
        service = build("youtube", "v3", credentials=creds, cache_discovery=False)
    try:
        ch = service.channels().list(part="contentDetails", mine=True).execute()
        items = ch.get("items") or []
        if not items:
            snap["note"] = "no channel for this token"
            return snap
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        ids: list[str] = []
        token = None
        for _ in range(MAX_PLAYLIST_PAGES):
            kw = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
            if token:
                kw["pageToken"] = token
            resp = service.playlistItems().list(**kw).execute()
            ids.extend(i["contentDetails"]["videoId"] for i in resp.get("items") or [])
            token = resp.get("nextPageToken")
            if not token:
                break
        videos: list[dict] = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            resp = service.videos().list(part="snippet,status", id=",".join(chunk)).execute()
            videos.extend(resp.get("items") or [])
    except Exception as exc:  # noqa: BLE001
        snap.update(status="error", note=f"YouTube API error: {type(exc).__name__}")
        return snap
    snap["items"] = items_from_videos(videos, now=now)
    snap.update(status="ok", note=f"{len(snap['items'])} scheduled video(s) via Data API "
                                   f"({len(ids)} recent uploads scanned)")
    return snap
