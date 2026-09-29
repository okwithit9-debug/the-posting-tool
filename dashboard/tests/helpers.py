"""Shared test helpers. Fixture brand only: example-brand / example.com."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from posting_dashboard.config import load_config

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HANDLE = "example-brand"
NOW = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)  # 10:00 AM UTC-7


def html(name: str) -> str:
    return (FIXTURES / "html" / name).read_text(encoding="utf-8")


def make_tool_root(tmp: Path) -> Path:
    """A fake The Posting Tool checkout (schedule_batch.py layout)."""
    root = tmp / "the-posting-tool"
    root.mkdir(parents=True)
    (root / "profile.json").write_text(json.dumps({
        "handle": HANDLE, "timezone": "Etc/GMT+7",
        "chrome": {"attach_only": True, "cdp": ""},
    }))
    for d in ("inbox", "posted", "failed", ".in_flight", "logs/screenshots"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def write_cfg(tmp: Path, root: Path, **extra) -> Path:
    cfg = {"posting_tool_root": str(root), "timezone": "Etc/GMT+7", **extra}
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg))
    return p


def cfg_for(tmp: Path, **extra):
    root = make_tool_root(tmp)
    return load_config(write_cfg(tmp, root, **extra)), root


def write_receipt(folder: Path, basename: str, platforms: dict, *, caption_block: dict | None = None,
                  media: bool = True) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{basename}.posted.json").write_text(json.dumps({
        "basename": basename, "started_at": "2026-09-30T15:00:00+00:00",
        "finalized_at": "2026-09-30T15:30:00+00:00", "platforms": platforms}))
    if caption_block is not None:
        (folder / f"{basename}.json").write_text(json.dumps(caption_block))
    if media:
        (folder / f"{basename}.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
