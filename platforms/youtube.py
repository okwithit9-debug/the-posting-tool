"""
platforms/youtube.py — YouTube Shorts poster (API + Studio fallback).

Tries the Google Data API first via the legacy youtube.py at project root.
When the API returns `uploadLimitExceeded` (the daily ~6-upload ceiling on
the default 10k-unit quota), we transparently switch to driving
studio.youtube.com via Playwright (`platforms/youtube_studio.post`) — same
return-dict shape, just `via: "studio"` in the receipt instead of "api".

The fallback uses the *account-level* upload limit (15-100+ videos/day
depending on channel trust), which is much higher than the API quota.
Result: the dispatch never gets stuck on the API ceiling again.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.json"

# Substrings in the legacy upload's captured stdout/exception that prove the
# failure was caused by the daily API upload limit (vs. e.g. a network blip
# or a malformed sidecar). When ANY of these appears, we fall back to Studio.
QUOTA_SIGNALS = (
    "uploadLimitExceeded",
    "exceeded the number of videos",
    "quotaExceeded",
    "dailyLimitExceeded",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_quota_failure(captured_output: str, exc: Optional[BaseException]) -> bool:
    """True if either the legacy upload's printed retries OR the raised
    exception (if any) names the daily-quota error.

    The legacy upload swallows exceptions internally and prints them as
    'Retry N/MAX: <HttpError ... uploadLimitExceeded ...>' lines, then
    returns None. So the captured stdout is usually where we see the signal.
    """
    haystack = (captured_output or "")
    if exc is not None:
        haystack += " " + str(exc)
    return any(sig in haystack for sig in QUOTA_SIGNALS)


def _post_via_studio(video: Path, captions: dict, scheduled_for: Optional[str],
                     api_failure_note: str) -> dict:
    """Dispatch the upload through the Playwright Studio module.

    api_failure_note gets stitched into the receipt so postmortems can see
    why the fallback was used.
    """
    try:
        from platforms.youtube_studio import post as studio_post
    except ImportError as e:
        return {
            "status": "failed", "via": "api",
            "error": f"api hit quota AND studio fallback failed to import: {e}",
            "api_quota_note": api_failure_note,
            "at": _now_iso(),
        }

    print(f"YouTube API quota exhausted — falling back to Studio web upload")
    result = studio_post(video, captions, scheduled_for=scheduled_for)
    # Annotate so the receipt shows both legs.
    result["api_quota_note"] = api_failure_note
    return result


def post(video: Path, captions: dict, *, scheduled_for: Optional[str] = None, **_) -> dict:
    """Upload a video to YouTube Shorts.

    Tries API first. On daily-quota failure, falls back to Studio (Playwright).
    All other API errors (auth, network, malformed video) propagate as
    `status: failed` without falling back — Studio wouldn't help with those.

    Args:
      video: absolute path to the .mp4 file
      captions: per-platform caption block: {"title": "...", "description": "..."}
      scheduled_for: optional ISO datetime — if provided, video is uploaded as
                     `private` with `publishAt = scheduled_for`. YouTube auto-
                     publishes at that time. If None, video goes public immediately.

    Returns:
      dict matching the schedule_batch contract, with an extra "via" field:
        - "api"    — uploaded via Data API (happy path)
        - "studio" — uploaded via Studio web UI (quota fallback)
    """
    title = (captions or {}).get("title")
    description = (captions or {}).get("description", "") or ""

    if not title:
        return {"status": "failed", "via": "api",
                "error": "youtube caption block missing 'title'", "at": _now_iso()}

    if not CONFIG_FILE.exists():
        return {"status": "failed", "via": "api",
                "error": "config.json not found", "at": _now_iso()}
    try:
        cfg = json.loads(CONFIG_FILE.read_text()).get("youtube") or {}
    except json.JSONDecodeError as e:
        return {"status": "failed", "via": "api",
                "error": f"config.json invalid: {e}", "at": _now_iso()}

    if not cfg.get("client_id") or not cfg.get("client_secret"):
        return {"status": "failed", "via": "api",
                "error": "youtube credentials not configured", "at": _now_iso()}

    # Import the legacy youtube.py from the project root (sibling of platforms/).
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import youtube as legacy_yt
    except ImportError as e:
        return {"status": "failed", "via": "api",
                "error": f"legacy youtube.py not importable: {e}", "at": _now_iso()}
    finally:
        try:
            sys.path.remove(str(PROJECT_ROOT))
        except ValueError:
            pass

    # Capture the legacy upload's stdout so we can inspect it for the quota
    # signal even when the function returns None (its internal retry loop
    # swallows the HttpError and just prints it).
    capture_buf = io.StringIO()
    raised_exc: Optional[BaseException] = None
    result = None
    try:
        # Tee: still print to the real stdout (so dispatch logs see the upload
        # progress lines as before), but ALSO capture into our buffer.
        class _Tee(io.TextIOBase):
            def __init__(self, *streams):
                self._streams = streams
            def write(self, s):
                for st in self._streams:
                    try:
                        st.write(s)
                    except Exception:
                        pass
                return len(s)
            def flush(self):
                for st in self._streams:
                    try:
                        st.flush()
                    except Exception:
                        pass

        tee = _Tee(sys.stdout, capture_buf)
        with redirect_stdout(tee), redirect_stderr(tee):
            result = legacy_yt.upload(
                file_path=str(video),
                title=title,
                description=description,
                schedule_time=scheduled_for,
                config=cfg,
            )
    except Exception as e:
        raised_exc = e

    captured = capture_buf.getvalue()

    # ---- Quota fallback path ----
    if (result is None) and _is_quota_failure(captured, raised_exc):
        # Build a short summary of WHY the API failed for the receipt.
        note = "uploadLimitExceeded after legacy retries"
        return _post_via_studio(video, captions, scheduled_for, note)

    # ---- Hard failure path (not quota — e.g. auth, network, malformed video) ----
    if raised_exc is not None:
        return {
            "status": "failed", "via": "api",
            "error": f"{type(raised_exc).__name__}: {raised_exc}",
            "at": _now_iso(),
        }
    if not result:
        return {
            "status": "failed", "via": "api",
            "error": "youtube upload returned None (non-quota failure — see logs)",
            "at": _now_iso(),
        }

    # ---- Happy path ----
    return {
        "status": "scheduled" if scheduled_for else "success",
        "url": result.get("url"),
        "scheduled_for": scheduled_for,
        "video_id": result.get("id"),
        "via": "api",
        "at": _now_iso(),
    }
