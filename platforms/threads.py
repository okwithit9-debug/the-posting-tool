"""
platforms/threads.py — Threads video poster (Playwright).

Goes through the native threads.com composer (NOT Meta Business Suite — the
user's IG/Threads aren't linked to the Business Suite they have access to).

Threads' native composer's scheduling support is uncertain on personal
accounts — see logic at end of post() that falls back to immediate post if
the schedule affordance isn't found.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session, clear_and_type
from platforms._pickers import pick_date_threads_style, set_threads_time
from platforms._preflight import (
    failure_receipt,
    live_surface_gate,
    native_list_proof_or_fail,
)
from platforms._safety_locks import pause_on_wall

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)


# 2026-06-04: Threads removed scheduling from the web composer. The Post
# Options popover (footer "sliders" button) now contains only
# Audience/Reply controls (Who can reply, Also share on…, Review and
# approve replies) — no Schedule, no clock icon, no calendar. The header
# More kebab also no longer surfaces a Schedule menuitem. Confirmed via
# two smoke tests on 2026-06-04 + full DOM dumps in logs/screenshots/
# threads_*_post_options_popover_dom_*.html.
#
# Until Meta brings scheduling back to the web composer OR we route
# Threads through Buffer/Threads API, post() short-circuits any
# scheduled_for request and posts immediately, tagging the receipt
# `scheduling_skipped: true` + `scheduling_unavailable_reason` so the
# audit trail is intact. Flip this to False to re-enable the legacy
# schedule-button hunt (kept intact for the day Threads rolls it back).
SCHEDULING_DISABLED = False
SCHEDULING_UNAVAILABLE_REASON = "ui_removed_2026-06"


# 2026-08-07: Threads enforces a hard 25-scheduled-post cap per account.
# When the queue is full, clicking the final Schedule button spawns a
# modal titled "Can't schedule thread" with the body text "You can
# schedule up to 25 threads. Cancel one to schedule more." The composer
# stays open (unsubmitted) behind it. Detected in run 42 across 4
# clips — all failed with the misleading "silent-commit" error until we
# looked at the screenshots. The verifiers below now detect this modal
# explicitly, dismiss it, and raise ThreadsQueueCapReached so callers
# emit a distinct, actionable error. Retry loops will keep failing
# until the user drops posts from the scheduled queue or waits for
# some to publish.
class ThreadsQueueCapReached(RuntimeError):
    """Raised when Threads' 25-scheduled-post cap modal appears on submit."""
    pass


def _threads_queue_cap_modal_visible(page) -> bool:
    """Return True iff Threads' scheduled-queue-full modal is on screen."""
    for locator_fn in (
        lambda: page.get_by_text("Can't schedule thread", exact=True).first,
        lambda: page.get_by_text("You can schedule up to 25 threads", exact=False).first,
    ):
        try:
            if locator_fn().is_visible(timeout=200):
                return True
        except Exception:
            continue
    return False


def _dismiss_threads_queue_cap_modal(page) -> None:
    """Click OK on the cap modal so composer state is clean for the next
    attempt / for any auto-retry cleanup."""
    for locator_fn in (
        lambda: page.get_by_role("button", name="OK", exact=True).first,
        lambda: page.locator('div[role="dialog"] button:has-text("OK")').first,
    ):
        try:
            btn = locator_fn()
            btn.click(timeout=1500)
            return
        except Exception:
            continue

# When SCHEDULING_DISABLED is True, every "scheduled" post collapses to
# an immediate post. Retry sweeps that only re-run Threads (no other
# platforms slowing things down) can therefore fire posts back-to-back
# every ~60-90s. Add a self-imposed sleep at the end of each post() so
# even a tight retry loop respects Threads' (tightened in 2026) rate
# limits. Conservative — adjust down if dispatches feel sluggish.
IMMEDIATE_POST_BACKOFF_SECONDS = 25


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    path = LOG_DIR / f"threads_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def _dismiss_save_to_drafts_modal(page, basename: str) -> bool:
    """If Threads' 'Save to drafts?' modal is up, click Cancel to stay in
    the composer.

    This dialog opens when Escape is pressed inside the composer (Threads
    interprets Escape as 'close composer'). Verified via screenshot
    2026-04-26 evening — the modal has three buttons stacked: Save /
    Don't save / Cancel. We want Cancel — Save would close the composer,
    Don't save would discard our typed caption + uploaded video.

    Returns True if the modal was found and dismissed, False otherwise.
    """
    # Cheap visibility check first — bail fast if no modal.
    try:
        title = page.get_by_text("Save to drafts?", exact=True).first
        if not title.is_visible(timeout=400):
            return False
    except Exception:
        return False

    _shot(page, basename, "save_dialog_visible")
    for getter in (
        lambda: page.get_by_role("button", name="Cancel", exact=True).first,
        lambda: page.get_by_text("Cancel", exact=True).first,
    ):
        try:
            btn = getter()
            btn.wait_for(state="visible", timeout=1500)
            btn.click(timeout=1500)
            page.wait_for_timeout(400)
            _shot(page, basename, "save_dialog_cancelled")
            return True
        except Exception:
            continue
    return False


def _threads_calendar_visible(page) -> bool:
    """Return True if Threads' schedule calendar popover is currently visible.

    The schedule popover renders three load-bearing affordances we can use
    as proof of life:
      1. A "Done" button (committing the schedule).
      2. A calendar header reading "<Month> <Year>" (e.g. "April 2026").
      3. A grid of numeric day buttons.

    Variable bookkeeping (kebab_clicked / direct_clicked) is unreliable
    because Playwright's click() can raise AFTER the click event already
    fired (race condition observed in the 2026-04-25 batch — every Threads
    post ran kebab_missing yet the screenshot showed the popover open).
    This DOM-state check is the truth.
    """
    # Cheapest signal first: the Done button is unique to the schedule popover.
    try:
        if page.get_by_role("button", name="Done", exact=True).first.is_visible(timeout=400):
            return True
    except Exception:
        pass
    # Next: a "Month YYYY" header.
    try:
        html = page.evaluate(
            """() => {
                const months = ['January','February','March','April','May','June',
                                'July','August','September','October','November','December'];
                const all = document.querySelectorAll('div, span, h1, h2, h3, h4, h5, h6');
                for (const el of all) {
                    if (el.children.length > 0) continue;
                    const t = (el.textContent || '').trim();
                    for (const m of months) {
                        if (t.startsWith(m + ' ')) {
                            const parts = t.split(' ');
                            if (parts.length === 2 && /^20\\d{2}$/.test(parts[1])) return true;
                        }
                    }
                }
                return false;
            }"""
        )
        if html:
            return True
    except Exception:
        pass
    return False


def _verify_threads_immediate_commit(page, basename: str, *, timeout_s: int = 20) -> bool:
    """After clicking the final Post button on an IMMEDIATE Threads post,
    prove the post actually committed before reporting success.

    Threads tears down the composer dialog on a successful submit and
    typically surfaces a brief confirmation toast ("Posted", "Thread
    posted", "Shared", etc.). Two acceptable success signals (whichever
    fires first wins):

      1. The composer's contenteditable detaches or hides.
      2. A status/alert toast containing 'posted', 'shared', or 'published'.

    Hard-fail short-circuits:
      - "Save to drafts?" modal appears — submit click was intercepted.
      - The composer dialog stays open AND a SECOND empty composer is now
        visible (the bug we just fixed: the wrong Post button was clicked
        on the feed background, which opened a fresh New thread composer
        while leaving the original composer untouched). Detect by counting
        contenteditable divs — original composer has the typed caption; a
        new empty one would push the visible count >=2 and the original
        editor is still attached.

    Returns True on commit, False otherwise. Caller treats False as a
    failed post — receipt routes to failed/ for proper retry instead of
    silently lying about success. Analogous to
    _verify_threads_scheduled_commit() — same problem domain, separated
    so each path can tune its own success-signal patterns.

    Why this exists (2026-06-08): the 2026-06-04 Option A patch shipped
    publish-immediately for Threads but copied the ORIGINAL immediate-
    post code path verbatim, which had no commit verification at all —
    it did wait_for_timeout(5000) + return success. Receipts claimed
    37 successful posts across runs 5-8 + retry sweeps; the live
    Threads profile showed zero of them landing. Symptoms exactly match
    the X regression patched 2026-04-28 — silent success on intercepted
    submit click.
    """
    deadline = time.monotonic() + float(timeout_s)
    composer_editor = page.locator('div[contenteditable="true"]').first
    toast_selector = 'div[role="status"], div[role="alert"]'
    save_dialog_text = page.get_by_text("Save to drafts?", exact=True).first
    while time.monotonic() < deadline:
        # Hard fail: Save-to-drafts dialog means submit didn't commit.
        try:
            if save_dialog_text.is_visible(timeout=200):
                _shot(page, basename, "immediate_commit_save_dialog")
                return False
        except Exception:
            pass
        # Hard fail (queue-cap): scheduled-queue-full modal — see
        # ThreadsQueueCapReached docstring at top of file. Rare on
        # immediate posts (the cap is about scheduled queue, not live)
        # but check anyway in case Threads reuses the modal.
        if _threads_queue_cap_modal_visible(page):
            _shot(page, basename, "immediate_commit_queue_cap_modal")
            _dismiss_threads_queue_cap_modal(page)
            raise ThreadsQueueCapReached(
                "Threads scheduled-queue cap reached (25/25). "
                "Cancel scheduled threads or wait for some to publish."
            )
        # Signal 1: confirmation toast.
        try:
            t = page.locator(toast_selector).first
            if t.is_visible(timeout=200):
                txt = (t.inner_text(timeout=300) or "").lower()
                if any(s in txt for s in ("posted", "shared", "published")):
                    return True
        except Exception:
            pass
        # Signal 2: composer editor detached/hidden.
        try:
            if composer_editor.count() == 0:
                return True
            if not composer_editor.is_visible(timeout=200):
                return True
        except Exception:
            return True  # detached locator throws — that's success
        page.wait_for_timeout(300)
    _shot(page, basename, "immediate_commit_unverified")
    return False


def _verify_threads_scheduled_commit(page, basename: str, *, timeout_s: int = 20) -> bool:
    """After clicking the final submit button on a SCHEDULED Threads post,
    prove the schedule actually committed before reporting success.

    Threads closes the composer dialog on a successful submit (whether
    scheduled or immediate). For a scheduled post we additionally expect
    a confirmation toast. Two acceptable success signals (whichever fires
    first wins):

      1. A status/alert toast whose text contains 'scheduled', 'will be
         posted', or 'queued' (Threads' confirmation banners — wording
         varies by build, so match loosely).
      2. The composer's contenteditable detaches or hides — Threads tears
         down the modal on a successful submit.

    Hard-fail short-circuit: if Threads' "Save to drafts?" modal appears
    while we're polling, the submit click was intercepted (the composer
    is still dirty, so the schedule never committed). Return False
    immediately rather than waiting out the full timeout.

    Returns True on commit, False on timeout / save-dialog appearance.
    Caller treats False as a failed schedule even though the popover-
    level `scheduled_ok` was True — analogous to the X regression patched
    2026-04-28 (`_verify_x_scheduled_commit`).
    """
    deadline = time.monotonic() + float(timeout_s)
    composer_editor = page.locator('div[contenteditable="true"]').first
    toast_selector = 'div[role="status"], div[role="alert"]'
    save_dialog_text = page.get_by_text("Save to drafts?", exact=True).first
    while time.monotonic() < deadline:
        # Hard fail: Save-to-drafts dialog means submit didn't commit.
        try:
            if save_dialog_text.is_visible(timeout=200):
                _shot(page, basename, "schedule_commit_save_dialog")
                return False
        except Exception:
            pass
        # Hard fail (queue-cap): scheduled-queue-full modal — see
        # ThreadsQueueCapReached docstring at top of file. Common on
        # scheduled posts once the account has 25 in the queue.
        if _threads_queue_cap_modal_visible(page):
            _shot(page, basename, "schedule_commit_queue_cap_modal")
            _dismiss_threads_queue_cap_modal(page)
            raise ThreadsQueueCapReached(
                "Threads scheduled-queue cap reached (25/25). "
                "Cancel scheduled threads or wait for some to publish."
            )
        # Signal 1: confirmation toast.
        try:
            t = page.locator(toast_selector).first
            if t.is_visible(timeout=200):
                txt = (t.inner_text(timeout=300) or "").lower()
                if any(s in txt for s in ("scheduled", "will be posted", "queued")):
                    return True
        except Exception:
            pass
        # Signal 2: composer editor detached/hidden.
        try:
            if composer_editor.count() == 0:
                return True
            if not composer_editor.is_visible(timeout=200):
                return True
        except Exception:
            return True  # detached locator throws — that's success
        page.wait_for_timeout(300)
    _shot(page, basename, "schedule_commit_unverified")
    return False


def _threads_native_scheduled_list_has_caption(page, basename: str, caption: str) -> bool:
    """Open Schedule from header ⋯ next to Drafts. Never goto /scheduled. Never Escape."""
    snippet = (caption or "").strip()[:40]
    if "threads.com/scheduled" in (page.url or "") or "threads.net/scheduled" in (page.url or ""):
        _shot(page, basename, "threads_scheduled_url_404")
        return False
    try:
        drafts = page.locator('svg[aria-label="Drafts"]').last
        drafts.wait_for(state="visible", timeout=4000)
        kebab = page.locator(':is(button,[role="button"]):has(svg[aria-label="More"])').last
        kebab.click(timeout=3000)
        page.wait_for_timeout(400)
        item = page.get_by_text("Schedule", exact=False).first
        item.click(timeout=3000)
        page.wait_for_timeout(800)
        _shot(page, basename, "native_scheduled_list_open")
        if snippet:
            try:
                if page.get_by_text(snippet, exact=False).first.is_visible(timeout=4000):
                    return True
            except Exception:
                pass
        body = (page.inner_text("body") or "")
        return bool(snippet and snippet in body)
    except Exception:
        _shot(page, basename, "native_scheduled_list_unopened")
        return False


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    caption = (captions or {}).get("caption", "")
    topic = (captions or {}).get("topic")
    if not caption:
        return {"status": "failed", "error": "threads caption block missing 'caption'", "at": _now_iso()}
    if len(caption) > 500:
        caption = caption[:497] + "..."

    basename = video.stem

    # ------------------------------------------------------------------
    # 2026-06-04 Threads web composer no longer has a Schedule UI. Capture
    # the caller's intended slot for the audit trail, then null out
    # scheduled_for so the existing schedule-button hunt is skipped and
    # we go straight to immediate post. See SCHEDULING_DISABLED comment
    # at top of file.
    # ------------------------------------------------------------------
    scheduling_target = None
    scheduling_skipped = False
    if SCHEDULING_DISABLED and scheduled_for:
        return failure_receipt(
            pause_on_wall(
                "missing_schedule_control",
                surface="threads",
                detail=(
                    f"Threads Schedule UI unavailable ({SCHEDULING_UNAVAILABLE_REASON}). "
                    "Human wall. Never Post now."
                ),
            ),
            scheduling_unavailable_reason=SCHEDULING_UNAVAILABLE_REASON,
            scheduling_target=scheduled_for,
        )

    try:
        with chrome_session() as page:
            page.goto("https://www.threads.com/", wait_until="domcontentloaded")

            if "/login" in page.url:
                return {"status": "failed",
                        "error": "not logged into Threads — run setup_playwright_logins.py",
                        "at": _now_iso()}

            gate = live_surface_gate(page, "threads", caption=caption, stage="landing")
            if gate:
                _shot(page, basename, "preflight_failed")
                return gate

            if not scheduled_for:
                return failure_receipt(
                    pause_on_wall(
                        "missing_schedule_control",
                        surface="threads",
                        detail="Native Schedule only. Never Post now.",
                    )
                )

            # Open the composer. Try multiple ways because Threads' UI varies.
            # Most reliable: click the "What's new?" inline placeholder at the
            # top of the feed (it expands into the full composer modal).
            opened = False
            for try_open in [
                lambda: page.get_by_placeholder("What's new?").first,
                lambda: page.locator('div[role="button"][aria-label*="Create" i]').first,
                lambda: page.locator('svg[aria-label="Create"]').first,
                lambda: page.locator('a[aria-label*="Create" i]').first,
                lambda: page.get_by_role("button", name="Create").first,
                lambda: page.get_by_role("link", name="Create").first,
            ]:
                try:
                    el = try_open()
                    el.wait_for(state="visible", timeout=3000)
                    el.click()
                    page.wait_for_timeout(800)
                    opened = True
                    break
                except Exception:
                    continue
            if not opened:
                # Last resort: keyboard shortcut.
                page.keyboard.press("p")
                page.wait_for_timeout(1000)

            # Type caption.
            textarea = page.locator('div[contenteditable="true"]').first
            textarea.wait_for(state="visible", timeout=10_000)
            clear_and_type(page, textarea, caption, delay=10)

            # Attach video — find the file input inside the composer.
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(str(video))

            # Wait for the upload to finish (preview appears).
            page.wait_for_timeout(5000)

            # Topic. Threads renders "Add a topic" as inline text inside
            # the username row (e.g. "YOUR_HANDLE > Add a topic"), NOT
            # as a <button> or labeled control — so Playwright's role/text
            # selectors miss it. We use the same JS-direct strategy that
            # worked for the kebab: walk the dialog DOM, find any clickable
            # ancestor of an element whose text contains "Add a topic",
            # and click that.
            topic_applied = False
            if topic:
                topic_clicked = False
                # Set if the new-input path successfully filled+committed the
                # topic — skips the legacy keyboard-type-and-click block
                # which would otherwise double-type the topic.
                topic_filled_via_new_path = False

                # ------------------------------------------------------------
                # 2026-06-15 NEW topic picker path. Meta renamed the inline
                # "Add a topic" row to "Community or topic" AND changed it
                # from delegated text-node to a real <input type="search"
                # placeholder="Community or topic">. The old coordinate-click
                # trick no longer applies — we can use a proper input now.
                # Try this FIRST; if it works, skip the legacy block.
                # Investigation: 2026-04 had 76 successes, 2026-05 mixed
                # (114 ok, 124 missing as the rename rolled out), 2026-06
                # had 0 successes and 95 misses — confirming the legacy
                # selectors no longer match. Live DOM dump from
                # threads.com/@YOUR_HANDLE new-composer (2026-06-15):
                #   textbox "Community or topic" placeholder="Community or topic" type="search"
                # ------------------------------------------------------------
                topic_input = None
                for getter in (
                    lambda: page.get_by_placeholder("Community or topic").first,
                    lambda: page.locator(
                        'input[placeholder="Community or topic"]'
                    ).first,
                    lambda: page.locator(
                        'input[placeholder*="topic" i][type="search"]'
                    ).first,
                ):
                    try:
                        el = getter()
                        el.wait_for(state="visible", timeout=2500)
                        topic_input = el
                        break
                    except Exception:
                        continue

                if topic_input is not None:
                    try:
                        topic_input.click(timeout=2000)
                        page.wait_for_timeout(250)
                        # fill() resets the value then types — cleaner than
                        # keyboard.type() for a true <input> element.
                        topic_input.fill(topic)
                        page.wait_for_timeout(900)
                        _shot(page, basename, "topic_input_filled")
                        # Suggestion list. Threads renders matches as
                        # role=option rows under the input.
                        suggestion_clicked = False
                        for sgetter in (
                            lambda: page.get_by_role("option", name=topic).first,
                            lambda: page.locator(
                                f'[role="option"]:has-text("{topic}")'
                            ).first,
                            lambda: page.get_by_text(f"#{topic}", exact=True).first,
                            lambda: page.get_by_text(topic, exact=True).first,
                        ):
                            try:
                                s = sgetter()
                                s.wait_for(state="visible", timeout=2000)
                                s.click()
                                suggestion_clicked = True
                                _shot(page, basename, "topic_suggestion_clicked")
                                break
                            except Exception:
                                continue
                        # Final fallback for the new input: Enter commits.
                        if not suggestion_clicked:
                            try:
                                page.keyboard.press("Enter")
                                page.wait_for_timeout(400)
                            except Exception:
                                pass
                        # Verify via the row-renders-the-topic check below.
                        # If verification passes, we set topic_applied=True
                        # and skip the legacy locator hunt. Mark "clicked"
                        # so the verify block runs.
                        topic_clicked = True
                        topic_filled_via_new_path = True
                    except Exception as e:
                        # New-input path errored — fall through to legacy.
                        _shot(page, basename, "topic_input_path_errored")
                        topic_clicked = False
                        topic_filled_via_new_path = False

                # Legacy path: try the inline "Add a topic" text (kept for
                # any cases where Meta rolls back, or A/B variants serve
                # the old composer).
                for getter in (
                    lambda: page.get_by_role("button", name="Add a topic").first,
                    lambda: page.get_by_text("Add a topic", exact=True).first,
                    lambda: page.locator('[role="button"]:has-text("Add a topic")').first,
                    lambda: page.locator('span:has-text("Add a topic")').first,
                    lambda: page.locator(':text("Add a topic")').first,
                ):
                    if topic_clicked:
                        break  # new path already opened the picker
                    try:
                        el = getter()
                        el.wait_for(state="visible", timeout=2000)
                        el.click()
                        topic_clicked = True
                        _shot(page, basename, "topic_clicked_via_locator")
                        break
                    except Exception:
                        continue

                # If standard locators missed, fall back to coordinate click.
                # Why coordinates instead of element.click(): Threads attaches
                # the topic-row click handler via event delegation on a far
                # ancestor (verified — walking up 10 levels from the "Add a
                # topic" text node found no element whose own listeners would
                # fire `el.click()`). A REAL DOM click at the screen
                # coordinate of the row triggers the bubbled event, which
                # the delegated handler catches.
                if not topic_clicked:
                    try:
                        bbox = page.evaluate("""() => {
                            const root = document.querySelector('[role="dialog"]') || document.body;
                            // Find the leaf element whose own text is "Add a topic"
                            // (not descendants — we want a leaf so we get a tight bbox).
                            const all = root.querySelectorAll('*');
                            let target = null;
                            for (const el of all) {
                                if (el.children.length > 0) continue;
                                const text = (el.textContent || '').trim();
                                if (text === 'Add a topic' || text === 'Add a topic...' ||
                                    text.toLowerCase() === 'add a topic') {
                                    target = el;
                                    break;
                                }
                            }
                            if (!target) return null;
                            // Dump the row's HTML (5 levels up) so we have
                            // ground truth on the actual DOM structure.
                            let row = target;
                            for (let i = 0; i < 5 && row.parentElement; i++) {
                                row = row.parentElement;
                            }
                            const r = target.getBoundingClientRect();
                            return {
                                x: r.left + r.width / 2,
                                y: r.top + r.height / 2,
                                w: r.width,
                                h: r.height,
                                rowHtml: row.outerHTML.slice(0, 4000),
                            };
                        }""")
                        if bbox:
                            # Save the row HTML for debugging.
                            try:
                                dump_path = LOG_DIR / (
                                    f"threads_{basename}_topic_row_dom_"
                                    f"{int(datetime.now().timestamp())}.html"
                                )
                                dump_path.write_text(bbox.get("rowHtml", ""))
                            except Exception:
                                pass
                            # Click at the actual coordinates — triggers real
                            # DOM click event that bubbles to delegated handler.
                            page.mouse.click(bbox["x"], bbox["y"])
                            page.wait_for_timeout(500)
                            topic_clicked = True
                            _shot(page, basename, "topic_clicked_via_coords")
                    except Exception:
                        pass
                if topic_clicked:
                    # The new-input path may have already filled and
                    # committed the topic above. If so, skip the legacy
                    # keyboard.type() block — otherwise we'd double-type.
                    if not topic_filled_via_new_path:
                        page.wait_for_timeout(400)
                        # Type the topic; Threads should show a suggestion list.
                        page.keyboard.type(topic, delay=15)
                        page.wait_for_timeout(900)
                        # Click the first suggestion that contains our text.
                        # Try a couple of locator shapes for the suggestion row.
                        for getter in (
                            lambda: page.get_by_role("option", name=topic).first,
                            lambda: page.get_by_role("button", name=topic).first,
                            # "#Topic" is a typical visible label for an
                            # autocomplete suggestion — try with and without #.
                            lambda: page.get_by_text(f"#{topic}", exact=True).first,
                            lambda: page.get_by_text(topic, exact=True).first,
                            # Last-resort: any clickable in the suggestion popup.
                            lambda: page.locator(f'[role="option"]:has-text("{topic}")').first,
                        ):
                            try:
                                el = getter()
                                el.wait_for(state="visible", timeout=2000)
                                el.click()
                                topic_applied = True
                                break
                            except Exception:
                                continue
                        # Final fallback: press Enter (creates the topic if Threads
                        # treats the typed string as a new tag — works on some
                        # accounts, no-op on others).
                        if not topic_applied:
                            try:
                                page.keyboard.press("Enter")
                                page.wait_for_timeout(300)
                                # DO NOT set topic_applied=True here. The Enter
                                # press is a HOPE, not a confirmation — on personal
                                # accounts the topic field commits no value and
                                # the row reverts to "Add a topic". The verification
                                # block below is the truth signal.
                            except Exception:
                                pass

                    # ---- VERIFY the topic actually applied. ----
                    # User reported (2026-04-26 dispatch) that topics were
                    # missing from posted threads despite topic_applied=True.
                    # Cause: the Enter-key fallback was setting the flag
                    # without checking the result. Now we read back the row
                    # and confirm it shows the topic, not "Add a topic".
                    page.wait_for_timeout(400)
                    try:
                        verified = page.evaluate(
                            """({topicName}) => {
                                const root = document.querySelector('[role="dialog"]') || document.body;
                                // Look for any visible text node showing the topic
                                // ("#Topic") OR confirming we're past the placeholder.
                                // 2026-06-15: include new placeholder text
                                // ("Community or topic") AND check input value
                                // since the new picker is a real <input>.
                                const texts = [];
                                const all = root.querySelectorAll('*');
                                for (const el of all) {
                                    if (el.children.length > 0) continue;
                                    const t = (el.textContent || '').trim();
                                    if (!t) continue;
                                    texts.push(t);
                                }
                                // Old placeholder OR new placeholder visible == still unfilled.
                                const hasPlaceholder =
                                    texts.includes('Add a topic') ||
                                    texts.includes('Community or topic');
                                const hasTopic = texts.some(
                                    t => t === '#' + topicName ||
                                         t === topicName ||
                                         t.toLowerCase() === ('#' + topicName).toLowerCase()
                                );
                                // Also check the topic <input> value — if user
                                // typed the topic but suggestion didn't commit,
                                // the input retains the typed text and we should
                                // NOT count that as applied.
                                const topicInput = root.querySelector(
                                    'input[placeholder="Community or topic"]'
                                );
                                const inputValue = topicInput ? topicInput.value : '';
                                const inputHasTopic =
                                    inputValue &&
                                    inputValue.toLowerCase() === topicName.toLowerCase();
                                return {
                                    hasPlaceholder,
                                    hasTopic,
                                    inputValue,
                                    inputHasTopic,
                                };
                            }""",
                            {"topicName": topic},
                        )
                        if verified.get("hasTopic") and not verified.get("hasPlaceholder"):
                            topic_applied = True
                            _shot(page, basename, "topic_applied_verified")
                        else:
                            topic_applied = False
                            _shot(page, basename, "topic_apply_unverified")
                            # CRITICAL: do NOT press Escape here. Threads
                            # interprets Escape in the composer as "close
                            # composer", opening a "Save to drafts?" modal
                            # whose backdrop blocks every subsequent click —
                            # killing the schedule trigger search. Verified
                            # via screenshot 2026-04-26 evening (the
                            # before_schedule_trigger_search shot showed the
                            # Save dialog up).
                            #
                            # Instead: click safely into the caption
                            # contenteditable to defocus the topic field
                            # without triggering the close-composer key.
                            try:
                                page.locator(
                                    'div[contenteditable="true"]'
                                ).first.click(timeout=1500)
                                page.wait_for_timeout(200)
                            except Exception:
                                pass
                    except Exception:
                        # Couldn't introspect — keep whatever flag we had.
                        pass
                else:
                    _shot(page, basename, "topic_button_missing")

            # Scheduling — Threads' personal-account composer DOES support
            # scheduling. The trigger location varies between Threads builds:
            #
            #   (A) Older: kebab (…) in composer header → "Schedule…" menuitem.
            #   (B) Current (verified 2026-04-26 via screenshot): a clock /
            #       schedule icon button directly in the composer HEADER row
            #       (right side, near the drafts icon and emoji icon).
            #
            # Both end up at the same calendar popover: header "Month YYYY"
            # with < > chevrons, 7-col day grid (Sun..Sat) with numeric
            # cells, then a time row "HH : MM AM/PM" and a Done button.
            #
            # Strategy: try ALL trigger paths (clock icon first, then kebab),
            # and after the loop check whether the calendar popover became
            # visible — that's the real success signal. The previous code
            # raised when the kebab couldn't be found by aria-label, even
            # though the popover was actually open from a sibling click
            # that succeeded but returned an exception (race condition).
            scheduled_ok = False
            if scheduled_for:
                try:
                    # SAFETY: dismiss any "Save to drafts?" / "Discard
                    # changes?" modal that may have opened from a stray
                    # Escape press earlier in this run (e.g., the topic
                    # apply path's defocus). Click Cancel to keep us in
                    # the composer — Save would close it, Don't save would
                    # discard our work.
                    _dismiss_save_to_drafts_modal(page, basename)

                    # Screenshot the composer modal first so we can SEE
                    # what's there even before we try to click anything.
                    _shot(page, basename, "before_schedule_trigger_search")

                    # Wait a beat — composer header icons can render late
                    # (after the file preview finishes loading).
                    page.wait_for_timeout(1500)

                    # Scope to the composer container so we don't match
                    # icons in the feed background.
                    #
                    # 2026-06-12: the composer is NO LONGER role="dialog".
                    # The popover dump from tonight's smoke test shows no
                    # visible [role="dialog"] anywhere — the composer wrapper
                    # carries role="menu" (aria-label=null). That made scope
                    # fall back to `page`, so the candidate-name loop clicked
                    # the FIRST "More"-named button on the whole page
                    # (sidebar/feed), never the composer's header kebab.
                    # Identify the composer by a feature unique to it: the
                    # Drafts icon in its header.
                    dialog = None
                    for sel in ('[role="dialog"]', '[role="menu"]'):
                        try:
                            cand = page.locator(sel).filter(
                                has=page.locator('svg[aria-label="Drafts"]')
                            ).first
                            cand.wait_for(state="visible", timeout=2000)
                            dialog = cand
                            break
                        except Exception:
                            continue
                    if dialog is None:
                        # Legacy fallback: plain role=dialog (pre-2026-06 builds).
                        try:
                            cand = page.get_by_role("dialog").first
                            cand.wait_for(state="visible", timeout=2000)
                            dialog = cand
                        except Exception:
                            dialog = None
                    scope = dialog if dialog else page
                    _shot(
                        page, basename,
                        "composer_scope_found" if dialog is not None
                        else "composer_scope_MISSING_page_fallback",
                    )

                    # ---- Path A: direct schedule/clock icon in composer header. ----
                    # The current Threads build (2026-04-26) renders the
                    # schedule trigger as a clock icon directly in the dialog
                    # header, NOT behind a kebab. Try it first.
                    direct_clicked = False
                    for getter in (
                        lambda: scope.get_by_role("button", name="Schedule").first,
                        lambda: scope.get_by_role("button", name="Schedule post").first,
                        lambda: scope.get_by_role("button", name="Schedule…").first,
                        lambda: scope.locator('button[aria-label="Schedule"]').first,
                        lambda: scope.locator('button[aria-label="Schedule post"]').first,
                        lambda: scope.locator(
                            '[role="button"][aria-label*="schedule" i]'
                        ).first,
                        lambda: scope.locator(
                            'button:has(svg[aria-label*="schedule" i])'
                        ).first,
                        lambda: scope.locator(
                            'button:has(svg[aria-label*="clock" i])'
                        ).first,
                    ):
                        try:
                            el = getter()
                            el.wait_for(state="visible", timeout=1200)
                            el.click()
                            direct_clicked = True
                            _shot(page, basename, "schedule_icon_clicked_direct")
                            break
                        except Exception:
                            continue

                    # ---- Path B: kebab / Post Options → Schedule… menu. ----
                    # Only attempt if path A didn't already open the popover.
                    #
                    # 2026-06-04: Threads moved the schedule trigger out of the
                    # header kebab. The header "More" button still exists but
                    # no longer contains a Schedule menuitem. The new trigger
                    # is a "Post Options" button at the BOTTOM-LEFT of the
                    # composer (icon: horizontal sliders). Clicking it opens a
                    # popover containing Schedule. Verified in
                    # logs/screenshots/threads_clipper_2026-06-03_*_kebab_or_icon_open_*.png
                    # — composer footer shows "Post Options" button next to "Post".
                    # So "Post Options" must be tried BEFORE the legacy "More"
                    # selectors, otherwise we click the now-useless header kebab
                    # and fail the Schedule menuitem lookup.
                    kebab_clicked = False
                    if not direct_clicked and not _threads_calendar_visible(page):
                        # 2026-06-12: most precise path first. The header kebab
                        # is the ONLY svg[aria-label="More"] inside the composer
                        # container. `.last` = innermost matching ancestor (the
                        # actual button wrapping the svg). Only when the composer
                        # container was found — a page-wide :has() would recreate
                        # the wrong-More bug this exists to fix.
                        if dialog is not None:
                            try:
                                el = dialog.locator(
                                    ':is(button, [role="button"])'
                                    ':has(svg[aria-label="More"])'
                                ).last
                                el.wait_for(state="visible", timeout=2500)
                                el.click()
                                kebab_clicked = True
                                _shot(page, basename, "kebab_clicked_svg_scoped")
                                page.wait_for_timeout(800)
                            except Exception:
                                pass

                    if not kebab_clicked and not direct_clicked and not _threads_calendar_visible(page):
                        candidate_names = [
                            # 2026-06-12: header kebab "More" is the CORRECT entry
                            # point for Schedule. Confirmed via Chrome MCP — the
                            # ••• kebab at top-right of the composer dialog opens
                            # a dropdown with: "Add AI label", "Mark as paid
                            # partnership", "Schedule…". The 2026-06-04 entry that
                            # put "Post Options" first was a misdiagnosis: that
                            # button opens an Audience/Reply-controls popover with
                            # NO Schedule menuitem. "Post Options" stays in the
                            # list at the END only as a defensive fallback for
                            # builds where Meta might reshuffle things again.
                            "More options", "More", "Options", "More actions",
                            "Composer options",
                            "Post Options", "Post options",
                        ]
                        for name in candidate_names:
                            try:
                                el = scope.get_by_role("button", name=name).first
                                el.wait_for(state="visible", timeout=1500)
                                el.click()
                                kebab_clicked = True
                                _shot(page, basename, f"kebab_clicked_role_button_{name.replace(' ', '_')}")
                                break
                            except Exception:
                                continue

                        if not kebab_clicked:
                            # CSS selector fallbacks.
                            css_fallbacks = [
                                'button[aria-label="More options"]',
                                'button[aria-label="More"]',
                                'button[aria-label="Options"]',
                                'div[role="button"][aria-label*="more" i]',
                                'div[role="button"][aria-label*="option" i]',
                                'svg[aria-label="More"]',
                                'svg[aria-label*="option" i]',
                                # Last resort: any small icon-only button at top of dialog
                                # with no name. Pick the second one (after Cancel/X).
                                '[aria-label="More"]',
                            ]
                            for sel in css_fallbacks:
                                try:
                                    el = (scope if dialog else page).locator(sel).first
                                    el.wait_for(state="visible", timeout=1500)
                                    el.click()
                                    kebab_clicked = True
                                    _shot(page, basename, f"kebab_clicked_css")
                                    break
                                except Exception:
                                    continue

                        if not kebab_clicked:
                            # Hover the dialog header to coax any visibility-on-
                            # hover icons into rendering, then look again.
                            try:
                                (scope if dialog else page).locator(
                                    'header, [role="banner"], div'
                                ).first.hover(timeout=1000)
                                page.wait_for_timeout(400)
                            except Exception:
                                pass

                        if not kebab_clicked:
                            # JS-direct kebab finder. We ask the page itself to
                            # walk the dialog DOM looking for any clickable
                            # element that LOOKS like a kebab (3-dot icon) —
                            # by aria-label hints OR by SVG shape (3 small
                            # circles, which is the visual pattern of a kebab).
                            try:
                                clicked_via_js = page.evaluate("""() => {
                                    const root = document.querySelector('[role="dialog"]') || document.body;
                                    const candidates = Array.from(root.querySelectorAll(
                                        'button, [role="button"], [tabindex="0"]'
                                    )).filter(el => el.offsetParent !== null);

                                    // Pass 1: aria-label hints.
                                    for (const el of candidates) {
                                        const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                                        if (/(more|option|action|kebab)/.test(aria)) {
                                            el.click();
                                            return 'aria:' + aria;
                                        }
                                    }

                                    // Pass 2: SVG shape — kebabs are 3 circles or 3 small dots.
                                    for (const el of candidates) {
                                        const svg = el.querySelector('svg');
                                        if (!svg) continue;
                                        const circles = svg.querySelectorAll('circle');
                                        if (circles.length === 3) {
                                            el.click();
                                            return 'svg-3-circles';
                                        }
                                        const paths = svg.querySelectorAll('path');
                                        if (paths.length === 3) {
                                            let allDots = true;
                                            for (const p of paths) {
                                                try {
                                                    const b = p.getBBox();
                                                    if (b.width > 8 || b.height > 8) { allDots = false; break; }
                                                } catch (e) { allDots = false; break; }
                                            }
                                            if (allDots) {
                                                el.click();
                                                return 'svg-3-dot-paths';
                                            }
                                        }
                                    }

                                    // Pass 3: positional — last small icon-only button
                                    // in the dialog header row (rightmost).
                                    const headerKids = Array.from(root.querySelectorAll(
                                        ':scope > div > div > div > button, header button'
                                    )).filter(el => el.offsetParent !== null);
                                    if (headerKids.length) {
                                        headerKids[headerKids.length - 1].click();
                                        return 'positional-last-header-button';
                                    }

                                    return null;
                                }""")
                                if clicked_via_js:
                                    kebab_clicked = True
                                    _shot(page, basename, f"kebab_clicked_js_{clicked_via_js[:30]}")
                            except Exception:
                                pass

                    # ---- Verify popover is now visible regardless of which
                    # path opened it. The calendar (or Schedule menuitem) is
                    # the real proof — variable state is unreliable because
                    # Playwright's click() can return an exception AFTER the
                    # actual click event already fired (race condition that
                    # was incorrectly flagging path A success as "missing").
                    page.wait_for_timeout(600)
                    if _threads_calendar_visible(page):
                        # Popover is open — skip menu navigation entirely.
                        _shot(page, basename, "schedule_popover_visible_immediate")
                        sched_clicked = True
                    elif not (direct_clicked or kebab_clicked):
                        # Nothing opened anything. Dump DOM and bail.
                        try:
                            if dialog:
                                html = dialog.evaluate("el => el.outerHTML")
                            else:
                                html = page.evaluate("() => document.body.outerHTML.slice(0, 80000)")
                            dump_path = LOG_DIR / (
                                f"threads_{basename}_schedule_dom_"
                                f"{int(datetime.now().timestamp())}.html"
                            )
                            dump_path.write_text(html)
                        except Exception:
                            pass
                        _shot(page, basename, "schedule_trigger_missing")
                        raise RuntimeError(
                            "Threads schedule trigger not found (no clock icon, "
                            "no kebab) — see schedule_trigger_missing screenshot "
                            "AND schedule_dom*.html in logs/screenshots/"
                        )
                    else:
                        # Kebab opened a menu but no calendar yet → menuitem step.
                        sched_clicked = False
                    _shot(page, basename, "kebab_or_icon_open")

                    # Step 2 (kebab path only): click "Schedule…" in the dropdown.
                    # Skipped if path A (clock icon) or path B opened the calendar
                    # popover directly.
                    if not sched_clicked:
                        for getter in (
                            lambda: page.get_by_role("menuitem", name="Schedule…").first,
                            lambda: page.get_by_role("menuitem", name="Schedule...").first,
                            lambda: page.get_by_role("menuitem", name="Schedule").first,
                            lambda: page.get_by_text("Schedule…", exact=True).first,
                            lambda: page.get_by_text("Schedule...", exact=True).first,
                            # Some dropdowns render it as a plain button inside the popover.
                            lambda: page.get_by_role("button", name="Schedule…").first,
                            lambda: page.get_by_role("button", name="Schedule").first,
                        ):
                            try:
                                el = getter()
                                el.wait_for(state="visible", timeout=3000)
                                el.click()
                                sched_clicked = True
                                break
                            except Exception:
                                continue
                        if not sched_clicked and not _threads_calendar_visible(page):
                            _shot(page, basename, "schedule_menuitem_missing")

                            # --- diagnostic dump #1: whatever's open right now ---
                            # Dump EVERY visible popover-like element so we see
                            # multiple containers (the first dump kept matching
                            # only the underlying composer dialog because of a
                            # menu/listbox preference; popovers turned out not
                            # to use those roles).
                            def _dump_all_popovers(label):
                                try:
                                    html = page.evaluate("""() => {
                                        const seen = new Set();
                                        const out = [];
                                        const sels = [
                                            '[role="dialog"]', '[role="menu"]',
                                            '[role="listbox"]', '[role="tooltip"]',
                                            'div[data-visualcompletion="ignore-dynamic"]',
                                            // bottom-sheet / popover containers
                                            'div[role="region"]',
                                        ];
                                        for (const sel of sels) {
                                            for (const el of document.querySelectorAll(sel)) {
                                                if (el.offsetParent === null) continue;
                                                if (seen.has(el)) continue;
                                                seen.add(el);
                                                out.push(
                                                    `<!-- ${sel} | role=${el.getAttribute('role')} | aria-label=${el.getAttribute('aria-label')} -->\\n`
                                                    + el.outerHTML.slice(0, 40000)
                                                );
                                            }
                                        }
                                        return out.join('\\n\\n=====\\n\\n');
                                    }""")
                                    dump_path = LOG_DIR / (
                                        f"threads_{basename}_{label}_dom_"
                                        f"{int(datetime.now().timestamp())}.html"
                                    )
                                    dump_path.write_text(html or "<empty/>")
                                except Exception:
                                    pass

                            _dump_all_popovers("post_options_popover")

                            # --- fallback: also try the HEADER kebab (legacy) ---
                            # If we just opened Post Options and Schedule wasn't
                            # there, close it and try the header "More" button
                            # in case Threads kept scheduling under the legacy
                            # path for some accounts/builds.
                            try:
                                # Click into the composer text area to close
                                # the bottom-sheet popover (Escape would spawn
                                # the Save-to-drafts modal — see comment below).
                                page.locator(
                                    'div[contenteditable="true"]'
                                ).first.click(timeout=1500)
                                page.wait_for_timeout(500)
                            except Exception:
                                pass

                            header_more_clicked = False
                            try:
                                # 2026-06-12: scope to the composer container and
                                # target the svg directly — page-wide
                                # '[aria-label="More"]'.first hit a hidden
                                # sidebar/feed element and timed out
                                # (header_more_unreachable in tonight's run).
                                more_btn = (
                                    dialog if dialog is not None else page
                                ).locator('svg[aria-label="More"]').last
                                more_btn.wait_for(state="visible", timeout=2000)
                                more_btn.click()
                                header_more_clicked = True
                                _shot(page, basename, "header_more_clicked_fallback")
                                page.wait_for_timeout(1200)
                            except Exception:
                                _shot(page, basename, "header_more_unreachable")

                            if header_more_clicked:
                                _dump_all_popovers("header_more_popover")

                                # Retry Schedule menuitem search against the
                                # header-More popover.
                                for getter in (
                                    lambda: page.get_by_role("menuitem", name="Schedule…").first,
                                    lambda: page.get_by_role("menuitem", name="Schedule...").first,
                                    lambda: page.get_by_role("menuitem", name="Schedule").first,
                                    lambda: page.get_by_text("Schedule…", exact=True).first,
                                    lambda: page.get_by_text("Schedule...", exact=True).first,
                                    lambda: page.get_by_role("button", name="Schedule…").first,
                                    lambda: page.get_by_role("button", name="Schedule").first,
                                    # Broader: anything containing "schedule" case-insensitive.
                                    lambda: page.locator(
                                        '[role="menuitem"]:has-text("chedule")'
                                    ).first,
                                    lambda: page.locator(
                                        'button:has-text("chedule")'
                                    ).first,
                                ):
                                    try:
                                        el = getter()
                                        el.wait_for(state="visible", timeout=2000)
                                        el.click()
                                        sched_clicked = True
                                        _shot(page, basename, "schedule_found_in_header_more")
                                        break
                                    except Exception:
                                        continue

                            if not sched_clicked and not _threads_calendar_visible(page):
                                raise RuntimeError(
                                    "Threads schedule menuitem not found in Post Options "
                                    "popover OR header More popover. See "
                                    "threads_<basename>_post_options_popover_dom_*.html "
                                    "and threads_<basename>_header_more_popover_dom_*.html "
                                    "in logs/screenshots/."
                                )
                        page.wait_for_timeout(800)
                    _shot(page, basename, "schedule_popover_open")

                    # Step 3: pick the date in the calendar.
                    dt = datetime.fromisoformat(scheduled_for)
                    date_ok = pick_date_threads_style(page, dt)
                    _shot(page, basename, "date_clicked" if date_ok else "date_pick_failed")
                    if not date_ok:
                        # Dump the calendar DOM so the next maintainer has
                        # the actual selectors to work from. Best-effort —
                        # if Threads' DOM has moved we want at least a
                        # partial dump.
                        try:
                            html = page.evaluate(
                                """() => {
                                    // Find an element whose text starts with a month name
                                    // (the calendar header), walk up to the calendar container.
                                    const months = ['January','February','March','April','May','June',
                                                    'July','August','September','October','November','December'];
                                    let header = null;
                                    for (const el of document.querySelectorAll('div, span, h1, h2, h3, h4')) {
                                        const t = (el.textContent || '').trim();
                                        if (months.some(m => t.startsWith(m + ' 20'))) {
                                            header = el; break;
                                        }
                                    }
                                    if (!header) return '<no-header-found/>';
                                    let node = header;
                                    for (let i = 0; i < 8; i++) {
                                        if (!node.parentElement) break;
                                        node = node.parentElement;
                                        // Stop when we reach an ancestor with at least 25 day-shaped children
                                        const dayCells = node.querySelectorAll('div, span, td, button');
                                        let count = 0;
                                        for (const c of dayCells) {
                                            const t = (c.textContent || '').trim();
                                            if (/^\\d{1,2}$/.test(t)) count++;
                                            if (count >= 25) break;
                                        }
                                        if (count >= 25) break;
                                    }
                                    return node.outerHTML.slice(0, 80000);
                                }"""
                            )
                            dump_path = LOG_DIR / (
                                f"threads_{basename}_calendar_dom_"
                                f"{int(datetime.now().timestamp())}.html"
                            )
                            dump_path.write_text(html or "<empty/>")
                        except Exception:
                            pass

                    # Step 4: set the time (HH : MM AM/PM).
                    time_ok = set_threads_time(page, dt)
                    _shot(page, basename, "time_set" if time_ok else "time_set_failed")

                    # Step 5: click Done to commit the schedule.
                    if date_ok and time_ok:
                        try:
                            page.get_by_role("button", name="Done", exact=True).first.click(timeout=3000)
                            page.wait_for_timeout(800)
                            scheduled_ok = True
                        except Exception:
                            _shot(page, basename, "done_btn_missing")
                except Exception:
                    _shot(page, basename, "schedule_flow_failed")
                    # CRITICAL: do NOT press Escape. Threads opens a "Save
                    # to drafts?" modal on Escape that blocks the immediate-
                    # post fallback. Click into the caption instead so the
                    # popover gets a focus-out event without an Escape key.
                    try:
                        page.locator(
                            'div[contenteditable="true"]'
                        ).first.click(timeout=1500)
                        page.wait_for_timeout(300)
                    except Exception:
                        pass
                    # If a Save dialog DID open from anywhere, dismiss it.
                    _dismiss_save_to_drafts_modal(page, basename)

            # Refuse silent immediate-post fallback when a schedule was
            # requested but the schedule popover never committed. Prior
            # behavior was to fall through and click Post, then return
            # status="success" with `scheduling_skipped: true` — but no
            # caller reads that flag, so 100% of failed-schedule attempts
            # silently posted now. (2026-04-28: 15/15 Threads receipts on
            # the prior 48h showed this; spacing was 2.5h on every other
            # platform but Threads fired all clips back-to-back.) Same
            # class of silent-fail as the X regression patched the same
            # day — see _verify_x_scheduled_commit.
            if scheduled_for and not scheduled_ok:
                _shot(page, basename, "schedule_flow_failed_no_submit")
                return {
                    "status": "failed",
                    "error": "Threads schedule flow did not complete "
                             "(calendar/time popover never committed). "
                             "Refusing to silently post immediately. "
                             "Check *_schedule_flow_failed*.png and "
                             "*_schedule_flow_failed_no_submit*.png "
                             "in logs/screenshots/.",
                    "scheduled_for": scheduled_for,
                    "topic": topic,
                    "composer": "threads.com_native",
                    "at": _now_iso(),
                }

            # Click the final submit. After a successful schedule the
            # composer's primary action button may relabel to "Schedule"
            # or stay as "Post" depending on the current Threads build —
            # try both.
            #
            # 2026-06-08: scope the submit-button lookup to the composer
            # DIALOG, not the whole page. The feed underneath the
            # composer has its own "Post" button in the top-right (the
            # "What's new?" placeholder that opens a fresh composer);
            # `page.get_by_role("button", name="Post", exact=True).first`
            # was matching THAT button instead of the composer's Post
            # submit. Click did nothing useful: the original composer
            # stayed open, a brand new empty composer opened, and the
            # immediate-post path returned status=success without
            # verifying. 37 lying receipts across runs 5-8 + retry
            # sweeps — none actually landed on threads.com. See the
            # 2026-06-08 entry in plan.md.
            submit_clicked = False
            try:
                _submit_scope = page.get_by_role("dialog").first
                _submit_scope.wait_for(state="visible", timeout=2000)
            except Exception:
                # Defensive: if the dialog role isn't found, fall back to
                # page-wide. The verifier downstream will still catch the
                # silent-fail case.
                _submit_scope = page
            submit_candidates = (
                [lambda: _submit_scope.get_by_role("button", name="Schedule", exact=True).first,
                 lambda: _submit_scope.get_by_role("button", name="Post", exact=True).first]
                if scheduled_ok else
                [lambda: _submit_scope.get_by_role("button", name="Post", exact=True).first]
            )
            for getter in submit_candidates:
                try:
                    btn = getter()
                    btn.wait_for(state="visible", timeout=8000)
                    btn.click()
                    submit_clicked = True
                    break
                except Exception:
                    continue
            if not submit_clicked:
                _shot(page, basename, "submit_btn_missing")
                return {"status": "failed",
                        "error": "Threads submit button (Post/Schedule) not found",
                        "at": _now_iso()}

            # If we asked for a scheduled post, prove the schedule actually
            # committed before declaring success — composer should close
            # and a confirmation toast should appear. Verifier polls up
            # to 20s and short-circuits to False if Threads' Save-to-
            # drafts dialog appears (means the submit click was eaten).
            if scheduled_ok:
                try:
                    commit_ok = _verify_threads_scheduled_commit(page, basename)
                except ThreadsQueueCapReached as cap_exc:
                    return {
                        "status": "failed",
                        "error": str(cap_exc),
                        "error_code": "threads_queue_cap_reached",
                        "scheduled_for": scheduled_for,
                        "scheduled_ok_in_popover": True,
                        "topic": topic,
                        "composer": "threads.com_native",
                        "at": _now_iso(),
                    }
                if not commit_ok:
                    return {
                        "status": "failed",
                        "error": "Threads schedule confirmed in popover "
                                 "(Done clicked) but final submit did not "
                                 "commit (composer still open or Save-to-"
                                 "drafts dialog appeared). Check the "
                                 "*_schedule_commit_unverified*.png "
                                 "screenshot.",
                        "scheduled_for": scheduled_for,
                        "scheduled_ok_in_popover": True,
                        "topic": topic,
                        "composer": "threads.com_native",
                        "at": _now_iso(),
                    }
                # Existing toast/detach lock stays. Batch proof is the native
                # list opened from header ⋯ next to Drafts — not the toast,
                # and never threads.com/scheduled (404).
                native_ok = _threads_native_scheduled_list_has_caption(page, basename, caption)
                proof_fail = native_list_proof_or_fail(
                    "threads",
                    native_list_has_item=native_ok,
                    composer_toast_seen=True,
                    proof_url=page.url,
                )
                if proof_fail:
                    _shot(page, basename, "native_list_proof_missing")
                    return failure_receipt(
                        proof_fail,
                        scheduled_for=scheduled_for,
                        topic=topic,
                        composer="threads.com_native",
                    )
                _shot(page, basename, "scheduled")
                return {
                    "status": "scheduled",
                    "url": None,
                    "scheduled_for": scheduled_for,
                    "topic": topic,
                    "composer": "threads.com_native",
                    "proof": "native_scheduled_list_via_header_kebab",
                    "at": _now_iso(),
                }

            # No schedule was requested — immediate post. PROVE the post
            # actually committed before declaring success: composer must
            # tear down, or a confirmation toast must appear, or BOTH.
            # Refusing to silently lie is the whole point — see the
            # _verify_threads_immediate_commit() docstring for the
            # 2026-06-08 regression history.
            try:
                immediate_ok = _verify_threads_immediate_commit(page, basename)
            except ThreadsQueueCapReached as cap_exc:
                fail_receipt = {
                    "status": "failed",
                    "error": str(cap_exc),
                    "error_code": "threads_queue_cap_reached",
                    "topic": topic,
                    "composer": "threads.com_native",
                    "at": _now_iso(),
                }
                if scheduling_skipped:
                    fail_receipt["scheduling_skipped_attempted"] = True
                    fail_receipt["scheduling_unavailable_reason"] = SCHEDULING_UNAVAILABLE_REASON
                    fail_receipt["scheduling_target"] = scheduling_target
                return fail_receipt
            if not immediate_ok:
                fail_receipt = {
                    "status": "failed",
                    "error": "Threads immediate-post final submit did not "
                             "commit (composer still open or Save-to-drafts "
                             "dialog appeared). Check the "
                             "*_immediate_commit_unverified*.png or "
                             "*_immediate_commit_save_dialog*.png screenshot.",
                    "topic": topic,
                    "composer": "threads.com_native",
                    "at": _now_iso(),
                }
                if scheduling_skipped:
                    fail_receipt["scheduling_skipped_attempted"] = True
                    fail_receipt["scheduling_unavailable_reason"] = SCHEDULING_UNAVAILABLE_REASON
                    fail_receipt["scheduling_target"] = scheduling_target
                return fail_receipt
            _shot(page, basename, "posted")

            # Self-imposed pacing — see IMMEDIATE_POST_BACKOFF_SECONDS at
            # top of file. Applies whenever we just immediate-posted a
            # clip that was meant to be scheduled (Threads tightened its
            # 2026 rate limits and back-to-back retry sweeps were
            # tripping temporary blocks). Skipped for organic
            # immediate-post calls — those are caller-controlled.
            if scheduling_skipped and IMMEDIATE_POST_BACKOFF_SECONDS > 0:
                page.wait_for_timeout(IMMEDIATE_POST_BACKOFF_SECONDS * 1000)

            receipt = {
                "status": "success",
                "url": None,
                "posted_at": _now_iso(),
                "topic": topic,
                "composer": "threads.com_native",
                "at": _now_iso(),
            }
            if scheduling_skipped:
                receipt["scheduling_skipped"] = True
                receipt["scheduling_unavailable_reason"] = SCHEDULING_UNAVAILABLE_REASON
                receipt["scheduling_target"] = scheduling_target
            return receipt
    except Exception as e:
        return {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
