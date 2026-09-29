#!/usr/bin/env python3
"""Build a throwaway demo (fixture brand only) and print its config path.

    python3 demo/make_demo.py --out /tmp/posting-demo
    python3 -m posting_dashboard --config /tmp/posting-demo/config.json serve

Everything is fake: example-brand, example.com. Times are relative to now.
Media: tiny generated clips if ffmpeg is on PATH, else generated PNG stills.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from posting_dashboard.ledger import append_event, make_event  # noqa: E402
from posting_dashboard.snapshots import write_snapshot  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
COLORS = [(37, 99, 235), (219, 39, 119), (5, 150, 105), (234, 88, 12), (124, 58, 237), (220, 38, 38),
          (14, 165, 233), (202, 138, 4)]


def png(path: Path, rgb, w=180, h=320):
    row = b"\x00" + bytes(rgb) * w
    raw = row * h
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)


def media(path_noext: Path, i: int, label: str) -> Path:
    rgb = COLORS[i % len(COLORS)]
    if shutil.which("ffmpeg"):
        out = path_noext.with_suffix(".mp4")
        color = "0x%02x%02x%02x" % rgb
        cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d=1",
               "-vf", f"drawbox=x=40:y=250:w=280:h=140:color=white@0.85:t=fill", "-pix_fmt", "yuv420p", str(out)]
        if subprocess.run(cmd).returncode == 0:
            return out
    out = path_noext.with_suffix(".png")
    png(out, rgb)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out).resolve()
    root = out / "the-posting-tool"
    if out.exists():
        shutil.rmtree(out)
    root.mkdir(parents=True)
    (root / "profile.json").write_text(json.dumps({
        "handle": "example-brand", "timezone": "America/Los_Angeles",
        "chrome": {"attach_only": True, "cdp": ""},
    }, indent=2))
    now = datetime.now(timezone.utc)
    base = now.astimezone(TZ).replace(minute=0, second=0, microsecond=0)

    def at(days, hour, minute):
        return (base + timedelta(days=days)).replace(hour=hour, minute=minute).isoformat()

    posted = root / "posted" / base.strftime("%Y-%m-%d")
    posted.mkdir(parents=True)
    shots = root / "logs/screenshots"
    shots.mkdir(parents=True)
    ledger = root / "logs/ledger.jsonl"

    packs = [
        ("demo-cold-brew", "Fresh cold brew, every Monday. Order ahead at https://example.com",
         {"x": at(1, 11, 0), "threads": at(1, 11, 0), "pinterest": at(1, 11, 0), "tiktok": at(1, 10, 30),
          "instagram": at(1, 10, 0)}),
        ("demo-roasting", "Behind the counter: how we roast in small batches https://example.com",
         {"x": at(1, 15, 0), "threads": at(1, 15, 0), "pinterest": at(1, 15, 0)}),
        ("demo-latte-art", "Latte art in 30 seconds (slow-mo) https://example.com",
         {"x": at(2, 19, 0), "tiktok": at(2, 18, 30), "instagram": at(2, 18, 0), "youtube": at(3, 9, 0)}),
        ("demo-pastry-menu", "Weekend pastry menu is live https://example.com",
         {"pinterest": at(3, 9, 0), "x": at(3, 11, 0)}),
    ]
    for i, (name, cap, plats) in enumerate(packs):
        m = media(posted / name, i, name)
        receipt = {"basename": name, "started_at": now.isoformat(), "finalized_at": now.isoformat(),
                   "platforms": {p: {"status": "scheduled", "scheduled_for": t, "at": now.isoformat()}
                                 for p, t in plats.items()}}
        (posted / f"{name}.posted.json").write_text(json.dumps(receipt, indent=2))
        (posted / f"{name}.json").write_text(json.dumps({
            "schema_version": 1, "media": {"filename": m.name},
            "destinations": {p: {"content": {"caption": cap}} for p in plats}}, indent=2))
        for p in plats:
            png(shots / f"{p}_{name}_scheduled_1.png", COLORS[(i + 3) % len(COLORS)], 320, 200)
    # Agent-path ledger entry (Threads long-thread) + a failure for history.
    m = media(posted / "demo-first-year", 5, "article")
    append_event(ledger, make_event(basename="demo-first-year", platform="threads", status="scheduled",
                                    scheduled_for=at(4, 8, 0), caption="Our first year in 12 photos (thread)",
                                    media_path=str(m), source="agent"))
    append_event(ledger, make_event(basename="demo-first-year", platform="x", status="scheduled",
                                    scheduled_for=at(4, 8, 0), caption="Our first year in 12 photos",
                                    media_path=str(m), source="agent"))
    append_event(ledger, make_event(basename="demo-roasting", platform="tiktok", status="failed",
                                    error="datetime picker did not stick", source="agent"))

    snaps = root / "logs/dashboard/snapshots"
    fresh = (now - timedelta(minutes=12)).isoformat()
    write_snapshot(snaps, "x", {"platform": "x", "status": "ok", "checked_at": fresh, "note": "5 scheduled row(s) read",
                                "items": [
        {"scheduled_for": at(1, 11, 0), "caption": "Fresh cold brew, every Monday. Order ahead", "date_only": False},
        {"scheduled_for": at(1, 15, 0), "caption": "Behind the counter: how we roast in small batches", "date_only": False},
        {"scheduled_for": at(2, 19, 0), "caption": "Latte art in 30 seconds (slow-mo)", "date_only": False},
        {"scheduled_for": at(3, 11, 0), "caption": "Weekend pastry menu is live", "date_only": False},
        {"scheduled_for": at(2, 12, 0), "caption": "Hand-scheduled reply about opening hours", "date_only": False,
         "url": "https://x.com/example-brand"},
    ]})
    write_snapshot(snaps, "tiktok", {"platform": "tiktok", "status": "ok", "checked_at": fresh,
                                     "note": "1 scheduled row(s) read", "items": [
        {"scheduled_for": at(1, 10, 30), "caption": "Fresh cold brew, every Monday.", "date_only": False}]})
    write_snapshot(snaps, "instagram", {"platform": "instagram", "status": "unknown", "checked_at": fresh,
                                        "note": "scheduled list not recognized", "items": []})
    write_snapshot(snaps, "pinterest", {"platform": "pinterest", "status": "ok", "checked_at": fresh,
                                        "note": "2 scheduled row(s) read", "items": [
        {"scheduled_for": at(1, 0, 0), "caption": "Fresh cold brew, every Monday.", "date_only": True},
        {"scheduled_for": at(3, 0, 0), "caption": "Weekend pastry menu is live", "date_only": True}]})
    write_snapshot(snaps, "threads", {"platform": "threads", "status": "disabled", "checked_at": fresh,
                                      "note": "disabled in config", "items": []})
    write_snapshot(snaps, "youtube_api", {"platform": "youtube", "status": "ok", "checked_at": fresh,
                                          "note": "1 scheduled video(s) via Data API", "items": [
        {"scheduled_for": at(3, 9, 0), "caption": "Latte art in 30 seconds (slow-mo)",
         "date_only": False, "video_id": "demoVideo01", "url": "https://studio.youtube.com/video/demoVideo01/edit"}]})

    cfg = out / "config.json"
    cfg.write_text(json.dumps({"posting_tool_root": str(root), "timezone": "America/Los_Angeles",
                               "port": 8765}, indent=2))
    print(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
