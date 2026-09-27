"""
platforms/x.py — X / Twitter video poster (Playwright).

We do NOT use the X API. The X account was banned previously after applying
for the Twitter Developer Portal — the dev-portal application is the leading
suspect. Going through the regular composer with a logged-in Chrome profile
keeps the account on the same posting pattern as a normal user.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session, clear_and_type

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    path = LOG_DIR / f"x_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def _wait_for_tweet_btn_enabled(page, btn, basename: str, *, timeout_s: int = 180) -> None:
    """Poll the tweet button's aria-disabled attribute until it's no longer
    'true', i.e. X has finished uploading + transcoding the video.

    Playwright's `wait_for(state="visible")` does NOT wait for enabled. The
    button is rendered up front and just gets aria-disabled flipped off when
    the upload finishes. This poller is what actually proves the upload is
    done before we try to click.

    Raises RuntimeError on timeout (and takes a debug screenshot first).
    """
    deadline = time.monotonic() + float(timeout_s)
    last_state = None
    while time.monotonic() < deadline:
        try:
            disabled_attr = btn.get_attribute("aria-disabled", timeout=2000)
        except Exception:
            disabled_attr = last_state
        last_state = disabled_attr
        if disabled_attr != "true":
            return
        page.wait_for_timeout(500)
    _shot(page, basename, "upload_processing_timeout")
    raise RuntimeError(
        f"X tweetButton stayed aria-disabled='true' for {timeout_s}s — "
        "upload/transcode did not finish in time."
    )


def _wait_for_post_committed(page, basename: str, *, timeout_s: int = 30) -> Optional[str]:
    """After clicking Post, wait until X has actually committed the tweet
    before letting chrome_session() tear the browser down.

    Polls every 250ms for any of:
      1. URL navigates to a /status/<id> page (X redirects after post).
      2. The composer textarea detaches from the DOM (modal closed).
      3. The "Your post was sent" toast appears.

    Returns the /status/ URL if we caught it, otherwise None.

    If no signal arrives within `timeout_s`, falls back to a 15s safety
    sleep so we never exit < ~4s after click — that's the failure mode
    where closing the tab cancels an in-flight upload.
    """
    deadline = time.monotonic() + float(timeout_s)
    composer = page.locator('div[data-testid="tweetTextarea_0"]').first
    toast_selectors = [
        'div[data-testid="toast"]',
        'div[role="status"]',
    ]
    while time.monotonic() < deadline:
        # Signal 1: URL navigated to /status/
        try:
            if "/status/" in page.url:
                return page.url
        except Exception:
            pass
        # Signal 2: composer closed (textarea detached or hidden)
        try:
            if composer.count() == 0 or not composer.is_visible(timeout=200):
                return None
        except Exception:
            # Detached locator throws — that itself means the composer
            # is gone, which is the success signal we wanted.
            return None
        # Signal 3: confirmation toast
        for sel in toast_selectors:
            try:
                if page.locator(sel).first.is_visible(timeout=200):
                    return None
            except Exception:
                continue
        page.wait_for_timeout(250)

    # No signal in `timeout_s` — log a warning screenshot and fall through
    # to a longer safety sleep. The post may still have committed; we just
    # didn't see the confirmation. Better to wait too long than tear down
    # the browser while X is still uploading.
    _shot(page, basename, "post_no_confirmation_signal_safety_sleep")
    page.wait_for_timeout(15_000)
    try:
        return page.url if "/status/" in page.url else None
    except Exception:
        return None


def _verify_x_scheduled_commit(page, basename: str, *, timeout_s: int = 20) -> bool:
    """After clicking the final Schedule button, prove the schedule actually
    committed before reporting success.

    Two acceptable signals (whichever fires first wins):
      1. Success toast text "Your post will be sent on" appears (X's standard
         schedule-confirmation toast — visible at the bottom of the home feed).
      2. The composer textarea (`tweetTextarea_0`) detaches or hides.

    Returns True on commit, False on timeout. If False, the caller should
    treat the post as failed even though `scheduled_ok` was True — the modal
    confirm fired but the final Schedule click was eaten by an overlay
    (Save post? dialog, Premium+ banner, etc.).
    """
    deadline = time.monotonic() + float(timeout_s)
    composer = page.locator('div[data-testid="tweetTextarea_0"]').first
    toast_selector = 'div[data-testid="toast"], div[role="status"]'
    toast_text_locator = page.get_by_text("Your post will be sent on", exact=False).first
    while time.monotonic() < deadline:
        # Signal 1: success toast text.
        try:
            if toast_text_locator.is_visible(timeout=200):
                return True
        except Exception:
            pass
        # Signal 1b: any toast container — if the schedule succeeded we'd
        # expect the success toast specifically, but a generic toast is at
        # least evidence the click did something.
        try:
            t = page.locator(toast_selector).first
            if t.is_visible(timeout=200):
                txt = (t.inner_text(timeout=300) or "").lower()
                if "will be sent" in txt or "scheduled" in txt:
                    return True
        except Exception:
            pass
        # Signal 2: composer detached/hidden.
        try:
            if composer.count() == 0:
                return True
            if not composer.is_visible(timeout=200):
                return True
        except Exception:
            return True  # detached locator throws — that's success too
        page.wait_for_timeout(300)
    _shot(page, basename, "schedule_commit_unverified")
    return False


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    caption = (captions or {}).get("caption", "")
    if not caption:
        return {"status": "failed", "error": "x caption block missing 'caption'", "at": _now_iso()}
    if len(caption) > 280:
        return {"status": "failed",
                "error": f"x caption too long ({len(caption)}>280 chars)", "at": _now_iso()}

    basename = video.stem

    try:
        with chrome_session() as page:
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded")

            # Bail clearly if not logged in (login form would appear).
            if "/login" in page.url or "/i/flow/login" in page.url:
                return {"status": "failed",
                        "error": "not logged into X — run setup_playwright_logins.py",
                        "at": _now_iso()}

            # File input for video. Hidden but reachable by selector.
            # We attach the file BEFORE typing the caption, because some
            # platforms auto-populate the composer with the filename when
            # a file is added — clear_and_type below wipes that.
            file_input = page.locator('input[data-testid="fileInput"]').first
            file_input.set_input_files(str(video))
            page.wait_for_timeout(2000)

            # Composer text area — clear any auto-populated text before typing.
            composer = page.locator('div[data-testid="tweetTextarea_0"]').first
            composer.wait_for(state="visible", timeout=20_000)
            clear_and_type(page, composer, caption, delay=8)

            # Wait for the upload to finalize. tweetButton is ALWAYS in the DOM
            # while the composer is open — what changes is `aria-disabled`.
            # X holds the button disabled until the video has been uploaded AND
            # transcoded. Waiting for `state="visible"` returns immediately while
            # the button is still disabled, which used to make us race the upload
            # and click a dead button. Poll aria-disabled instead.
            post_btn = page.locator('button[data-testid="tweetButton"]').first
            try:
                post_btn.wait_for(state="visible", timeout=120_000)
            except Exception:
                _shot(page, basename, "post_btn_missing")

            _wait_for_tweet_btn_enabled(page, post_btn, basename, timeout_s=180)

            scheduled_ok = False
            if scheduled_for:
                try:
                    # Click the calendar (schedule) icon. X has shipped the icon
                    # under several testids over the years — try the known set.
                    schedule_clicked = False
                    for sel in [
                        'button[data-testid="scheduleOption"]',
                        'button[data-testid="scheduledTweetIcon"]',
                        'button[aria-label="Schedule post"]',
                        'button[aria-label="Schedule"]',
                        'div[role="button"][aria-label*="Schedule" i]',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            btn.wait_for(state="visible", timeout=4000)
                            btn.click()
                            schedule_clicked = True
                            break
                        except Exception:
                            continue
                    if not schedule_clicked:
                        _shot(page, basename, "schedule_btn_missing")
                        raise RuntimeError("X schedule button not found")

                    # Wait for the schedule modal to actually render before we
                    # try to fill its selects. Without this the selects can be
                    # in the DOM but the React state isn't bound yet, so
                    # select_option silently no-ops.
                    page.wait_for_timeout(400)
                    try:
                        page.locator('div[role="dialog"], div[aria-labelledby*="modal" i]').first \
                            .wait_for(state="visible", timeout=8000)
                    except Exception:
                        # Some X builds don't wrap in role=dialog. Tolerate it.
                        pass
                    _shot(page, basename, "schedule_modal_open")

                    dt = datetime.fromisoformat(scheduled_for)
                    _x_set_schedule(page, dt)
                    _shot(page, basename, "schedule_modal_filled")

                    # Confirm. Two known testids; first wins.
                    confirm_clicked = False
                    for sel in [
                        'button[data-testid="scheduledConfirmationPrimaryAction"]',
                        'button[data-testid="confirmationSheetConfirm"]',
                    ]:
                        try:
                            btn = page.locator(sel).first
                            btn.wait_for(state="visible", timeout=6000)
                            btn.click()
                            confirm_clicked = True
                            break
                        except Exception:
                            continue
                    if not confirm_clicked:
                        # Last resort: a button labeled "Confirm" inside the dialog.
                        try:
                            page.get_by_role("button", name="Confirm").first.click(timeout=3000)
                            confirm_clicked = True
                        except Exception:
                            pass
                    if not confirm_clicked:
                        _shot(page, basename, "schedule_confirm_missing")
                        raise RuntimeError("X schedule confirm button not found")

                    page.wait_for_timeout(1200)
                    scheduled_ok = True
                except Exception as e:
                    # Dump the modal DOM so we can SEE the actual structure
                    # (real attribute names) for selector work next iteration.
                    try:
                        modal = page.locator('div[role="dialog"]').first
                        html = modal.evaluate("el => el.outerHTML")
                        dump_path = LOG_DIR / (
                            f"x_{basename}_schedule_dom_"
                            f"{int(datetime.now().timestamp())}.html"
                        )
                        dump_path.write_text(html)
                    except Exception:
                        pass
                    _shot(page, basename, "schedule_failed")
                    # CRITICAL: do NOT press Escape AND do NOT click the
                    # modal's X-close button. Both trigger X's "Save post?
                    # Save / Discard" draft dialog. Save sends to drafts
                    # (post never goes live); Discard kills the composer
                    # (tweetButton vanishes — vid 2 retry hit this exact
                    # cascade 2026-04-26 evening, leaving the post unpublished).
                    #
                    # The clean exit: click the schedule TOGGLE button
                    # AGAIN. The schedule icon in the composer footer is a
                    # toggle — re-clicking it dismisses the schedule modal
                    # without dirtying the composer state, so the Save
                    # dialog never opens.
                    toggled_off = False
                    for sel in [
                        'button[data-testid="scheduleOption"]',
                        'button[data-testid="scheduledTweetIcon"]',
                        'button[aria-label="Schedule post"]',
                        'button[aria-label="Schedule"]',
                        'div[role="button"][aria-label*="Schedule" i]',
                    ]:
                        try:
                            page.locator(sel).first.click(timeout=1500)
                            toggled_off = True
                            break
                        except Exception:
                            continue
                    if toggled_off:
                        page.wait_for_timeout(500)
                        _shot(page, basename, "schedule_toggle_off_clean")

            # SAFETY NET: if a "Save post?" draft dialog still showed up
            # somehow, we have no clean dismissal — Save sends to drafts,
            # Discard kills the composer. Best we can do is click Save (the
            # post lives as a draft and the user can manually publish it
            # later from x.com/drafts). Better than the alternative.
            try:
                save_dialog = page.get_by_text("Save post?", exact=True).first
                if save_dialog.is_visible(timeout=600):
                    _shot(page, basename, "save_dialog_appeared_unexpectedly")
                    save_btn = page.get_by_role(
                        "button", name="Save", exact=True
                    ).first
                    save_btn.click(timeout=2000)
                    page.wait_for_timeout(800)
                    _shot(page, basename, "save_dialog_saved_to_drafts")
                    return {
                        "status": "failed",
                        "error": "X scheduling failed AND the immediate-post "
                                 "fallback was blocked by a Save-post-as-draft "
                                 "dialog. The composer was saved to drafts — "
                                 "manually publish from x.com/drafts.",
                        "saved_to_drafts": True,
                        "at": _now_iso(),
                    }
            except Exception:
                pass

            # Click whatever the bottom-right button is — text is "Schedule"
            # if scheduled_ok, "Post" otherwise.
            final_btn = page.locator('button[data-testid="tweetButton"]').first
            final_btn.wait_for(state="visible", timeout=15_000)

            # On some clips X leaves a translucent overlay div (class r-ipm5af)
            # over the composer footer that intercepts pointer events on the
            # tweet button. Press Escape to dismiss any popovers/tooltips that
            # might be that overlay before clicking.
            #
            # CRITICAL: only press Escape on the IMMEDIATE-POST path. Pressing
            # Escape AFTER schedule_modal_confirm puts the composer in a
            # "dirty" state and X opens the Save post? / Save / Discard dialog
            # over the Schedule button — that dialog z-indexes above the
            # button so the next click is intercepted, falls back to
            # force=True, and lands on the dialog. Schedule never commits but
            # we still report status=scheduled because scheduled_ok is True.
            # That's the 2026-04-28 silent-fail regression (5/5 silent fails).
            if not scheduled_ok:
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(250)
                except Exception:
                    pass

            # Re-confirm the button is enabled (in case Escape closed something
            # that was holding it ready) and then click. Fall back to force
            # click if the overlay STILL intercepts pointer events — at this
            # point the button is verified enabled, so a force click hits the
            # right element regardless of the overlay.
            _wait_for_tweet_btn_enabled(page, final_btn, basename, timeout_s=20)
            try:
                final_btn.click(timeout=8000)
            except Exception:
                _shot(page, basename, "final_click_intercepted_force_fallback")
                final_btn.click(force=True, timeout=8000)

            # Wait for X to ACTUALLY commit the post before tearing down the
            # browser. The previous flat 4s sleep was too short for larger
            # clips — closing the tab mid-flight cancels the upload.
            #
            # Success signals (any one is enough):
            #   1. URL contains "/status/" (X navigated to the new post)
            #   2. The composer modal closed (tweetTextarea_0 detached)
            #   3. "Your post was sent" toast appeared
            #
            # Poll up to 30s. If none arrive, fall back to a 15s safety sleep
            # — long enough that even slow networks finish committing before
            # chrome_session() exits.
            url = _wait_for_post_committed(page, basename, timeout_s=30)

            label = "scheduled" if scheduled_ok else "posted_immediate"
            _shot(page, basename, label)

            if scheduled_ok:
                # Real verification: schedule_ok=True only proves the modal
                # confirm fired, NOT that the final Schedule click committed.
                # Look for the post-commit signals before reporting success:
                #   1. "Your post will be sent on..." success toast.
                #   2. Composer textarea detached (modal closed).
                # If neither, return failed — receipt routes to failed/ and
                # retry-failed re-runs only X.
                if not _verify_x_scheduled_commit(page, basename):
                    return {
                        "status": "failed",
                        "error": "X schedule confirmed in modal but final "
                                 "Schedule click did not commit (composer "
                                 "still open, no 'Your post will be sent on' "
                                 "toast). Check the *_scheduled_*.png "
                                 "screenshot — likely the Save post? dialog "
                                 "or another overlay intercepted the click.",
                        "scheduled_for": scheduled_for,
                        "scheduled_ok_in_modal": True,
                        "at": _now_iso(),
                    }
                return {
                    "status": "scheduled",
                    "url": None,
                    "scheduled_for": scheduled_for,
                    "at": _now_iso(),
                }
            else:
                return {
                    "status": "success",
                    "url": url,
                    "posted_at": _now_iso(),
                    "scheduling_skipped": scheduled_for is not None,
                    "at": _now_iso(),
                }
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _round_up_to_5_min(dt: datetime) -> datetime:
    """Round dt up to the next 5-minute boundary (X's minute dropdown only
    has 5-minute increments: 00, 05, 10, ..., 55).

    Round UP rather than to nearest, so we never land earlier than planned —
    that would risk scheduling in the past.
    """
    cleaned = dt.replace(second=0, microsecond=0)
    remainder = cleaned.minute % 5
    if remainder == 0:
        return cleaned
    return cleaned + timedelta(minutes=(5 - remainder))


def _x_set_schedule(page, dt: datetime) -> None:
    """Fill X's schedule selects from a datetime.

    DOM dump 2026-04-26 evening (`x_*_schedule_dom_*.html`) confirmed: X's
    schedule modal IS using native `<select>` elements after all. They're
    identified by `id="SELECTOR_1"` through `SELECTOR_6` (NOT by aria-label
    — which is why every prior attempt with `select[aria-label="Month"]`
    silently no-op'd). Labels are in sibling `<label>` elements with
    aria-labelledby pointing at them.

    Mapping (from the dump):
      SELECTOR_1 = Month     (value "1"-"12", text "January".."December")
      SELECTOR_2 = Day       (value "1"-"31", text "1".."31")
      SELECTOR_3 = Year      (value "2026"..)
      SELECTOR_4 = Hour      (value "1"-"12", 12-hour)
      SELECTOR_5 = Minute    (value "0"-"59" UNPADDED — every minute available,
                              there's no 5-minute increment limit)
      SELECTOR_6 = AM/PM     (value "am"/"pm" lowercase, text "AM"/"PM")
    """
    cleaned = dt.replace(second=0, microsecond=0)
    hour_12 = ((cleaned.hour - 1) % 12) + 1
    ampm_lower = "pm" if cleaned.hour >= 12 else "am"

    # Each entry: (field_label_for_error, list of selectors to try, value to set).
    # Selectors are tried in order; first one that works wins. Falls back from
    # the verified 2026-04-26 selector through the legacy aria-label patterns
    # in case X ships a different build later.
    fields = [
        ("Month",
         ['select#SELECTOR_1', 'select[aria-labelledby="SELECTOR_1_LABEL"]',
          'select[aria-label="Month"]'],
         str(cleaned.month)),
        ("Day",
         ['select#SELECTOR_2', 'select[aria-labelledby="SELECTOR_2_LABEL"]',
          'select[aria-label="Day"]'],
         str(cleaned.day)),
        ("Year",
         ['select#SELECTOR_3', 'select[aria-labelledby="SELECTOR_3_LABEL"]',
          'select[aria-label="Year"]'],
         str(cleaned.year)),
        ("Hour",
         ['select#SELECTOR_4', 'select[aria-labelledby="SELECTOR_4_LABEL"]',
          'select[aria-label="Hour"]'],
         str(hour_12)),
        ("Minute",
         ['select#SELECTOR_5', 'select[aria-labelledby="SELECTOR_5_LABEL"]',
          'select[aria-label="Minute"]'],
         str(cleaned.minute)),
        ("AM/PM",
         ['select#SELECTOR_6', 'select[aria-labelledby="SELECTOR_6_LABEL"]',
          'select[aria-label="AM/PM"]'],
         ampm_lower),
    ]

    failures = []
    for field_label, selectors, value in fields:
        if _x_set_select(page, selectors, value):
            continue
        failures.append((field_label, value))

    if failures:
        raise RuntimeError(
            f"X schedule: could not set fields {failures}. "
            f"Selectors drifted again — check the latest "
            f"x_*_schedule_dom_*.html dump in logs/screenshots/."
        )


def _x_set_select(page, selectors, value) -> bool:
    """Set a single `<select>` to `value` (the option's value attr) by trying
    each selector in order. Returns True on first success.

    Also tries the same value as a label string in case the option doesn't
    have a value= attr but only visible text (rare on X but cheap defense).
    """
    for sel in selectors:
        try:
            page.select_option(sel, value=value, timeout=1500)
            return True
        except Exception:
            pass
        try:
            page.select_option(sel, label=value, timeout=1200)
            return True
        except Exception:
            continue
    return False


def _x_set_field_legacy_DEAD(page, field, native_selectors, candidates) -> bool:
    """DEAD CODE — kept only for reference. Real implementation is _x_set_select.
    Custom-button-dropdown approach was based on a wrong reading of the
    schedule modal. The DOM dump 2026-04-26 confirmed X uses native selects.
    """
    # Strategy A: native <select>.
    for sel in native_selectors:
        for v in candidates:
            try:
                page.select_option(sel, value=v, timeout=1200)
                return True
            except Exception:
                pass
            try:
                page.select_option(sel, label=v, timeout=1200)
                return True
            except Exception:
                pass

    # Strategy B: custom button-style dropdown. Open the trigger, then
    # click the option whose text matches one of `candidates`.
    if not _x_open_field_dropdown(page, field):
        return False

    page.wait_for_timeout(350)

    # Listbox is now visible. Pick the first matching option.
    for v in candidates:
        for getter in (
            lambda v=v: page.get_by_role("option", name=v, exact=True).first,
            lambda v=v: page.get_by_role("menuitem", name=v, exact=True).first,
            lambda v=v: page.locator(f'[role="option"]:has-text("{v}")').first,
            lambda v=v: page.locator(f'[role="menuitem"]:has-text("{v}")').first,
            # Last resort: any visible element with EXACT text match inside
            # an open listbox/menu container.
            lambda v=v: page.locator(
                f'[role="listbox"] *:text-is("{v}"), '
                f'[role="menu"] *:text-is("{v}")'
            ).first,
        ):
            try:
                el = getter()
                el.wait_for(state="visible", timeout=1200)
                el.click()
                page.wait_for_timeout(150)
                return True
            except Exception:
                continue

    # Couldn't find the option. Try to close any open dropdown so the next
    # field can open cleanly.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def _x_open_field_dropdown(page, field) -> bool:
    """Click the trigger button for a named X schedule field.

    Tries multiple selectors: aria-label, role=combobox, then a JS-direct
    walk that finds any clickable element inside the schedule dialog whose
    aria-label or labelledby text matches `field`.
    """
    selectors = [
        f'button[aria-label="{field}"]',
        f'div[role="button"][aria-label="{field}"]',
        f'[role="combobox"][aria-label="{field}"]',
        f'[aria-label="{field}"][role]',
        # X often labels the trigger via a sibling <label>; we can't easily
        # CSS-select that, so the JS walker below handles it.
    ]
    for sel in selectors:
        try:
            page.locator(sel).first.click(timeout=1500)
            return True
        except Exception:
            continue

    # JS-direct walker. Look inside the schedule dialog for any focusable
    # element whose own or labelledby text equals `field` (e.g. "Month").
    try:
        clicked = page.evaluate(
            """(field) => {
                const root = document.querySelector(
                    'div[role="dialog"], div[aria-labelledby*="modal" i]'
                ) || document.body;
                // Find candidate clickables in the dialog.
                const els = Array.from(root.querySelectorAll(
                    'button, [role="button"], [role="combobox"], [tabindex="0"]'
                )).filter(el => el.offsetParent !== null);
                const wanted = field.toLowerCase();
                // Pass 1: aria-label exact match.
                for (const el of els) {
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (aria === wanted) { el.click(); return 'aria-exact'; }
                }
                // Pass 2: aria-label contains.
                for (const el of els) {
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (aria.includes(wanted)) { el.click(); return 'aria-contains'; }
                }
                // Pass 3: aria-labelledby — resolve and compare.
                for (const el of els) {
                    const labId = el.getAttribute('aria-labelledby');
                    if (!labId) continue;
                    const lab = document.getElementById(labId);
                    if (lab && (lab.textContent || '').trim().toLowerCase() === wanted) {
                        el.click(); return 'labelledby';
                    }
                }
                // Pass 4: positional — find the <label> with matching text,
                // then click its sibling/descendant button.
                const labels = Array.from(root.querySelectorAll('label, span, div'))
                    .filter(el => el.offsetParent !== null);
                for (const lab of labels) {
                    const text = (lab.textContent || '').trim().toLowerCase();
                    if (text !== wanted) continue;
                    // Look for a button in the same row (next sibling or parent's children).
                    let row = lab.parentElement;
                    for (let i = 0; i < 3 && row; i++) {
                        const btn = row.querySelector(
                            'button, [role="button"], [role="combobox"]'
                        );
                        if (btn && btn.offsetParent !== null) {
                            btn.click(); return 'positional';
                        }
                        row = row.parentElement;
                    }
                }
                return null;
            }""",
            field,
        )
        return bool(clicked)
    except Exception:
        return False
