"""
schedule_batch.py — dispatch-triggered batch scheduler.

Called by the agent when the user dispatches a posting session (3-4x/day, roughly
every 6h, mostly mornings + midday). Produces a JSON plan that tells the agent:

    For each video in the inbox × each enabled platform,
    schedule it via the platform's native scheduler at time T,
    then move the video to posted/<date>/ with a receipt.

This file does NOT drive Chrome. The runtime flow is:

    1. the agent calls: `python3 schedule_batch.py plan --horizon-hours 6 --json`
       → JSON plan printed to stdout.
    2. the agent shows the plan to the user and confirms.
    3. For each (video, platform) in the plan, the agent follows playbooks/<platform>.md
       in Chrome to perform the native scheduling. As each succeeds/fails, the agent
       calls:
           python3 schedule_batch.py record <basename> <platform> --status scheduled \\
               --scheduled-for <iso> [--url <url>] [--error <msg>]
    4. Once every platform for a video has a recorded result, the agent calls:
           python3 schedule_batch.py finalize <basename>
       which moves the files to posted/<date>/ (all-ok) or failed/ (any error)
       and writes the consolidated receipt.

Legacy `post_next.py` remains for the hourly-tick / one-at-a-time model; this
file is the dispatch-triggered / batch-scheduling replacement.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from watch_inbox import (
    PROJECT_ROOT,
    INBOX_DIR,
    STATE_FILE,
    list_pending_videos,
    sidecar_path,
    validate_sidecar,
    parse_iso,
)

from platforms._preflight import (
    daily_remaining_map,
    reset_first_post_cache,
)
from platforms._safety_locks import (
    DAILY_CAP_SURFACES,
    evaluate_batch_preflight,
    format_remaining,
    load_user_profile,
    refuse_unschedulable_batch,
)

# ----------------------------------------------------------------------------
# Paths + config
# ----------------------------------------------------------------------------

POSTED_DIR = PROJECT_ROOT / "posted"
FAILED_DIR = PROJECT_ROOT / "failed"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "schedule_batch.log"
TOPICS_FILE = PROJECT_ROOT / "topics.json"
IN_FLIGHT_DIR = PROJECT_ROOT / ".in_flight"
IN_FLIGHT_DIR.mkdir(exist_ok=True)

PLATFORM_ORDER = ["youtube", "x", "threads", "reddit", "tiktok", "instagram", "pinterest"]

DEFAULT_HORIZON_HOURS = 240         # 10 days — matches TikTok's max scheduling horizon (the tightest)
DEFAULT_EARLIEST_OFFSET_MINUTES = 30  # respect platform scheduler minimums
DEFAULT_MAX_BATCH = 96              # 240h / 2.5h spacing = 96 slots; under Pinterest's 100-scheduled-pins cap
DEFAULT_SPACING_HOURS = 2.5         # fixed gap between consecutive videos in a batch

# Threads enforces a hard cap on per-account future-scheduled posts. Hit on
# 2026-05-05: dispatch successfully scheduled 25, then every subsequent
# attempt failed at the final commit step ("Save to drafts" dialog appeared).
# When at the cap, api_post() short-circuits Threads and records status="skipped"
# with quota_skipped=True, so the video still finalizes to posted/ rather than
# polluting failed/. The queue drains naturally as posts publish.
THREADS_QUOTA_CAP = 25

# Self-healing cap on the cross-dispatch floor. If state.last_scheduled_for is
# further in the future than this, build_plan ignores it and starts the next
# batch at now+earliest_offset instead. Without this, repeated dispatches keep
# ratcheting last_scheduled_for forward (each batch claims ~12.5h), drifting
# the queue 3+ days out. Manual override: `reset-floor` subcommand.
FLOOR_CAP_HOURS = 24

# Why 10 days: Clipper produces ~5 videos per run, run 2-3x/day → 10-15 videos/day
# inbound. At 3h spacing we can publish 8/day. The 10-day horizon lets a single
# dispatch drain a large inbox into the queue all the way out to TikTok's max
# scheduling window. After Pinterest's 14-day window or TikTok's 10-day window
# we have to stop; this matches the tighter of the two.


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True, file=sys.stderr)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


# ----------------------------------------------------------------------------
# State + topics (shared with post_next.py)
# ----------------------------------------------------------------------------

def read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {}


def update_state(updates: dict) -> dict:
    state = read_state()
    state.update(updates)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


def load_topics() -> dict:
    if not TOPICS_FILE.exists():
        return {}
    try:
        return json.loads(TOPICS_FILE.read_text())
    except json.JSONDecodeError as e:
        log(f"ERROR: topics.json invalid JSON: {e}")
        return {}


def apply_rotation(captions: dict, topics: dict, rotation_index: int) -> tuple[dict, dict]:
    """Same semantics as post_next.apply_rotation — sidecar values win, slots
    fill in missing subreddit/topic/board."""
    rotation = topics.get("rotation", [])
    pinterest_board = topics.get("pinterest_board")
    if not rotation:
        return captions, {"name": "(no rotation configured)", "index": rotation_index}
    slot = rotation[rotation_index % len(rotation)]
    out = {p: dict(b) for p, b in captions.items()}
    if "reddit" in out and not out["reddit"].get("subreddit"):
        out["reddit"]["subreddit"] = slot.get("reddit", "")
    if "threads" in out and not out["threads"].get("topic"):
        out["threads"]["topic"] = slot.get("threads", "")
    if "pinterest" in out and pinterest_board and not out["pinterest"].get("board"):
        out["pinterest"]["board"] = pinterest_board
    return out, {"name": slot.get("name", "?"), "index": rotation_index % len(rotation)}


# ----------------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------------

def compute_stagger(
    n: int,
    start: datetime,
    horizon_hours: float,
    spacing_hours: Optional[float] = DEFAULT_SPACING_HOURS,
) -> list[datetime]:
    """Schedule times for n videos.

    If `spacing_hours` is set (default 3h), use a FIXED gap between consecutive
    videos: [start, start + spacing, start + 2*spacing, ...]. Cap at the count
    that fits inside `horizon_hours` from `start` so we don't schedule past the
    next dispatch window. Returning fewer than n is fine — the caller drops the
    extras and they roll forward to the next dispatch.

    If `spacing_hours` is None, fall back to "spread evenly across horizon"
    behavior (legacy).
    """
    if n <= 0:
        return []
    if n == 1:
        return [start]

    if spacing_hours is not None and spacing_hours > 0:
        step = timedelta(hours=spacing_hours)
        horizon_end = start + timedelta(hours=horizon_hours)
        out: list[datetime] = []
        for i in range(n):
            t = start + step * i
            if t > horizon_end:
                break
            out.append(t)
        return out

    # Legacy fallback: spread evenly across horizon.
    padding = timedelta(minutes=15)
    span = timedelta(hours=horizon_hours) - padding
    if span.total_seconds() <= 0:
        return [start + timedelta(minutes=15 * i) for i in range(n)]
    step = span / (n - 1)
    return [start + step * i for i in range(n)]


def build_plan(
    *,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    earliest_offset_minutes: int = DEFAULT_EARLIEST_OFFSET_MINUTES,
    max_videos: int = DEFAULT_MAX_BATCH,
    spacing_hours: Optional[float] = DEFAULT_SPACING_HOURS,
    now: Optional[datetime] = None,
) -> dict:
    if now is None:
        now = datetime.now(timezone.utc).astimezone()

    topics = load_topics()
    paused = set(topics.get("paused_platforms", []) or [])
    enabled = [p for p in PLATFORM_ORDER if p not in paused]

    videos = list_pending_videos()
    # Filter out invalid sidecars upfront so the plan only contains real work.
    ready: list[tuple[Path, dict]] = []
    invalid: list[dict] = []
    for v in videos:
        ok, reason, sidecar = validate_sidecar(v)
        if not ok:
            invalid.append({"basename": v.stem, "reason": reason})
            continue

        # Respect do_not_post_before on the sidecar — if the earliest scheduled
        # slot would still be before that time, the planner can still schedule
        # it for ≥ dnpb. But if dnpb is beyond the horizon, skip in this batch.
        dnpb = parse_iso(sidecar.get("do_not_post_before", "") or "")
        if dnpb is not None and dnpb > now + timedelta(hours=horizon_hours):
            continue

        ready.append((v, sidecar))

    ready = ready[:max_videos]

    # Figure out the schedule times. With fixed spacing, compute_stagger may
    # return fewer slots than there are ready videos — those tail videos roll
    # forward to the next dispatch.
    #
    # IMPORTANT: respect previously-scheduled posts so consecutive dispatches
    # don't cluster. Read state.last_scheduled_for (the latest scheduled time
    # from the previous dispatch's plan) and use it as a floor.
    # Without this, a fresh dispatch starts at "now+30min" even if the
    # previous dispatch just scheduled posts 22 minutes ago — verified
    # 2026-04-26 evening when vid 3 was scheduled 1:44 PM and vid 4 was
    # then scheduled 2:06 PM (only 22 min apart, not the configured 2.5h).
    start = now + timedelta(minutes=earliest_offset_minutes)
    state = read_state()
    last_sched_iso = state.get("last_scheduled_for", "")
    if last_sched_iso:
        try:
            last_sched = parse_iso(last_sched_iso)
            if last_sched is not None:
                # Self-healing cap: if the previous floor is further than
                # FLOOR_CAP_HOURS in the future, treat it as drift and start
                # fresh from now+earliest_offset. See FLOOR_CAP_HOURS comment
                # at top of file for context.
                drift_hours = (last_sched - now).total_seconds() / 3600.0
                if drift_hours > FLOOR_CAP_HOURS:
                    log(
                        f"PLAN: ignoring stale floor last_scheduled_for={last_sched_iso} "
                        f"({drift_hours:.1f}h in future > {FLOOR_CAP_HOURS}h cap) — "
                        f"starting from now+{earliest_offset_minutes}min"
                    )
                else:
                    candidate = last_sched + timedelta(hours=spacing_hours)
                    if candidate > start:
                        start = candidate
        except Exception:
            pass
    times = compute_stagger(len(ready), start, horizon_hours, spacing_hours=spacing_hours)
    rolled_forward = ready[len(times):]
    ready = ready[: len(times)]

    rotation_index = int(read_state().get("rotation_index", 0))

    videos_plan: list[dict] = []
    for (path, sidecar), scheduled in zip(ready, times):
        # Respect do_not_post_before if it bumps past `scheduled`.
        dnpb = parse_iso(sidecar.get("do_not_post_before", "") or "")
        if dnpb is not None and dnpb > scheduled:
            scheduled = dnpb

        raw_captions = sidecar.get("captions", {})
        captions, slot_info = apply_rotation(raw_captions, topics, rotation_index)
        rotation_index += 1

        # Requested platforms (sidecar `platforms` field), minus paused ones.
        requested = sidecar.get("platforms", ["all"])
        if requested == ["all"] or requested == "all":
            requested = list(PLATFORM_ORDER)
        requested = [p for p in requested if p in enabled and p in captions]

        video_entry = {
            "basename": path.stem,
            "video_path": str(path),
            "scheduled_for": scheduled.isoformat(),
            "rotation_slot": slot_info,
            "platforms": {p: captions[p] for p in requested},
            "skipped_paused": [p for p in sidecar.get("platforms", ["all"])
                              if p in paused and p != "all"],
        }
        if "tiktok" in video_entry["platforms"]:
            # Keep the datetime the sidecar/planner asked for. Do not snap
            # every user onto an fixed even-hour :45 example cadence.
            video_entry["tiktok_scheduled_for"] = video_entry["scheduled_for"]
        videos_plan.append(video_entry)

    # Persist the LATEST scheduled time so the next dispatch's start floor
    # respects this batch (prevents clustering across dispatches).
    if videos_plan:
        try:
            latest = max(v["scheduled_for"] for v in videos_plan)
            update_state({"last_scheduled_for": latest})
        except Exception:
            pass

    remaining = daily_remaining_map(POSTED_DIR)
    planned_counts: dict[str, int] = {}
    for v in videos_plan:
        for p in v["platforms"]:
            planned_counts[p] = planned_counts.get(p, 0) + 1

    return {
        "planned_at": now.isoformat(),
        "horizon_hours": horizon_hours,
        "earliest_schedule_offset_minutes": earliest_offset_minutes,
        "spacing_hours": spacing_hours,
        "enabled_platforms": enabled,
        "paused_platforms": sorted(paused),
        "videos": videos_plan,
        "invalid": invalid,
        "rolled_forward": [{"basename": p.stem, "reason": "exceeds horizon at current spacing"}
                           for p, _ in rolled_forward],
        "next_rotation_index": rotation_index,
        "remaining_slots": remaining,
        "planned_counts": planned_counts,
        "posting_tool_profile": os.environ.get("POSTING_TOOL_PROFILE", ""),
    }


# ----------------------------------------------------------------------------
# In-flight receipts
# ----------------------------------------------------------------------------

def in_flight_path(basename: str) -> Path:
    return IN_FLIGHT_DIR / f"{basename}.json"


def load_in_flight(basename: str) -> dict:
    p = in_flight_path(basename)
    if not p.exists():
        return {
            "basename": basename,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "platforms": {},
        }
    return json.loads(p.read_text())


def save_in_flight(basename: str, data: dict) -> None:
    in_flight_path(basename).write_text(json.dumps(data, indent=2))


def record_result(
    basename: str,
    platform: str,
    *,
    status: str,
    scheduled_for: Optional[str] = None,
    url: Optional[str] = None,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    data = load_in_flight(basename)
    entry = {
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if scheduled_for:
        entry["scheduled_for"] = scheduled_for
    if url:
        entry["url"] = url
    if error:
        entry["error"] = error
    if extra:
        entry.update(extra)
    data["platforms"][platform] = entry
    save_in_flight(basename, data)
    log(f"RECORD {basename} / {platform} -> {status}")


# ----------------------------------------------------------------------------
# Finalize — move files, write consolidated receipt, advance rotation
# ----------------------------------------------------------------------------

def finalize(basename: str, *, advance_rotation: bool = True) -> int:
    """Move a video + sidecar out of inbox, write the consolidated receipt,
    and optionally advance the rotation index.

    Args:
      advance_rotation: True for normal first-time finalization (the rotation
        slot is "consumed" by this video). False for retry finalization,
        because the *original* finalize already advanced rotation when the
        video first moved to failed/. Re-advancing on retry would skip a
        rotation slot.
    """
    video = INBOX_DIR / f"{basename}.mp4"
    side = sidecar_path(video)

    in_flight = load_in_flight(basename)
    platforms = in_flight.get("platforms", {})
    any_failure = any(
        e.get("status") not in ("scheduled", "success", "skipped")
        for e in platforms.values()
    )
    # Treat a "skipped: paused" as non-failure. Treat "failed" as failure.

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    if any_failure or not platforms:
        dest_dir = FAILED_DIR
        dest_dir.mkdir(exist_ok=True)
        receipt_name = f"{basename}.posted.json"
    else:
        dest_dir = POSTED_DIR / today
        dest_dir.mkdir(parents=True, exist_ok=True)
        receipt_name = f"{basename}.posted.json"

    # Move files if still in inbox.
    if video.exists():
        shutil.move(str(video), str(dest_dir / video.name))
    if side.exists():
        shutil.move(str(side), str(dest_dir / side.name))

    # Write consolidated receipt. Preserve any retry metadata (e.g.,
    # retry_of_previous) so audit trail is intact.
    consolidated = {
        "basename": basename,
        "started_at": in_flight.get("started_at"),
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms,
    }
    if "retry_of_previous" in in_flight:
        consolidated["retry_of_previous"] = in_flight["retry_of_previous"]
    (dest_dir / receipt_name).write_text(json.dumps(consolidated, indent=2))

    # Advance rotation index (every finalized video advances by 1, same as
    # legacy post_next behavior — so failures don't re-use the slot). On a
    # retry, the original finalize already advanced rotation, so we don't.
    if advance_rotation:
        state = read_state()
        rotation_index = int(state.get("rotation_index", 0)) + 1
        update_state({
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
            "last_video_basename": basename,
            "rotation_index": rotation_index,
        })
    else:
        update_state({
            "last_posted_at": datetime.now(timezone.utc).isoformat(),
            "last_video_basename": basename,
        })

    # Remove in-flight receipt.
    p = in_flight_path(basename)
    if p.exists():
        p.unlink()

    log(f"FINALIZED {basename} -> {'failed/' if any_failure else 'posted/' + today + '/'}"
        f"{' (retry)' if not advance_rotation else ''}")
    return 2 if any_failure else 0


# ----------------------------------------------------------------------------
# API-routed platform dispatch (Reddit, YouTube, etc.)
# ----------------------------------------------------------------------------

def _count_future_threads_scheduled() -> int:
    """Count Threads receipts in posted/ whose scheduled_for is still in the future.

    Used by api_post() to short-circuit Threads when the per-account scheduled-
    queue cap is reached. Cheap to run (~50ms even with hundreds of receipts)
    so we just call it on every Threads attempt rather than caching.
    """
    now = datetime.now(timezone.utc)
    n = 0
    if not POSTED_DIR.exists():
        return 0
    for path in POSTED_DIR.glob("**/*.posted.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        th = (d.get("platforms") or {}).get("threads") or {}
        if th.get("status") != "scheduled":
            continue
        sf = th.get("scheduled_for")
        if not sf:
            continue
        try:
            if datetime.fromisoformat(sf) > now:
                n += 1
        except ValueError:
            continue
    return n


def api_post(basename: str, platform: str, *, scheduled_for: Optional[str] = None) -> int:
    """Look up captions for `basename`, dispatch to platforms/<platform>.post(),
    record the result, and return 0/2 for ok/failed (matches finalize semantics).

    Reads from inbox/<basename>.{mp4,json}. Applies rotation defaults from
    topics.json the same way build_plan does. Skips if platform is in
    paused_platforms.

    `scheduled_for` is passed to the platform module as a kwarg — YouTube uses
    it as `publishAt`. Reddit ignores it (no scheduler). If None, platforms post
    immediately or to "public now".
    """
    import importlib

    video = INBOX_DIR / f"{basename}.mp4"
    side = sidecar_path(video)
    if not video.exists():
        log(f"ERROR: {video} not found")
        return 2
    if not side.exists():
        log(f"ERROR: {side} not found")
        return 2

    sidecar = json.loads(side.read_text())
    raw_captions = sidecar.get("captions", {})

    topics = load_topics()
    paused = set(topics.get("paused_platforms", []) or [])
    if platform in paused:
        record_result(basename, platform, status="skipped",
                      error=f"{platform} is in paused_platforms")
        log(f"SKIP {platform} (paused)")
        return 0

    # Native Schedule only. Never Post now. YouTube API publishAt is native
    # schedule; Reddit has no scheduler and is paused.
    _schedule_required = platform in {
        "instagram", "tiktok", "threads", "pinterest", "x", "youtube",
    }
    if _schedule_required and not scheduled_for:
        fail = refuse_unschedulable_batch()
        record_result(basename, platform, status="failed", error=fail.message,
                      extra={"error_code": fail.code, "post_now_forbidden": True})
        log(f"FAIL {platform} native-schedule-required")
        return 2

    from platforms._safety_locks import check_one_url_cta
    cap_block = (raw_captions or {}).get(platform) or {}
    cta_text = " ".join(
        str(cap_block.get(k) or "") for k in ("caption", "description", "title", "cta", "url")
    )
    cta_fail = check_one_url_cta(
        cta_text,
        surface=platform,
        allowed_host=(load_user_profile().allowed_cta_host() or None),
    )
    if cta_fail:
        record_result(basename, platform, status="failed", error=cta_fail.message,
                      extra={"error_code": cta_fail.code, "post_now_forbidden": True})
        log(f"FAIL {platform} {cta_fail.code}")
        return 2

    # Threads-specific: short-circuit if the future-scheduled queue is at the
    # 25-post cap. Without this, every attempt past the cap burns ~45s of
    # Playwright automation only to fail at the final commit step. Marking the
    # receipt status="skipped" (vs "failed") keeps the video on the posted/
    # path provided every other platform succeeded — failed/ doesn't get
    # polluted with quota hits. The quota_skipped flag in extra makes these
    # findable by a future retry-quota-skipped sweep.
    if platform == "threads":
        queued = _count_future_threads_scheduled()
        if queued >= THREADS_QUOTA_CAP:
            record_result(
                basename, platform, status="skipped",
                error=f"Threads scheduled-queue full ({queued}/{THREADS_QUOTA_CAP}). "
                      f"Will be eligible for retry once posts publish and the queue drains.",
                extra={"quota_skipped": True, "queue_count": queued, "queue_cap": THREADS_QUOTA_CAP},
            )
            log(f"SKIP threads (queue full: {queued}/{THREADS_QUOTA_CAP})")
            return 0

    state = read_state()
    rotation_index = int(state.get("rotation_index", 0))
    captions, slot_info = apply_rotation(raw_captions, topics, rotation_index)

    if platform not in captions:
        record_result(basename, platform, status="skipped",
                      error="no caption block for this platform")
        log(f"SKIP {platform} (no caption)")
        return 0

    try:
        mod = importlib.import_module(f"platforms.{platform}")
    except ModuleNotFoundError as e:
        record_result(basename, platform, status="failed",
                      error=f"platforms/{platform}.py not importable: {e}")
        return 2

    poster = getattr(mod, "post", None)
    if poster is None:
        record_result(basename, platform, status="failed",
                      error=f"platforms/{platform}.py has no post() function")
        return 2

    log(f"API-POST {basename} -> {platform} (slot: {slot_info['name']}, scheduled_for: {scheduled_for or '-'})")
    result = poster(video, captions[platform], scheduled_for=scheduled_for)
    if not isinstance(result, dict):
        result = {"status": "failed", "error": f"non-dict result: {result!r}"}
    status = result.get("status", "failed")
    record_result(
        basename, platform,
        status=status,
        url=result.get("url"),
        scheduled_for=result.get("scheduled_for"),
        error=result.get("error"),
        extra={k: v for k, v in result.items()
               if k not in {"status", "url", "scheduled_for", "error", "at"}},
    )
    return 0 if status in ("success", "scheduled", "skipped") else 2


# ----------------------------------------------------------------------------
# Live-posting batch preflight (Sep 1 2026). Existing locks stay.
# ----------------------------------------------------------------------------

_PACK_HARD_FAIL_SKIP = frozenset(
    {
        "daily_rate_limit_exhausted",
        "daily_rate_limit_would_overflow",
    }
)


def _captions_from_plan(plan: dict) -> list[dict]:
    return [v.get("platforms") or {} for v in plan.get("videos") or []]


def run_batch_preflight(plan: dict, *, immediate: bool = False):
    """Fail closed before any surface is touched. Rate-limit exhaust = skip, not abort."""
    remaining = plan.get("remaining_slots") or daily_remaining_map(POSTED_DIR)
    daily_used = {
        surface: int((remaining.get(surface) or {}).get("used") or 0)
        for surface in DAILY_CAP_SURFACES
    }
    result = evaluate_batch_preflight(
        captions_by_video=_captions_from_plan(plan),
        daily_used=daily_used,
        planned_counts=plan.get("planned_counts") or {},
        immediate=immediate,
    )
    for surface, info in (result.remaining_slots or remaining).items():
        log(f"PREFLIGHT {format_remaining(info, surface=surface)}")
    hard = [f for f in result.failures if f.code not in _PACK_HARD_FAIL_SKIP]
    return result, hard


def _scheduled_for_platform(video_entry: dict, platform: str, fallback: Optional[str]) -> Optional[str]:
    if platform == "tiktok":
        return video_entry.get("tiktok_scheduled_for") or fallback
    return fallback


def _maybe_skip_daily_cap(basename: str, platform: str, leftover: dict[str, int]) -> bool:
    if platform not in leftover:
        return False
    if leftover[platform] > 0:
        leftover[platform] -= 1
        return False
    cap = DAILY_CAP_SURFACES.get(platform, 0)
    record_result(
        basename, platform, status="skipped",
        error=(
            f"{platform} daily cap reached (0 of {cap} left). "
            "Not dispatching this surface — do not fail at 2am."
        ),
        extra={"quota_skipped": True, "daily_cap": cap, "daily_remaining": 0},
    )
    log(f"SKIP {platform} (0 of {cap} daily slots left)")
    return True

def dispatch(
    *,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
    earliest_offset_minutes: int = DEFAULT_EARLIEST_OFFSET_MINUTES,
    max_videos: int = DEFAULT_MAX_BATCH,
    spacing_hours: Optional[float] = DEFAULT_SPACING_HOURS,
    dry_run: bool = False,
    platform_filter: Optional[list[str]] = None,
) -> int:
    """Build a plan, then for every (video × enabled platform) call
    platforms/<platform>.post() and record the result. Finalize each video
    once all its platforms are recorded.

    Returns 0 if every video finalized clean (success or scheduled), 2 if any
    video had a failure on at least one platform, 1 if there was nothing to do.
    """
    plan = build_plan(
        horizon_hours=horizon_hours,
        earliest_offset_minutes=earliest_offset_minutes,
        max_videos=max_videos,
        spacing_hours=spacing_hours,
    )
    if not plan["videos"]:
        log("DISPATCH: nothing to schedule")
        return 1

    reset_first_post_cache()
    preflight, hard = run_batch_preflight(plan, immediate=False)
    if hard:
        for fail in hard:
            log(f"PREFLIGHT FAIL {fail.code}: {fail.message}")
            if fail.human_step:
                log(f"  human_step: {fail.human_step}")
        log("DISPATCH: batch refused — fix preflight, do not Post now")
        return 2

    leftover = {
        surface: int((preflight.remaining_slots.get(surface) or {}).get("remaining") or 0)
        for surface in DAILY_CAP_SURFACES
    }

    log(f"DISPATCH: {len(plan['videos'])} video(s), {len(plan['enabled_platforms'])} platform(s)")
    if plan.get("rolled_forward"):
        log(f"  rolled forward to next dispatch: {len(plan['rolled_forward'])}")

    overall_failure = False
    for v in plan["videos"]:
        basename = v["basename"]
        scheduled_for = v["scheduled_for"]
        platforms_to_post = list(v["platforms"].keys())
        if platform_filter:
            platforms_to_post = [p for p in platforms_to_post if p in platform_filter]
        log(f"  video: {basename}  ->  {platforms_to_post}  @ {scheduled_for}")

        for platform in platforms_to_post:
            slot = _scheduled_for_platform(v, platform, scheduled_for)
            if dry_run:
                log(f"    DRY-RUN: would post {basename} -> {platform} @ {slot}")
                continue
            if _maybe_skip_daily_cap(basename, platform, leftover):
                continue
            rc = api_post(basename, platform, scheduled_for=slot)
            if rc != 0:
                overall_failure = True

        if dry_run:
            log(f"    DRY-RUN: would finalize {basename}")
            continue

        # Finalize the video — moves to posted/<date>/ or failed/.
        rc = finalize(basename)
        if rc != 0:
            overall_failure = True

    return 2 if overall_failure else 0


# ----------------------------------------------------------------------------
# Retry — re-run only the platforms that failed for videos in failed/
# ----------------------------------------------------------------------------

def retry_failed_videos(
    *,
    only_videos: Optional[list[str]] = None,
    only_platforms: Optional[list[str]] = None,
    dry_run: bool = False,
    immediate: bool = False,
    earliest_offset_minutes: int = DEFAULT_EARLIEST_OFFSET_MINUTES,
    spacing_hours: Optional[float] = DEFAULT_SPACING_HOURS,
    horizon_hours: float = DEFAULT_HORIZON_HOURS,
) -> int:
    """For each video in `failed/`, re-attempt only the platforms that
    previously failed. Platforms that already succeeded last run are
    NOT re-posted (no duplicate posts).

    By default, retries are STAGGERED across future time slots with the
    same defaults as a regular dispatch (start = now + earliest_offset,
    3h spacing between videos). This avoids the "5 videos posted to
    TikTok in 7 minutes" problem. Pass `immediate=True` to post all
    retries immediately instead.

    Per-video flow:
      1. Read the existing `failed/<basename>.posted.json` receipt.
      2. Identify platforms with status not in {success, scheduled, skipped}.
      3. Back up the old receipt to `<basename>.posted.json.previous`
         (audit trail in case the retry also fails).
      4. Move the video + sidecar from `failed/` back to `inbox/` so
         `api_post` and `finalize` can find them.
      5. Pre-populate `.in_flight/<basename>.json` with the previously-OK
         platform records so `finalize` sees them as already-done.
      6. For each failed platform, call `api_post(basename, platform,
         scheduled_for=<computed>)`.
      7. `finalize(basename, advance_rotation=False)` — moves to
         `posted/<today>/` if everything's now ok, else stays in `failed/`.
         We do NOT advance rotation because the original finalize already
         did.

    Args:
      only_videos: optional list of basenames to limit retry to.
                   If None, retries every receipt in failed/.
      only_platforms: optional list of platforms to limit retry to.
      dry_run: print what would happen, don't move files or post.
      immediate: if True, post retries immediately (scheduled_for=None).
                 Default False = stagger across future slots.
      earliest_offset_minutes: minutes from now to first retry's slot.
      spacing_hours: hours between consecutive retry slots.
      horizon_hours: drop retries that wouldn't fit in the horizon.

    Return code: 0 if every retry finalized clean, 2 if any video had a
    failure, 1 if there was nothing to do.
    """
    if immediate:
        fail = refuse_unschedulable_batch()
        log(f"RETRY refused: {fail.message}")
        log(f"  human_step: {fail.human_step}")
        return 2

    if not FAILED_DIR.exists():
        log("RETRY: no failed/ directory")
        return 1

    receipts = sorted(FAILED_DIR.glob("*.posted.json"))
    if not receipts:
        log("RETRY: no failed videos to retry")
        return 1

    # First pass: figure out which receipts actually have failed platforms
    # to retry, so we can compute one staggered schedule across them.
    eligible: list[tuple[Path, str, dict, dict]] = []
    for receipt_path in receipts:
        if not receipt_path.name.endswith(".posted.json"):
            continue
        basename = receipt_path.name[: -len(".posted.json")]
        if only_videos and basename not in only_videos:
            continue
        try:
            old = json.loads(receipt_path.read_text())
        except Exception as e:
            log(f"RETRY: skipping unreadable {receipt_path.name}: {e}")
            continue
        old_platforms = old.get("platforms", {})
        failed_for_video = {
            p: e for p, e in old_platforms.items()
            if e.get("status") not in ("success", "scheduled", "skipped")
        }
        if only_platforms:
            failed_for_video = {
                p: e for p, e in failed_for_video.items() if p in only_platforms
            }
        if not failed_for_video:
            continue
        eligible.append((receipt_path, basename, old, failed_for_video))

    if not eligible:
        log("RETRY: no failed platforms to retry")
        return 1

    # Compute staggered schedule times — one slot per eligible video,
    # using the same conventions as a regular dispatch.
    schedule_for_video: dict[str, Optional[str]] = {}
    if immediate:
        for _, basename, _, _ in eligible:
            schedule_for_video[basename] = None
        log(f"RETRY: posting {len(eligible)} video(s) IMMEDIATELY (--immediate)")
    else:
        start = datetime.now(timezone.utc).astimezone() + timedelta(minutes=earliest_offset_minutes)
        times = compute_stagger(len(eligible), start, horizon_hours, spacing_hours=spacing_hours)
        if len(times) < len(eligible):
            log(f"RETRY: only {len(times)} of {len(eligible)} videos fit in the horizon "
                f"({horizon_hours}h at {spacing_hours}h spacing). Tail will be skipped this run.")
        for (path, basename, _, _), t in zip(eligible, times):
            schedule_for_video[basename] = t.isoformat()
        # Trim eligible to the slots that fit.
        eligible = eligible[: len(times)]
        log(f"RETRY: scheduling {len(eligible)} video(s) starting "
            f"{start.strftime('%H:%M %Z')}, {spacing_hours}h apart")

    overall_failure = False
    handled = 0

    for receipt_path, basename, old, failed_for_video in eligible:
        old_platforms = old.get("platforms", {})
        scheduled_for = schedule_for_video.get(basename)
        when = scheduled_for if scheduled_for else "immediate"
        log(f"RETRY: {basename} — retrying {len(failed_for_video)} platform(s): "
            f"{', '.join(failed_for_video.keys())} @ {when}")

        if dry_run:
            handled += 1
            continue

        video_failed = FAILED_DIR / f"{basename}.mp4"
        side_failed = FAILED_DIR / f"{basename}.json"
        if not video_failed.exists():
            log(f"  ERROR: video not found at {video_failed} — skipping")
            continue
        if not side_failed.exists():
            log(f"  ERROR: sidecar not found at {side_failed} — skipping")
            continue

        # Back up the old receipt so audit trail isn't lost. The new finalize
        # will write a fresh receipt at the destination (posted/ or failed/).
        backup = receipt_path.with_name(f"{basename}.posted.json.previous")
        try:
            if backup.exists():
                backup.unlink()
            receipt_path.rename(backup)
        except Exception as e:
            log(f"  WARN: could not back up old receipt: {e}")

        # Move video + sidecar back to inbox (api_post + finalize read from inbox).
        try:
            shutil.move(str(video_failed), str(INBOX_DIR / video_failed.name))
            shutil.move(str(side_failed), str(INBOX_DIR / side_failed.name))
        except Exception as e:
            log(f"  ERROR: could not move files into inbox: {e} — aborting this retry")
            # Best-effort: restore backup so the receipt isn't orphaned.
            try:
                if backup.exists():
                    backup.rename(receipt_path)
            except Exception:
                pass
            overall_failure = True
            continue

        # Pre-populate in-flight with the previously-OK platform records.
        # finalize sums these in with the retry results to decide posted/ vs failed/.
        existing_ok = {
            p: e for p, e in old_platforms.items()
            if e.get("status") in ("success", "scheduled", "skipped")
        }
        in_flight_data = {
            "basename": basename,
            "started_at": old.get("started_at") or datetime.now(timezone.utc).isoformat(),
            "platforms": existing_ok,
            "retry_of_previous": backup.name,
        }
        save_in_flight(basename, in_flight_data)

        # Retry each failed platform with the scheduled_for already
        # computed above (per-video stagger, or None for immediate).
        for platform in list(failed_for_video.keys()):
            rc = api_post(basename, platform, scheduled_for=scheduled_for)
            if rc != 0:
                overall_failure = True

        # Finalize without advancing rotation (original finalize did that).
        rc = finalize(basename, advance_rotation=False)
        if rc != 0:
            overall_failure = True

        handled += 1

    if handled == 0:
        log("RETRY: nothing to do")
        return 1
    return 2 if overall_failure else 0


# ----------------------------------------------------------------------------
# Pretty-print plan
# ----------------------------------------------------------------------------

def print_plan_human(plan: dict) -> None:
    print(f"Planned at:     {plan['planned_at']}")
    print(f"Horizon:        {plan['horizon_hours']}h")
    print(f"Earliest slot:  +{plan['earliest_schedule_offset_minutes']}min from now")
    spacing = plan.get("spacing_hours")
    print(f"Spacing:        {spacing}h between videos" if spacing else "Spacing:        spread evenly across horizon")
    print(f"Enabled:        {', '.join(plan['enabled_platforms'])}")
    remaining = plan.get("remaining_slots") or {}
    if remaining:
        print("Remaining today:")
        for surface, info in remaining.items():
            print(f"  {format_remaining(info, surface=surface)}")
    if plan["paused_platforms"]:
        print(f"Paused:         {', '.join(plan['paused_platforms'])}")
    print(f"Videos to schedule: {len(plan['videos'])}")
    rolled = plan.get("rolled_forward", [])
    if rolled:
        print(f"Rolled forward to next dispatch (don't fit horizon): {len(rolled)}")
        for r in rolled:
            print(f"  - {r['basename']}")
    if plan["invalid"]:
        print(f"Invalid sidecars (will skip): {len(plan['invalid'])}")
        for bad in plan["invalid"]:
            print(f"  - {bad['basename']}: {bad['reason']}")
    print()
    for i, v in enumerate(plan["videos"], 1):
        slot = v["rotation_slot"]
        print(f"  [{i}] {v['basename']}")
        print(f"      scheduled_for: {v['scheduled_for']}")
        if v.get("tiktok_scheduled_for"):
            print(f"      tiktok_slot:   {v['tiktok_scheduled_for']}")
        print(f"      rotation_slot: {slot['name']} (idx {slot['index']})")
        print(f"      platforms:     {', '.join(v['platforms'].keys())}")
        if v.get("skipped_paused"):
            print(f"      skipped (paused): {', '.join(v['skipped_paused'])}")
        print()


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    # plan
    p_plan = sub.add_parser("plan", help="Compute and print a batch schedule plan")
    p_plan.add_argument("--horizon-hours", type=float, default=DEFAULT_HORIZON_HOURS)
    p_plan.add_argument("--earliest-offset-minutes", type=int, default=DEFAULT_EARLIEST_OFFSET_MINUTES)
    p_plan.add_argument("--max-videos", type=int, default=DEFAULT_MAX_BATCH)
    p_plan.add_argument("--spacing-hours", type=float, default=DEFAULT_SPACING_HOURS,
                        help="Fixed gap between consecutive videos. Set to 0 to spread evenly across horizon.")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable")

    # record
    p_rec = sub.add_parser("record", help="Record a platform-level result for a video")
    p_rec.add_argument("basename")
    p_rec.add_argument("platform")
    p_rec.add_argument("--status", required=True, choices=["scheduled", "success", "failed", "skipped"])
    p_rec.add_argument("--scheduled-for", default=None)
    p_rec.add_argument("--url", default=None)
    p_rec.add_argument("--error", default=None)

    # finalize
    p_fin = sub.add_parser("finalize", help="Move a video out of the inbox with consolidated receipt")
    p_fin.add_argument("basename")

    # show
    p_show = sub.add_parser("show", help="Print the in-flight receipt for a video")
    p_show.add_argument("basename")

    # api-post: dispatch a single (basename, platform) through platforms/<platform>.py
    # Used for API-routed platforms (currently: reddit, youtube). The platform's
    # post() function does the actual upload; this wrapper handles caption lookup,
    # rotation defaults, and recording the result.
    p_api = sub.add_parser(
        "api-post",
        help="Post a single (basename, platform) via platforms/<platform>.post() and record the result",
    )
    p_api.add_argument("basename")
    p_api.add_argument("platform")
    p_api.add_argument("--scheduled-for", default=None,
                       help="ISO datetime to schedule (passed through to platform.post). "
                            "If omitted, the platform decides — YouTube goes public now, Reddit posts now.")

    # dispatch: full end-to-end run. Used by the launchd LaunchAgent.
    # plan → for each (video × enabled platform) call platforms/<name>.post()
    # → finalize each video.
    p_disp = sub.add_parser(
        "dispatch",
        help="Run a full end-to-end dispatch: plan, post every (video×platform), finalize",
    )
    p_disp.add_argument("--horizon-hours", type=float, default=DEFAULT_HORIZON_HOURS)
    p_disp.add_argument("--earliest-offset-minutes", type=int, default=DEFAULT_EARLIEST_OFFSET_MINUTES)
    p_disp.add_argument("--max-videos", type=int, default=DEFAULT_MAX_BATCH)
    p_disp.add_argument("--spacing-hours", type=float, default=DEFAULT_SPACING_HOURS)
    p_disp.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without calling any platform.post()")
    p_disp.add_argument("--platforms", default=None,
                        help="Comma-separated platform list to restrict the dispatch to "
                             "(default: all enabled in topics.json).")

    # retry-failed: re-run only the platforms that failed for videos in failed/.
    p_retry = sub.add_parser(
        "retry-failed",
        help="Re-run only the failed platforms for each video in failed/. "
             "Platforms that already succeeded are NOT re-posted.",
    )
    p_retry.add_argument("--video", action="append", default=[],
                         metavar="BASENAME",
                         help="Limit retry to specific basename(s); may be repeated. "
                              "Default: every receipt in failed/.")
    p_retry.add_argument("--platform", action="append", default=[],
                         metavar="PLATFORM",
                         help="Limit retry to specific platform(s); may be repeated.")
    p_retry.add_argument("--immediate", action="store_true",
                         help="REFUSED. Native Schedule only — never Post now. "
                              "Kept so old scripts fail closed instead of publishing now.")
    p_retry.add_argument("--earliest-offset-minutes", type=int,
                         default=DEFAULT_EARLIEST_OFFSET_MINUTES,
                         help="Minutes from now to first retry's scheduled slot.")
    p_retry.add_argument("--spacing-hours", type=float,
                         default=DEFAULT_SPACING_HOURS,
                         help="Hours between consecutive retry slots.")
    p_retry.add_argument("--horizon-hours", type=float,
                         default=DEFAULT_HORIZON_HOURS,
                         help="Drop retries that wouldn't fit in this horizon.")
    p_retry.add_argument("--dry-run", action="store_true",
                         help="Print what would be retried without doing it.")

    # reset-floor: wipe state.last_scheduled_for so the next dispatch starts
    # fresh from now+earliest_offset instead of being anchored to the previous
    # batch's tail. Use this when the queue has drifted and you want to force
    # close-in scheduling. (The 24h FLOOR_CAP also self-heals this in build_plan,
    # but reset-floor is the manual override.)
    sub.add_parser(
        "reset-floor",
        help="Clear state.last_scheduled_for so the next dispatch starts at "
             "now+earliest_offset (manual override for queue drift).",
    )

    p_pre = sub.add_parser(
        "preflight",
        help="Run live-posting batch gates without posting. Prints remaining IG/Threads slots.",
    )
    p_pre.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "plan":
        plan = build_plan(
            horizon_hours=args.horizon_hours,
            earliest_offset_minutes=args.earliest_offset_minutes,
            max_videos=args.max_videos,
            spacing_hours=(args.spacing_hours if args.spacing_hours and args.spacing_hours > 0 else None),
        )
        if args.json:
            print(json.dumps(plan, indent=2))
        else:
            print_plan_human(plan)
        return 0

    if args.cmd == "record":
        record_result(
            args.basename,
            args.platform,
            status=args.status,
            scheduled_for=args.scheduled_for,
            url=args.url,
            error=args.error,
        )
        return 0

    if args.cmd == "finalize":
        return finalize(args.basename)

    if args.cmd == "show":
        data = load_in_flight(args.basename)
        print(json.dumps(data, indent=2))
        return 0

    if args.cmd == "api-post":
        return api_post(args.basename, args.platform, scheduled_for=args.scheduled_for)

    if args.cmd == "dispatch":
        platform_filter = (args.platforms.split(",") if args.platforms else None)
        return dispatch(
            horizon_hours=args.horizon_hours,
            earliest_offset_minutes=args.earliest_offset_minutes,
            max_videos=args.max_videos,
            spacing_hours=(args.spacing_hours if args.spacing_hours and args.spacing_hours > 0 else None),
            dry_run=args.dry_run,
            platform_filter=platform_filter,
        )

    if args.cmd == "retry-failed":
        return retry_failed_videos(
            only_videos=args.video or None,
            only_platforms=args.platform or None,
            dry_run=args.dry_run,
            immediate=args.immediate,
            earliest_offset_minutes=args.earliest_offset_minutes,
            spacing_hours=(args.spacing_hours if args.spacing_hours and args.spacing_hours > 0 else None),
            horizon_hours=args.horizon_hours,
        )

    if args.cmd == "reset-floor":
        old = read_state().get("last_scheduled_for", "")
        if not old:
            print("RESET-FLOOR: state.last_scheduled_for was already empty — nothing to clear.")
            return 0
        update_state({"last_scheduled_for": ""})
        print(f"RESET-FLOOR: cleared state.last_scheduled_for (was {old}).")
        print("Next dispatch will start at now + earliest_offset_minutes.")
        return 0

    if args.cmd == "preflight":
        plan = build_plan()
        result, hard = run_batch_preflight(plan, immediate=False)
        payload = {
            "ok": not hard,
            "hard_failures": [f.as_dict() for f in hard],
            "all_failures": [f.as_dict() for f in result.failures],
            "remaining_slots": result.remaining_slots or plan.get("remaining_slots"),
            "chrome_mode": result.chrome_mode,
            "profile_id": result.profile_id,
            "videos": len(plan.get("videos") or []),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print_plan_human(plan)
            print("Preflight:", "PASS" if payload["ok"] else "FAIL")
            for fail in hard:
                print(f"  FAIL {fail.code}: {fail.message}")
                if fail.human_step:
                    print(f"    {fail.human_step}")
        return 0 if payload["ok"] else 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
