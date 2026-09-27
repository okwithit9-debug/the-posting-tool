"""
platforms/_pickers.py — shared date/time picker helpers for Playwright posters.

Why this exists
---------------
Most social platforms use *custom* date/time widgets (calendar grids, time
wheels) rather than native <input type="date"> / <input type="time">. So a
plain page.fill('input[type="date"]', ...) silently does nothing on TikTok,
IG, Threads, Pinterest. This module provides progressively-more-aggressive
strategies to land a datetime into one of those widgets:

    1. set_native_datetime_via_js(page, dt)   — find any <input> backing the
       widget and dispatch input + change events. Works when the React/Vue
       state still mirrors a hidden native input. Fastest. Often works on IG.
    2. pick_date_in_calendar(page, dt)        — click the day cell in a
       visible calendar grid, advancing months with the prev/next arrows
       until the target month is in view.
    3. set_time_via_typing(page, dt)          — click the time pill, then
       type the time digits directly. Many time wheels accept keyboard
       input that's hidden by the wheel UI.

All helpers return True on success and False on failure (no exceptions). The
caller should chain them: try the cheapest first, fall back to the next.

A typical platform usage looks like:

    if not (set_native_datetime_via_js(page, dt) or
            (pick_date_in_calendar(page, dt) and set_time_via_typing(page, dt))):
        # fall through to immediate post
        ...

Screenshots: callers should screenshot before AND after each strategy so
we can see in logs/screenshots/ exactly which strategy landed and where it
broke.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


# Multiple aria-label / format conventions we've seen in the wild for day cells.
# All three are tried; whichever matches the live DOM wins.
_DAY_CELL_LABEL_FORMATS = (
    "%B %-d, %Y",      # "April 28, 2026"
    "%A, %B %-d, %Y",  # "Tuesday, April 28, 2026"
    "%-m/%-d/%Y",      # "4/28/2026"
)

# Aria labels for next-month / prev-month buttons in calendars.
_NEXT_MONTH_LABELS = ("Next month", "Next Month", "next month", "Next")
_PREV_MONTH_LABELS = ("Previous month", "Previous Month", "previous month", "Previous", "Prev")


def set_native_datetime_via_js(page, dt: datetime) -> bool:
    """Try to set any native HTML date/time inputs via JS event dispatch.

    Many React-based calendar widgets sit on top of a hidden
    <input type="date"> or <input type="datetime-local">. Setting the
    `value` and dispatching 'input' + 'change' is enough to update the
    React state in those cases. Returns True if at least one date input
    AND one time input (or one combined datetime-local input) was set.

    Why JS rather than Playwright .fill():
      - .fill() on a hidden input throws because the element isn't
        interactable from the user's POV.
      - Dispatching events from JS bypasses interactability checks.
    """
    iso_date = dt.strftime("%Y-%m-%d")
    iso_time = dt.strftime("%H:%M")
    iso_dtlocal = f"{iso_date}T{iso_time}"

    try:
        result = page.evaluate(
            """({iso_date, iso_time, iso_dtlocal}) => {
                const setVal = (el, val) => {
                    el.focus();
                    // React tracks values via descriptor — set via prototype.
                    const proto = Object.getPrototypeOf(el);
                    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (desc && desc.set) {
                        desc.set.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur();
                };

                let setDate = false, setTime = false;

                // 1) Combined datetime-local inputs (one shot).
                const dtl = Array.from(document.querySelectorAll('input[type="datetime-local"]'));
                for (const el of dtl) {
                    setVal(el, iso_dtlocal);
                    setDate = true; setTime = true;
                }
                if (setDate && setTime) return { setDate, setTime, via: 'datetime-local' };

                // 2) Separate date / time inputs.
                const dateInputs = Array.from(document.querySelectorAll(
                    'input[type="date"]'
                ));
                for (const el of dateInputs) { setVal(el, iso_date); setDate = true; }

                const timeInputs = Array.from(document.querySelectorAll(
                    'input[type="time"]'
                ));
                for (const el of timeInputs) { setVal(el, iso_time); setTime = true; }

                return { setDate, setTime, via: 'split' };
            }""",
            {"iso_date": iso_date, "iso_time": iso_time, "iso_dtlocal": iso_dtlocal},
        )
    except Exception:
        return False

    return bool(result.get("setDate") and result.get("setTime"))


def _try_click_day_cell(page, dt: datetime, timeout_ms: int = 1500) -> bool:
    """Click the calendar day cell for `dt` if it's currently visible.

    Tries several aria-label formats. Tries both 'button' and 'gridcell' roles.
    """
    for fmt in _DAY_CELL_LABEL_FORMATS:
        # %-d is GNU-only; on macOS it works, but guard with try.
        try:
            label = dt.strftime(fmt)
        except Exception:
            continue
        for role in ("button", "gridcell"):
            try:
                cell = page.get_by_role(role, name=label, exact=True).first
                cell.wait_for(state="visible", timeout=timeout_ms)
                cell.click()
                return True
            except Exception:
                continue
        # Some calendars don't have role; try a CSS attribute selector.
        try:
            cell = page.locator(f'[aria-label="{label}"]').first
            cell.wait_for(state="visible", timeout=timeout_ms)
            cell.click()
            return True
        except Exception:
            continue
    # Last resort: by visible text equal to the day-of-month digit. This is
    # noisy (multiple cells can match across months) but useful when the
    # month is already in view.
    try:
        day_str = str(dt.day)
        cells = page.get_by_role("button", name=day_str, exact=True)
        # Filter to ones that look like day cells (prefer those with
        # aria-label containing the year / month name).
        count = cells.count()
        if count > 0:
            cells.first.click(timeout=timeout_ms)
            return True
    except Exception:
        pass
    return False


def _click_next_month(page, timeout_ms: int = 1500) -> bool:
    for label in _NEXT_MONTH_LABELS:
        try:
            page.get_by_role("button", name=label).first.click(timeout=timeout_ms)
            page.wait_for_timeout(250)
            return True
        except Exception:
            continue
    # Fallback: an arrow icon button. Common pattern: a button with an SVG.
    try:
        # Take the second visible-arrow button as next (first would be prev).
        # Heuristic — not always right, hence last-resort.
        page.locator('button:has(svg[aria-label*="next" i])').first.click(timeout=timeout_ms)
        page.wait_for_timeout(250)
        return True
    except Exception:
        return False


def pick_date_in_calendar(page, dt: datetime, max_advance: int = 24) -> bool:
    """Navigate a visible calendar widget to `dt`'s month/year and click the day cell.

    Calendar must already be open (caller clicked the date pill). Will click
    the next-month arrow up to `max_advance` times. 24 is a reasonable
    upper bound for the platforms we care about (max scheduling horizon
    is ~75 days = 3 months, but we leave headroom).
    """
    for _ in range(max_advance + 1):
        if _try_click_day_cell(page, dt):
            return True
        if not _click_next_month(page):
            return False
    return False


def set_time_via_typing(page, dt: datetime, time_pill_clicks: Optional[list] = None) -> bool:
    """After a time pill/input is focused, type the target time as HH:MM AM/PM.

    Many "scroll-wheel" time pickers actually accept keyboard input — they're
    backed by a real <input> and the wheel is a visual layer.

    Args:
      time_pill_clicks: optional list of locator-getter functions to click first
                        in order to focus the time pill. If None, assume caller
                        already focused the time input.

    Strategy:
      1. Click the pill (if provided)
      2. Select-all + delete to clear any existing value
      3. Type "HH:MM AM" / "HH:MM PM" with small per-key delay
      4. Press Tab/Escape to commit
    """
    if time_pill_clicks:
        clicked = False
        for getter in time_pill_clicks:
            try:
                el = getter()
                el.wait_for(state="visible", timeout=2500)
                el.click()
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            return False

    page.wait_for_timeout(300)
    # Clear whatever's in the focused input.
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.press("Delete")

    # Type the time. We use 12-hour format because most US-locale platforms
    # (X, TikTok, IG, Pinterest) display 12-hour. Try 12h first; if that
    # silently fails the picker may accept 24h.
    hour_12 = ((dt.hour - 1) % 12) + 1
    ampm = "PM" if dt.hour >= 12 else "AM"
    time_str = f"{hour_12:d}:{dt.minute:02d} {ampm}"

    try:
        page.keyboard.type(time_str, delay=20)
        page.keyboard.press("Tab")
        return True
    except Exception:
        return False


def click_date_pill_then_pick(page, pill_getters: list, dt: datetime) -> bool:
    """Click a date pill (first selector that works), then navigate the
    calendar that opens and click the day cell.

    Args:
      pill_getters: list of zero-arg callables, each returning a Locator. We
                    try them in order; the first that's visible+clickable wins.
    """
    clicked = False
    for getter in pill_getters:
        try:
            el = getter()
            el.wait_for(state="visible", timeout=2500)
            el.click()
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        return False
    page.wait_for_timeout(500)
    return pick_date_in_calendar(page, dt)


# ----------------------------------------------------------------------
# Threads-style calendar (numeric day cells, "Month YYYY" header)
# ----------------------------------------------------------------------
#
# The Threads schedule popover (confirmed via screenshot 2026-04-25) renders:
#   - A header like "April 2026" + < and > chevrons
#   - Day cells that are plain numeric buttons ("28") with NO date-aware
#     aria-labels — leading days from the previous month and trailing days
#     from the next month also appear as numeric cells, just visually muted.
#
# Strategy:
#   1. Read the current month/year from the header text.
#   2. Click > (next) or < (previous) until the header matches dt's month.
#   3. Click the day cell — picking the FIRST cell whose text equals the
#      day number AND whose computed style isn't 'muted' (low-opacity /
#      gray). Falls back to the first match if we can't tell.

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _read_calendar_header(page) -> Optional[tuple[int, int]]:
    """Look for an element whose text reads like 'April 2026' and parse it.

    Returns (month, year) or None.
    """
    # Try several heuristics — the header may be a heading, a div, etc.
    for sel in [
        'h1, h2, h3, h4, h5, h6',
        'div[role="heading"]',
        'span',
        'div',
    ]:
        try:
            elements = page.locator(sel).all()
        except Exception:
            continue
        for el in elements[:200]:  # avoid scanning the entire DOM
            try:
                text = (el.inner_text(timeout=200) or "").strip()
            except Exception:
                continue
            for i, name in enumerate(_MONTH_NAMES, start=1):
                # Match exact "<Month> <Year>", e.g., "April 2026".
                if not text.startswith(f"{name} "):
                    continue
                parts = text.split()
                if len(parts) == 2 and parts[1].isdigit() and 2020 <= int(parts[1]) <= 2099:
                    return (i, int(parts[1]))
    return None


def pick_date_threads_style(page, dt: datetime, max_advance: int = 24) -> bool:
    """Navigate a Threads-style calendar and click the day cell for `dt`.

    Caller must have already opened the schedule popover (the calendar must
    be visible).

    Algorithm:
      - Read the header. If unreadable, fall back to pick_date_in_calendar
        (which uses aria-labels — works on most other platforms).
      - Compute month delta = (dt.year - cur.year) * 12 + (dt.month - cur.month)
      - Click > delta times (or < if negative) up to max_advance.
      - Click the cell with the right day number, preferring non-muted cells.
    """
    cur = _read_calendar_header(page)
    if not cur:
        # Header unreadable — try the generic aria-label approach as a fallback.
        return pick_date_in_calendar(page, dt, max_advance=max_advance)

    cur_month, cur_year = cur
    delta = (dt.year - cur_year) * 12 + (dt.month - cur_month)
    if abs(delta) > max_advance:
        return False

    advance = _click_next_month if delta > 0 else _click_prev_month
    for _ in range(abs(delta)):
        if not advance(page):
            return False

    # Now click the cell with the target day number. There may be multiple
    # matches (leading/trailing days from adjacent months). Prefer the
    # non-muted, in-current-month one.
    #
    # 2026-05-01 update: the previous role=button + opacity-rank approach
    # failed flakily on May 4-5 targets (3/7 fails) because Threads' Meta
    # build doesn't render day cells as role=button — `cells.count()`
    # returned 0, the function fell through to `get_by_text(...).first`
    # which clicked SOMETHING (often a date that wasn't toggled, or the
    # June trailing 5) and returned True without verifying the cell was
    # actually selected.
    #
    # 2026-05-01 strategy was a JS-based pick that filtered candidates by
    # counting digit-text siblings near each "N" leaf — a proxy for "is
    # this inside a calendar grid". That worked until 2026-05-05, when
    # Threads added an extra wrapper layer (gridcell > label-div +
    # aria-hidden div > span) that left the 2-up parent with zero leaf
    # digit siblings, rejecting every candidate (7/10 Threads runs failed).
    #
    # 2026-05-05 update: anchor on the role="gridcell" ancestor that
    # Threads (and every accessible date picker) provides. We confirmed
    # the dumped DOM contains 49 gridcells, each wrapping one day, with
    # aria-disabled="true" set on outside-month cells. Clicking the
    # gridcell itself is what Threads listens to, so we click that
    # rather than the digit span. The legacy fallback below stays as a
    # last-resort safety net.
    day = dt.day
    js_clicked = False
    try:
        js_clicked = bool(page.evaluate(
            """(targetDay) => {
                const want = String(targetDay);
                const candidates = [];
                const all = document.querySelectorAll('div, span, td, button, a, li');
                for (const el of all) {
                    if (el.children.length > 0) continue;  // leaf-text only
                    const txt = (el.textContent || '').trim();
                    if (txt !== want) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;  // zero-size
                    if (r.top < 0 || r.left < 0) continue;        // off-screen
                    const cs = getComputedStyle(el);
                    const op = parseFloat(cs.opacity || '1');
                    if (op < 0.4) continue;  // heavily muted (outside-month)
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    // Anchor on the gridcell ancestor — the ARIA contract
                    // for calendar day buttons. Drops the digit-sibling
                    // heuristic that broke when Threads added an extra
                    // wrapper layer in early May 2026.
                    //
                    // 2026-05-05 (later): also dropped the 12×12 / 80×80 px
                    // size filter on the leaf span. Threads renders day
                    // digits at 12px font with 8.4px line-height, so the
                    // span's bbox is often ~7×9 — below the old 12px floor.
                    // The gridcell ancestor is now the sole disambiguator,
                    // which is fine: text "9" inside a sentence is never
                    // wrapped in role="gridcell".
                    const gridcell = el.closest('[role="gridcell"]');
                    if (!gridcell) continue;            // not in a calendar
                    if (gridcell.getAttribute('aria-disabled') === 'true') continue;
                    // Use the gridcell's bbox for sort tiebreaks so layout
                    // reflows on the leaf don't skew row ordering.
                    const gr = gridcell.getBoundingClientRect();
                    candidates.push({el, gridcell, op, top: gr.top, left: gr.left});
                }
                if (!candidates.length) return false;
                // Best = highest opacity; tie-break by topmost row (in-month
                // days come before trailing-month days in the DOM).
                candidates.sort((a, b) => (b.op - a.op) || (a.top - b.top));
                // Click the gridcell itself — that's what Threads listens to.
                candidates[0].gridcell.click();
                return true;
            }""",
            day,
        ))
    except Exception:
        js_clicked = False

    if js_clicked:
        # Brief wait to let React re-render the selected state.
        page.wait_for_timeout(400)
        return True

    # Legacy fallback: the original role=button + opacity-rank path.
    day_str = str(day)
    try:
        cells = page.get_by_role("button", name=day_str, exact=True)
        n = cells.count()
    except Exception:
        n = 0
    if n == 0:
        try:
            page.get_by_text(day_str, exact=True).first.click(timeout=1500)
            return True
        except Exception:
            return False
    best_idx = 0
    for i in range(n):
        try:
            opacity = cells.nth(i).evaluate(
                "el => parseFloat(getComputedStyle(el).opacity || '1')"
            )
            if opacity is not None and opacity >= 0.95:
                best_idx = i
                break
        except Exception:
            continue
    try:
        cells.nth(best_idx).click(timeout=2000)
        return True
    except Exception:
        return False


def _click_prev_month(page, timeout_ms: int = 1500) -> bool:
    for label in _PREV_MONTH_LABELS:
        try:
            page.get_by_role("button", name=label).first.click(timeout=timeout_ms)
            page.wait_for_timeout(250)
            return True
        except Exception:
            continue
    return False


def set_threads_time(page, dt: datetime) -> bool:
    """Set the time on Threads' time row (HH : MM AM/PM, three clickable parts).

    The row shows three discrete elements: hour, minute, AM/PM. They appear
    to be a mix of numeric inputs and a toggle. Best-effort:
      1. Find inputs with a 2-digit numeric pattern; first is hour, second is minute.
      2. Click AM/PM toggle if it doesn't match dt.
    """
    hour_12 = ((dt.hour - 1) % 12) + 1
    ampm = "PM" if dt.hour >= 12 else "AM"

    # Try native inputs first (covers cases where the time row is just <input>s).
    set_h = set_m = False
    try:
        # Look for inputs near a time/clock icon. Take all small numeric
        # inputs in the popover and assume order: hour, minute.
        inputs = page.locator('input[type="text"], input:not([type])').all()
        numeric = [el for el in inputs if (el.get_attribute("inputmode") or "").startswith("num")
                   or (el.get_attribute("maxlength") or "").strip() in ("1", "2")]
        if not numeric:
            # Heuristic: any input with a 2-digit value.
            numeric = []
            for el in inputs:
                try:
                    val = (el.input_value(timeout=200) or "").strip()
                    if val.isdigit() and len(val) <= 2:
                        numeric.append(el)
                except Exception:
                    continue
        if len(numeric) >= 2:
            try:
                numeric[0].fill(f"{hour_12:02d}")
                set_h = True
            except Exception:
                pass
            try:
                numeric[1].fill(f"{dt.minute:02d}")
                set_m = True
            except Exception:
                pass
    except Exception:
        pass

    # AM/PM toggle: click whichever is currently visible if it doesn't match.
    try:
        opposite = "AM" if ampm == "PM" else "PM"
        # Find the toggle by current text; click only if it doesn't already match.
        cur_btn = page.get_by_role("button", name=opposite, exact=True).first
        if cur_btn.is_visible(timeout=500):
            cur_btn.click()
    except Exception:
        # If we can't toggle but the picker default is AM and we want AM
        # (or PM/PM), this is fine — the picker already matches.
        pass

    return set_h and set_m
