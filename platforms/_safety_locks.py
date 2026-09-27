"""Portable live-posting gates (preflight, native-list proof, one URL, walls, caps).

These sit in front of a batch schedule. They do not replace existing per-surface
locks (Threads refuse-to-fall-through, Threads 25-queue cap, TikTok
schedule-mismatch, X Escape-on-scheduled-path, etc.).

User identity comes from profile.json / env. This module ships no brand
handle, Chrome profile name, or CTA host. Missing configured handle = fail
closed. Never launch a second Chrome / TPT-Content-Manager-Chrome.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

# Example cadence helper only (documented, not a required default).
# Fixed UTC-7 offset used only by the example-cadence helper below.
EXAMPLE_CADENCE_TZ = ZoneInfo("Etc/GMT+7")

FORBIDDEN_CHROME_LAUNCHERS = frozenset(
    {
        "TPT-Content-Manager-Chrome",
        "tpt-content-manager-chrome",
        "Google Chrome TPT-Content-Manager-Chrome",
    }
)

DEFAULT_CDP = "http://127.0.0.1:9222"
DEFAULT_DAILY_CAPS = {
    "instagram": 25,
    "threads": 25,
}

PROFESSIONAL_SURFACES = frozenset({"instagram", "tiktok", "pinterest"})
# Default IG/Threads daily caps. Overridable via profile.json daily_caps.
DAILY_CAP_SURFACES = dict(DEFAULT_DAILY_CAPS)

LINK_IN_BIO_HOSTS = frozenset(
    {
        "linktr.ee",
        "lnk.bio",
        "beacons.ai",
        "bio.link",
        "allmylinks.com",
        "carrd.co",
    }
)
LINK_IN_BIO_PHRASES = (
    "link in bio",
    "linkinbio",
    "link-in-bio",
    "links in bio",
)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+|www\.[^\s<>\"')\]]+", re.IGNORECASE)

# Composer toasts are not proof. Encoded so agents stop treating them as commit.
COMPOSER_TOAST_IS_NOT_PROOF = True

# Native Schedule only. These labels are never a commit click.
FORBIDDEN_COMMIT_LABELS = frozenset(
    {
        "post now",
        "share",
        "publish immediately",
        "publish now",
        "post",
        "now",
    }
)


# ---------------------------------------------------------------------------
# Per-surface COMMIT click + native-list PROOF (Sep 1 2026 live posting)
# ---------------------------------------------------------------------------

SURFACE_CONTRACTS: dict[str, dict[str, Any]] = {
    "threads": {
        "commit_click": "footer_schedule",
        "open_schedule_ui": "header_kebab_next_to_drafts",
        "proof": "native_scheduled_list_via_header_kebab",
        "proof_not": (
            "composer_toast",
            "https://www.threads.com/scheduled",
            "https://threads.com/scheduled",
        ),
        "never": (
            "escape",
            "cancel",
            "leave_composer",
            "save_to_drafts",
            "post_now",
            "navigate_threads_com_scheduled",  # 404s
        ),
        "notes": (
            "threads.com/scheduled 404s. Header ⋯ (next to Drafts) opens "
            "Schedule. Footer Schedule commits. Cancel/leave = Save to drafts "
            "and kills the schedule. Never Escape."
        ),
    },
    "tiktok": {
        "commit_click": "schedule",
        "when_to_post": "Schedule",  # not Now
        "proof": "tiktokstudio_content_list",
        "proof_not": ("composer_toast", "upload_editor_still_open"),
        "never": (
            "post_now",
            "now",
            "navigate_away_after_attach",
            "discard_this_post",
        ),
        "slot": "requested_datetime_must_stick",
        "notes": (
            "Never navigate away from the Upload editor after attach "
            "(Discard this post kills the schedule). When to post → Schedule "
            "(not Now). Verify the datetime the sidecar/profile asked for "
            "actually stuck. Proof = Studio Content list. Even-hour :45 "
            "is a documented example cadence, not a required default."
        ),
    },
    "instagram": {
        "commit_click": "schedule_content",
        "alt_commit_click": "schedule",
        "proof": "native_scheduled_list",
        "proof_not": ("composer_toast", "share_spinner"),
        "requires_professional": True,
        "never": ("post_now", "share"),
        "notes": (
            "Business or Creator required. If Instagram hides Schedule content "
            "after Professional convert, do NOT Post now / Share."
        ),
    },
    "pinterest": {
        "commit_click": "publish_at_a_later_date",
        "proof": "native_scheduled_list",
        "proof_not": ("composer_toast",),
        "requires_professional": True,
        "never": ("publish_immediately", "post_now"),
        "notes": "Business account required. Never revert to Publish immediately.",
    },
    "x": {
        "commit_click": "schedule",
        "proof": "native_scheduled_list",
        "proof_url": "https://x.com/compose/post/unsent/scheduled",
        "proof_not": ("composer_toast_alone",),
        "never": ("escape_on_scheduled_path", "post_now"),
        "notes": "Existing X Escape lock stays. Toast alone is not batch proof.",
    },
}


WALL_KINDS = (
    "verify_its_you",
    "threads_posting_restriction",
    "missing_schedule_control",
    "datetime_picker_stuck",
    "chrome_crash_reopen",
)

WALL_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("verify_its_you", re.compile(r"verify\s*it['’]?s\s*you|verify\s+your\s+identity", re.I)),
    (
        "threads_posting_restriction",
        re.compile(r"posting\s+restriction|you\s+can['’]?t\s+post|temporarily\s+restricted", re.I),
    ),
    (
        "chrome_crash_reopen",
        re.compile(r"\breopen\b.*chrome|aw,\s*snap|didn['’]?t\s+restore|restore\s+pages", re.I),
    ),
    (
        "datetime_picker_stuck",
        re.compile(r"invalid\s+date|can['’]?t\s+set\s+(the\s+)?(date|time)|picker\s+stuck", re.I),
    ),
)


@dataclass(frozen=True)
class LockFailure:
    """A batch/surface must STOP. Never Post now as a fallback."""

    code: str
    message: str
    surface: Optional[str] = None
    human_step: Optional[str] = None
    remaining: Optional[dict] = None

    def as_dict(self) -> dict:
        out: dict[str, Any] = {
            "ok": False,
            "status": "failed",
            "error": self.message,
            "error_code": self.code,
            "post_now_forbidden": True,
        }
        if self.surface:
            out["surface"] = self.surface
        if self.human_step:
            out["human_step"] = self.human_step
            out["paused_on_wall"] = True
        if self.remaining is not None:
            out["remaining"] = self.remaining
        return out


@dataclass
class PreflightResult:
    ok: bool
    failures: list[LockFailure] = field(default_factory=list)
    remaining_slots: dict[str, dict[str, int]] = field(default_factory=dict)
    profile_id: str = ""
    chrome_mode: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "profile_id": self.profile_id,
            "chrome_mode": self.chrome_mode,
            "remaining_slots": self.remaining_slots,
            "failures": [f.as_dict() for f in self.failures],
        }


# ---------------------------------------------------------------------------
# User profile (file + env). Env wins. No shipped brand identity.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserProfile:
    handle: str = ""
    cta_host: str = ""
    site: str = ""
    attach_chrome: bool = False
    chrome_profile_directory: str = ""
    chrome_profile_name: str = ""
    cdp: str = DEFAULT_CDP
    timezone_name: str = ""
    daily_caps: dict[str, int] = field(default_factory=dict)
    source_path: str = ""

    def configured_handle(self) -> str:
        return normalize_handle(self.handle)

    def allowed_cta_host(self) -> str:
        host = (self.cta_host or "").strip().lower().lstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if host:
            return host
        site = (self.site or "").strip()
        if not site:
            return ""
        parsed = urlparse(site if "://" in site else f"https://{site}")
        netloc = (parsed.netloc or "").lower().lstrip(".")
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc

    def daily_cap(self, surface: str) -> Optional[int]:
        key = (surface or "").lower()
        if key in self.daily_caps:
            return int(self.daily_caps[key])
        return DEFAULT_DAILY_CAPS.get(key)

    def zoneinfo(self) -> ZoneInfo | timezone:
        name = (
            (os.environ.get("POSTING_TOOL_TIMEZONE") or "").strip()
            or (self.timezone_name or "").strip()
        )
        if name:
            try:
                return ZoneInfo(name)
            except Exception:
                pass
        return timezone.utc


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_profile_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_user_profile(path: Optional[Path] = None) -> UserProfile:
    """Load user identity from profile.json, then overlay env."""
    source = path
    if source is None:
        env_path = (os.environ.get("POSTING_TOOL_PROFILE_FILE") or "").strip()
        source = Path(env_path) if env_path else _repo_root() / "profile.json"
    data = _read_profile_file(source)

    chrome = data.get("chrome") if isinstance(data.get("chrome"), dict) else {}
    handles = data.get("handles") if isinstance(data.get("handles"), dict) else {}
    file_handle = (
        data.get("expected_handle")
        or data.get("handle")
        or handles.get("threads")
        or handles.get("instagram")
        or ""
    )
    file_cta = data.get("cta_host") or ""
    file_dir = chrome.get("profile_directory") or data.get("chrome_profile_directory") or ""
    file_name = chrome.get("profile_name") or data.get("chrome_profile_name") or ""
    file_cdp = chrome.get("cdp") or data.get("cdp") or DEFAULT_CDP
    file_attach = bool(chrome.get("attach_only") or data.get("attach_chrome"))
    caps = data.get("daily_caps") if isinstance(data.get("daily_caps"), dict) else {}

    env_handle = (os.environ.get("POSTING_TOOL_HANDLE") or "").strip()
    env_cta = (os.environ.get("POSTING_TOOL_CTA_HOST") or "").strip()
    env_dir = (os.environ.get("POSTING_TOOL_CHROME_PROFILE_DIRECTORY") or "").strip()
    env_name = (os.environ.get("POSTING_TOOL_CHROME_PROFILE_NAME") or "").strip()
    env_cdp = (os.environ.get("POSTING_TOOL_CDP") or "").strip()
    env_attach = (os.environ.get("POSTING_TOOL_ATTACH_CHROME") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    parsed_caps: dict[str, int] = {}
    for key, value in caps.items():
        try:
            parsed_caps[str(key).lower()] = int(value)
        except (TypeError, ValueError):
            continue

    return UserProfile(
        handle=env_handle or str(file_handle or ""),
        cta_host=env_cta or str(file_cta or ""),
        site=str(data.get("site") or ""),
        attach_chrome=env_attach or file_attach,
        chrome_profile_directory=env_dir or str(file_dir or ""),
        chrome_profile_name=env_name or str(file_name or ""),
        cdp=env_cdp or str(file_cdp or DEFAULT_CDP),
        timezone_name=str(data.get("timezone") or ""),
        daily_caps=parsed_caps,
        source_path=str(source) if source else "",
    )


def posting_tool_profile() -> str:
    return (os.environ.get("POSTING_TOOL_PROFILE") or "").strip().lower()


def is_attach_only_profile(profile: Optional[UserProfile] = None) -> bool:
    profile = profile or load_user_profile()
    return bool(profile.attach_chrome)


def normalize_handle(value: Optional[str]) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if "://" in text:
        try:
            path = urlparse(text).path
            if path:
                text = path
        except Exception:
            pass
    text = text.lstrip("/")
    if text.startswith("@"):
        text = text[1:]
    text = text.split("/")[0].split("?")[0].strip()
    return text.lower()


def expected_handle_for(
    surface: str,
    configured: Optional[str] = None,
    profile: Optional[UserProfile] = None,
) -> str:
    if configured:
        return normalize_handle(configured)
    profile = profile or load_user_profile()
    return profile.configured_handle()


def check_live_handle(
    observed: Optional[str],
    *,
    surface: str,
    configured: Optional[str] = None,
    profile: Optional[UserProfile] = None,
) -> Optional[LockFailure]:
    """Live handle must match configured handle. Missing config = fail closed."""
    want = expected_handle_for(surface, configured, profile=profile)
    if not want:
        return LockFailure(
            code="handle_not_configured",
            message=(
                f"{surface}: no handle configured in profile.json or "
                "POSTING_TOOL_HANDLE. Fail closed — will not guess a default "
                "brand. Wrong-account dispatch is worse than a skipped batch."
            ),
            surface=surface,
            human_step="Set the user's handle in profile.json or POSTING_TOOL_HANDLE and retry.",
        )
    handle = normalize_handle(observed)
    if not handle:
        return LockFailure(
            code="handle_missing",
            message=f"{surface}: live handle could not be read. Expected @{want}. STOP.",
            surface=surface,
            human_step="Confirm the tab is logged in as the configured handle. Do not switch accounts.",
        )
    if handle != want:
        return LockFailure(
            code="handle_mismatch",
            message=(
                f"{surface}: live handle @{handle} does not match configured "
                f"@{want}. STOP. Do not switch accounts."
            ),
            surface=surface,
            human_step="Wrong account is signed in. STOP. Do not switch accounts from the tool.",
        )
    return None


def check_professional_account(
    surface: str,
    *,
    is_professional: Optional[bool],
    schedule_ui_exists: Optional[bool] = None,
) -> Optional[LockFailure]:
    """IG / TikTok / Pinterest: Business or Creator required. Personal accounts cannot schedule."""
    if surface not in PROFESSIONAL_SURFACES:
        return None
    if is_professional is False or (
        is_professional is None and schedule_ui_exists is False
    ):
        return LockFailure(
            code="professional_required",
            message=(
                f"{surface}: Business or Creator account required. A Personal "
                "account cannot schedule and is a wall, not a tool bug. Failing the check "
                "now so this is not mistaken for a broken tool."
            ),
            surface=surface,
            human_step=(
                f"Convert this {surface} account to Business or Creator, confirm "
                "the Schedule control is visible, then retry. Do not Post now."
            ),
        )
    return None


def check_schedule_ui_exists(
    surface: str, *, schedule_ui_exists: bool, looks_like_toast: bool = False
) -> Optional[LockFailure]:
    """Schedule UI must actually exist (not a toast). Missing = wall, never Post now."""
    if looks_like_toast and not schedule_ui_exists:
        return LockFailure(
            code="schedule_ui_is_toast_not_control",
            message=(
                f"{surface}: saw a schedule toast, not a Schedule control. "
                "Toast is not the Schedule UI. STOP. Do not Post now."
            ),
            surface=surface,
            human_step="Find the real Schedule control. If Instagram hid Schedule content after Professional convert, do not Share/Post now.",
        )
    if not schedule_ui_exists:
        return LockFailure(
            code="missing_schedule_control",
            message=(
                f"{surface}: Schedule UI is missing. Native Schedule only. "
                "Never Post now."
            ),
            surface=surface,
            human_step=_wall_human_step("missing_schedule_control", surface),
        )
    return None


def is_forbidden_commit_click(label: Optional[str], *, surface: str) -> bool:
    """True if this click would publish now instead of native Schedule."""
    if not label:
        return False
    text = " ".join(str(label).strip().lower().split())
    never = SURFACE_CONTRACTS.get(surface, {}).get("never", ())
    if text in FORBIDDEN_COMMIT_LABELS:
        # Threads/X/TikTok "Post" is immediate. Schedule path must not hit it.
        return True
    if text in {n.replace("_", " ") for n in never}:
        return True
    if "post now" in text or "publish immediately" in text:
        return True
    commit = str(SURFACE_CONTRACTS.get(surface, {}).get("commit_click") or "").lower()
    if commit and text == commit.replace("_", " "):
        return False
    return False


def commit_click_for(surface: str) -> str:
    return str(SURFACE_CONTRACTS.get(surface, {}).get("commit_click") or "schedule")


def proof_kind_for(surface: str) -> str:
    return str(SURFACE_CONTRACTS.get(surface, {}).get("proof") or "native_scheduled_list")


def composer_toast_counts_as_proof() -> bool:
    return not COMPOSER_TOAST_IS_NOT_PROOF


def threads_scheduled_url_is_valid_proof(url: Optional[str]) -> bool:
    """threads.com/scheduled 404s — never treat it as proof."""
    if not url:
        return False
    lowered = url.strip().lower()
    if "threads.com/scheduled" in lowered or "threads.net/scheduled" in lowered:
        return False
    return False  # URL navigation is never Threads proof; header ⋯ list is.


def check_proof(
    surface: str,
    *,
    native_list_has_item: bool,
    composer_toast_seen: bool = False,
    proof_url: Optional[str] = None,
) -> Optional[LockFailure]:
    if surface == "threads" and proof_url and not threads_scheduled_url_is_valid_proof(proof_url):
        if "scheduled" in proof_url.lower():
            return LockFailure(
                code="threads_scheduled_url_404",
                message=(
                    "Threads proof cannot use threads.com/scheduled (it 404s). "
                    "Open Schedule from the header ⋯ next to Drafts."
                ),
                surface="threads",
                human_step="In the composer header, click ⋯ next to Drafts → Schedule. Confirm the item on that native list.",
            )
    if native_list_has_item:
        return None
    if composer_toast_seen:
        return LockFailure(
            code="proof_was_composer_toast",
            message=(
                f"{surface}: composer toast is not proof. Native scheduled list "
                f"did not show the item ({proof_kind_for(surface)})."
            ),
            surface=surface,
            human_step=f"Open the native scheduled list for {surface} and confirm the post is there. Do not retry via Post now.",
        )
    return LockFailure(
        code="native_list_proof_missing",
        message=(
            f"{surface}: schedule not proven on the native list "
            f"({proof_kind_for(surface)}). Composer state is not enough."
        ),
        surface=surface,
        human_step=f"Verify on the native scheduled list ({proof_kind_for(surface)}). Do not Post now.",
    )


def _host_of(url: str) -> str:
    text = url.strip()
    if text.lower().startswith("www."):
        text = "http://" + text
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    found = URL_RE.findall(text)
    # Mashed links: https://a.comhttps://b.com
    mashed = re.findall(r"https?://[^\s]+https?://", text, flags=re.I)
    extra: list[str] = []
    for m in mashed:
        extra.extend(URL_RE.findall(m))
    out: list[str] = []
    for u in found + extra:
        cleaned = u.rstrip(".,);")
        if cleaned not in out:
            out.append(cleaned)
    return out


def check_one_url_cta(
    caption: str,
    *,
    surface: str,
    allowed_host: Optional[str] = None,
) -> Optional[LockFailure]:
    """Caption/description CTA is a single host from profile.json / env.

    Wipe-composer is a runtime step (see wipe_required_before_paste). This
    check refuses mashed links, link-in-bio-as-CTA, and extra domains.
    """
    host = (allowed_host or load_user_profile().allowed_cta_host()).lower().lstrip(".")
    lowered = (caption or "").lower()
    for phrase in LINK_IN_BIO_PHRASES:
        if phrase in lowered:
            return LockFailure(
                code="cta_link_in_bio",
                message=(
                    f"{surface}: caption uses link-in-bio as CTA. Use a single "
                    f"{host or 'brand'} URL. No link-in-bio."
                ),
                surface=surface,
            )
    urls = extract_urls(caption or "")
    if not urls:
        return None  # soft CTA omitted is allowed; mashed/extra are not
    if not host:
        return LockFailure(
            code="cta_host_not_configured",
            message=(
                f"{surface}: caption has a URL but no CTA host is configured "
                "(profile.json cta_host / site, or POSTING_TOOL_CTA_HOST). "
                "Fail closed — will not invent a domain."
            ),
            surface=surface,
        )
    if len(urls) > 1:
        return LockFailure(
            code="cta_mashed_or_multiple_urls",
            message=(
                f"{surface}: caption has {len(urls)} URLs ({', '.join(urls)}). "
                "CTA must be a single URL. Wipe composer before paste."
            ),
            surface=surface,
        )
    url_host = _host_of(urls[0])
    if url_host in LINK_IN_BIO_HOSTS:
        return LockFailure(
            code="cta_link_in_bio_host",
            message=(
                f"{surface}: {urls[0]} is a link-in-bio host. Use a single "
                f"{host or 'brand'} URL."
            ),
            surface=surface,
        )
    if host and url_host and url_host != host and not url_host.endswith("." + host):
        return LockFailure(
            code="cta_extra_domain",
            message=(
                f"{surface}: CTA host {url_host!r} is not {host!r}. "
                "No extra domains."
            ),
            surface=surface,
        )
    return None


def wipe_required_before_paste() -> bool:
    return True


def detect_wall_kind(page_text: str) -> Optional[str]:
    if not page_text:
        return None
    for kind, pattern in WALL_TEXT_PATTERNS:
        if pattern.search(page_text):
            return kind
    return None


def _wall_human_step(kind: str, surface: Optional[str] = None) -> str:
    where = f" on {surface}" if surface else ""
    steps = {
        "verify_its_you": (
            f"A Verify-it's-you wall is up{where}. Complete it yourself in "
            "the already-open Chrome window. Do not Post now. Do not launch "
            "a second Chrome."
        ),
        "threads_posting_restriction": (
            "Threads posting restriction. Stop. Do not Post now. Wait or "
            "resolve the restriction in the already-open Chrome profile."
        ),
        "missing_schedule_control": (
            f"Schedule control is missing{where}. Stop. Do not Post now. "
            "If Instagram hid Schedule content after Professional convert, leave the composer."
        ),
        "datetime_picker_stuck": (
            f"Datetime picker is stuck{where}. Stop. Do not Post now. "
            "Fix the picker in the existing window or retry later."
        ),
        "chrome_crash_reopen": (
            "Chrome crash Reopen dialog. That Reopen is a second Chrome. "
            "Do not click Reopen. Do not launch TPT-Content-Manager-Chrome. "
            "Return to the already-open Chrome profile named in profile.json."
        ),
    }
    return steps.get(
        kind,
        f"Human wall ({kind}){where}. Pause. Never Post now as fallback.",
    )


def pause_on_wall(
    kind: str, *, surface: Optional[str] = None, detail: str = ""
) -> LockFailure:
    extra = f" {detail}" if detail else ""
    return LockFailure(
        code=f"human_wall_{kind}",
        message=(
            f"Paused on wall: {kind}.{extra} Native Schedule only. "
            "Never Post now as fallback."
        ),
        surface=surface,
        human_step=_wall_human_step(kind, surface),
    )


def scan_page_for_wall(page_text: str, *, surface: Optional[str] = None) -> Optional[LockFailure]:
    kind = detect_wall_kind(page_text)
    if kind:
        return pause_on_wall(kind, surface=surface)
    return None


def snap_tiktok_even_hour_45(dt: datetime) -> datetime:
    """Example cadence helper (even-hour :45, fixed UTC-7). Not a required default."""
    local = dt.astimezone(EXAMPLE_CADENCE_TZ) if dt.tzinfo else dt.replace(tzinfo=EXAMPLE_CADENCE_TZ)
    hour = local.hour if local.hour % 2 == 0 else local.hour + 1
    day = local.date()
    if hour >= 24:
        day = day + timedelta(days=1)
        hour = 0
    candidate = datetime(
        day.year, day.month, day.day, hour, 45, 0, tzinfo=EXAMPLE_CADENCE_TZ
    )
    if candidate < local:
        candidate = candidate + timedelta(hours=2)
    return candidate


def is_even_hour_45_example(dt: datetime) -> bool:
    local = dt.astimezone(EXAMPLE_CADENCE_TZ) if dt.tzinfo else dt.replace(tzinfo=EXAMPLE_CADENCE_TZ)
    return local.hour % 2 == 0 and local.minute == 45 and local.second == 0


def check_tiktok_slot(dt: datetime) -> Optional[LockFailure]:
    """Any parseable requested datetime is legal. Even-hour :45 is not required."""
    if not isinstance(dt, datetime):
        return LockFailure(
            code="tiktok_slot_unparseable",
            message=f"TikTok scheduled_for is unparseable ({dt!r}). Native Schedule needs a real datetime.",
            surface="tiktok",
            human_step="Pass the datetime the sidecar/profile asked for. Verify that slot stuck before commit.",
        )
    return None


def check_requested_datetime_stuck(
    requested: datetime,
    actual: Optional[datetime],
    *,
    surface: str,
    slack_seconds: int = 120,
) -> Optional[LockFailure]:
    if actual is None:
        return LockFailure(
            code="datetime_not_stuck",
            message=(
                f"{surface}: requested {requested.isoformat()} did not stick in "
                "the picker. PAUSE. Do not Post now."
            ),
            surface=surface,
            human_step="Re-set the datetime the sidecar/profile asked for. Never Post now.",
        )
    delta = abs((actual - requested).total_seconds())
    if delta > slack_seconds:
        return LockFailure(
            code="datetime_not_stuck",
            message=(
                f"{surface}: picker shows {actual.isoformat()} but sidecar asked "
                f"for {requested.isoformat()}. The requested slot must actually stick."
            ),
            surface=surface,
            human_step="Fix the picker so the requested datetime sticks. Do not Post now.",
        )
    return None


def daily_cap_for(surface: str, profile: Optional[UserProfile] = None) -> Optional[int]:
    profile = profile or load_user_profile()
    return profile.daily_cap(surface)


def remaining_slots(used: int, *, surface: str) -> dict[str, int]:
    cap = daily_cap_for(surface) or 0
    left = max(0, cap - max(0, used))
    return {"used": max(0, used), "cap": cap, "remaining": left}


def check_daily_rate_limit(used: int, *, surface: str, planning_count: int = 1) -> Optional[LockFailure]:
    """Refuse to dispatch into a 2am failure. Surface remaining slots first."""
    cap = daily_cap_for(surface)
    if cap is None:
        return None
    info = remaining_slots(used, surface=surface)
    if info["remaining"] <= 0:
        return LockFailure(
            code="daily_rate_limit_exhausted",
            message=(
                f"{surface}: 0 of {cap} daily slots left ({info['used']} used). "
                "Not dispatching — do not fail at 2am."
            ),
            surface=surface,
            remaining=info,
            human_step=f"Wait until the next day (your timezone). {surface} has 0 of {cap} left.",
        )
    if planning_count > info["remaining"]:
        return LockFailure(
            code="daily_rate_limit_would_overflow",
            message=(
                f"{surface}: {info['remaining']} of {cap} left, batch wants "
                f"{planning_count}. Truncate to remaining slots before dispatch. "
                "Do not fail at 2am."
            ),
            surface=surface,
            remaining=info,
        )
    return None


def format_remaining(info: dict[str, int], *, surface: str) -> str:
    return f"{surface}: {info.get('remaining', 0)} of {info.get('cap', 0)} left"


@dataclass(frozen=True)
class ChromeLaunchDecision:
    ok: bool
    mode: str  # attach_cdp | launch_persistent
    error: Optional[LockFailure] = None
    profile_directory: str = ""
    profile_name: str = ""
    cdp_url: str = ""


def chrome_launch_decision(
    *,
    require_tpt: Optional[bool] = None,
    would_launch_named: Optional[str] = None,
    attach: Optional[bool] = None,
    profile: Optional[UserProfile] = None,
) -> ChromeLaunchDecision:
    """Attach to the already-open Chrome named in config. Never a second Chrome.

    `require_tpt` is accepted for call-site compatibility and treated as
    attach-only. There is no default Chrome profile name.
    """
    profile = profile or load_user_profile()
    attach_only = profile.attach_chrome if attach is None else attach
    if require_tpt is True:
        attach_only = True
    directory = (profile.chrome_profile_directory or "").strip()
    name = (profile.chrome_profile_name or "").strip()
    cdp = (profile.cdp or DEFAULT_CDP).strip()

    if would_launch_named:
        lowered = would_launch_named.strip().lower()
        if lowered in {n.lower() for n in FORBIDDEN_CHROME_LAUNCHERS} or "tpt-content-manager-chrome" in lowered:
            return ChromeLaunchDecision(
                ok=False,
                mode="attach_cdp",
                error=pause_on_wall(
                    "chrome_crash_reopen",
                    detail=(
                        f"Refusing to launch {would_launch_named}. Attach to "
                        "the already-open Chrome profile named in profile.json "
                        "/ POSTING_TOOL_CHROME_PROFILE_NAME. Never launch a "
                        "named TPT-Content-Manager-Chrome."
                    ),
                ),
                profile_directory=directory,
                profile_name=name,
                cdp_url=cdp,
            )

    if not attach_only:
        return ChromeLaunchDecision(
            ok=True,
            mode="launch_persistent",
            profile_directory=directory,
            profile_name=name,
            cdp_url=cdp,
        )

    if not directory or not name:
        return ChromeLaunchDecision(
            ok=False,
            mode="attach_cdp",
            error=LockFailure(
                code="chrome_profile_not_configured",
                message=(
                    "Attach-only Chrome is on, but chrome.profile_directory / "
                    "chrome.profile_name (or POSTING_TOOL_CHROME_PROFILE_DIRECTORY "
                    "/ POSTING_TOOL_CHROME_PROFILE_NAME) are missing. Fail closed "
                    "— will not default to any profile."
                ),
                human_step=(
                    "Set the already-open Chrome profile the user configured, "
                    "then retry. Do not launch a second Chrome."
                ),
            ),
            profile_directory=directory,
            profile_name=name,
            cdp_url=cdp,
        )

    return ChromeLaunchDecision(
        ok=True,
        mode="attach_cdp",
        profile_directory=directory,
        profile_name=name,
        cdp_url=cdp,
    )


def verify_attached_chrome_profile(
    *,
    profile_directory: Optional[str],
    profile_name: Optional[str],
    expected_directory: Optional[str] = None,
    expected_name: Optional[str] = None,
    profile: Optional[UserProfile] = None,
) -> Optional[LockFailure]:
    profile = profile or load_user_profile()
    want_dir = (expected_directory or profile.chrome_profile_directory or "").strip()
    want_name = (expected_name or profile.chrome_profile_name or "").strip()
    directory = (profile_directory or "").strip()
    name = (profile_name or "").strip()
    if not want_dir or not want_name:
        return LockFailure(
            code="chrome_profile_not_configured",
            message=(
                "No Chrome profile directory/name configured. Fail closed — "
                "will not guess a profile name."
            ),
            human_step="Set chrome.profile_directory and chrome.profile_name in profile.json.",
        )
    if directory and directory != want_dir:
        return LockFailure(
            code="wrong_chrome_profile_directory",
            message=(
                f"Attached Chrome directory is {directory!r}, expected "
                f"{want_dir!r}. STOP. Do not switch profiles."
            ),
            human_step=(
                f"Use the already-open Chrome {want_dir} named {want_name}. "
                "Do not launch a second Chrome."
            ),
        )
    if name and name.lower() != want_name.lower():
        return LockFailure(
            code="wrong_chrome_profile_name",
            message=(
                f"Attached Chrome profile is {name!r}, expected "
                f"{want_name!r}. STOP. Do not switch profiles."
            ),
            human_step=(
                f"Return to the already-open Chrome named {want_name}. "
                "Do not click Reopen. Do not launch TPT-Content-Manager-Chrome."
            ),
        )
    return None


def refuse_unschedulable_batch() -> LockFailure:
    return LockFailure(
        code="native_schedule_required",
        message="Native Schedule only. Never Post now. A batch cannot run as immediate publish.",
        human_step="Pass scheduled_for and the five live-posting gates. Do not use --immediate.",
    )


def check_captions_batch(
    captions_by_surface: dict[str, Any],
    *,
    allowed_host: Optional[str] = None,
) -> list[LockFailure]:
    failures: list[LockFailure] = []
    for surface, block in (captions_by_surface or {}).items():
        if not isinstance(block, dict):
            continue
        text = " ".join(
            str(block.get(k) or "")
            for k in ("caption", "description", "title", "cta", "url")
        )
        fail = check_one_url_cta(text, surface=surface, allowed_host=allowed_host)
        if fail:
            failures.append(fail)
    return failures


def first_post_preflight_failures(
    surface: str,
    *,
    observed_handle: Optional[str],
    configured_handle: Optional[str] = None,
    is_professional: Optional[bool] = None,
    schedule_ui_exists: Optional[bool] = None,
    schedule_looks_like_toast: bool = False,
    page_text: str = "",
) -> list[LockFailure]:
    """PREFLIGHT before first post per surface."""
    failures: list[LockFailure] = []
    wall = scan_page_for_wall(page_text, surface=surface)
    if wall:
        failures.append(wall)
        return failures
    handle_fail = check_live_handle(
        observed_handle, surface=surface, configured=configured_handle
    )
    if handle_fail:
        failures.append(handle_fail)
        return failures  # STOP; do not switch accounts; skip later checks
    prof = check_professional_account(
        surface, is_professional=is_professional, schedule_ui_exists=schedule_ui_exists
    )
    if prof:
        failures.append(prof)
    if schedule_ui_exists is not None:
        sched = check_schedule_ui_exists(
            surface,
            schedule_ui_exists=bool(schedule_ui_exists),
            looks_like_toast=schedule_looks_like_toast,
        )
        if sched:
            failures.append(sched)
    return failures


def evaluate_batch_preflight(
    *,
    require_tpt_profile: bool = False,
    captions_by_video: Iterable[dict[str, Any]],
    daily_used: dict[str, int],
    planned_counts: dict[str, int],
    allowed_cta_host: Optional[str] = None,
    immediate: bool = False,
    configured_handle: Optional[str] = None,
    attach_chrome: Optional[bool] = None,
    profile: Optional[UserProfile] = None,
) -> PreflightResult:
    """Batch-level gates. No browser. Dispatch must not start if this fails.

    `require_tpt_profile` is accepted for call-site compatibility and ignored
    as an identity gate. Handle + Chrome profile come from profile.json / env.
    """
    profile = profile or load_user_profile()
    failures: list[LockFailure] = []
    profile_id = posting_tool_profile() or ("attach" if profile.attach_chrome else "")

    handle = normalize_handle(configured_handle) or profile.configured_handle()
    if not handle:
        failures.append(
            LockFailure(
                code="handle_not_configured",
                message=(
                    "No handle in profile.json / POSTING_TOOL_HANDLE. "
                    "Fail closed. Set the user's handle before dispatch."
                ),
                human_step="Copy profile.example.json to profile.json and set YOUR_HANDLE.",
            )
        )

    launch = chrome_launch_decision(
        require_tpt=True if require_tpt_profile else None,
        attach=attach_chrome,
        profile=profile,
    )
    if not launch.ok and launch.error:
        failures.append(launch.error)
    chrome_mode = launch.mode

    if immediate:
        failures.append(refuse_unschedulable_batch())

    remaining: dict[str, dict[str, int]] = {}
    for surface in DAILY_CAP_SURFACES:
        used = int(daily_used.get(surface, 0) or 0)
        info = remaining_slots(used, surface=surface)
        remaining[surface] = info
        planned = int(planned_counts.get(surface, 0) or 0)
        if planned <= 0:
            continue
        overflow = check_daily_rate_limit(used, surface=surface, planning_count=planned)
        if overflow and overflow.code == "daily_rate_limit_exhausted":
            failures.append(overflow)
        # would_overflow is a truncate signal, not a hard batch fail — caller
        # must cut the batch to remaining. Record it as remaining only.

    host = allowed_cta_host or profile.allowed_cta_host() or None
    for captions in captions_by_video:
        failures.extend(check_captions_batch(captions, allowed_host=host))

    return PreflightResult(
        ok=not failures,
        failures=failures,
        remaining_slots=remaining,
        profile_id=profile_id,
        chrome_mode=chrome_mode,
    )
