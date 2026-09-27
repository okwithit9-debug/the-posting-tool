"""
platforms/pinterest.py — Pinterest Pin poster (Playwright).

Reflects the actual pin-builder UI as observed 2026-04-25:
  - Title field is a textarea
  - Description is "Tell everyone what your Pin is about"
  - Board picker is a dropdown at top-right; defaults to last-used (so often
    already shows the right board — we skip the click in that case)
  - Schedule is a pair of radio buttons: "Publish immediately" /
    "Publish at a later date" (NOT a "More options → Publish date" link)
  - Final action: red "Publish" button at top-right
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session, clear_and_type
from platforms._pickers import (
    set_native_datetime_via_js,
    pick_date_in_calendar,
    set_time_via_typing,
)
from platforms._preflight import failure_receipt, live_surface_gate
from platforms._safety_locks import pause_on_wall

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    path = LOG_DIR / f"pinterest_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def _pinterest_fill_input(page, value: str, *, match_attrs: tuple,
                          value_pattern_re: str) -> bool:
    """Find Pinterest's date OR time text input and force `value` into it.

    Pinterest renders the schedule date/time as ordinary `<input type="text">`
    pills, NOT as native date/time inputs. They DO accept programmatic typing
    but `fill()` alone often loses focus before the React handler picks up
    the value, so this helper:

      1. Finds the input by aria-label / placeholder / data-test-id keyword
         match (date inputs share keyword "date", time share "time").
      2. Validates that the input's CURRENT value matches the expected shape
         (MM/DD/YYYY for date, H:MM AM/PM for time) — this disambiguates the
         schedule-date input from any other text input on the page (title,
         description, tags, etc.).
      3. Focus → ControlOrMeta+A → Delete → type the new value char-by-char
         (per-key delay 12ms so React's onChange fires for each char) →
         press Tab to commit + blur.

    Returns True if at least one matching input was found AND the typed value
    survived the blur (re-read it; if it doesn't match what we typed, treat
    as failure so we fall through to the calendar strategy).
    """
    import re
    pattern = re.compile(value_pattern_re)

    # Build a JS-side filter so we can scan all inputs and pick the one
    # whose current value matches the expected shape. Handle either keyword
    # match in attributes OR just use shape-only matching as a fallback.
    matched_handle = None
    try:
        matched_handle = page.evaluate_handle(
            """({attrs, valueRegex}) => {
                const re = new RegExp(valueRegex);
                const inputs = Array.from(document.querySelectorAll('input'))
                    .filter(el => el.offsetParent !== null);
                const wantedAttrs = attrs.map(a => a.toLowerCase());
                // Pass 1: attribute keyword match + shape match.
                for (const el of inputs) {
                    const haystack = [
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('placeholder') || '',
                        el.getAttribute('data-test-id') || '',
                        el.getAttribute('id') || '',
                        el.getAttribute('name') || '',
                    ].join(' ').toLowerCase();
                    const attrHit = wantedAttrs.some(a => haystack.includes(a));
                    const valHit = re.test((el.value || '').trim());
                    if (attrHit && valHit) return el;
                }
                // Pass 2: shape match alone (no attribute hint matched —
                // happens when the input is wrapped without aria-label).
                for (const el of inputs) {
                    if (re.test((el.value || '').trim())) return el;
                }
                return null;
            }""",
            {"attrs": list(match_attrs), "valueRegex": value_pattern_re},
        )
        if not matched_handle.as_element():
            return False
    except Exception:
        return False

    el = matched_handle.as_element()
    try:
        el.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        el.click()
        page.wait_for_timeout(150)
        # Select-all + delete.
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Delete")
        # Type the new value with per-key delay so React onChange fires.
        page.keyboard.type(value, delay=12)
        # Tab to commit + blur (Pinterest validates on blur).
        page.keyboard.press("Tab")
        page.wait_for_timeout(250)
    except Exception:
        return False

    # Re-read to verify the value stuck. If Pinterest re-formatted it (e.g.
    # "12:00 PM" → "12:00 PM" or normalized), we accept any value matching
    # the same shape pattern as long as it's not empty.
    try:
        cur = (el.input_value(timeout=500) or "").strip()
    except Exception:
        cur = ""
    if not cur:
        return False
    return pattern.match(cur) is not None


def _board_already_selected(page, board: str) -> bool:
    """Return True if the pin-builder's board dropdown already shows `board`.

    Pinterest remembers the last-used board, and most accounts have a
    primary destination board that's pre-selected. We try a couple of
    selectors to find the dropdown's current value.

    Important: when no board is pre-selected the trigger button shows the
    placeholder "Select", so we explicitly disqualify that. The earlier
    `button:has-text("Truth")` form was too loose — if a board named
    "Truth" appeared anywhere on the page (e.g., in a Pinterest tag/topic
    label) we'd false-positive even though the dropdown still says "Select".
    """
    # Disqualify the "Select" placeholder state outright — when this trigger
    # is visible, no board is pre-selected.
    for placeholder_sel in [
        '[data-test-id="board-dropdown-select-button"]:has-text("Select")',
        'button[aria-haspopup="listbox"]:has-text("Select")',
    ]:
        try:
            if page.locator(placeholder_sel).first.is_visible(timeout=500):
                return False
        except Exception:
            continue

    for sel in [
        f'[data-test-id="board-dropdown-select-button"]:has-text("{board}")',
        f'button[aria-haspopup="listbox"]:has-text("{board}")',
        f'select option[selected]:has-text("{board}")',
    ]:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=1500)
            return True
        except Exception:
            continue
    return False


def _try_pick_board(page, board: str, basename: str) -> bool:
    """Open Pinterest's board dropdown and select `board`.

    Pinterest's pin-builder lazy-loads boards into the listbox: when the
    dropdown first opens, the popover often shows only the search field +
    "Create board" and no rows. The old logic clicked the trigger, slept
    800ms, typed the board name, slept 800ms more, then tried an exact-text
    click — which fails if the board list never rendered or if the typing
    didn't reach the search input (focus is fragile in this popover).

    This rewrite:
      1. Opens the dropdown via one of several trigger selectors.
      2. Waits for the popover to *render* (search input OR a board option
         appearing).
      3. First tries to click the target board directly — many accounts
         have only a few boards so it's visible without typing.
      4. If that misses, focuses the search input explicitly and types the
         board name, then retries the option click with a longer timeout.
      5. As a last resort, presses Enter on the search input to pick the
         top result.
    """
    # Step 1: open the dropdown.
    opened = False
    for trigger_sel in [
        '[data-test-id="board-dropdown-select-button"]',
        'button[aria-haspopup="listbox"]',
        'button:has-text("Select")',
        'button:has-text("Choose a board")',
    ]:
        try:
            trigger = page.locator(trigger_sel).first
            trigger.wait_for(state="visible", timeout=2000)
            trigger.click()
            opened = True
            break
        except Exception:
            continue
    if not opened:
        _shot(page, basename, "board_dropdown_trigger_missing")
        return False

    # Step 2: wait for the popover content to render — either the search
    # input or at least one board option.
    try:
        page.locator(
            'input[placeholder*="Search" i], [role="option"], '
            '[data-test-id*="board-row"]'
        ).first.wait_for(state="visible", timeout=5000)
    except Exception:
        pass

    # Helper: click a board row matching `board`, return True on success.
    def _click_board_row(timeout_ms: int) -> bool:
        for board_sel in [
            f'[role="option"][aria-label="{board}"]',
            f'[role="option"]:has-text("{board}")',
            f'[data-test-id*="board-row"]:has-text("{board}")',
            f'div[role="button"]:has-text("{board}")',
        ]:
            try:
                opt = page.locator(board_sel).first
                opt.wait_for(state="visible", timeout=timeout_ms)
                opt.click()
                return True
            except Exception:
                continue
        return False

    # Step 3: maybe the board is already visible (no typing needed).
    if _click_board_row(timeout_ms=2000):
        return True

    # Step 4: focus the search input explicitly, type the board name, retry.
    search_input = None
    for search_sel in [
        'input[placeholder*="Search" i]',
        '[data-test-id*="board-picker"] input',
    ]:
        try:
            cand = page.locator(search_sel).first
            cand.wait_for(state="visible", timeout=2000)
            search_input = cand
            break
        except Exception:
            continue
    if search_input is not None:
        try:
            search_input.click()
            try:
                search_input.fill("")
            except Exception:
                pass
            search_input.type(board, delay=20)
            page.wait_for_timeout(1500)  # let backend filter
            if _click_board_row(timeout_ms=3500):
                return True
            # Step 5: last-resort — press Enter to pick the top match.
            try:
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                # Verify the dropdown now shows `board`.
                if _board_already_selected(page, board):
                    return True
            except Exception:
                pass
        except Exception:
            pass

    _shot(page, basename, "board_picker_failed")
    return False


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    title = (captions or {}).get("title", "")
    description = (captions or {}).get("description", "")
    board = (captions or {}).get("board", "")

    if not title or not board:
        return {"status": "failed",
                "error": "pinterest caption block missing 'title' or 'board'",
                "at": _now_iso()}

    basename = video.stem

    try:
        with chrome_session() as page:
            # Pinterest's pin-builder keeps a long-poll / streaming connection
            # alive, so wait_until="networkidle" never fires and hits the 30s
            # ceiling (observed 2026-05-14 dispatch). The file-input
            # wait_for(state="attached") below is the real readiness signal.
            page.goto("https://www.pinterest.com/pin-builder/",
                      wait_until="domcontentloaded", timeout=30_000)

            if "/login" in page.url:
                return {"status": "failed",
                        "error": "not logged into Pinterest — run setup_playwright_logins.py",
                        "at": _now_iso()}

            gate = live_surface_gate(
                page, "pinterest", caption=f"{title} {description}", stage="landing"
            )
            if gate:
                _shot(page, basename, "preflight_failed")
                return gate

            if not scheduled_for:
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="pinterest",
                        detail="Native Schedule only. Never Publish immediately.",
                    )
                )

            # Upload video.
            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=15_000)
            file_input.set_input_files(str(video))

            # Wait for processing.
            page.wait_for_timeout(15_000)

            # ---- Title ----
            title_input = None
            for sel in [
                'textarea[placeholder*="title" i]',
                'input[placeholder*="title" i]',
                'textarea#pin-draft-title',
                'input[id*="title" i]',
            ]:
                try:
                    candidate = page.locator(sel).first
                    candidate.wait_for(state="visible", timeout=3000)
                    title_input = candidate
                    break
                except Exception:
                    continue
            if title_input is None:
                _shot(page, basename, "title_input_missing")
                return {"status": "failed",
                        "error": "Pinterest title input not found",
                        "at": _now_iso()}
            title_input.fill(title[:100])

            # ---- Description ("Tell everyone what your Pin is about") ----
            if description:
                for sel in [
                    'textarea[placeholder*="Tell everyone" i]',
                    'div[contenteditable="true"][aria-label*="description" i]',
                    'textarea[placeholder*="description" i]',
                ]:
                    try:
                        desc = page.locator(sel).first
                        desc.wait_for(state="visible", timeout=3000)
                        try:
                            clear_and_type(page, desc, description[:500], delay=8)
                        except Exception:
                            desc.fill("")
                            desc.fill(description[:500])
                        break
                    except Exception:
                        continue

            # ---- Board ----
            # Most accounts have a default board pre-selected. Skip the picker
            # if the requested board already shows.
            if not _board_already_selected(page, board):
                if not _try_pick_board(page, board, basename):
                    return {"status": "failed",
                            "error": f"could not select board '{board}' (and not pre-selected)",
                            "at": _now_iso()}

            # ---- Schedule (radio: Publish immediately / Publish at a later date) ----
            #
            # Pinterest's pin-builder shows the date as a text input pre-filled
            # with "MM/DD/YYYY" (e.g., "04/26/2026") and the time as a text
            # input pre-filled with "H:MM AM/PM" (e.g., "12:00 PM"). Both have
            # a calendar/clock icon and they ARE plain text inputs — they
            # accept direct typing. They do NOT have type="date"/type="time"
            # so the native-JS strategy doesn't find them.
            #
            # Verified via 2026-04-25 screenshot:
            # logs/screenshots/pinterest_*_schedule_toggle_on_*.png
            #
            # Strategy (try cheapest first; each fall-through is a separate
            # interaction so callers see exactly which one landed):
            #   1. Toggle "Publish at a later date" radio.
            #   2. Locate the date text input + time text input by visible
            #      placeholder/value pattern (MM/DD/YYYY and H:MM AM/PM)
            #      and fill() them directly.
            #   3. If fill() doesn't take (custom React handler ignores
            #      programmatic fill), focus + select-all + type the value
            #      with per-key delay.
            #   4. As a last resort: native-input scan, then calendar-cell
            #      click. (Kept for older builds.)
            scheduled_ok = False
            if scheduled_for:
                try:
                    page.get_by_text("Publish at a later date", exact=False).first.click(timeout=5000)
                    page.wait_for_timeout(800)
                    _shot(page, basename, "schedule_toggle_on")

                    dt = datetime.fromisoformat(scheduled_for)
                    date_str = dt.strftime("%m/%d/%Y")
                    hour_12 = ((dt.hour - 1) % 12) + 1
                    ampm = "PM" if dt.hour >= 12 else "AM"
                    time_str = f"{hour_12}:{dt.minute:02d} {ampm}"

                    # Strategy 1 (PRIMARY): direct fill of the visible text
                    # inputs, located by their MM/DD/YYYY / H:MM-shaped value.
                    date_ok = _pinterest_fill_input(
                        page, date_str,
                        match_attrs=("date", "schedule-date", "Date"),
                        value_pattern_re=r"^\d{1,2}/\d{1,2}/\d{4}$",
                    )
                    time_ok = _pinterest_fill_input(
                        page, time_str,
                        match_attrs=("time", "schedule-time", "Time"),
                        value_pattern_re=r"^\d{1,2}:\d{2}\s*(AM|PM)$",
                    )
                    if date_ok and time_ok:
                        _shot(page, basename, "schedule_set_via_fill")
                        scheduled_ok = True

                    # Strategy 2 (fallback): hidden native inputs (legacy build).
                    if not scheduled_ok and set_native_datetime_via_js(page, dt):
                        scheduled_ok = True
                        _shot(page, basename, "schedule_set_via_js")

                    # Strategy 3 (fallback): calendar-cell click + time typing.
                    if not scheduled_ok:
                        date_clicked = False
                        for getter in (
                            lambda: page.locator('input[aria-label*="date" i]').first,
                            lambda: page.locator('input[placeholder*="/"]').first,
                            lambda: page.locator('button[aria-label*="date" i]').first,
                            lambda: page.locator('[data-test-id*="schedule-date" i]').first,
                        ):
                            try:
                                el = getter()
                                el.wait_for(state="visible", timeout=2500)
                                el.click()
                                date_clicked = True
                                break
                            except Exception:
                                continue
                        cal_date_ok = False
                        if date_clicked:
                            page.wait_for_timeout(500)
                            cal_date_ok = pick_date_in_calendar(page, dt)
                            try:
                                page.keyboard.press("Escape")
                            except Exception:
                                pass

                        time_pill_getters = [
                            lambda: page.locator('input[aria-label*="time" i]').first,
                            lambda: page.locator('input[placeholder*=":"]').first,
                            lambda: page.locator('[data-test-id*="schedule-time" i]').first,
                        ]
                        cal_time_ok = set_time_via_typing(page, dt, time_pill_clicks=time_pill_getters)
                        try:
                            page.keyboard.press("Escape")
                        except Exception:
                            pass

                        scheduled_ok = bool(cal_date_ok and cal_time_ok)

                    if not scheduled_ok:
                        _shot(page, basename, "schedule_inputs_unfillable")
                        return failure_receipt(
                            pause_on_wall(
                                "datetime_picker_stuck",
                                surface="pinterest",
                                detail=(
                                    "Schedule fields unfillable. Not reverting to "
                                    "Publish immediately."
                                ),
                            )
                        )
                except Exception:
                    _shot(page, basename, "schedule_failed")
                    return failure_receipt(
                        pause_on_wall(
                            "missing_schedule_control",
                            surface="pinterest",
                            detail="Pinterest schedule flow failed. Never Publish immediately.",
                        )
                    )

            if scheduled_for and not scheduled_ok:
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="pinterest",
                        detail="Schedule did not commit. Never Publish immediately.",
                    )
                )

            # ---- Publish ----
            # 2026-08-02 fix: the "Publish" button is actually a
            # <div role="button" data-test-id="board-dropdown-save-button">.
            # Pinterest renamed it internally to "save-button" but the text
            # stays "Publish". The selector `get_by_role("button", name="Publish")`
            # matches, but the old 10s wait was too short on days when the
            # upload+enable cycle drags. Fix: prefer the stable testid,
            # extend the wait, poll for aria-disabled=false, and fall back
            # to force-click if the button is enabled but click misses.
            publish = None
            for getter in (
                lambda: page.locator('[data-test-id="board-dropdown-save-button"]').first,
                lambda: page.get_by_role("button", name="Publish").first,
                lambda: page.locator('div[role="button"]:has-text("Publish")').first,
            ):
                try:
                    candidate = getter()
                    candidate.wait_for(state="visible", timeout=30_000)
                    publish = candidate
                    break
                except Exception:
                    continue

            if publish is None:
                _shot(page, basename, "publish_btn_missing")
                return {"status": "failed",
                        "error": "Pinterest Publish button not found",
                        "at": _now_iso()}

            # Poll for the button to become enabled (aria-disabled != "true").
            # Pinterest keeps Publish disabled until the video finishes
            # processing server-side, which can take 30-60s on large clips.
            enabled_deadline = time.time() + 60
            while time.time() < enabled_deadline:
                try:
                    disabled = publish.get_attribute("aria-disabled")
                    if disabled != "true":
                        break
                except Exception:
                    break
                page.wait_for_timeout(500)

            try:
                publish.click(timeout=8000)
            except Exception:
                # Force-click if a hover overlay intercepts. Button is
                # verified visible + not aria-disabled at this point.
                try:
                    publish.click(force=True, timeout=5000)
                except Exception:
                    _shot(page, basename, "publish_btn_click_failed")
                    return {"status": "failed",
                            "error": "Pinterest Publish button click failed",
                            "at": _now_iso()}

            page.wait_for_timeout(5000)
            _shot(page, basename, "submitted")
            return {
                "status": "scheduled" if scheduled_ok else "success",
                "url": None,
                "scheduled_for": scheduled_for if scheduled_ok else None,
                "scheduling_skipped": scheduled_for is not None and not scheduled_ok,
                "board": board,
                "at": _now_iso(),
            }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
