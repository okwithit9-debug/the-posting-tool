"""
platforms/youtube_studio.py — YouTube Studio video uploader (Playwright fallback).

This module exists so that when the YouTube Data API hits its daily
`uploadLimitExceeded` ceiling (~6 uploads/day on the default 10k-unit quota),
the dispatch can transparently switch to driving studio.youtube.com in the
shared Playwright Chrome profile and keep posting against the much larger
*account-level* daily limit (15-100+ videos depending on channel trust).

Drop-in for platforms/youtube.py — same `post(video, captions, *, scheduled_for)`
signature and same return-dict shape, so api/youtube.py can call this on quota
hit without any caller changes.

Architecture notes (Studio is the trickiest of all 6 platforms because):
  - Studio is a Polymer/Lit app — most controls live behind multiple shadow
    DOM boundaries. We use page.locator with `>>>` shadow-piercing where
    Playwright supports it, plus JS evaluate() fallbacks for the gnarliest
    paths (the schedule date/time picker is the worst offender).
  - The upload flow is a 4-step modal: Details → Video elements → Checks →
    Visibility. Most steps auto-advance via "Next" but checks can stall on
    copyright matches, so we wait with a generous timeout.
  - Schedule is on the Visibility step. Toggling the "Schedule" radio reveals
    a date picker + time field. Date is `MMM DD, YYYY`; time is `H:MM AM/PM`.
  - "Made for kids" is a REQUIRED radio. We always set "No, it's not made for
    kids" (general audience).

First-run requirement: user must be signed into studio.youtube.com inside
the dedicated Playwright profile (~/.posting_tool_chrome). See
setup_playwright_logins.py — YouTube Studio is in its PLATFORMS list.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from platforms._chrome import chrome_session

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs" / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Studio caps title at 100 chars, description at ~5000.
TITLE_CAP = 100
DESCRIPTION_CAP = 4900

# Generous upload-processing wait. A 15MB Short usually finishes "Checks" in
# under 60s, but copyright-match scans can take 2-3 min on bad days.
UPLOAD_PROCESS_TIMEOUT_MS = 240_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shot(page, basename: str, label: str) -> str:
    """Save a screenshot under logs/screenshots/youtube_studio_<basename>_<label>_<ts>.png.

    Mirrors the pattern from threads.py / x.py so debugging is consistent
    across all browser-driven platforms. Filenames include a unix timestamp
    so multiple runs don't collide.
    """
    path = LOG_DIR / f"youtube_studio_{basename}_{label}_{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(path), full_page=False)
    except Exception:
        return ""
    return str(path)


def _dump_dom(page, basename: str, label: str) -> str:
    """Save the full page HTML for postmortem analysis on selector failures.

    Studio's shadow DOM is opaque to a regular page.content() call, so we
    walk all open shadow roots and serialize them too. Useful when a
    locator times out and we need to see what was actually on the page.
    """
    path = LOG_DIR / f"youtube_studio_{basename}_{label}_{int(datetime.now().timestamp())}.html"
    try:
        html = page.evaluate(
            """() => {
                const dump = (node) => {
                    let out = node.outerHTML || '';
                    const all = node.querySelectorAll('*');
                    for (const el of all) {
                        if (el.shadowRoot) {
                            out += `\\n<!-- shadow of ${el.tagName} -->\\n`;
                            out += el.shadowRoot.innerHTML;
                        }
                    }
                    return out;
                };
                return dump(document.documentElement);
            }"""
        )
        path.write_text(html or "")
    except Exception:
        return ""
    return str(path)


def _truncate(text: str, cap: int) -> str:
    if not text:
        return ""
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


def _format_studio_date(dt: datetime) -> str:
    """Studio's date input expects 'MMM DD, YYYY' (e.g. 'May 10, 2026')."""
    # %-d is non-portable; build day-of-month manually.
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _format_studio_time(dt: datetime) -> str:
    """Studio's time input expects 'H:MM AM/PM' (e.g. '9:30 AM').

    Studio is lenient about leading zeros on the hour but strict about
    the AM/PM suffix and the colon separator.
    """
    hour_12 = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{hour_12}:{dt.minute:02d} {ampm}"


# ----------------------------------------------------------------------------
# Studio flow steps — each is a small, screenshotted unit so failures pinpoint.
# ----------------------------------------------------------------------------


def _open_upload_modal(page, basename: str) -> None:
    """Land on Studio and trigger the upload modal.

    Two paths:
      1. Direct nav to /upload (sometimes works, sometimes redirects to dashboard).
      2. Click the "Create" button (camera+ icon, top-right) → "Upload videos" menu item.

    We try (1) first. If the upload modal isn't visible after navigation, we
    fall back to (2).
    """
    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2000)
    _shot(page, basename, "01_studio_landed")

    # Path 1: try direct nav. Studio sometimes accepts /channel/UC.../videos/upload
    # but the bare /upload path is the most stable across channel ids.
    try:
        page.goto("https://studio.youtube.com/upload", wait_until="domcontentloaded", timeout=20_000)
        page.wait_for_timeout(1500)
        if _upload_modal_is_open(page):
            _shot(page, basename, "02_upload_modal_via_direct_nav")
            return
    except Exception:
        pass

    # Path 2: click Create (camera+ icon) → Upload videos.
    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_timeout(1500)
    _shot(page, basename, "02_dashboard_for_create_click")

    for getter in (
        lambda: page.get_by_role("button", name="Create").first,
        lambda: page.locator('ytcp-button#create-icon-button').first,
        lambda: page.locator('button[aria-label="Create"]').first,
        lambda: page.locator('[id="upload-icon"]').first,
    ):
        try:
            btn = getter()
            btn.wait_for(state="visible", timeout=3000)
            btn.click(timeout=3000)
            page.wait_for_timeout(800)
            break
        except Exception:
            continue

    # Then the "Upload videos" menu item.
    for getter in (
        lambda: page.get_by_role("menuitem", name=re.compile(r"Upload videos?", re.I)).first,
        lambda: page.get_by_text("Upload videos", exact=False).first,
    ):
        try:
            item = getter()
            item.wait_for(state="visible", timeout=3000)
            item.click(timeout=3000)
            page.wait_for_timeout(1500)
            break
        except Exception:
            continue

    _shot(page, basename, "03_upload_modal_via_create_menu")


def _upload_modal_is_open(page) -> bool:
    """Truth check: is the upload modal up and waiting for a file?"""
    try:
        # The Select Files button only renders inside the upload modal.
        if page.get_by_role("button", name=re.compile(r"Select files?", re.I)).first.is_visible(timeout=600):
            return True
    except Exception:
        pass
    try:
        if page.locator('input[type="file"][name="Filedata"]').first.is_visible(timeout=600):
            return True
    except Exception:
        pass
    try:
        # The drag-drop region has this label across builds.
        if page.get_by_text("Drag and drop video files to upload", exact=False).first.is_visible(timeout=600):
            return True
    except Exception:
        pass
    return False


def _set_file(page, video: Path, basename: str) -> None:
    """Hand the video file to Studio's upload modal.

    First-run-fix (2026-05-10): set_input_files on the hidden <input> looked
    like it worked but didn't trigger Studio's upload — modal sat on "Drag
    and drop / Select files". The reliable path is to click "Select files"
    and catch the resulting filechooser event with expect_file_chooser,
    which is how Studio's JS attaches the upload pipeline.

    After file handoff, we poll for the modal to advance to the Details step
    (title contenteditable + "Details" heading) — proof the upload actually
    started. If the modal doesn't advance within ~30s we raise so the
    caller can record a clean failure receipt instead of timing out later
    on a missing Title field.
    """
    # Capture the file chooser the Select-files button raises.
    select_btn = None
    for getter in (
        lambda: page.get_by_role("button", name="Select files", exact=True).first,
        lambda: page.get_by_role("button", name=re.compile(r"Select files?", re.I)).first,
        lambda: page.locator('ytcp-button:has-text("Select files")').first,
    ):
        try:
            btn = getter()
            btn.wait_for(state="visible", timeout=5000)
            select_btn = btn
            break
        except Exception:
            continue
    if select_btn is None:
        # Last-resort: try the hidden input the way we used to. It usually
        # doesn't work but it costs nothing and unblocks if Studio ever
        # changes the button label.
        try:
            page.locator('input[type="file"]').first.set_input_files(str(video))
        except Exception as e:
            _shot(page, basename, "04_select_button_missing")
            raise RuntimeError(f"studio: Select files button not found ({e})")
    else:
        with page.expect_file_chooser() as fc_info:
            select_btn.click(timeout=3000)
        chooser = fc_info.value
        chooser.set_files(str(video))

    # Poll for the Details step. Two reliable signals:
    #   - the title contenteditable becomes visible
    #   - the "Details" heading appears at the top of the step
    deadline_ms = 60_000
    elapsed = 0
    interval = 1500
    advanced = False
    while elapsed < deadline_ms:
        try:
            if page.get_by_text("Details", exact=True).first.is_visible(timeout=400):
                advanced = True
                break
        except Exception:
            pass
        try:
            # Current (2026-07): title custom element is ytcp-social-suggestions-textbox#title-textarea
            if page.locator('ytcp-social-suggestions-textbox#title-textarea').first.is_visible(timeout=400):
                advanced = True
                break
        except Exception:
            pass
        try:
            # Legacy fallback in case Studio flips names again
            if page.locator('ytcp-mention-textbox#title-textarea').first.is_visible(timeout=400):
                advanced = True
                break
        except Exception:
            pass
        try:
            if page.locator('div[aria-label*="title that describes" i]').first.is_visible(timeout=400):
                advanced = True
                break
        except Exception:
            pass
        page.wait_for_timeout(interval)
        elapsed += interval

    _shot(page, basename, "04_file_set" if advanced else "04_file_set_no_advance")
    if not advanced:
        _dump_dom(page, basename, "04_no_advance_dom")
        raise RuntimeError("studio: upload modal didn't advance to Details step within 60s")


def _set_title(page, title: str, basename: str) -> None:
    """Replace the auto-populated title (filename) with our caption title.

    Studio's title field is a contenteditable div inside an <ytcp-social-suggestions-textbox>
    custom element. We address it via the visible 'Title (required)' label.
    """
    title = _truncate(title, TITLE_CAP)

    # Find the title contenteditable. It's the first textbox and it's required.
    #
    # 2026-07-24 fix: Studio's title element is
    # `<ytcp-social-suggestions-textbox id="title-textarea">` — NOT the
    # `ytcp-mention-textbox` custom element the prior code assumed (that
    # 2025-rename note in the previous docstring was inverted; the element
    # never actually flipped). Inside is `div#textbox[contenteditable="true"]`
    # with the aria-label "Add a title that describes your video...".
    #
    # The description field is a twin `ytcp-social-suggestions-textbox`
    # WITHOUT the `#title-textarea` id — so any generic `contenteditable=true`
    # selector matches two and we can't distinguish. We anchor via id.
    for getter in (
        # Preferred: shadow-pierce through the specific title custom element.
        lambda: page.locator('ytcp-social-suggestions-textbox#title-textarea div[contenteditable="true"]').first,
        # Fallback: id anchor without the tag name.
        lambda: page.locator('#title-textarea div[contenteditable="true"]').first,
        # aria-label anchor — description says "Tell viewers about your video",
        # title says "Add a title that describes your video", so "a title" is
        # unique enough for the contenteditable to be the title's.
        lambda: page.locator('div[contenteditable="true"][aria-label*="a title" i]').first,
        # Legacy `ytcp-mention-textbox` fallbacks in case Studio ships a rename.
        lambda: page.locator('ytcp-mention-textbox#title-textarea div[contenteditable="true"]').first,
        lambda: page.locator('ytcp-mention-textbox#title-textarea').first,
        # Last-resort: label-based (Studio's <span id="label-text"> is not a
        # true <label for=…>, so Playwright's get_by_label may miss it — but
        # keeping it for hardware-lottery cases).
        lambda: page.get_by_label("Title (required)").first,
        lambda: page.locator('div[aria-label*="title that describes" i]').first,
        lambda: page.locator('div[contenteditable="true"]').first,
    ):
        try:
            box = getter()
            box.wait_for(state="visible", timeout=8000)
            box.click(timeout=3000)
            # Clear (Studio pre-populated with filename).
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(150)
            box.type(title, delay=10)
            page.wait_for_timeout(300)
            _shot(page, basename, "05_title_typed")
            return
        except Exception:
            continue

    raise RuntimeError("studio: could not locate Title contenteditable")


def _set_description(page, description: str, basename: str) -> None:
    """Type description into the second contenteditable. Optional — skip if empty."""
    if not description:
        return
    description = _truncate(description, DESCRIPTION_CAP)

    for getter in (
        # 2026-07-24 fix: same rename as title — description is
        # ytcp-social-suggestions-textbox#description-textarea, not
        # ytcp-mention-textbox.
        lambda: page.locator('ytcp-social-suggestions-textbox#description-textarea div[contenteditable="true"]').first,
        lambda: page.locator('#description-textarea div[contenteditable="true"]').first,
        lambda: page.locator('div[contenteditable="true"][aria-label*="Tell viewers about your video" i]').first,
        # Legacy fallbacks
        lambda: page.get_by_label("Description").first,
        lambda: page.locator('ytcp-mention-textbox#description-textarea div[contenteditable="true"]').first,
        lambda: page.locator('ytcp-mention-textbox#description-textarea').first,
        lambda: page.locator('div[aria-label*="Tell viewers about your video"]').first,
        # Last-resort: second contenteditable on the page (first is the title).
        lambda: page.locator('div[contenteditable="true"]').nth(1),
    ):
        try:
            box = getter()
            box.wait_for(state="visible", timeout=4000)
            box.click(timeout=3000)
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")
            page.wait_for_timeout(100)
            box.type(description, delay=4)
            page.wait_for_timeout(200)
            _shot(page, basename, "06_description_typed")
            return
        except Exception:
            continue
    # Description is optional — log and move on if we couldn't find the field.
    _shot(page, basename, "06_description_skipped")


def _select_not_made_for_kids(page, basename: str) -> None:
    """Required radio: 'No, it's not made for kids'.

    Studio refuses to advance to step 2 until 'Made for kids' is answered.
    User content is treated as general-audience, so we always pick No.

    2026-08-01 fix: selectors themselves still match (verified via
    live Studio — count=1, visible=True for all 3 candidates).
    The failure was TIMING: production flow jumps from _set_description
    to this function with no scroll, so the audience section may not be
    rendered/interactable yet, and the old 5s wait_for was too short.
    Fix: (1) scroll the section into view first, (2) longer wait, (3)
    click the label div if the outer radio custom element intercepts.
    """
    # Nudge the audience section into view so its interior radios render.
    for anchor_getter in (
        lambda: page.get_by_text("Is this video made for kids?", exact=False).first,
        lambda: page.locator('ytkc-made-for-kids-select').first,
        lambda: page.get_by_text("Audience", exact=False).first,
    ):
        try:
            anchor_getter().scroll_into_view_if_needed(timeout=3000)
            page.wait_for_timeout(400)
            break
        except Exception:
            continue

    # Wait for the section to actually render before probing individual
    # elements (Studio can lazy-mount the radio group after scroll).
    try:
        page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first.wait_for(
            state="attached", timeout=15_000
        )
    except Exception:
        pass

    for getter in (
        # Preferred: the outer custom element, which reliably takes clicks.
        lambda: page.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').first,
        # Role-based (works when a11y tree is populated).
        lambda: page.get_by_role("radio", name=re.compile(r"No,? it'?s not made for kids", re.I)).first,
        # Fall back to clicking the visible label <ytcp-ve> — its parent is the
        # radio button. Studio treats label clicks as radio activations.
        lambda: page.get_by_text("No, it's not made for kids", exact=False).first,
    ):
        try:
            radio = getter()
            radio.wait_for(state="visible", timeout=12_000)
            try:
                radio.click(timeout=4000)
            except Exception:
                # Force click as final resort — element is verified visible.
                radio.click(force=True, timeout=4000)
            page.wait_for_timeout(400)
            _shot(page, basename, "07_not_for_kids_selected")
            return
        except Exception:
            continue

    _shot(page, basename, "07_not_for_kids_failed")
    raise RuntimeError("studio: could not locate 'Not made for kids' radio")


def _click_next(page, basename: str, step_label: str) -> None:
    """Click the bottom-right Next button to advance to the next step."""
    for getter in (
        lambda: page.get_by_role("button", name="Next", exact=True).first,
        lambda: page.locator('ytcp-button#next-button').first,
        lambda: page.locator('button:has-text("Next")').first,
    ):
        try:
            btn = getter()
            btn.wait_for(state="visible", timeout=8000)
            btn.click(timeout=3000)
            page.wait_for_timeout(1500)
            _shot(page, basename, f"08_next_{step_label}")
            return
        except Exception:
            continue
    raise RuntimeError(f"studio: Next button missing on step {step_label}")


def _wait_for_visibility_step(page, basename: str) -> None:
    """The flow advances Details → Elements → Checks → Visibility.

    Studio doesn't always auto-advance Checks (copyright/ads scans can stall),
    so we click Next as needed until the Visibility step's "Save or publish"
    heading is visible.
    """
    deadline_ms = UPLOAD_PROCESS_TIMEOUT_MS
    elapsed = 0
    interval = 2000

    while elapsed < deadline_ms:
        # If Visibility step is showing, we're done.
        try:
            heading = page.get_by_text("Save or publish", exact=False).first
            if heading.is_visible(timeout=400):
                _shot(page, basename, "09_visibility_step_reached")
                return
        except Exception:
            pass
        # Studio enables Next as soon as a step's checks pass — keep clicking it.
        try:
            nxt = page.get_by_role("button", name="Next", exact=True).first
            if nxt.is_visible(timeout=400) and nxt.is_enabled(timeout=400):
                nxt.click(timeout=2000)
                page.wait_for_timeout(1200)
        except Exception:
            pass
        page.wait_for_timeout(interval)
        elapsed += interval

    _shot(page, basename, "09_visibility_step_TIMEOUT")
    _dump_dom(page, basename, "09_visibility_step_TIMEOUT_dom")
    raise RuntimeError("studio: timed out waiting for Visibility step")


def _set_schedule(page, scheduled_for_iso: str, basename: str) -> None:
    """Toggle the Schedule radio and set date + time inputs.

    Studio's schedule UI:
      - Radio: 'Schedule' (vs Public / Private / Unlisted).
      - Reveals a date picker (text input, MMM DD, YYYY) + time picker (H:MM AM/PM).
      - The date input accepts free-text typing (no need to drive the calendar).
    """
    target = datetime.fromisoformat(scheduled_for_iso)
    date_str = _format_studio_date(target)
    time_str = _format_studio_time(target)

    # Click Schedule radio.
    schedule_clicked = False
    for getter in (
        lambda: page.get_by_role("radio", name="Schedule", exact=True).first,
        lambda: page.locator('tp-yt-paper-radio-button[name="SCHEDULE"]').first,
        lambda: page.get_by_text("Schedule", exact=True).first,
    ):
        try:
            r = getter()
            r.wait_for(state="visible", timeout=5000)
            r.click(timeout=3000)
            page.wait_for_timeout(800)
            schedule_clicked = True
            break
        except Exception:
            continue
    if not schedule_clicked:
        _shot(page, basename, "10_schedule_radio_missing")
        raise RuntimeError("studio: Schedule radio not clickable")

    _shot(page, basename, "10_schedule_radio_clicked")

    # Set date.
    for getter in (
        lambda: page.get_by_label("Date", exact=True).first,
        lambda: page.locator('input[aria-label*="Date" i]').first,
        lambda: page.locator('ytcp-date-picker input').first,
    ):
        try:
            date_in = getter()
            date_in.wait_for(state="visible", timeout=4000)
            date_in.click(timeout=2000)
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")
            date_in.type(date_str, delay=20)
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            break
        except Exception:
            continue
    _shot(page, basename, "11_date_set")

    # Set time.
    for getter in (
        lambda: page.get_by_label("Time", exact=True).first,
        lambda: page.locator('input[aria-label*="Time" i]').first,
        lambda: page.locator('ytcp-form-input-container:has-text("Time") input').first,
    ):
        try:
            time_in = getter()
            time_in.wait_for(state="visible", timeout=4000)
            time_in.click(timeout=2000)
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")
            time_in.type(time_str, delay=20)
            page.keyboard.press("Enter")
            page.wait_for_timeout(400)
            break
        except Exception:
            continue
    _shot(page, basename, "12_time_set")


def _commit_schedule(page, basename: str) -> Optional[str]:
    """Click Schedule (the final commit button on the Visibility step).

    Returns the resulting public URL if Studio shows it in the success
    sheet, otherwise None — the receipt will still mark scheduled, just
    without a URL.
    """
    for getter in (
        lambda: page.get_by_role("button", name="Schedule", exact=True).first,
        lambda: page.locator('ytcp-button#done-button').first,
        lambda: page.locator('button:has-text("Schedule")').first,
    ):
        try:
            btn = getter()
            btn.wait_for(state="visible", timeout=8000)
            btn.click(timeout=3000)
            page.wait_for_timeout(2500)
            _shot(page, basename, "13_schedule_committed")
            break
        except Exception:
            continue
    else:
        _shot(page, basename, "13_schedule_button_missing")
        raise RuntimeError("studio: final Schedule button not found")

    # The success sheet shows the video URL. Try to capture it.
    url = None
    for getter in (
        lambda: page.locator('a[href*="youtu.be/"]').first,
        lambda: page.locator('a[href*="youtube.com/shorts/"]').first,
        lambda: page.locator('a[href*="youtube.com/watch"]').first,
    ):
        try:
            link = getter()
            link.wait_for(state="visible", timeout=5000)
            url = link.get_attribute("href")
            if url:
                break
        except Exception:
            continue

    # Close the success dialog.
    for getter in (
        lambda: page.get_by_role("button", name="Close", exact=True).first,
        lambda: page.locator('ytcp-button#close-button').first,
    ):
        try:
            getter().click(timeout=2000)
            break
        except Exception:
            continue

    return url


# ----------------------------------------------------------------------------
# Public entrypoint — matches platforms/youtube.py post() signature.
# ----------------------------------------------------------------------------


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    """Upload a video to YouTube via the Studio web UI (Playwright fallback).

    Returns the same shape as the API path:
      {
        "status": "scheduled" | "success" | "failed",
        "url": ...,
        "scheduled_for": ...,
        "video_id": None,           # Studio doesn't expose the id pre-publish
        "via": "studio",            # marker so receipts can tell paths apart
        "at": ISO timestamp,
        "error": str (only on failure),
      }
    """
    title = (captions or {}).get("title")
    description = (captions or {}).get("description", "") or ""
    if not title:
        return {"status": "failed", "via": "studio",
                "error": "youtube caption block missing 'title'", "at": _now_iso()}

    if not video.exists():
        return {"status": "failed", "via": "studio",
                "error": f"video not found: {video}", "at": _now_iso()}

    basename = video.stem  # for screenshot filenames

    try:
        with chrome_session() as page:
            _open_upload_modal(page, basename)

            if not _upload_modal_is_open(page):
                _dump_dom(page, basename, "no_upload_modal_dom")
                return {"status": "failed", "via": "studio",
                        "error": "studio upload modal never opened — login may have lapsed",
                        "at": _now_iso()}

            _set_file(page, video, basename)
            _set_title(page, title, basename)
            _set_description(page, description, basename)
            _select_not_made_for_kids(page, basename)
            _click_next(page, basename, "details")

            # Steps 2 (Video elements) and 3 (Checks) auto-advance most of the
            # time. The helper below clicks Next as needed and waits for the
            # Visibility heading to appear.
            _wait_for_visibility_step(page, basename)

            if scheduled_for:
                _set_schedule(page, scheduled_for, basename)
                url = _commit_schedule(page, basename)
                return {
                    "status": "scheduled",
                    "url": url,
                    "scheduled_for": scheduled_for,
                    "video_id": None,
                    "via": "studio",
                    "at": _now_iso(),
                }

            # No schedule — pick Public and click Publish.
            for getter in (
                lambda: page.get_by_role("radio", name="Public", exact=True).first,
                lambda: page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').first,
            ):
                try:
                    getter().click(timeout=3000)
                    page.wait_for_timeout(400)
                    break
                except Exception:
                    continue
            for getter in (
                lambda: page.get_by_role("button", name="Publish", exact=True).first,
                lambda: page.locator('ytcp-button#done-button').first,
            ):
                try:
                    getter().click(timeout=3000)
                    page.wait_for_timeout(2500)
                    break
                except Exception:
                    continue

            _shot(page, basename, "13_published_immediately")
            return {
                "status": "success",
                "url": None,
                "scheduled_for": None,
                "video_id": None,
                "via": "studio",
                "at": _now_iso(),
            }

    except Exception as e:
        return {
            "status": "failed",
            "via": "studio",
            "error": f"{type(e).__name__}: {e}",
            "at": _now_iso(),
        }
