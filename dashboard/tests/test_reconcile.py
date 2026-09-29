import unittest
from datetime import timedelta
from zoneinfo import ZoneInfo

from posting_dashboard.reconcile import (FAILED, IMMEDIATE, MISSING, NOT_TRACKED, PUBLISHED, UNVERIFIED,
                                     VERIFIED, captions_agree, reconcile_platform)
from tests.helpers import NOW

TZ = ZoneInfo("Etc/GMT+7")


def row(sf="2026-10-01T11:22:00-07:00", cap="Your posting queue, on autopilot", **kw):
    return {"basename": "b", "platform": "x", "status": "scheduled", "scheduled_for": sf, "caption": cap, **kw}


def snap(items, status="ok", age_min=5):
    return {"platform": "x", "status": status, "checked_at": (NOW - timedelta(minutes=age_min)).isoformat(),
            "items": items}


def item(sf="2026-10-01T11:22:00-07:00", cap="Your posting queue, on autopilot. Free", **kw):
    return {"scheduled_for": sf, "caption": cap, "date_only": False, **kw}


def rec(tracked, s, **kw):
    return reconcile_platform(tracked, s, now=NOW, tz=TZ, **kw)


class ReconcileTests(unittest.TestCase):
    def test_verified(self):
        t, u = rec([row()], snap([item()]))
        self.assertEqual(t[0]["display_status"], VERIFIED)
        self.assertEqual(u, [])

    def test_time_tolerance(self):
        t, _ = rec([row()], snap([item(sf="2026-10-01T11:23:30-07:00")]))
        self.assertEqual(t[0]["display_status"], VERIFIED)
        t, _ = rec([row()], snap([item(sf="2026-10-01T11:30:00-07:00")]))
        self.assertEqual(t[0]["display_status"], MISSING)

    def test_missing_and_not_tracked(self):
        t, u = rec([row()], snap([item(sf="2026-10-02T09:00:00-07:00", cap="Something else entirely")]))
        self.assertEqual(t[0]["display_status"], MISSING)
        self.assertEqual([x["display_status"] for x in u], [NOT_TRACKED])

    def test_empty_ok_list_means_missing(self):
        t, _ = rec([row()], snap([]))
        self.assertEqual(t[0]["display_status"], MISSING)

    def test_unknown_blocked_or_stale_snapshot_never_flags_missing(self):
        for s in (snap([], status="unknown"), snap([], status="blocked"), snap([], age_min=10_000), None):
            t, u = rec([row()], s)
            self.assertEqual(t[0]["display_status"], UNVERIFIED)
            self.assertEqual(u, [])

    def test_log_only_does_not_affect_status(self):
        t, u = rec([row()], snap([]), affects_status=False)
        self.assertEqual(t[0]["display_status"], UNVERIFIED)
        self.assertEqual(u, [])

    def test_caption_mismatch_same_time_is_missing(self):
        t, u = rec([row()], snap([item(cap="A totally different post about cats")]))
        self.assertEqual(t[0]["display_status"], MISSING)
        self.assertEqual(len(u), 1)

    def test_no_native_caption_unique_time_matches(self):
        t, _ = rec([row()], snap([item(cap="")]))
        self.assertEqual(t[0]["display_status"], VERIFIED)

    def test_no_native_caption_ambiguous_stays_unverified(self):
        t, _ = rec([row()], snap([item(cap=""), item(cap="")]))
        self.assertEqual(t[0]["display_status"], UNVERIFIED)

    def test_date_only_matches_same_local_day(self):
        t, _ = rec([row(sf="2026-10-03T08:00:00-07:00", cap="Free social scheduler checklist")],
                   snap([item(sf="2026-10-03T00:00:00-07:00", cap="Free social scheduler checklist", date_only=True)]))
        self.assertEqual(t[0]["display_status"], VERIFIED)

    def test_youtube_video_id_match(self):
        t, _ = rec([row(video_id="abc", cap="x")], snap([item(sf="2026-10-05T09:00:00-07:00", cap="y", video_id="abc")]))
        self.assertEqual(t[0]["display_status"], VERIFIED)

    def test_history_statuses(self):
        rows = [row(sf="2026-09-29T11:22:00-07:00"), row(status="failed"),
                row(status="success", went_out_immediately=True)]
        t, _ = rec(rows, snap([]))
        self.assertEqual([r["display_status"] for r in t], [PUBLISHED, FAILED, IMMEDIATE])

    def test_past_native_items_are_not_untracked(self):
        _, u = rec([], snap([item(sf="2026-09-29T11:22:00-07:00")]))
        self.assertEqual(u, [])

    def test_captions_agree(self):
        self.assertTrue(captions_agree("Hello world https://example.com", "hello, world!"))
        self.assertIsNone(captions_agree("", "x"))
        self.assertFalse(captions_agree("Schedule once, post everywhere", "Your posting queue"))


if __name__ == "__main__":
    unittest.main()
