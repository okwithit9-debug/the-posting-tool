"""
platforms/reddit.py — Reddit video poster via PRAW (script-auth).

Why API instead of Chrome MCP for Reddit:
  Chrome MCP refuses to navigate reddit.com (extension safety blocklist).
  Reddit's API is free, doesn't require approval, and won't ban the account
  for legitimate use — unlike X's dev portal, which is the leading suspect
  for a prior X developer-portal ban.

This module fits the schedule_batch.py contract (a `post(video, captions)`
function returning {status, url, ...}). It posts immediately — Reddit's API
does not expose post scheduling for non-mod accounts, and our cadence is
1 Reddit post per dispatch anyway, so "post now" is the right behavior.

Auth:
  Script-type app from https://www.reddit.com/prefs/apps
  Credentials live in config.json under "reddit":
    {
      "client_id":     "<14-char string under app name>",
      "client_secret": "<27-char secret>",
      "username":      "<reddit username, no u/ prefix>",
      "password":      "<reddit password>",
      "user_agent":    "PostingTool/0.1 by YOUR_HANDLE"
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_creds() -> Optional[dict]:
    if not CONFIG_FILE.exists():
        return None
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except json.JSONDecodeError:
        return None
    creds = cfg.get("reddit") or {}
    required = ["client_id", "client_secret", "username", "password", "user_agent"]
    if not all(creds.get(k) for k in required):
        return None
    return creds


def post(video: Path, captions: dict, **_) -> dict:
    """Submit a video post to a subreddit via Reddit's API.

    Args:
      video: absolute path to the .mp4 file
      captions: the per-platform caption block, e.g.
        {"title": "...", "subreddit": "example"}

    Returns:
      dict with at least {"status": "success" | "failed" | "skipped",
                          "at": ISO timestamp, ...optionally url, error}
    """
    title = (captions or {}).get("title")
    subreddit = (captions or {}).get("subreddit")

    if not title or not subreddit:
        return {
            "status": "failed",
            "error": "reddit caption block missing 'title' or 'subreddit'",
            "at": _now_iso(),
        }

    creds = _load_creds()
    if creds is None:
        return {
            "status": "skipped",
            "error": "reddit credentials not configured in config.json (need client_id, client_secret, username, password)",
            "at": _now_iso(),
        }

    try:
        import praw  # lazy import so the module imports even without praw installed
    except ImportError:
        return {
            "status": "skipped",
            "error": "praw not installed — run: pip3 install praw",
            "at": _now_iso(),
        }

    try:
        reddit = praw.Reddit(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            username=creds["username"],
            password=creds["password"],
            user_agent=creds["user_agent"],
        )
        # Sanity check: this raises if creds are wrong.
        _ = reddit.user.me()

        sub = reddit.subreddit(subreddit)
        submission = sub.submit_video(
            title=title,
            video_path=str(video),
            videogif=False,
            without_websockets=False,
        )
        # PRAW returns a Submission object once the upload finalizes.
        url = f"https://reddit.com{submission.permalink}"
        return {
            "status": "success",
            "url": url,
            "subreddit": subreddit,
            "post_id": submission.id,
            "at": _now_iso(),
        }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
