"""
watch_inbox.py — decide whether it's time to post the next video, and which one.

Pure decision logic. No Chrome, no Playwright, no posting. Just:
  - List pending videos in inbox/
  - Read state.json for last_posted_at
  - Compute the adaptive target interval (clamp 1h..4h)
  - Return POST(basename) | WAIT(seconds) | IDLE | INVALID(basename, reason)

Runnable standalone for inspection:
    python3 watch_inbox.py

Importable by post_next.py:
    from watch_inbox import decide
    decision = decide()
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
INBOX_DIR = PROJECT_ROOT / "inbox"
STATE_FILE = PROJECT_ROOT / "state.json"

MIN_INTERVAL_HOURS = 1.0
MAX_INTERVAL_HOURS = 4.0
DAY_HOURS = 24.0

# Files in inbox/ that are not videos and should be ignored.
IGNORE_NAMES = {"README.md", "_example.json", ".gitkeep", ".DS_Store"}


# ----------------------------------------------------------------------------
# Data types
# ----------------------------------------------------------------------------

@dataclass
class Decision:
    action: str  # "post" | "wait" | "idle" | "invalid"
    basename: Optional[str] = None
    seconds_until_next: Optional[int] = None
    reason: Optional[str] = None
    pending_count: int = 0
    target_interval_hours: Optional[float] = None
    last_posted_at: Optional[str] = None


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        # Corrupt state file — treat as fresh, but don't silently overwrite.
        # post_next.py is responsible for repairing it on the next successful post.
        return {}


def parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # fromisoformat handles "2026-04-24T14:00:00-07:00" on Python 3.11+
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Inbox scanning
# ----------------------------------------------------------------------------

def list_pending_videos() -> list[Path]:
    """Return inbox .mp4 files sorted by mtime (oldest first)."""
    if not INBOX_DIR.exists():
        return []
    videos = []
    for p in INBOX_DIR.iterdir():
        if p.name in IGNORE_NAMES:
            continue
        if p.suffix.lower() != ".mp4":
            continue
        videos.append(p)
    videos.sort(key=lambda p: p.stat().st_mtime)
    return videos


def sidecar_path(video: Path) -> Path:
    return video.with_suffix(".json")


def validate_sidecar(video: Path) -> tuple[bool, Optional[str], Optional[dict]]:
    """Return (is_valid, error_reason, parsed_sidecar)."""
    side = sidecar_path(video)
    if not side.exists():
        return False, f"missing sidecar {side.name}", None
    try:
        data = json.loads(side.read_text())
    except json.JSONDecodeError as e:
        return False, f"invalid JSON in {side.name}: {e}", None

    captions = data.get("captions")
    if not isinstance(captions, dict) or not captions:
        return False, "sidecar missing 'captions' object", None

    requested = data.get("platforms", ["all"])
    if requested == ["all"] or requested == "all":
        requested = ["youtube", "tiktok", "instagram", "threads", "x", "pinterest", "reddit"]

    missing = [p for p in requested if p not in captions]
    if missing:
        return False, f"sidecar missing captions for: {', '.join(missing)}", None

    return True, None, data


# ----------------------------------------------------------------------------
# Spacing
# ----------------------------------------------------------------------------

def compute_target_interval_hours(pending_count: int) -> float:
    """24h / count, clamped to [1h, 4h]. count<=0 returns the max."""
    if pending_count <= 0:
        return MAX_INTERVAL_HOURS
    raw = DAY_HOURS / pending_count
    return max(MIN_INTERVAL_HOURS, min(MAX_INTERVAL_HOURS, raw))


def is_eligible_now(video: Path, sidecar: dict, now: datetime) -> tuple[bool, Optional[str]]:
    """Check do_not_post_before guard. Returns (eligible, reason_if_not)."""
    dnpb = sidecar.get("do_not_post_before")
    if not dnpb:
        return True, None
    target = parse_iso(dnpb)
    if target is None:
        return True, None  # Unparseable — don't block on it
    if now < target:
        wait_s = int((target - now).total_seconds())
        return False, f"do_not_post_before in {wait_s}s"
    return True, None


# ----------------------------------------------------------------------------
# Main decision
# ----------------------------------------------------------------------------

def decide(now: Optional[datetime] = None) -> Decision:
    if now is None:
        now = datetime.now(timezone.utc)

    state = load_state()
    last_posted_at = parse_iso(state.get("last_posted_at", ""))

    videos = list_pending_videos()
    pending_count = len(videos)

    target_h = compute_target_interval_hours(pending_count)

    # Idle: nothing in the inbox.
    if not videos:
        return Decision(
            action="idle",
            pending_count=0,
            target_interval_hours=target_h,
            last_posted_at=state.get("last_posted_at"),
        )

    # Spacing check: have we waited long enough since the last post?
    if last_posted_at is not None:
        elapsed_s = (now - last_posted_at).total_seconds()
        target_s = target_h * 3600.0
        if elapsed_s < target_s:
            return Decision(
                action="wait",
                seconds_until_next=int(target_s - elapsed_s),
                pending_count=pending_count,
                target_interval_hours=target_h,
                last_posted_at=state.get("last_posted_at"),
            )

    # Pick the oldest eligible video.
    for video in videos:
        ok, reason, sidecar = validate_sidecar(video)
        if not ok:
            return Decision(
                action="invalid",
                basename=video.stem,
                reason=reason,
                pending_count=pending_count,
                target_interval_hours=target_h,
                last_posted_at=state.get("last_posted_at"),
            )
        eligible, why_not = is_eligible_now(video, sidecar, now)
        if not eligible:
            # Skip this one and try the next — it has a future do_not_post_before.
            continue
        return Decision(
            action="post",
            basename=video.stem,
            pending_count=pending_count,
            target_interval_hours=target_h,
            last_posted_at=state.get("last_posted_at"),
        )

    # All videos blocked by do_not_post_before — wait until the soonest one.
    return Decision(
        action="wait",
        seconds_until_next=int(target_h * 3600),
        reason="all pending videos blocked by do_not_post_before",
        pending_count=pending_count,
        target_interval_hours=target_h,
        last_posted_at=state.get("last_posted_at"),
    )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    rem = m % 60
    return f"{h}h{rem:02d}m"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    decision = decide()

    if as_json:
        print(json.dumps(asdict(decision), indent=2))
        return 0

    print(f"pending in inbox:        {decision.pending_count}")
    print(f"target interval:         {decision.target_interval_hours:.2f}h")
    print(f"last posted at:          {decision.last_posted_at or '(never)'}")
    print(f"decision:                {decision.action.upper()}")
    if decision.basename:
        print(f"  basename:              {decision.basename}")
    if decision.seconds_until_next is not None:
        print(f"  next eligible in:      {_human_duration(decision.seconds_until_next)}")
    if decision.reason:
        print(f"  reason:                {decision.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
