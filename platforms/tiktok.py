"""
platforms/tiktok.py — TikTok video poster (Playwright).

TikTok is the trickiest of the platforms:
  - Aggressive bot detection (we use playwright-stealth via _chrome.py)
  - Native scheduler caps at 10 days ahead, ≥15 min minimum
  - Hashtag autocompletion is finicky — we type one tag at a time

Handle comes from profile.json / POSTING_TOOL_HANDLE. No shipped account.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session
from platforms._pickers import (
    set_native_datetime_via_js,
    pick_date_in_calendar,
    set_time_via_typing,
)
from platforms._preflight import (
    failure_receipt,
    gate_commit_click,
    live_surface_gate,
    native_list_proof_or_fail,
    tiktok_scheduled_for_or_fail,
)
from platforms._safety_locks import pause_on_wall


def _tiktok_round_up_to_increment(dt: datetime, increment_min: int = 15) -> datetime:
    """TikTok's time picker offers slots in N-minute increments (often 15).
    Round dt UP so we never schedule earlier than planned.
    """
    cleaned = dt.replace(second=0, microsecond=0)
    rem = cleaned.minute % increment_min
    if rem == 0:
        return cleaned
    return cleaned + timedelta(minutes=(increment_min - rem))


def _tiktok_pick_date(page, dt: datetime, basename: str) -> bool:
    """Set TikTok's date field for the new readonly-input UI (2026-04-26+).

    TikTok Studio replaced the old "📅 YYYY-MM-DD ▼" text-bearing dropdown
    with a `<input type="text" readonly value="YYYY-MM-DD">` inside a
    `TUXFormField` wrapper. The value lives on `input.value`, NOT in
    `textContent` — which is why the earlier walker (`_tiktok_open_dropdown`)
    found nothing and silently fell through to TikTok's auto-default
    (~now+15min on whatever day was already showing).

    Strategy:
      1. Locate the date input by its value-shape regex inside the
         `.scheduled-picker` / `.scheduled-container` root.
      2. Try a React-aware direct `.value` set + input/change events. If
         TikTok's React handlers accept it, this is a one-shot win and no
         picker has to open.
      3. Otherwise, click the input, wait for the calendar popup, and click
         the cell with `aria-label="YYYY-MM-DD"` — TikTok's calendar cells
         use ISO aria-labels.
      4. Fall back to the shared `pick_date_in_calendar` helper, then to a
         day-number button click as the last resort.
    """
    target_iso = dt.strftime("%Y-%m-%d")

    # Strategy 1: React-aware direct set on the readonly input.
    try:
        ok = page.evaluate(
            """(iso) => {
                const root = document.querySelector('.scheduled-picker')
                          || document.querySelector('.scheduled-container');
                if (!root) return false;
                const inputs = Array.from(root.querySelectorAll('input.TUXTextInputCore-input'));
                const dateInput = inputs.find(i => /^\\d{4}-\\d{2}-\\d{2}$/.test(i.value || ''));
                if (!dateInput) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(dateInput, iso);
                dateInput.dispatchEvent(new Event('input', {bubbles: true}));
                dateInput.dispatchEvent(new Event('change', {bubbles: true}));
                dateInput.dispatchEvent(new Event('blur', {bubbles: true}));
                return true;
            }""",
            target_iso,
        )
        page.wait_for_timeout(400)
        # Verify — the input may snap back if React rejected the set.
        if ok:
            cur = _tiktok_read_input_value(page, "date")
            if cur == target_iso:
                return True
    except Exception:
        pass

    # Strategy 2: click the input → calendar popup → click ISO cell.
    clicked = False
    try:
        clicked = page.evaluate(
            """() => {
                const root = document.querySelector('.scheduled-picker')
                          || document.querySelector('.scheduled-container');
                if (!root) return false;
                const inputs = Array.from(root.querySelectorAll('input.TUXTextInputCore-input'));
                const dateInput = inputs.find(i => /^\\d{4}-\\d{2}-\\d{2}$/.test(i.value || ''));
                if (!dateInput) return false;
                // Click the wrapper too — some builds need wrapper-click to open.
                const wrap = dateInput.closest('.TUXTextInputCore') || dateInput;
                wrap.click();
                dateInput.click();
                return true;
            }"""
        )
    except Exception:
        clicked = False

    if not clicked:
        _shot(page, basename, "date_input_not_found")
        return False

    page.wait_for_timeout(700)
    _shot(page, basename, "date_calendar_open")

    # Dump the open calendar's DOM so future selector debugging has the
    # actual structure to work from. Saved next to the schedule_dom dump.
    try:
        cal_html = page.evaluate(
            """() => {
                // Look for the calendar popup near the date input.
                // TikTok's popups usually live in a portal at body level.
                const cands = Array.from(document.querySelectorAll(
                    '[class*="calendar" i], [class*="datepicker" i], [role="dialog"], [role="grid"]'
                )).filter(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 50 && r.height > 50;
                });
                if (!cands.length) return document.body.outerHTML.slice(0, 80000);
                // Pick the largest visible candidate.
                cands.sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return (br.width * br.height) - (ar.width * ar.height);
                });
                return cands[0].outerHTML.slice(0, 80000);
            }"""
        )
        dump_path = LOG_DIR / (
            f"tiktok_{basename}_calendar_dom_"
            f"{int(datetime.now().timestamp())}.html"
        )
        dump_path.write_text(cal_html)
    except Exception:
        pass

    # TikTok's calendar (verified via DOM dump 2026-04-26 04:25 UTC):
    #   .calendar-wrapper
    #     .month-header-wrapper [.arrow][.month-title][.year-title][.arrow]
    #     .day-header-wrapper   (Sun..Sat)
    #     .days-wrapper × N rows
    #       .day-span-container
    #         <span class="day [valid][selected]">DD</span>
    # Past/leading days lack `.valid`. Selected day has `.selected`. The two
    # `.arrow` spans navigate prev/next month. No aria-labels, no roles.
    target_month_name = dt.strftime("%B")  # "April"
    target_year = dt.strftime("%Y")
    target_day = str(dt.day)

    # Navigate to the right month, max 12 hops in either direction.
    for _attempt in range(12):
        header = page.evaluate(
            """() => {
                const root = document.querySelector('.calendar-wrapper');
                if (!root) return null;
                const m = root.querySelector('.month-title');
                const y = root.querySelector('.year-title');
                return {
                    month: m ? (m.textContent || '').trim() : '',
                    year:  y ? (y.textContent || '').trim() : '',
                };
            }"""
        )
        if not header:
            break
        if header["month"] == target_month_name and header["year"] == target_year:
            break

        # Decide direction: target before current → prev arrow; after → next.
        # NOTE: strptime returns a naive datetime; `dt` is tz-aware (planner
        # passes an offset). Comparing them raises TypeError. Stamp `cur_dt`
        # with `dt`'s tzinfo so the comparison is well-defined. Without this,
        # cross-month scheduling crashes the calendar nav (2026-04-30: 26/26
        # May-target tiktok posts failed here).
        try:
            cur_dt = datetime.strptime(
                f"{header['month']} {header['year']}", "%B %Y"
            ).replace(tzinfo=dt.tzinfo)
        except Exception:
            cur_dt = dt
        # First arrow is prev (left, rotate 90), second is next (right, rotate -90).
        arrow_idx = 0 if dt < cur_dt else 1
        try:
            page.evaluate(
                """(idx) => {
                    const arrows = document.querySelectorAll('.calendar-wrapper .arrow');
                    if (arrows.length >= 2) arrows[idx].click();
                }""",
                arrow_idx,
            )
            page.wait_for_timeout(300)
        except Exception:
            break

    # Click the .day.valid span whose text matches target_day. Use JS so
    # we hit the real React handler (the spans aren't role=button, so
    # Playwright's role-based locators won't find them).
    clicked = False
    try:
        clicked = page.evaluate(
            """(target) => {
                const root = document.querySelector('.calendar-wrapper');
                if (!root) return false;
                // Only "valid" days are selectable. There may be a "1" from
                // the next month (also valid) — we want the FIRST valid match
                // since days are ordered chronologically; the past-month
                // leading days are NOT valid, so the first valid match is
                // always the current-month one.
                const cells = Array.from(root.querySelectorAll('.day.valid'));
                const match = cells.find(el => (el.textContent || '').trim() === target);
                if (!match) return false;
                match.click();
                return true;
            }""",
            target_day,
        )
    except Exception:
        clicked = False

    if clicked:
        page.wait_for_timeout(400)
        if _tiktok_read_input_value(page, "date") == target_iso:
            return True

    _shot(page, basename, "date_cell_not_found")
    return False


def _tiktok_open_dropdown(page, value_re) -> bool:
    """Click any visible element whose inner text contains a substring matching
    `value_re`. Used to open TikTok's date / time dropdowns regardless of
    the icon + chevron + element-type combination they render with.

    Robustness layers (try each, return on first success):
      1. Playwright `has_text=` on common roles (button / div[role=button] /
         role=combobox).
      2. JS walker over standard interactive selectors (Pass A).
      3. JS walker over ALL visible elements with text matches; for each
         match, walk up the parent chain looking for a clickable ancestor
         (cursor:pointer, click handler, role=button, etc.). This catches
         the case where TikTok's dropdown is a styled `<div>` with no role.
      4. JS walker that filters matches further by "looks like a dropdown" —
         has an SVG chevron child OR a sibling chevron. Reduces false
         positives in case of two text matches on the page.

    Returns True if any click landed.
    """
    # Layer 1: Playwright's `has_text=` filter (substring or regex match).
    for getter in (
        lambda: page.locator('button').filter(has_text=value_re).first,
        lambda: page.locator('div[role="button"]').filter(has_text=value_re).first,
        lambda: page.locator('[role="combobox"]').filter(has_text=value_re).first,
    ):
        try:
            el = getter()
            el.wait_for(state="visible", timeout=2000)
            el.click()
            return True
        except Exception:
            continue

    # Layers 2-4: a single JS pass that does all three. Returns the strategy
    # name that matched (for logging) or null.
    try:
        result = page.evaluate(
            """(pat) => {
                const re = new RegExp(pat);
                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return false;
                    const cs = getComputedStyle(el);
                    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
                    return true;
                };
                const isClickable = (el) => {
                    if (!el || !el.tagName) return false;
                    const tag = el.tagName.toLowerCase();
                    if (tag === 'button' || tag === 'a' || tag === 'select') return true;
                    const role = (el.getAttribute && el.getAttribute('role')) || '';
                    if (['button','combobox','listbox','menuitem','option'].includes(role)) return true;
                    if (el.onclick) return true;
                    if (el.getAttribute && el.getAttribute('tabindex') !== null) return true;
                    try {
                        const cs = getComputedStyle(el);
                        if (cs.cursor === 'pointer') return true;
                    } catch (e) {}
                    return false;
                };

                // Layer 2: standard interactive selectors.
                const stdCands = Array.from(document.querySelectorAll(
                    'button, [role="button"], [role="combobox"], [tabindex="0"]'
                )).filter(isVisible);
                for (const el of stdCands) {
                    const text = (el.textContent || '').trim();
                    if (re.test(text)) {
                        el.click();
                        return 'layer2:' + text.slice(0, 30);
                    }
                }

                // Layer 3: text-match anywhere → walk up to clickable ancestor.
                const allEls = Array.from(document.querySelectorAll('*')).filter(isVisible);
                for (const el of allEls) {
                    if (el.children && el.children.length > 0) {
                        // Skip composite elements; we want leaf-ish text nodes.
                        // (We'll still match buttons in layer 4 below.)
                        continue;
                    }
                    const text = (el.textContent || '').trim();
                    if (!re.test(text)) continue;
                    // Walk up to find a clickable ancestor (max 6 levels).
                    let node = el;
                    for (let i = 0; i < 6 && node; i++) {
                        if (isClickable(node)) {
                            node.click();
                            return 'layer3:' + (node.tagName || '?') + ':' + text.slice(0, 30);
                        }
                        node = node.parentElement;
                    }
                }

                // Layer 4: filter matches by "looks like a dropdown" — text +
                // descendant SVG (the chevron). Final fallback.
                for (const el of allEls) {
                    if (!el.children || el.children.length === 0) continue;
                    const text = (el.textContent || '').trim();
                    if (!re.test(text)) continue;
                    if (text.length > 80) continue;  // skip large containers
                    const svg = el.querySelector && el.querySelector('svg');
                    if (!svg) continue;
                    // Walk up max 4 levels for a clickable ancestor.
                    let node = el;
                    for (let i = 0; i < 4 && node; i++) {
                        if (isClickable(node)) {
                            node.click();
                            return 'layer4:' + (node.tagName || '?') + ':' + text.slice(0, 30);
                        }
                        node = node.parentElement;
                    }
                    // Last resort: click el itself.
                    el.click();
                    return 'layer4-direct:' + text.slice(0, 30);
                }

                return null;
            }""",
            value_re.pattern,
        )
        return bool(result)
    except Exception:
        return False


def _tiktok_pick_time(page, dt: datetime, basename: str) -> bool:
    """Set TikTok's time field for the new readonly-input UI (2026-04-26+).

    TikTok Studio's time control is now a `<input type="text" readonly
    value="HH:MM">` that opens a TWO-COLUMN scroll picker on click. The
    columns are the hour (00–23) and the minute (00, 05, 10, … 55 — **5-min
    increments, not 15**). Active slot has class `tiktok-timepicker-is-active`.

    The picker container is `.tiktok-timepicker-time-picker-container` and
    is hidden via the `.tiktok-timepicker-invisible` class until the input
    is clicked.

    Strategy:
      1. React-aware direct `.value` set on the time input. If accepted,
         done.
      2. Click the input, wait for the picker container to lose
         `tiktok-timepicker-invisible`, then click the HH option in
         column 1 (`.tiktok-timepicker-left`) and the MM option in
         column 2 (`.tiktok-timepicker-right`).
      3. If exact MM not found, try the next 5-min step up to +30min.
    """
    rounded = _tiktok_round_up_to_increment(dt, 5)
    target_hh = rounded.strftime("%H")
    target_mm = rounded.strftime("%M")
    target_hhmm = rounded.strftime("%H:%M")

    # Strategy 1: React-aware direct set.
    try:
        ok = page.evaluate(
            """(hhmm) => {
                const root = document.querySelector('.scheduled-picker')
                          || document.querySelector('.scheduled-container');
                if (!root) return false;
                const inputs = Array.from(root.querySelectorAll('input.TUXTextInputCore-input'));
                const timeInput = inputs.find(i => /^\\d{1,2}:\\d{2}$/.test(i.value || ''));
                if (!timeInput) return false;
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(timeInput, hhmm);
                timeInput.dispatchEvent(new Event('input', {bubbles: true}));
                timeInput.dispatchEvent(new Event('change', {bubbles: true}));
                timeInput.dispatchEvent(new Event('blur', {bubbles: true}));
                return true;
            }""",
            target_hhmm,
        )
        page.wait_for_timeout(400)
        if ok:
            cur = _tiktok_read_input_value(page, "time")
            if cur == target_hhmm:
                return True
    except Exception:
        pass

    # Strategy 2: click input to open the two-column picker.
    clicked = False
    try:
        clicked = page.evaluate(
            """() => {
                const root = document.querySelector('.scheduled-picker')
                          || document.querySelector('.scheduled-container');
                if (!root) return false;
                const inputs = Array.from(root.querySelectorAll('input.TUXTextInputCore-input'));
                const timeInput = inputs.find(i => /^\\d{1,2}:\\d{2}$/.test(i.value || ''));
                if (!timeInput) return false;
                const wrap = timeInput.closest('.TUXTextInputCore') || timeInput;
                wrap.click();
                timeInput.click();
                return true;
            }"""
        )
    except Exception:
        clicked = False

    if not clicked:
        _shot(page, basename, "time_input_not_found")
        return False

    # Wait for picker visibility (it starts with .tiktok-timepicker-invisible).
    try:
        page.wait_for_selector(
            '.tiktok-timepicker-time-picker-container:not(.tiktok-timepicker-invisible)',
            timeout=3500,
        )
    except Exception:
        # Some builds don't toggle the class — give the popup time anyway.
        page.wait_for_timeout(700)

    _shot(page, basename, "time_picker_open")

    # TikTok's time picker is a wheel-style scroller — each column is a
    # vertically scrollable container (.tiktok-timepicker-time-scroll-container)
    # whose centered item gets the .tiktok-timepicker-is-active class.
    # Clicking `.tiktok-timepicker-option-item` does NOTHING (verified by
    # `time_value_did_not_apply` failure 2026-04-26 04:25 UTC), and setting
    # `scrollTop` programmatically + dispatching a synthetic 'scroll' event
    # also doesn't update React's selection (the picker's wheel handler
    # listens for real WheelEvents). The reliable path is `page.mouse.wheel()`
    # which dispatches a real wheel event at the cursor position.
    def _scroll_column_to(column_class: str, target: str) -> bool:
        # Read column metrics + active-vs-target item delta.
        info = page.evaluate(
            """({colClass, target}) => {
                const opts = Array.from(document.querySelectorAll(
                    '.tiktok-timepicker-option-text.' + colClass
                ));
                if (!opts.length) return null;
                const targetSpan = opts.find(el => (el.textContent || '').trim() === target);
                if (!targetSpan) return null;
                const activeSpan = opts.find(el => el.classList.contains('tiktok-timepicker-is-active'));
                const list = targetSpan.closest('.tiktok-timepicker-option-list');
                const container = targetSpan.closest('.tiktok-timepicker-time-scroll-container');
                if (!list || !container) return null;
                const items = Array.from(list.children);
                const tIdx = items.findIndex(it => it.contains(targetSpan));
                const aIdx = activeSpan
                    ? items.findIndex(it => it.contains(activeSpan))
                    : 0;
                const itemH = items[0].offsetHeight || 36;
                const cRect = container.getBoundingClientRect();
                return {
                    cx: cRect.left + cRect.width / 2,
                    cy: cRect.top + cRect.height / 2,
                    deltaItems: tIdx - aIdx,
                    itemH: itemH,
                };
            }""",
            {"colClass": column_class, "target": target},
        )
        if not info:
            return False

        # Move mouse over the column so wheel scrolls it (not the page).
        page.mouse.move(info["cx"], info["cy"])
        page.wait_for_timeout(150)

        # Send wheel ticks. Each tick = one item-height worth of scroll. Real
        # wheel events trigger TikTok's WheelEvent handler which updates the
        # active item. Sign convention: dy > 0 scrolls down (toward later
        # items at higher index).
        delta_items = info["deltaItems"]
        if delta_items != 0:
            ticks = abs(delta_items)
            per_tick = info["itemH"] * (1 if delta_items > 0 else -1)
            for _ in range(ticks):
                page.mouse.wheel(0, per_tick)
                page.wait_for_timeout(60)

        page.wait_for_timeout(300)

        # Verify by re-reading the active item's text.
        active_text = page.evaluate(
            """(colClass) => {
                const opts = Array.from(document.querySelectorAll(
                    '.tiktok-timepicker-option-text.' + colClass
                ));
                const a = opts.find(el => el.classList.contains('tiktok-timepicker-is-active'));
                return a ? (a.textContent || '').trim() : null;
            }""",
            column_class,
        )
        return active_text == target

    hh_set = _scroll_column_to("tiktok-timepicker-left", target_hh)
    if not hh_set:
        _shot(page, basename, "time_hh_not_found")
        return False
    page.wait_for_timeout(300)

    mm_set = _scroll_column_to("tiktok-timepicker-right", target_mm)
    if not mm_set:
        # Bump 5/10/.../30 in case the slot is filtered out at edge.
        for bump in (5, 10, 15, 20, 25, 30):
            bumped = rounded + timedelta(minutes=bump)
            if _scroll_column_to("tiktok-timepicker-right", bumped.strftime("%M")):
                mm_set = True
                if bumped.strftime("%H") != target_hh:
                    _scroll_column_to("tiktok-timepicker-left", bumped.strftime("%H"))
                break
    if not mm_set:
        _shot(page, basename, "time_slot_not_found")
        return False

    page.wait_for_timeout(500)

    # Close picker by clicking the section title (outside the picker).
    try:
        page.locator('.scheduled-title-container').first.click(timeout=800)
    except Exception:
        try:
            page.evaluate("document.body.click()")
        except Exception:
            pass
    page.wait_for_timeout(400)

    # VERIFY: did the input value actually change to what we wanted?
    # Without this check, returning True here silently produces
    # `time_set=True, displayed=<TikTok's default>` in the receipt.
    final = _tiktok_read_input_value(page, "time")
    if final != target_hhmm:
        # Try the bumped value too (in case fallback bumped us).
        for bump in (5, 10, 15, 20, 25, 30):
            bumped = (rounded + timedelta(minutes=bump)).strftime("%H:%M")
            if final == bumped:
                return True
        _shot(page, basename, "time_value_did_not_apply")
        return False
    return True


def _tiktok_read_input_value(page, kind: str) -> Optional[str]:
    """Read the current value of TikTok's time or date readonly input.

    `kind` is "time" or "date". Returns the input.value string or None.
    """
    try:
        return page.evaluate(
            """(kind) => {
                const root = document.querySelector('.scheduled-picker')
                          || document.querySelector('.scheduled-container');
                if (!root) return null;
                const inputs = Array.from(root.querySelectorAll('input.TUXTextInputCore-input'));
                const dateRe = /^\\d{4}-\\d{2}-\\d{2}$/;
                const timeRe = /^\\d{1,2}:\\d{2}$/;
                for (const inp of inputs) {
                    const v = inp.value || '';
                    if (kind === 'date' && dateRe.test(v)) return v;
                    if (kind === 'time' && timeRe.test(v)) return v;
                }
                return null;
            }""",
            kind,
        )
    except Exception:
        return None


def _tiktok_overwrite_description(page, cap_box, caption: str, basename: str) -> bool:
    """Replace TikTok's auto-populated description (filename) with `caption`.

    TikTok Studio uses a Lexical-based contenteditable that auto-populates
    with the uploaded filename. Plain `keyboard.press("Control+A")` +
    `Backspace` is unreliable here — the editor sometimes swallows the
    keystroke before its selection state updates, leaving the cursor at
    the END of the filename. Then typing appends the caption to the
    filename instead of replacing it.

    Strategy (each step verified, with progressively more aggressive
    fallbacks until the field reflects the desired caption):

      1. Focus the field (single click + small wait so React commits focus).
      2. Triple-click the field — browser-native "select line".
      3. Cmd+A (Playwright `ControlOrMeta+a`) for multi-line select.
      4. Type the caption — typing replaces a selection.
      5. Verify the field's text equals the caption. If not:
         5a. Use Locator.evaluate to set the contenteditable's text via DOM,
             dispatching the input/change events that React/Lexical needs.
         5b. Re-verify.

    Returns True if the field ends up matching `caption`.
    """
    # Step 1: focus.
    cap_box.click()
    page.wait_for_timeout(250)

    # Step 2: triple-click to select the visible line.
    try:
        cap_box.click(click_count=3)
        page.wait_for_timeout(150)
    except Exception:
        pass

    # Step 3: select-all (works on Lexical when focus is committed).
    cap_box.press("ControlOrMeta+a")
    page.wait_for_timeout(150)

    # Step 4: type the caption. Typing replaces a selection.
    cap_box.type(caption, delay=12)
    page.wait_for_timeout(500)

    # Step 5: verify the field's text matches what we wanted.
    def _read_field() -> str:
        try:
            return (cap_box.inner_text(timeout=800) or "").strip()
        except Exception:
            return ""

    actual = _read_field()
    if actual == caption.strip():
        return True

    # Was the caption appended to the filename? That's the original bug.
    # Detect it: if the field ENDS with our caption but is longer than it,
    # the prefix is leftover (filename or anything else).
    if actual.endswith(caption.strip()) and len(actual) > len(caption):
        # Try a clean DOM rewrite.
        try:
            cap_box.evaluate("""(el, txt) => {
                el.focus();
                // Select everything currently in the editor.
                const range = document.createRange();
                range.selectNodeContents(el);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
                // Delete the selection. execCommand is deprecated but still
                // the most-supported way to mutate Lexical/Draft editors
                // without going through their internal command queue.
                try { document.execCommand('delete'); } catch (e) {}
                // Now insert the desired text via execCommand so the editor
                // dispatches its own beforeinput/input events.
                try { document.execCommand('insertText', false, txt); } catch (e) {
                    // Last-resort: set textContent and synthesize an input event.
                    el.textContent = txt;
                    el.dispatchEvent(new InputEvent('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }""", caption)
            page.wait_for_timeout(500)
        except Exception:
            pass

        actual = _read_field()
        if actual == caption.strip():
            return True

    # Final fallback: brute-force backspace through whatever's there, then type.
    try:
        cap_box.click()
        cap_box.press("ControlOrMeta+End")
        page.wait_for_timeout(100)
        # Delete forwards and backwards aggressively.
        for _ in range(max(len(actual), 200) + 20):
            cap_box.press("Backspace")
        page.wait_for_timeout(200)
        cap_box.type(caption, delay=12)
        page.wait_for_timeout(400)
    except Exception:
        pass

    actual = _read_field()
    return actual == caption.strip()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _tiktok_read_displayed_schedule(page) -> Optional[str]:
    """Read TikTok's currently-displayed schedule as an ISO datetime string.

    Reads `input.value` directly off the two readonly inputs inside
    `.scheduled-picker` (or `.scheduled-container`). The previous
    implementation walked `textContent`, which is empty on `<input>`
    elements — that's why receipts could falsely report `success`
    while TikTok had auto-defaulted the schedule to "now+15min".

    Returns None if either field can't be parsed. Stamps the result with
    your configured timezone (POSTING_TOOL_TIMEZONE / profile.json; UTC
    by default), which should match the timezone TikTok displays.
    """
    date_str = _tiktok_read_input_value(page, "date")
    time_str = _tiktok_read_input_value(page, "time")
    if not date_str or not time_str:
        return None
    if len(time_str.split(":")[0]) == 1:
        time_str = "0" + time_str
    try:
        from platforms._safety_locks import load_user_profile
        zone = load_user_profile().zoneinfo()
        naive = datetime.fromisoformat(f"{date_str}T{time_str}:00")
        return naive.replace(tzinfo=zone).isoformat()
    except Exception:
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    path = LOG_DIR / f"tiktok_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    caption = (captions or {}).get("caption", "")
    if not caption:
        return {"status": "failed", "error": "tiktok caption block missing 'caption'", "at": _now_iso()}

    basename = video.stem

    try:
        with chrome_session() as page:
            page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")

            if "/login" in page.url or "/signup" in page.url:
                return {"status": "failed",
                        "error": "not logged into TikTok — run setup_playwright_logins.py",
                        "at": _now_iso()}

            gate = live_surface_gate(page, "tiktok", caption=caption, stage="landing")
            if gate:
                _shot(page, basename, "preflight_failed")
                return gate

            if not scheduled_for:
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="tiktok",
                        detail="Native Schedule only. When to post → Schedule, never Now.",
                    )
                )
            snapped, snap_fail = tiktok_scheduled_for_or_fail(scheduled_for)
            if snap_fail:
                return failure_receipt(snap_fail)
            scheduled_for = snapped or scheduled_for

            # Defensive: dismiss any stale "Are you sure you want to exit?"
            # modal left over from a previous attempt's interrupted upload.
            # If the previous Playwright session crashed mid-flow, TikTok
            # Studio's TUXModal can persist across page loads via the
            # persistent profile, intercepting all subsequent clicks. The
            # modal has two buttons — "Exit" (discards progress) and
            # "Cancel" (dismisses modal, stays on upload page). We click
            # Cancel. Added 2026-06-15 after run-13 30-ce-the-lot failure
            # where "Are you sure you want to exit?" intercepted the
            # description contenteditable click for 30s straight.
            try:
                exit_modal = page.locator('h1:has-text("Are you sure you want to exit")').first
                if exit_modal.is_visible(timeout=2000):
                    _shot(page, basename, "stale_exit_modal_present")
                    cancel_btn = page.locator('button:has-text("Cancel")').first
                    cancel_btn.click(timeout=3000)
                    page.wait_for_timeout(500)
                    _shot(page, basename, "stale_exit_modal_dismissed")
            except Exception:
                # No modal present — common case, proceed.
                pass

            # Upload video — find hidden file input.
            file_input = page.locator('input[type="file"]').first
            file_input.wait_for(state="attached", timeout=15_000)
            file_input.set_input_files(str(video))
            upload_url = page.url or ""

            # Wait for the upload preview to render — TikTok shows a thumbnail.
            page.wait_for_timeout(8000)
            try:
                page.wait_for_selector('div[class*="VideoCard"]', timeout=60_000)
            except Exception:
                _shot(page, basename, "upload_preview_missing")

            # Caption. TikTok Studio auto-populates the description with
            # the uploaded filename (e.g. "clipper_2026-04-25_..."), so
            # we MUST clear before typing or the caption gets appended
            # to the filename. Use clear_and_type which handles macOS
            # vs Linux modifier keys correctly via Playwright's
            # ControlOrMeta+a (Cmd on Mac, Ctrl on Linux). The hand-rolled
            # "Control+A" approach used previously did not select-all
            # on macOS Chrome — it does "go to start of line", leaving
            # the filename in place.
            #
            # Selector: prefer aria-label / data attributes over .first,
            # because there can be other contenteditables in the DOM
            # (cover-text overlay, hashtag picker) that .first might pick.
            cap_box = None
            for sel in [
                'div[contenteditable="true"][aria-label*="description" i]',
                'div[contenteditable="true"][data-text="true"]',
                'div[role="textbox"][aria-label*="description" i]',
                'div[contenteditable="true"]',  # last-resort fallback
            ]:
                try:
                    candidate = page.locator(sel).first
                    candidate.wait_for(state="visible", timeout=4000)
                    cap_box = candidate
                    break
                except Exception:
                    continue
            if cap_box is None:
                _shot(page, basename, "caption_box_missing")
                return {"status": "failed",
                        "error": "TikTok description box not found",
                        "at": _now_iso()}

            # Overwrite the auto-populated filename with the real caption,
            # verifying we don't end up with `<filename><caption>`.
            overwrote = _tiktok_overwrite_description(page, cap_box, caption, basename)
            page.wait_for_timeout(400)
            _shot(page, basename, "caption_typed" if overwrote else "caption_overwrite_failed")
            if not overwrote:
                # Don't fail the whole post over this — TikTok may still
                # accept the post with the prefix and the user can edit it
                # post-hoc — but log it loud so we know it happened.
                _shot(page, basename, "caption_has_filename_prefix")

            composer_gate = live_surface_gate(
                page, "tiktok", caption=caption, stage="composer"
            )
            if composer_gate:
                _shot(page, basename, "composer_preflight_failed")
                return composer_gate

            # Never navigate away from the Upload editor after attach.
            if "upload" not in (page.url or "").lower() and "upload" in upload_url.lower():
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="tiktok",
                        detail=(
                            "Left the Upload editor after attach. Discard this post "
                            "kills the schedule. Stay on the editor until Schedule commits."
                        ),
                    )
                )

            scheduled_ok = False
            if scheduled_for:
                # Toggle Schedule on (a switch labeled "Schedule" near bottom).
                toggle_clicked = False
                for getter in (
                    lambda: page.get_by_text("Schedule", exact=True).first,
                    lambda: page.locator('div[data-tt-element-id*="schedule" i]').first,
                    lambda: page.locator('label:has-text("Schedule")').first,
                ):
                    try:
                        el = getter()
                        el.scroll_into_view_if_needed()
                        el.click(timeout=3000)
                        toggle_clicked = True
                        break
                    except Exception:
                        continue
                if not toggle_clicked:
                    _shot(page, basename, "schedule_toggle_missing")
                    return {"status": "failed",
                            "error": "TikTok schedule toggle not found (account may not have scheduling)",
                            "at": _now_iso()}

                page.wait_for_timeout(800)
                _shot(page, basename, "schedule_toggle_on")

                # Dump the full Settings section DOM so if the dropdown
                # walker still misses, we have raw HTML to inspect (see
                # logs/screenshots/tiktok_*_schedule_dom_*.html).
                try:
                    html = page.evaluate(
                        """() => {
                            // Find the Settings card containing the schedule controls.
                            const headings = Array.from(document.querySelectorAll(
                                'div, h1, h2, h3, h4, h5, h6, span'
                            )).filter(el => (el.textContent || '').trim() === 'When to post');
                            if (!headings.length) return document.body.outerHTML.slice(0, 60000);
                            // Walk up to find a substantial container.
                            let node = headings[0];
                            for (let i = 0; i < 5; i++) {
                                if (!node.parentElement) break;
                                node = node.parentElement;
                                if (node.querySelectorAll('button, [role="button"], input').length >= 3) break;
                            }
                            return node.outerHTML.slice(0, 80000);
                        }"""
                    )
                    dump_path = LOG_DIR / (
                        f"tiktok_{basename}_schedule_dom_"
                        f"{int(datetime.now().timestamp())}.html"
                    )
                    dump_path.write_text(html)
                except Exception:
                    pass

                dt = datetime.fromisoformat(scheduled_for)

                # TikTok Studio's schedule UI (2026-04-26+) is TWO readonly
                # text inputs inside `.scheduled-picker`:
                #   - Time:  <input readonly value="HH:MM">
                #   - Date:  <input readonly value="YYYY-MM-DD">
                # The values live on `input.value`, not `textContent`. We set
                # via the React-aware `.value` setter first; if the picker
                # opens (TikTok rejects direct sets), we click HH+MM in the
                # two-column scroll picker and the calendar cell.
                date_ok = _tiktok_pick_date(page, dt, basename)
                time_ok = _tiktok_pick_time(page, dt, basename)

                # Truth check: read input.value back. If TikTok's defaults
                # are still in place (auto ~now+15min), the receipt will
                # show that — and we must NOT submit, because doing so
                # silently posts at the wrong time. Previously we
                # overwrote `scheduled_for` with `displayed` to mask this;
                # that's exactly how 3 of 5 videos in the 2026-04-26
                # late-evening dispatch ended up at the wrong time
                # (2 immediate, 1 at +15min) while the receipt reported
                # `status=success`.
                displayed = _tiktok_read_displayed_schedule(page)
                displayed_dt = None
                if displayed:
                    try:
                        displayed_dt = datetime.fromisoformat(displayed)
                    except Exception:
                        displayed_dt = None

                if displayed_dt:
                    delta_seconds = abs((displayed_dt - dt).total_seconds())
                    schedule_matches = delta_seconds <= 5 * 60  # 5-min tolerance
                else:
                    schedule_matches = False

                scheduled_ok = bool(date_ok and time_ok and schedule_matches)

                if not scheduled_ok:
                    _shot(page, basename, "schedule_inputs_unfillable")
                    return {
                        "status": "failed",
                        "error": (
                            "TikTok schedule mismatch: planned="
                            f"{dt.isoformat()} displayed={displayed} "
                            f"date_set={date_ok} time_set={time_ok}"
                        ),
                        "displayed_schedule": displayed,
                        "date_set": date_ok,
                        "time_set": time_ok,
                        "at": _now_iso(),
                    }
                _shot(page, basename, "schedule_verified")

            # COMMIT click is Schedule only. Never Post now.
            forbidden = gate_commit_click("tiktok", "Post now")
            if not scheduled_ok:
                _shot(page, basename, "schedule_not_set_no_submit")
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="tiktok",
                        detail="When to post is not Schedule. Never Post now.",
                    )
                )
            try:
                # Scroll to absolute bottom of the page.
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
                _shot(page, basename, "scrolled_to_bottom")

                action_clicked = False
                action_candidates = [
                    lambda: page.get_by_role("button", name="Schedule", exact=True).first,
                    lambda: page.locator('button:has-text("Schedule")').last,
                ]
                for getter in action_candidates:
                    try:
                        btn = getter()
                        btn.wait_for(state="visible", timeout=4000)
                        btn.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        if not btn.is_enabled(timeout=1000):
                            page.wait_for_timeout(2000)
                        btn.click(timeout=3000)
                        action_clicked = True
                        break
                    except Exception:
                        continue

                if not action_clicked:
                    _shot(page, basename, "submit_btn_missing")
                    return failure_receipt(
                        pause_on_wall(
                            "missing_schedule_control",
                            surface="tiktok",
                            detail="Schedule commit button not found. Do not click Post now.",
                        )
                    )
            except Exception as e:
                _shot(page, basename, "submit_flow_failed")
                return {"status": "failed",
                        "error": f"TikTok submit failed: {type(e).__name__}: {e}",
                        "at": _now_iso()}

            # "Continue to post?" → Post now is a wall. Never click it.
            page.wait_for_timeout(2000)
            confirm_handled = False
            try:
                if page.get_by_text("Continue to post", exact=False).first.is_visible(timeout=1500):
                    _shot(page, basename, "continue_to_post_wall")
                    return failure_receipt(
                        pause_on_wall(
                            "missing_schedule_control",
                            surface="tiktok",
                            detail="Continue to post / Post now modal is a wall. Do not click Post now.",
                        )
                    )
            except Exception:
                pass

            page.wait_for_timeout(4000)

            # Proof AFTER commit only — now it is safe to leave the Upload editor.
            native_ok = False
            toast_seen = False
            try:
                toast_seen = page.locator('div[role="status"], div[role="alert"]').first.is_visible(timeout=800)
            except Exception:
                toast_seen = False
            try:
                page.goto(
                    "https://www.tiktok.com/tiktokstudio/content",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                page.wait_for_timeout(1500)
                snippet = (caption or "").strip()[:40]
                if snippet:
                    native_ok = page.get_by_text(snippet, exact=False).first.is_visible(timeout=5000)
            except Exception:
                native_ok = False
            proof_fail = native_list_proof_or_fail(
                "tiktok",
                native_list_has_item=bool(native_ok),
                composer_toast_seen=bool(toast_seen),
            )
            if proof_fail:
                _shot(page, basename, "native_list_proof_missing")
                return failure_receipt(proof_fail, confirm_modal_handled=confirm_handled)

            _shot(page, basename, "scheduled_on_content_list")
            return {
                "status": "scheduled",
                "url": None,
                "scheduled_for": scheduled_for if scheduled_ok else None,
                "proof": "tiktokstudio_content_list",
                "confirm_modal_handled": confirm_handled,
                "at": _now_iso(),
            }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
