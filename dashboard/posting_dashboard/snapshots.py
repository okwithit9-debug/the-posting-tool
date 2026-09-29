"""Native-list snapshots written by the read-only sync.

``<snapshots_dir>/<check>.json`` holds the latest result per check::

    {"platform": "x", "check": "x", "status": "ok"|"unknown"|"blocked"|"error"|"disabled",
     "checked_at": ISO, "note": str, "screenshot": path|None,
     "items": [{"scheduled_for": ISO|None, "date_only": bool,
                "caption": str, "url": str|None, "video_id": str|None}]}

Only ``status == "ok"`` snapshots can verify or flag anything. An ``ok``
snapshot with zero items means "the list was read and is empty" (the parser
must have seen a positive empty-state marker to return that).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SNAPSHOT_STATUSES = ("ok", "unknown", "blocked", "error", "disabled")


def write_snapshot(dirpath: Path, check: str, snap: dict) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    snap = dict(snap)
    snap.setdefault("check", check)
    snap.setdefault("checked_at", datetime.now(timezone.utc).isoformat())
    if snap.get("status") not in SNAPSHOT_STATUSES:
        snap["status"] = "unknown"
    path = dirpath / f"{check}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_snapshot(dirpath: Path, check: str) -> Optional[dict]:
    path = dirpath / f"{check}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_all(dirpath: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not dirpath.is_dir():
        return out
    for p in sorted(dirpath.glob("*.json")):
        snap = read_snapshot(dirpath, p.stem)
        if snap:
            out[p.stem] = snap
    return out
