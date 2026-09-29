"""CLI: python3 -m posting_dashboard [serve|record|backfill|sync|queue]"""

from __future__ import annotations

import argparse
import json
import sys

from . import PLATFORMS
from .config import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m posting_dashboard", description=__doc__)
    ap.add_argument("--config", default=None, help="dashboard config JSON (default: dashboard/config.json if present)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run the local page on 127.0.0.1 (default)")
    s.add_argument("--port", type=int, default=None)
    s.add_argument("--open", action="store_true", help="open the page in your browser")

    r = sub.add_parser("record", help="append one result to the ledger (REQUIRED after every native Schedule proof)")
    r.add_argument("basename")
    r.add_argument("platform", choices=PLATFORMS)
    r.add_argument("--status", required=True, choices=["scheduled", "success", "failed", "skipped", "cancelled"])
    r.add_argument("--scheduled-for", default=None, help="ISO-8601 WITH offset, e.g. 2026-10-01T11:22:00-07:00")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--caption", default=None, help="caption text (only the first 200 chars are stored)")
    g.add_argument("--caption-file", default=None, help="read caption from a file")
    r.add_argument("--media", default=None, help="path to the media file (for the thumbnail)")
    r.add_argument("--proof-screenshot", default=None, help="path to the native-list proof screenshot")
    r.add_argument("--url", default=None, help="native URL of the item, if known")
    r.add_argument("--video-id", default=None, help="YouTube video id, if any")
    r.add_argument("--error", default=None)

    sub.add_parser("backfill", help="seed the ledger from posted/ and failed/ receipts (idempotent)")

    y = sub.add_parser("sync", help="run the read-only native-list checks now")
    y.add_argument("--only", default=None, help="comma list: x,tiktok,instagram,pinterest,threads,youtube_api,youtube_studio")

    q = sub.add_parser("queue", help="print the queue JSON (what the page shows)")
    q.add_argument("--history", action="store_true")

    args = ap.parse_args(argv)
    overrides = {}
    if getattr(args, "port", None):
        overrides["port"] = args.port
    cfg = load_config(args.config, overrides or None)
    cmd = args.cmd or "serve"

    if cmd == "serve":
        from .server import serve
        serve(cfg, args.config, open_browser=getattr(args, "open", False))
        return 0

    if cmd == "record":
        from .ledger import append_event, make_event
        caption = args.caption
        if args.caption_file:
            with open(args.caption_file, encoding="utf-8") as f:
                caption = f.read()
        try:
            ev = make_event(basename=args.basename, platform=args.platform, status=args.status,
                            scheduled_for=args.scheduled_for, caption=caption, media_path=args.media,
                            proof_screenshot=args.proof_screenshot, url=args.url,
                            video_id=args.video_id, error=args.error, source="agent")
        except ValueError as exc:
            print(f"record refused: {exc}", file=sys.stderr)
            return 2
        append_event(cfg.ledger_path, ev)
        print(f"recorded {ev['basename']} / {ev['platform']} -> {ev['event']} ({ev.get('scheduled_for') or '-'})")
        return 0

    if cmd == "backfill":
        from .ledger import backfill
        from .receipts import receipt_events, receipt_rows
        rows = receipt_rows(cfg.runtime_path("posted"), cfg.runtime_path("failed"), None,
                            cfg.runtime_path("screenshots"))
        n = backfill(cfg.ledger_path, receipt_events(rows))
        print(f"backfill: appended {n} event(s) from {len(rows)} receipt row(s)")
        return 0

    if cmd == "sync":
        from .sync.runner import run_sync
        only = [c.strip() for c in args.only.split(",")] if args.only else None
        res = run_sync(cfg, only)
        bad = [c for c, s in res.items() if s.get("status") in ("error",)]
        return 1 if bad else 0

    if cmd == "queue":
        from .index import build_queue
        print(json.dumps(build_queue(cfg, show_history=args.history), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
