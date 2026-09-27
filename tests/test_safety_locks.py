#!/usr/bin/env python3
"""Unit tests for portable live-posting gates. Fixture brand only — no live Chrome."""

from __future__ import annotations

import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from platforms._safety_locks import (
    UserProfile,
    check_live_handle,
    check_one_url_cta,
    check_professional_account,
    check_proof,
    check_requested_datetime_stuck,
    check_schedule_ui_exists,
    check_tiktok_slot,
    chrome_launch_decision,
    commit_click_for,
    composer_toast_counts_as_proof,
    evaluate_batch_preflight,
    first_post_preflight_failures,
    format_remaining,
    is_even_hour_45_example,
    is_forbidden_commit_click,
    load_user_profile,
    normalize_handle,
    pause_on_wall,
    remaining_slots,
    scan_page_for_wall,
    snap_tiktok_even_hour_45,
    threads_scheduled_url_is_valid_proof,
    verify_attached_chrome_profile,
    wipe_required_before_paste,
)
from platforms._preflight import count_daily_uses, daily_remaining_map, gate_commit_click

EXAMPLE_HANDLE = "example-brand"
EXAMPLE_HOST = "example.com"
EXAMPLE_CHROME_DIR = "Profile 1"
EXAMPLE_CHROME_NAME = "Example Brand"


def _env_without_identity(**extra: str) -> dict:
    cleaned = {
        "POSTING_TOOL_HANDLE": "",
        "POSTING_TOOL_CTA_HOST": "",
        "POSTING_TOOL_CHROME_PROFILE_DIRECTORY": "",
        "POSTING_TOOL_CHROME_PROFILE_NAME": "",
        "POSTING_TOOL_ATTACH_CHROME": "",
        "POSTING_TOOL_PROFILE": "",
        "POSTING_TOOL_PROFILE_FILE": extra.get("POSTING_TOOL_PROFILE_FILE", "/tmp/does-not-exist-profile.json"),
        "POSTING_TOOL_CDP": "",
        "POSTING_TOOL_TIMEZONE": extra.get("POSTING_TOOL_TIMEZONE", "UTC"),
    }
    cleaned.update(extra)
    return cleaned


class HandlePreflightTests(unittest.TestCase):
    def test_fixture_handle_normalizes(self):
        self.assertEqual(normalize_handle("@Example-Brand"), EXAMPLE_HANDLE)
        self.assertEqual(
            normalize_handle("https://www.threads.net/@example-brand"),
            EXAMPLE_HANDLE,
        )

    def test_wrong_handle_is_stop_do_not_switch(self):
        fail = check_live_handle(
            "wrongbrand", surface="instagram", configured=f"@{EXAMPLE_HANDLE}"
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "handle_mismatch")
        self.assertIn("Do not switch accounts", fail.human_step)
        self.assertIn("STOP", fail.message)

    def test_generic_wrong_handle_stops(self):
        fail = check_live_handle(
            "@wrongbrand", surface="threads", configured=EXAMPLE_HANDLE
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "handle_mismatch")

    def test_matching_configured_handle_passes(self):
        self.assertIsNone(
            check_live_handle(
                EXAMPLE_HANDLE, surface="x", configured=f"@{EXAMPLE_HANDLE}"
            )
        )

    def test_missing_configured_handle_fails_closed(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            fail = check_live_handle("someone", surface="instagram")
            self.assertIsNotNone(fail)
            self.assertEqual(fail.code, "handle_not_configured")


class ProfessionalAndScheduleUiTests(unittest.TestCase):
    def test_personal_ig_fails_before_tool_looks_broken(self):
        fail = check_professional_account(
            "instagram", is_professional=False, schedule_ui_exists=False
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "professional_required")
        self.assertIn("not a tool bug", fail.message)
        self.assertIn("Do not Post now", fail.human_step)

    def test_personal_inferred_from_missing_schedule_ui(self):
        fail = check_professional_account(
            "tiktok", is_professional=None, schedule_ui_exists=False
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "professional_required")

    def test_missing_schedule_ui_is_wall_not_post_now(self):
        fail = check_schedule_ui_exists(
            "instagram", schedule_ui_exists=False, looks_like_toast=False
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "missing_schedule_control")
        self.assertTrue(fail.as_dict()["post_now_forbidden"])
        blob = (fail.human_step or "").lower() + (fail.message or "").lower()
        self.assertIn("do not post now", blob)

    def test_schedule_toast_is_not_the_control(self):
        fail = check_schedule_ui_exists(
            "instagram", schedule_ui_exists=False, looks_like_toast=True
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "schedule_ui_is_toast_not_control")


class ProofAndCommitTests(unittest.TestCase):
    def test_composer_toast_is_not_proof(self):
        self.assertFalse(composer_toast_counts_as_proof())
        fail = check_proof(
            "threads", native_list_has_item=False, composer_toast_seen=True
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "proof_was_composer_toast")

    def test_native_list_is_proof(self):
        self.assertIsNone(
            check_proof("tiktok", native_list_has_item=True, composer_toast_seen=True)
        )

    def test_threads_scheduled_url_404_is_not_proof(self):
        self.assertFalse(threads_scheduled_url_is_valid_proof("https://www.threads.com/scheduled"))
        fail = check_proof(
            "threads",
            native_list_has_item=False,
            proof_url="https://www.threads.com/scheduled",
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "threads_scheduled_url_404")
        self.assertIn("header", fail.human_step.lower())

    def test_commit_click_encoded_per_surface(self):
        self.assertEqual(commit_click_for("threads"), "footer_schedule")
        self.assertEqual(commit_click_for("tiktok"), "schedule")
        self.assertEqual(commit_click_for("instagram"), "schedule_content")
        self.assertTrue(is_forbidden_commit_click("Post now", surface="tiktok"))
        self.assertTrue(is_forbidden_commit_click("Share", surface="instagram"))
        self.assertTrue(is_forbidden_commit_click("Publish immediately", surface="pinterest"))
        fail = gate_commit_click("tiktok", "Post now")
        self.assertIsNotNone(fail)
        self.assertTrue(fail.as_dict()["paused_on_wall"])


class OneUrlCtaTests(unittest.TestCase):
    def test_wipe_required_before_paste(self):
        self.assertTrue(wipe_required_before_paste())

    def test_single_configured_url_ok(self):
        self.assertIsNone(
            check_one_url_cta(
                f"New batch out now https://{EXAMPLE_HOST}/batch",
                surface="instagram",
                allowed_host=EXAMPLE_HOST,
            )
        )

    def test_mashed_links_fail(self):
        fail = check_one_url_cta(
            f"https://{EXAMPLE_HOST} https://other.com/x",
            surface="threads",
            allowed_host=EXAMPLE_HOST,
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "cta_mashed_or_multiple_urls")

    def test_link_in_bio_fails(self):
        fail = check_one_url_cta(
            "Full guide — link in bio",
            surface="instagram",
            allowed_host=EXAMPLE_HOST,
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "cta_link_in_bio")

    def test_extra_domain_fails(self):
        fail = check_one_url_cta(
            "See https://bit.ly/abc",
            surface="pinterest",
            allowed_host=EXAMPLE_HOST,
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "cta_extra_domain")

    def test_url_without_configured_host_fails_closed(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            fail = check_one_url_cta(
                "See https://example.com/x",
                surface="instagram",
                allowed_host=None,
            )
            self.assertIsNotNone(fail)
            self.assertEqual(fail.code, "cta_host_not_configured")


class WallTests(unittest.TestCase):
    def test_verify_its_you_pauses(self):
        fail = scan_page_for_wall("Verify it's you to continue", surface="instagram")
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "human_wall_verify_its_you")
        self.assertIn("Do not Post now", fail.human_step)

    def test_threads_restriction_pauses(self):
        fail = scan_page_for_wall("You have a posting restriction", surface="threads")
        self.assertIsNotNone(fail)
        self.assertTrue(fail.code.endswith("threads_posting_restriction"))

    def test_chrome_reopen_is_second_chrome(self):
        fail = pause_on_wall("chrome_crash_reopen")
        self.assertIn("second Chrome", fail.human_step)
        self.assertIn("TPT-Content-Manager-Chrome", fail.human_step)

    def test_datetime_picker_stuck(self):
        fail = scan_page_for_wall("Can't set the date", surface="tiktok")
        self.assertIsNotNone(fail)
        self.assertIn("datetime_picker_stuck", fail.code)


class RateLimitTests(unittest.TestCase):
    def test_surfaces_remaining_slots(self):
        info = remaining_slots(13, surface="instagram")
        self.assertEqual(info, {"used": 13, "cap": 25, "remaining": 12})
        self.assertEqual(format_remaining(info, surface="instagram"), "instagram: 12 of 25 left")

    def test_pack_preflight_surfaces_remaining_and_refuses_exhausted(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            result = evaluate_batch_preflight(
                captions_by_video=[],
                daily_used={"instagram": 25, "threads": 10},
                planned_counts={"instagram": 1, "threads": 2},
                configured_handle=EXAMPLE_HANDLE,
                attach_chrome=False,
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.remaining_slots["threads"]["remaining"], 15)
        codes = {f.code for f in result.failures}
        self.assertIn("daily_rate_limit_exhausted", codes)

    def test_count_daily_uses_from_receipts(self):
        with TemporaryDirectory() as tmp:
            posted = Path(tmp) / "posted" / "2026-09-02"
            posted.mkdir(parents=True)
            (posted / "clip.posted.json").write_text(
                """{"platforms": {"instagram": {"status": "scheduled", "at": "2026-09-02T18:00:00+00:00"}}}"""
            )
            now = datetime.fromisoformat("2026-09-02T20:00:00+00:00")
            with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
                self.assertEqual(count_daily_uses(posted.parent, "instagram", now=now), 1)
                leftover = daily_remaining_map(posted.parent, now=now)
            self.assertEqual(leftover["instagram"]["remaining"], 24)


class ChromeProfileTests(unittest.TestCase):
    def test_attach_without_chrome_name_fails_closed(self):
        with mock.patch.dict(os.environ, _env_without_identity(POSTING_TOOL_ATTACH_CHROME="1"), clear=False):
            decision = chrome_launch_decision()
            self.assertFalse(decision.ok)
            self.assertEqual(decision.error.code, "chrome_profile_not_configured")

    def test_attach_uses_configured_profile_never_second_chrome(self):
        env = _env_without_identity(
            POSTING_TOOL_ATTACH_CHROME="1",
            POSTING_TOOL_CHROME_PROFILE_DIRECTORY=EXAMPLE_CHROME_DIR,
            POSTING_TOOL_CHROME_PROFILE_NAME=EXAMPLE_CHROME_NAME,
        )
        with mock.patch.dict(os.environ, env, clear=False):
            decision = chrome_launch_decision()
            self.assertTrue(decision.ok)
            self.assertEqual(decision.mode, "attach_cdp")
            self.assertEqual(decision.profile_directory, EXAMPLE_CHROME_DIR)
            self.assertEqual(decision.profile_name, EXAMPLE_CHROME_NAME)
            blocked = chrome_launch_decision(would_launch_named="TPT-Content-Manager-Chrome")
            self.assertFalse(blocked.ok)
            self.assertIn("TPT-Content-Manager-Chrome", blocked.error.message)

    def test_wrong_attached_profile_stops(self):
        profile = UserProfile(
            chrome_profile_directory=EXAMPLE_CHROME_DIR,
            chrome_profile_name=EXAMPLE_CHROME_NAME,
        )
        fail = verify_attached_chrome_profile(
            profile_directory="Default",
            profile_name="Person 1",
            profile=profile,
        )
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "wrong_chrome_profile_directory")

    def test_without_attach_keeps_existing_persistent_launch(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            decision = chrome_launch_decision()
            self.assertTrue(decision.ok)
            self.assertEqual(decision.mode, "launch_persistent")


class TiktokSlotTests(unittest.TestCase):
    def test_example_cadence_helper_still_snaps(self):
        dt = datetime.fromisoformat("2026-09-02T15:10:00-07:00")
        snapped = snap_tiktok_even_hour_45(dt)
        self.assertTrue(is_even_hour_45_example(snapped))
        self.assertEqual(snapped.hour, 16)
        self.assertEqual(snapped.minute, 45)

    def test_requested_slot_is_not_forced_to_example_cadence(self):
        dt = datetime.fromisoformat("2026-09-02T15:10:00-07:00")
        self.assertIsNone(check_tiktok_slot(dt))

    def test_requested_datetime_must_stick(self):
        want = datetime.fromisoformat("2026-09-02T15:10:00+00:00")
        got = datetime.fromisoformat("2026-09-02T18:00:00+00:00")
        fail = check_requested_datetime_stuck(want, got, surface="tiktok")
        self.assertIsNotNone(fail)
        self.assertEqual(fail.code, "datetime_not_stuck")
        self.assertIsNone(check_requested_datetime_stuck(want, want, surface="tiktok"))


class CombinedFirstPostTests(unittest.TestCase):
    def test_first_post_preflight_stops_on_wrong_handle_before_schedule(self):
        failures = first_post_preflight_failures(
            "instagram",
            observed_handle="wrongbrand",
            configured_handle=EXAMPLE_HANDLE,
            is_professional=True,
            schedule_ui_exists=True,
        )
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].code, "handle_mismatch")

    def test_immediate_pack_refused(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            result = evaluate_batch_preflight(
                captions_by_video=[],
                daily_used={"instagram": 0, "threads": 0},
                planned_counts={},
                immediate=True,
                configured_handle=EXAMPLE_HANDLE,
                attach_chrome=False,
            )
        self.assertFalse(result.ok)
        self.assertIn("native_schedule_required", {f.code for f in result.failures})

    def test_cta_in_pack_preflight(self):
        with mock.patch.dict(os.environ, _env_without_identity(), clear=False):
            result = evaluate_batch_preflight(
                captions_by_video=[
                    {"instagram": {"caption": f"go https://spam.example/x and https://{EXAMPLE_HOST}"}}
                ],
                daily_used={"instagram": 0, "threads": 0},
                planned_counts={"instagram": 1},
                allowed_cta_host=EXAMPLE_HOST,
                configured_handle=EXAMPLE_HANDLE,
                attach_chrome=False,
            )
        self.assertFalse(result.ok)
        self.assertTrue(any(f.code == "cta_mashed_or_multiple_urls" for f in result.failures))

    def test_load_user_profile_from_file_placeholders(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(
                json.dumps(
                    {
                        "handle": "YOUR_HANDLE",
                        "cta_host": "yourdomain.com",
                        "chrome": {
                            "attach_only": True,
                            "profile_directory": EXAMPLE_CHROME_DIR,
                            "profile_name": "the name you gave that Chrome profile",
                        },
                    }
                )
            )
            with mock.patch.dict(
                os.environ,
                _env_without_identity(POSTING_TOOL_PROFILE_FILE=str(path)),
                clear=False,
            ):
                profile = load_user_profile(path)
            self.assertEqual(profile.configured_handle(), "your_handle")
            self.assertEqual(profile.allowed_cta_host(), "yourdomain.com")
            self.assertEqual(profile.chrome_profile_directory, EXAMPLE_CHROME_DIR)


REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_DOCS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "INSTALL.md",
    REPO_ROOT / "profile.example.json",
    REPO_ROOT / "docs" / "PLAYBOOK.md",
    REPO_ROOT / "docs" / "CLOUD_MODEL_PRIMARY.md",
    REPO_ROOT / "docs" / "SETUP_GATES.md",
    REPO_ROOT / "docs" / "SNAPSHOT_EVALS.md",
)

# Generic identity leaks that must never land in the repo: real home-directory
# paths, non-example email addresses, and non-default Chrome profile numbers.
FORBIDDEN_IDENTITY_PATTERNS = (
    re.compile(r"/Users/(?!<)[A-Za-z0-9._-]+/"),
    re.compile(r"/home/(?!<)[A-Za-z0-9._-]+/"),
    re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(com|org|net)\b)[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    re.compile(r"Profile\s*([2-9]|\d{2,})\b"),
    re.compile(r"drive\.google\.com/drive/folders/", re.I),
)
TEXT_SUFFIXES = {".py", ".md", ".json", ".sh", ".command", ".plist", ".txt", ".example"}


class RepoIdentityTests(unittest.TestCase):
    def test_core_docs_exist_and_name_the_tool(self):
        for path in CORE_DOCS:
            self.assertTrue(path.is_file(), f"missing doc: {path}")
        readme = (REPO_ROOT / "README.md").read_text()
        self.assertIn("The Posting Tool", readme)
        self.assertIn("docs/PLAYBOOK.md", readme)
        playbook = (REPO_ROOT / "docs" / "PLAYBOOK.md").read_text()
        self.assertIn("YOUR_HANDLE", playbook)
        self.assertIn("YOUR_CTA_HOST", playbook)

    def test_no_personal_identity_in_tree(self):
        hits = []
        skip_dirs = {".git", "__pycache__", ".venv", "venv"}
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or any(part in skip_dirs for part in path.parts):
                continue
            if path.suffix not in TEXT_SUFFIXES and path.name not in {".gitignore", ".env.example"}:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(errors="ignore")
            for pat in FORBIDDEN_IDENTITY_PATTERNS:
                for match in pat.finditer(text):
                    line_no = text.count("\n", 0, match.start()) + 1
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{line_no}: {match.group(0)!r}")
        self.assertEqual(hits, [], "personal identity strings found:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
