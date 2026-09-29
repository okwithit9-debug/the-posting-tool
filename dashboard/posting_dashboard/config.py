"""Dashboard configuration.

Load order: built-in defaults < config JSON (``--config`` or
``$POSTING_DASHBOARD_CONFIG`` or ``dashboard/config.json``) < env overrides.

Paths in the config are resolved relative to the config file's directory,
except the runtime paths (posted/failed/ledger/...) which are resolved
relative to ``posting_tool_root`` (this repo). Handles, timezone and the
CDP URL come from the repo's ``profile.json``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from . import PLATFORMS

DASHBOARD_DIR = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    # The Posting Tool checkout (this repo). Relative to the dashboard folder.
    "posting_tool_root": "..",
    # Display timezone. null = profile.json "timezone", else UTC.
    "timezone": None,
    "host": "127.0.0.1",
    "port": 8765,
    # Relative to posting_tool_root. logs/ is gitignored.
    "ledger": "logs/ledger.jsonl",
    "snapshots_dir": "logs/dashboard/snapshots",
    # Runtime dirs, relative to posting_tool_root (schedule_batch.py layout).
    "paths": {
        "posted": "posted",
        "failed": "failed",
        "in_flight": ".in_flight",
        "inbox": "inbox",
        "screenshots": "logs/screenshots",
        "state": "state.json",
    },
    # Optional lock file a dispatch holds (flock). Checks refuse while held.
    "dispatch_lock": ".dispatch.lock",
    "window": {"past_hours": 24, "future_days": 14},
    "show_history": False,
    # A native-list snapshot older than this no longer verifies anything.
    "snapshot_max_age_minutes": 360,
    "match": {"minutes_tolerance": 2, "caption_prefix_chars": 40},
    "checks": {
        "x": {"enabled": True},
        "tiktok": {"enabled": True},
        "instagram": {"enabled": True},
        "pinterest": {"enabled": True},
        # Opens an (empty) composer to reach the scheduled list. Off by
        # default; even when on, log-only unless affects_status is true.
        "threads": {"enabled": False, "affects_status": False},
        # YouTube Data API read (no browser). Skips cleanly if no token.
        "youtube_api": {"enabled": True},
        # YouTube Studio browser read. Off: the Data API covers it.
        "youtube_studio": {"enabled": False},
    },
    # Attach-only: connect to a Chrome YOU already opened with
    # --remote-debugging-port and open one extra tab. cdp_url null = use
    # profile.json chrome.cdp. Nothing configured = browser checks "blocked".
    # The dashboard never launches Chrome.
    "browser": {"cdp_url": None},
    # Optional overrides for native-list URLs (Meta moves the Planner).
    "urls": {},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: dict
    config_dir: Path
    posting_tool_root: Path
    profile_json: dict = field(default_factory=dict)

    # ---- simple accessors -------------------------------------------------
    @property
    def timezone_name(self) -> str:
        return str(self.raw.get("timezone") or self.profile_json.get("timezone") or "UTC")

    @property
    def tz(self):
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("UTC")

    @property
    def host(self) -> str:
        return str(self.raw.get("host") or "127.0.0.1")

    @property
    def port(self) -> int:
        return int(self.raw.get("port") or 8765)

    def _rt(self, value: str) -> Path:
        p = Path(os.path.expanduser(str(value)))
        return p if p.is_absolute() else (self.posting_tool_root / p)

    @property
    def ledger_path(self) -> Path:
        return self._rt(self.raw.get("ledger") or "logs/ledger.jsonl")

    @property
    def snapshots_dir(self) -> Path:
        return self._rt(self.raw.get("snapshots_dir") or "logs/dashboard/snapshots")

    def runtime_path(self, key: str) -> Optional[Path]:
        """posted / failed / in_flight / inbox / screenshots / state."""
        val = (self.raw.get("paths") or {}).get(key) or DEFAULTS["paths"].get(key)
        return self._rt(val) if val else None

    @property
    def dispatch_lock_path(self) -> Path:
        return self._rt(self.raw.get("dispatch_lock") or ".dispatch.lock")

    @property
    def cdp_url(self) -> str:
        b = self.raw.get("browser") or {}
        chrome = self.profile_json.get("chrome") if isinstance(self.profile_json.get("chrome"), dict) else {}
        return str(b.get("cdp_url") or chrome.get("cdp") or "")

    def check_enabled(self, name: str) -> bool:
        return bool(((self.raw.get("checks") or {}).get(name) or {}).get("enabled"))

    def check_opt(self, name: str, key: str, default=None):
        return ((self.raw.get("checks") or {}).get(name) or {}).get(key, default)

    @property
    def expected_handles(self) -> dict[str, str]:
        """profile.json "handles" per platform, else "expected_handle"/"handle"."""
        pj = self.profile_json
        default = str(pj.get("expected_handle") or pj.get("handle") or "")
        if default.upper().startswith("YOUR_"):
            default = ""
        per = pj.get("handles") if isinstance(pj.get("handles"), dict) else {}
        out = {p: str(per.get(p) or default) for p in PLATFORMS}
        return {k: v for k, v in out.items() if v}

    def media_roots(self) -> list[Path]:
        roots = []
        for key in ("posted", "failed", "inbox", "screenshots", "in_flight"):
            p = self.runtime_path(key)
            if p:
                roots.append(p)
        roots.append(self.snapshots_dir)
        extra = self.raw.get("extra_media_roots") or []
        roots.extend(self._rt(x) for x in extra)
        return roots

    def to_public_dict(self) -> dict:
        """What the page may see (no filesystem paths)."""
        checks = {}
        for name in list(PLATFORMS) + ["youtube_api", "youtube_studio"]:
            if name in (self.raw.get("checks") or {}):
                checks[name] = self.check_enabled(name)
        return {
            "timezone": self.timezone_name,
            "window": self.raw.get("window"),
            "show_history": bool(self.raw.get("show_history")),
            "checks": checks,
            "threads_affects_status": bool(self.check_opt("threads", "affects_status", False)),
        }


def load_config(path: Optional[str | Path] = None, overrides: Optional[dict] = None) -> Config:
    cfg_path: Optional[Path] = None
    if path:
        cfg_path = Path(path)
    elif os.environ.get("POSTING_DASHBOARD_CONFIG"):
        cfg_path = Path(os.environ["POSTING_DASHBOARD_CONFIG"])
    elif (DASHBOARD_DIR / "config.json").is_file():
        cfg_path = DASHBOARD_DIR / "config.json"

    raw = dict(DEFAULTS)
    config_dir = DASHBOARD_DIR
    if cfg_path is not None:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        raw = _deep_merge(DEFAULTS, data)
        config_dir = cfg_path.resolve().parent
    if overrides:
        raw = _deep_merge(raw, overrides)

    root = Path(os.path.expanduser(str(raw["posting_tool_root"])))
    if not root.is_absolute():
        root = (config_dir / root).resolve()

    profile_json: dict = {}
    prof_file = Path(os.environ.get("POSTING_TOOL_PROFILE_FILE") or (root / "profile.json"))
    if prof_file.is_file():
        try:
            profile_json = json.loads(prof_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            profile_json = {}
        if not isinstance(profile_json, dict):
            profile_json = {}
    return Config(raw=raw, config_dir=config_dir, posting_tool_root=root,
                  profile_json=profile_json)
