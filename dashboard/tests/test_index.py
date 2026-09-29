import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from posting_dashboard.index import build_queue
from posting_dashboard.ledger import append_event, make_event
from posting_dashboard.snapshots import write_snapshot
from tests.helpers import NOW, cfg_for, write_receipt


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg, self.root = cfg_for(Path(self.tmp.name))
        posted = self.root / "posted/2026-09-30"
        failed = self.root / "failed"
        write_receipt(posted, "demo-clip-1", {
            "x": {"status": "scheduled", "scheduled_for": "2026-10-01T11:22:00-07:00"},
            "tiktok": {"status": "scheduled", "scheduled_for": "2026-10-01T10:45:00-07:00"},
            "reddit": {"status": "skipped"},
        }, caption_block={"destinations": {"x": {"content": {"caption": "Your posting queue, on autopilot"}},
                                           "tiktok": {"content": {"caption": "TikTok caption here"}}}})
        # Partial success inside failed/: the scheduled platform must still show.
        write_receipt(failed, "demo-clip-2", {
            "pinterest": {"status": "scheduled", "scheduled_for": "2026-10-03T08:00:00-07:00"},
            "instagram": {"status": "failed", "error": "Schedule control missing"},
        }, caption_block={"captions": {"pinterest": {"title": "Free social scheduler checklist"}}})
        (failed / "demo-clip-2.posted.json.previous").write_text("{}")
        # Legacy immediate post + far-future + old.
        write_receipt(posted, "demo-clip-3", {
            "pinterest": {"status": "success", "scheduling_skipped": True},
            "x": {"status": "scheduled", "scheduled_for": "2026-11-30T11:22:00-07:00"},
        }, media=False)
        shots = self.root / "logs/screenshots"
        (shots / "tiktok_demo-clip-1_scheduled_on_content_list_1.png").write_bytes(b"png")

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, **kw):
        return build_queue(self.cfg, now=NOW, **kw)

    def test_receipts_become_rows_in_window(self):
        data = self.q()
        got = {(r["basename"], r["platform"], r["display_status"]) for r in data["rows"]}
        self.assertEqual(got, {
            ("demo-clip-1", "x", "scheduled_unverified"),
            ("demo-clip-1", "tiktok", "scheduled_unverified"),
            ("demo-clip-2", "pinterest", "scheduled_unverified"),
        })
        tik = next(r for r in data["rows"] if r["platform"] == "tiktok")
        self.assertTrue(tik["media_path"].endswith("demo-clip-1.mp4"))
        self.assertTrue(tik["proof_screenshot"].endswith("_scheduled_on_content_list_1.png"))
        self.assertEqual(tik["caption_preview"], "TikTok caption here")
        self.assertNotIn("caption", tik)  # only the preview leaves the server

    def test_history_toggle_shows_failed_and_immediate(self):
        stats = {(r["basename"], r["platform"]): r["display_status"] for r in self.q(show_history=True)["rows"]}
        self.assertEqual(stats[("demo-clip-2", "instagram")], "failed")
        # Legacy immediate has no time -> not placeable, hidden; failed kept.
        self.assertNotIn(("demo-clip-3", "pinterest"), stats)

    def test_ledger_wins_over_receipt(self):
        append_event(self.cfg.ledger_path, make_event(
            basename="demo-clip-1", platform="x", status="cancelled", scheduled_for=None, source="agent"))
        data = self.q(show_history=True)
        x = next(r for r in data["rows"] if r["basename"] == "demo-clip-1" and r["platform"] == "x")
        self.assertEqual(x["display_status"], "skipped")

    def test_agent_only_ledger_rows_appear(self):
        append_event(self.cfg.ledger_path, make_event(
            basename="agent-clip-9", platform="threads", status="scheduled",
            scheduled_for="2026-10-01T11:22:00-07:00", caption="Queue once.", source="agent"))
        self.assertIn(("agent-clip-9", "threads"), {(r["basename"], r["platform"]) for r in self.q()["rows"]})

    def test_snapshot_verifies_and_flags(self):
        write_snapshot(self.cfg.snapshots_dir, "x", {
            "platform": "x", "status": "ok", "checked_at": (NOW - timedelta(minutes=3)).isoformat(),
            "items": [{"scheduled_for": "2026-10-01T11:22:00-07:00", "caption": "Your posting queue, on autopilot",
                       "date_only": False},
                      {"scheduled_for": "2026-10-02T09:00:00-07:00", "caption": "Hand-scheduled post",
                       "date_only": False, "url": "https://x.com/i/status/1"}]})
        write_snapshot(self.cfg.snapshots_dir, "tiktok", {
            "platform": "tiktok", "status": "ok", "checked_at": (NOW - timedelta(minutes=3)).isoformat(),
            "items": []})
        rows = self.q()["rows"]
        st = {(r["basename"], r["platform"]): r["display_status"] for r in rows}
        self.assertEqual(st[("demo-clip-1", "x")], "verified")
        self.assertEqual(st[("demo-clip-1", "tiktok")], "missing")
        untracked = [r for r in rows if r["display_status"] == "not_tracked"]
        self.assertEqual(len(untracked), 1)
        self.assertEqual(untracked[0]["link"], "https://x.com/i/status/1")
        self.assertEqual(untracked[0]["status_label"], "not tracked by the tool")

    def test_threads_snapshot_log_only_by_default(self):
        append_event(self.cfg.ledger_path, make_event(
            basename="agent-clip-9", platform="threads", status="scheduled",
            scheduled_for="2026-10-01T11:22:00-07:00", source="agent"))
        write_snapshot(self.cfg.snapshots_dir, "threads", {
            "platform": "threads", "status": "ok", "checked_at": NOW.isoformat(), "items": []})
        data = self.q()
        th = next(r for r in data["rows"] if r["platform"] == "threads")
        self.assertEqual(th["display_status"], "scheduled_unverified")
        self.assertFalse(data["platforms"]["threads"]["affects_status"])


if __name__ == "__main__":
    unittest.main()
