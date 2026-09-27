"""Runtime preflight helpers for the five live-posting locks.

Pure decisions live in `_safety_locks`. This module talks to a Playwright
page (optional) and to on-disk receipts so dispatch can fail closed before
anyone hits Post now.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from platforms._safety_locks import (
    DAILY_CAP_SURFACES,
    LockFailure,
    check_one_url_cta,
    check_proof,
    check_tiktok_slot,
    commit_click_for,
    first_post_preflight_failures,
    is_forbidden_commit_click,
    load_user_profile,
    pause_on_wall,
    remaining_slots,
    scan_page_for_wall,
    wipe_required_before_paste,
)

# First-post-per-surface cache for a single dispatch process.
_FIRST_POST_CACHE: dict[str, list[LockFailure]] = {}


def reset_first_post_cache() -> None:
    _FIRST_POST_CACHE.clear()


def _page_text(page) -> str:
    try:
        return page.evaluate(
            """() => (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 20000)"""
        ) or ""
    except Exception:
        try:
            return page.inner_text("body") or ""
        except Exception:
            return ""


def _visible_text_has(page, pattern: str) -> bool:
    try:
        loc = page.get_by_text(pattern, exact=False).first
        return bool(loc.is_visible(timeout=400))
    except Exception:
        return False


def read_live_handle(page, surface: str) -> str:
    """Best-effort live handle. Empty string means missing (STOP)."""
    try:
        if surface in ("threads", "instagram"):
            hrefs = page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.getAttribute('href') || '')
                    .slice(0, 80)"""
            ) or []
            for href in hrefs:
                href = str(href)
                if surface == "threads":
                    if "/@" in href:
                        token = href.split("/@")[-1].split("/")[0].split("?")[0]
                        if token and token.lower() not in {"login", "search"}:
                            return token
                else:
                    import re
                    m = re.match(r"^/([A-Za-z0-9._]+)/?$", href)
                    reserved = {"reels", "explore", "accounts", "stories", "direct", "create"}
                    if m and m.group(1).lower() not in reserved:
                        return m.group(1)
        if surface == "tiktok":
            try:
                loc = page.locator('[data-e2e="profile-icon"], [data-e2e="nav-profile"] a').first
                href = loc.get_attribute("href", timeout=800) or ""
                token = href.rstrip("/").split("/")[-1]
                if token.startswith("@"):
                    token = token[1:]
                if token:
                    return token
            except Exception:
                pass
        if surface == "pinterest":
            try:
                href = page.locator('a[href*="/_created/"], a[data-test-id="header-profile"]').first.get_attribute("href", timeout=800) or ""
                token = [p for p in href.split("/") if p][:1]
                if token:
                    return token[0]
            except Exception:
                pass
        if surface == "x":
            try:
                href = page.locator('a[data-testid="AppTabBar_Profile_Link"]').first.get_attribute("href", timeout=800) or ""
                token = href.strip("/").split("/")[0]
                if token:
                    return token
            except Exception:
                pass
    except Exception:
        return ""
    return ""


def schedule_ui_visible(page, surface: str) -> tuple[bool, bool]:
    """Return (control_exists, looks_like_toast_only)."""
    toast_only = False
    try:
        if surface == "instagram":
            for label in ("Schedule content", "Schedule this post"):
                if _visible_text_has(page, label):
                    return True, False
            if _visible_text_has(page, "scheduled"):
                toast_only = True
            return False, toast_only
        if surface == "tiktok":
            if _visible_text_has(page, "When to post") or _visible_text_has(page, "Schedule"):
                return True, False
            return False, False
        if surface == "pinterest":
            if _visible_text_has(page, "Publish at a later date"):
                return True, False
            return False, False
        if surface == "threads":
            # Control is header ⋯ next to Drafts, not a toast and not /scheduled.
            try:
                drafts = page.locator('svg[aria-label="Drafts"]').first
                if drafts.is_visible(timeout=800):
                    return True, False
            except Exception:
                pass
            return False, False
        if surface == "x":
            try:
                btn = page.locator('button[aria-label="Schedule post"], button[aria-label="Schedule"]').first
                if btn.is_visible(timeout=800):
                    return True, False
            except Exception:
                pass
            return False, False
    except Exception:
        return False, False
    return False, False


def looks_professional(page, surface: str, *, schedule_exists: bool) -> Optional[bool]:
    text = _page_text(page).lower()
    if surface not in ("instagram", "tiktok", "pinterest"):
        return True
    if "switch to professional" in text or "convert to professional" in text:
        return False
    if "personal account" in text and "professional" not in text:
        return False
    if schedule_exists:
        return True
    if surface == "tiktok" and "tiktokstudio" in (getattr(page, "url", "") or "").lower():
        return True
    if "professional dashboard" in text or "creator studio" in text or "business account" in text:
        return True
    return None


def run_first_post_preflight(
    page,
    surface: str,
    *,
    configured_handle: Optional[str] = None,
    force: bool = False,
    stage: str = "landing",
) -> list[LockFailure]:
    cache_key = f"{surface}:{stage}"
    if not force and cache_key in _FIRST_POST_CACHE:
        return _FIRST_POST_CACHE[cache_key]
    page_text = _page_text(page)
    wall = scan_page_for_wall(page_text, surface=surface)
    if wall:
        _FIRST_POST_CACHE[cache_key] = [wall]
        return [wall]
    exists, toast_only = schedule_ui_visible(page, surface)
    if stage == "landing" and not exists:
        exists = None  # composer hasn't opened; don't fail Schedule-missing yet
        toast_only = False
    professional = looks_professional(page, surface, schedule_exists=bool(exists))
    observed = read_live_handle(page, surface)
    failures = first_post_preflight_failures(
        surface,
        observed_handle=observed,
        configured_handle=configured_handle,
        is_professional=professional,
        schedule_ui_exists=exists,
        schedule_looks_like_toast=toast_only,
        page_text=page_text,
    )
    _FIRST_POST_CACHE[cache_key] = failures
    return failures


def gate_commit_click(surface: str, label: str) -> Optional[LockFailure]:
    if is_forbidden_commit_click(label, surface=surface):
        return pause_on_wall(
            "missing_schedule_control",
            surface=surface,
            detail=(
                f"Refusing commit click {label!r}. Encoded COMMIT is "
                f"{commit_click_for(surface)!r}. Never Post now."
            ),
        )
    return None


def caption_or_wall(
    caption: str,
    *,
    surface: str,
    allowed_host: Optional[str] = None,
) -> Optional[LockFailure]:
    if not wipe_required_before_paste():
        return None
    return check_one_url_cta(caption, surface=surface, allowed_host=allowed_host)


def tiktok_scheduled_for_or_fail(scheduled_for: str) -> tuple[Optional[str], Optional[LockFailure]]:
    """Return the requested datetime. Do not snap to any fixed cadence."""
    try:
        dt = datetime.fromisoformat(scheduled_for)
    except Exception:
        return None, pause_on_wall(
            "datetime_picker_stuck",
            surface="tiktok",
            detail=f"Unparseable scheduled_for={scheduled_for!r}",
        )
    fail = check_tiktok_slot(dt)
    if fail:
        return None, fail
    return scheduled_for, None


def user_today(now: Optional[datetime] = None) -> str:
    zone = load_user_profile().zoneinfo()
    stamp = now or datetime.now(zone)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=zone)
    return stamp.astimezone(zone).strftime("%Y-%m-%d")


def local_today(now: Optional[datetime] = None) -> str:
    """Day stamp in the user timezone (UTC unless profile/env sets one)."""
    return user_today(now)


def count_daily_uses(
    posted_dir: Path,
    surface: str,
    *,
    now: Optional[datetime] = None,
    extra_receipts: Optional[list[dict]] = None,
) -> int:
    """Count scheduled/success receipts for `surface` on today's date (your timezone)."""
    day = user_today(now)
    n = 0
    paths: list[Path] = []
    if posted_dir.exists():
        paths.extend(posted_dir.glob("**/*.posted.json"))
    for path in paths:
        try:
            import json
            data = json.loads(path.read_text())
        except Exception:
            continue
        n += _receipt_counts_today(data, surface, day)
    for data in extra_receipts or []:
        n += _receipt_counts_today(data, surface, day)
    return n


def _receipt_counts_today(data: dict, surface: str, day: str) -> int:
    block = (data.get("platforms") or {}).get(surface) or {}
    status = block.get("status")
    if status not in ("scheduled", "success"):
        return 0
    for key in ("at", "posted_at", "scheduled_for"):
        raw = block.get(key) or ""
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=load_user_profile().zoneinfo())
        if dt.astimezone(load_user_profile().zoneinfo()).strftime("%Y-%m-%d") == day:
            return 1
    # Fallback: receipt sitting in posted/<today>/
    return 0


def daily_remaining_map(posted_dir: Path, *, now: Optional[datetime] = None) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for surface in DAILY_CAP_SURFACES:
        used = count_daily_uses(posted_dir, surface, now=now)
        out[surface] = remaining_slots(used, surface=surface)
    return out


def failure_receipt(fail: LockFailure, **extra: Any) -> dict:
    receipt = fail.as_dict()
    receipt["at"] = datetime.now(load_user_profile().zoneinfo()).isoformat()
    receipt.update(extra)
    return receipt


def native_list_proof_or_fail(
    surface: str,
    *,
    native_list_has_item: bool,
    composer_toast_seen: bool = False,
    proof_url: Optional[str] = None,
) -> Optional[LockFailure]:
    return check_proof(
        surface,
        native_list_has_item=native_list_has_item,
        composer_toast_seen=composer_toast_seen,
        proof_url=proof_url,
    )


def configured_handle_from_env() -> Optional[str]:
    handle = load_user_profile().configured_handle()
    return handle or None


def live_surface_gate(
    page, surface: str, *, caption: str = "", stage: str = "landing"
) -> Optional[dict]:
    """Run first-post-per-surface preflight + CTA + walls. None = continue."""
    profile = load_user_profile()
    host = profile.allowed_cta_host() or None
    wall = scan_page_for_wall(_page_text(page), surface=surface)
    if wall:
        return failure_receipt(wall)
    fails = run_first_post_preflight(
        page, surface, configured_handle=configured_handle_from_env(), stage=stage
    )
    if fails:
        return failure_receipt(fails[0])
    cta = caption_or_wall(caption, surface=surface, allowed_host=host)
    if cta:
        return failure_receipt(cta)
    return None
