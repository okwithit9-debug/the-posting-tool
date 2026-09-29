"""Run the read-only checks and write snapshots."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from typing import Iterable, Optional

from ..config import Config
from ..locks import PostingRunActive, browser_check_guard, hold_flock
from ..snapshots import write_snapshot
from . import youtube_api
from .browser import BrowserUnavailable, open_page
from .readers import READERS
from .readonly import ReadOnlyPage, ReadOnlyViolation

BROWSER_CHECKS = ("x", "tiktok", "instagram", "pinterest", "threads", "youtube_studio")
ALL_CHECKS = BROWSER_CHECKS + ("youtube_api",)


def _handle_for(cfg: Config, check: str) -> Optional[str]:
    override = (cfg.raw.get("expected_handles") or {})
    plat = "youtube" if check.startswith("youtube") else check
    return override.get(plat) or cfg.expected_handles.get(plat)


def run_sync(cfg: Config, checks: Optional[Iterable[str]] = None, *, log=print) -> dict[str, dict]:
    wanted = list(checks) if checks else list(ALL_CHECKS)
    results: dict[str, dict] = {}
    snap_dir = cfg.snapshots_dir
    snap_dir.mkdir(parents=True, exist_ok=True)

    def save(check: str, snap: dict) -> None:
        snap.setdefault("checked_at", datetime.now(timezone.utc).isoformat())
        write_snapshot(snap_dir, check, snap)
        results[check] = snap
        log(f"[{check}] {snap.get('status')}: {snap.get('note', '')}")

    with hold_flock(snap_dir / ".sync.lock", what="another dashboard sync"):
        for check in wanted:
            if not cfg.check_enabled(check):
                save(check, {"platform": "youtube" if check.startswith("youtube") else check,
                             "status": "disabled", "items": [], "note": "disabled in config"})
        if "youtube_api" in wanted and cfg.check_enabled("youtube_api"):
            save("youtube_api", youtube_api.read_scheduled(cfg.posting_tool_root))

        browser_wanted = [c for c in wanted if c in BROWSER_CHECKS and cfg.check_enabled(c)]
        if not browser_wanted:
            return results
        try:
            guard = browser_check_guard(cfg.dispatch_lock_path, cfg.runtime_path("in_flight"))
            guard.__enter__()
        except PostingRunActive as exc:
            for c in browser_wanted:
                save(c, {"platform": "youtube" if c.startswith("youtube") else c,
                         "status": "blocked", "items": [], "note": f"refused: {exc}"})
            return results
        try:
            try:
                session = open_page(cfg.cdp_url)
                page = session.__enter__()
            except BrowserUnavailable as exc:
                for c in browser_wanted:
                    save(c, {"platform": "youtube" if c.startswith("youtube") else c,
                             "status": "blocked", "items": [], "note": str(exc)})
                return results
            try:
                for c in browser_wanted:
                    ro = ReadOnlyPage(page, c)
                    try:
                        snap = READERS[c](ro, snap_dir, tz=cfg.tz, handle=_handle_for(cfg, c),
                                          urls=cfg.raw.get("urls") or {})
                    except ReadOnlyViolation as exc:
                        snap = {"status": "error", "items": [], "note": f"read-only guard stopped: {exc}"}
                    except Exception as exc:  # noqa: BLE001
                        snap = {"status": "error", "items": [], "note": f"{type(exc).__name__}: {str(exc)[:160]}"}
                    snap.setdefault("platform", "youtube" if c.startswith("youtube") else c)
                    snap["check"] = c
                    if c == "threads" and not cfg.check_opt("threads", "affects_status", False):
                        snap["note"] = (snap.get("note") or "") + " [log-only]"
                    save(c, snap)
            finally:
                with contextlib.suppress(Exception):
                    session.__exit__(None, None, None)
        finally:
            with contextlib.suppress(Exception):
                guard.__exit__(None, None, None)
    return results
