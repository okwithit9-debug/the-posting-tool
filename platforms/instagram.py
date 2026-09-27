"""
platforms/instagram.py — Instagram Reels poster (Playwright, instagram.com).

Goes through the native instagram.com web composer. Native Schedule only.
Missing Schedule (Personal account, or hidden after Professional convert)
is a wall — never Share / Post now.

Why instagram.com and not Meta Business Suite (2026-04-26 evening):
  Spent 9 rounds trying to drive Meta Business Suite (business.facebook.com).
  Caption typing into BS's Draft.js editor is actively guarded against
  automated input — every typing strategy triggers BS's left navigation
  sidebar to expand and the read-back returns 0 chars. Meta is winning that
  fight.

  The instagram.com web composer has a simpler contenteditable that just
  works with page.keyboard.type. Posts always go up immediately. We accept
  no IG scheduling for now; user manually schedules IG via the mobile app
  if needed. The receipt sets schedule_unsupported_on_account=True when a
  scheduled_for is requested but can't be set.

Login state lives in ~/.posting_tool_chrome (the Posting Tool's dedicated
profile, populated by bs_login.command / setup_playwright_logins.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session, clear_and_type
from platforms._preflight import (
    failure_receipt,
    gate_commit_click,
    live_surface_gate,
    native_list_proof_or_fail,
)
from platforms._safety_locks import pause_on_wall

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    path = LOG_DIR / f"instagram_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def _ig_modal_open(page) -> bool:
    """Did Instagram's create modal actually appear?

    NOTE: this is upload-modal-shaped, but the same selectors match other
    IG dialog modals (notifications nag, save-login nag, etc.) — caller
    must ensure those are dismissed first or this returns true while the
    actual upload modal is not on screen.
    """
    candidates = [
        'div[role="dialog"]',
        'input[type="file"]',
        'button:has-text("Select from computer")',
        'div:has-text("Create new post")',
    ]
    for sel in candidates:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=2500)
            return True
        except Exception:
            continue
    return False


# Buttons that dismiss IG's pre-composer nag modals without opting in.
# Order matters: most-specific first, generic last.
_IG_NAG_DISMISS_BUTTONS = (
    "Not Now",       # Turn on Notifications, Save Your Login Info
    "Not now",       # case-variant
    "Cancel",        # cookies / consent flows
    "Decline",       # consent
    "Allow essential cookies only",  # EU consent
    "Reject all",    # consent
    "Close",         # generic dialog close button
)


def _dismiss_ig_nag_modals(page, basename: str, *, max_passes: int = 3) -> int:
    """Click 'Not Now' / 'Cancel' / etc. on IG's pre-composer nag modals.

    Returns the count of dismissed modals. Multiple may stack (e.g. cookies
    then notifications) — pass count caps the loop so a stuck-open modal
    can't infinite-loop.

    Per-pass: try each known dismiss-button label in order. First visible
    match gets clicked. Wait briefly, then re-check. If a pass dismisses
    nothing, exit.
    """
    dismissed = 0
    for pass_num in range(1, max_passes + 1):
        clicked_this_pass = False
        for label in _IG_NAG_DISMISS_BUTTONS:
            try:
                btn = page.get_by_role("button", name=label, exact=True).first
                btn.wait_for(state="visible", timeout=1500)
            except Exception:
                continue
            try:
                btn.click(timeout=1500)
                dismissed += 1
                clicked_this_pass = True
                _shot(page, basename, f"00_dismissed_nag_{label.replace(' ', '_').lower()}_p{pass_num}")
                page.wait_for_timeout(700)
                break  # re-scan from top in case dismiss cascaded another modal
            except Exception:
                continue
        if not clicked_this_pass:
            break
    return dismissed


def _ig_open_and_fill_schedule(page, basename: str, scheduled_for: str) -> bool:
    """Open Advanced settings → Schedule content and fill datetime. Never Share."""
    from datetime import datetime as _dt

    try:
        dt = _dt.fromisoformat(scheduled_for)
    except Exception:
        return False
    for getter in (
        lambda: page.get_by_text("Advanced settings", exact=False).first,
        lambda: page.get_by_text("Advanced Settings", exact=True).first,
    ):
        try:
            el = getter()
            if el.is_visible(timeout=1500):
                el.click()
                page.wait_for_timeout(400)
                break
        except Exception:
            continue
    toggle = None
    for getter in (
        lambda: page.get_by_text("Schedule content", exact=False).first,
        lambda: page.get_by_text("Schedule this post", exact=True).first,
    ):
        try:
            el = getter()
            el.wait_for(state="visible", timeout=2500)
            toggle = el
            break
        except Exception:
            continue
    if toggle is None:
        return False
    try:
        toggle.click()
        page.wait_for_timeout(500)
    except Exception:
        return False
    date_val = dt.strftime("%Y-%m-%d")
    time_val = dt.strftime("%H:%M")
    filled = False
    try:
        date_input = page.locator('input[type="date"]').first
        if date_input.is_visible(timeout=1500):
            date_input.fill(date_val)
            filled = True
    except Exception:
        pass
    try:
        time_input = page.locator('input[type="time"]').first
        if time_input.is_visible(timeout=1500):
            time_input.fill(time_val)
            filled = True
    except Exception:
        pass
    _shot(page, basename, "schedule_fields_set" if filled else "schedule_fields_missing")
    return True


def _ig_toast_seen(page) -> bool:
    try:
        return page.locator('div[role="status"], div[role="alert"]').first.is_visible(timeout=400)
    except Exception:
        return False


def _ig_native_scheduled_list_has_caption(page, caption: str) -> bool:
    """Proof = native scheduled list, not the composer toast or Share spinner."""
    snippet = (caption or "").strip()[:40]
    for getter in (
        lambda: page.get_by_text("Scheduled", exact=False).first,
        lambda: page.get_by_role("link", name="Scheduled content").first,
        lambda: page.locator('a[href*="scheduled"]').first,
    ):
        try:
            el = getter()
            if el.is_visible(timeout=1500):
                el.click()
                page.wait_for_timeout(800)
                break
        except Exception:
            continue
    if snippet:
        try:
            if page.get_by_text(snippet, exact=False).first.is_visible(timeout=4000):
                return True
        except Exception:
            pass
    try:
        body = page.inner_text("body") or ""
        if snippet and snippet in body:
            return True
        if "scheduled" in body.lower() and "share" not in (page.url or "").lower():
            # List opened but caption not found — not proof.
            return False
    except Exception:
        return False
    return False


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    caption = (captions or {}).get("caption", "")
    if not caption:
        return {"status": "failed", "error": "instagram caption block missing 'caption'", "at": _now_iso()}
    if len(caption) > 2200:
        caption = caption[:2197] + "..."

    basename = video.stem

    try:
        with chrome_session() as page:
            # wait_until="domcontentloaded" (not "networkidle"). Instagram keeps
            # background sockets/analytics firing indefinitely, so networkidle
            # never settles and goto times out at 30s — observed 2026-05-27 +
            # 2026-05-28, every IG post in the dispatch failed here with
            # "TimeoutError: Page.goto: ... waiting until 'networkidle'".
            # DOMContentLoaded is sufficient because (a) URL is set at navigation
            # commit so the /accounts/login redirect check below still works,
            # and (b) the real readiness gate is the Home-icon wait_for() right
            # after.
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=30_000)

            if "/accounts/login" in page.url:
                return {"status": "failed",
                        "error": "not logged into Instagram — run setup_playwright_logins.py",
                        "at": _now_iso()}

            # Wait for the sidebar to render. Home icon is the most reliable proof.
            try:
                page.locator('svg[aria-label="Home"]').first.wait_for(
                    state="visible", timeout=15_000
                )
            except Exception:
                page.wait_for_timeout(2000)
            page.wait_for_timeout(2000)

            # Dismiss any "Turn on Notifications" / "Save your login info" /
            # cookie-consent nag modals BEFORE trying to compose. These modals
            # intercept clicks on the + sidebar and on the Post popover. When
            # they're present, the composer flow looks like it's working
            # (popover_clicked falls through to keyboard 'c', _ig_modal_open
            # returns true because the NAG modal is open) but the file input
            # is never reachable. Verified 2026-05-01: 7/7 IG posts failed at
            # 06_file_input_missing because the screenshot at 05_modal_open
            # showed the notifications nag, not the upload composer.
            _dismiss_ig_nag_modals(page, basename)

            gate = live_surface_gate(page, "instagram", caption=caption, stage="landing")
            if gate:
                _shot(page, basename, "preflight_failed")
                return gate

            # Step 1: Click "+" in left sidebar. Target the interactive parent
            # (<a> / <div role="link">), NOT the SVG icon.
            _shot(page, basename, "01_before_plus")
            plus_clicked = False
            attempts = [
                ("role-link-New post",   lambda: page.get_by_role("link", name="New post").first),
                ("role-button-New post", lambda: page.get_by_role("button", name="New post").first),
                ("role-link-Create",     lambda: page.get_by_role("link", name="Create").first),
                ("role-button-Create",   lambda: page.get_by_role("button", name="Create").first),
                ("svg-parent-a",         lambda: page.locator('a:has(svg[aria-label="New post"])').first),
                ("svg-parent-roleLink",  lambda: page.locator('div[role="link"]:has(svg[aria-label="New post"])').first),
                ("svg-parent-button",    lambda: page.locator('button:has(svg[aria-label="New post"])').first),
                ("svg-create-parent",    lambda: page.locator('a:has(svg[aria-label="Create"])').first),
                ("href-create",          lambda: page.locator('a[href$="/create/"]').first),
            ]
            log_label = "02_no_plus_click"
            for label, get_loc in attempts:
                try:
                    loc = get_loc()
                    loc.wait_for(state="visible", timeout=3000)
                    loc.scroll_into_view_if_needed()
                    loc.click()
                    log_label = f"02_clicked_{label}"
                    plus_clicked = True
                    break
                except Exception:
                    continue
            if not plus_clicked:
                page.keyboard.press("c")
                log_label = "02_keyboard_c"
                plus_clicked = True
            page.wait_for_timeout(1500)
            _shot(page, basename, log_label)

            # Step 2: Popover with Post / Reel / Story / Live. Click Post.
            popover_clicked = False
            for label, action in [
                ("menuitem-Post", lambda: page.get_by_role("menuitem", name="Post").first),
                ("button-Post",   lambda: page.get_by_role("button", name="Post", exact=True).first),
                ("link-Post",     lambda: page.get_by_role("link", name="Post", exact=True).first),
                ("text-Post",     lambda: page.get_by_text("Post", exact=True).first),
                ("menuitem-Reel", lambda: page.get_by_role("menuitem", name="Reel").first),
                ("button-Reel",   lambda: page.get_by_role("button", name="Reel", exact=True).first),
                ("text-Reel",     lambda: page.get_by_text("Reel", exact=True).first),
            ]:
                try:
                    el = action()
                    el.wait_for(state="visible", timeout=2500)
                    el.click()
                    popover_clicked = True
                    _shot(page, basename, f"03_popover_{label}")
                    break
                except Exception:
                    continue
            if not popover_clicked:
                _shot(page, basename, "03_no_popover")
            page.wait_for_timeout(2000)

            if not _ig_modal_open(page):
                _shot(page, basename, "04_modal_didnt_open")
                return {"status": "failed",
                        "error": "Instagram upload modal did not open after + → Post",
                        "at": _now_iso()}

            # Type-picker fallback click (if modal shows Post/Reel/Story type chooser).
            try:
                page.get_by_role("button", name="Post", exact=True).first.click(timeout=2000)
                page.wait_for_timeout(800)
            except Exception:
                pass
            _shot(page, basename, "05_modal_open")

            # Step 3: Upload video. File input is hidden but in DOM.
            file_input_handle = None
            try:
                file_input_handle = page.evaluate_handle(
                    """() => {
                        const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                        if (!inputs.length) return null;
                        const videoInput = inputs.find(i =>
                            (i.accept || '').toLowerCase().includes('video') ||
                            (i.accept || '').toLowerCase().includes('mp4'));
                        return videoInput || inputs[0];
                    }"""
                )
                if not file_input_handle.as_element():
                    file_input_handle = None
            except Exception:
                file_input_handle = None

            if file_input_handle is None:
                try:
                    fi = page.locator('input[type="file"]').first
                    fi.wait_for(state="attached", timeout=15_000)
                    fi.set_input_files(str(video))
                except Exception as e:
                    _shot(page, basename, "06_file_input_missing")
                    return {"status": "failed",
                            "error": f"Instagram file input not found: {e}",
                            "at": _now_iso()}
            else:
                file_input_handle.as_element().set_input_files(str(video))
            _shot(page, basename, "06_file_set")

            # Wait for upload + processing.
            page.wait_for_timeout(8000)

            # Step 4: Click Next through 1-3 wizard steps (crop / filter / etc).
            for _ in range(3):
                try:
                    next_btn = page.get_by_role("button", name="Next").first
                    next_btn.wait_for(state="visible", timeout=5000)
                    next_btn.click()
                    page.wait_for_timeout(1500)
                except Exception:
                    break
            _shot(page, basename, "07_after_next")

            # Step 5: Wipe composer, then type caption (one URL CTA).
            cap_box = page.locator('div[contenteditable="true"]').first
            cap_box.wait_for(state="visible", timeout=15_000)
            clear_and_type(page, cap_box, caption, delay=10)
            _shot(page, basename, "08_caption_typed")

            if not scheduled_for:
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="instagram",
                        detail="Native Schedule only. Never Share / Post now.",
                    )
                )

            composer_gate = live_surface_gate(
                page, "instagram", caption=caption, stage="composer"
            )
            if composer_gate:
                _shot(page, basename, "composer_preflight_failed")
                return composer_gate

            scheduled_ok = _ig_open_and_fill_schedule(page, basename, scheduled_for)
            if not scheduled_ok:
                _shot(page, basename, "schedule_ui_missing")
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="instagram",
                        detail=(
                            "Schedule content is not on this composer (Personal, "
                            "or hidden after Professional convert). Do not Share."
                        ),
                    )
                )

            share_forbidden = gate_commit_click("instagram", "Share")
            if share_forbidden:
                # Encoded COMMIT is Schedule content, never Share.
                pass
            try:
                action = page.get_by_role("button", name="Schedule", exact=True).first
                action.wait_for(state="visible", timeout=10_000)
                action.click()
            except Exception:
                try:
                    action = page.get_by_text("Schedule content", exact=False).last
                    action.click(timeout=5_000)
                except Exception:
                    _shot(page, basename, "09_schedule_btn_missing")
                    return failure_receipt(
                        pause_on_wall(
                            "missing_schedule_control",
                            surface="instagram",
                            detail="Schedule commit button missing. Do not Share.",
                        )
                    )

            page.wait_for_timeout(2000)
            native_ok = _ig_native_scheduled_list_has_caption(page, caption)
            proof_fail = native_list_proof_or_fail(
                "instagram",
                native_list_has_item=native_ok,
                composer_toast_seen=_ig_toast_seen(page),
            )
            if proof_fail:
                _shot(page, basename, "native_list_proof_missing")
                return failure_receipt(proof_fail)

            return {
                "status": "scheduled",
                "url": None,
                "scheduled_for": scheduled_for,
                "composer": "instagram.com_native",
                "proof": "native_scheduled_list",
                "at": _now_iso(),
            }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
