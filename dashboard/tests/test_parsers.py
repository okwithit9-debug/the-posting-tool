import unittest
from zoneinfo import ZoneInfo

from posting_dashboard.sync.parsers import parse_native_list
from tests.helpers import HANDLE, NOW, html

TZ = ZoneInfo("Etc/GMT+7")


def parse(check, name, handle=HANDLE):
    return parse_native_list(check, html(name), now=NOW, tz=TZ, expected_handle=handle)


class XParserTests(unittest.TestCase):
    def test_scheduled_rows(self):
        s = parse("x", "x_scheduled.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["items"]), 2)
        self.assertEqual(s["items"][0]["scheduled_for"], "2026-10-01T11:22:00-07:00")
        self.assertTrue(s["items"][0]["caption"].startswith("Your posting queue"))

    def test_positive_empty_state_is_ok_and_empty(self):
        s = parse("x", "x_empty.html")
        self.assertEqual((s["status"], s["items"]), ("ok", []))

    def test_wall_is_blocked(self):
        self.assertEqual(parse("x", "x_wall.html")["status"], "blocked")

    def test_changed_ui_is_unknown_not_empty(self):
        s = parse("x", "x_changed_ui.html")
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(s["items"], [])

    def test_wrong_or_missing_handle_is_unknown(self):
        self.assertEqual(parse("x", "x_wrong_handle.html")["status"], "unknown")
        self.assertEqual(parse("x", "x_scheduled.html", handle=None)["status"], "unknown")

    def test_unreadable_time_poisons_the_read(self):
        s = parse("x", "x_bad_time.html")
        self.assertEqual(s["status"], "unknown")
        self.assertEqual(s["items"], [])


class OtherParserTests(unittest.TestCase):
    def test_tiktok_only_scheduled_rows(self):
        s = parse("tiktok", "tiktok_content.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["items"]), 1)
        it = s["items"][0]
        self.assertEqual(it["scheduled_for"], "2026-10-02T10:45:00-07:00")
        self.assertIn("copy-pasting", it["caption"])
        self.assertTrue(it["url"].startswith("https://www.tiktok.com/@example-brand/video/"))

    def test_tiktok_published_only_is_unknown(self):
        # Rows seen but none scheduled and no empty marker: don't claim "empty".
        self.assertEqual(parse("tiktok", "tiktok_published_only.html")["status"], "unknown")

    def test_instagram_planner(self):
        s = parse("instagram", "instagram_planner.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["items"]), 1)
        self.assertEqual(s["items"][0]["scheduled_for"], "2026-10-02T10:35:00-07:00")

    def test_pinterest_date_only(self):
        s = parse("pinterest", "pinterest_created.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual([i["date_only"] for i in s["items"]], [True, True])
        self.assertEqual(s["items"][0]["caption"], "Free social scheduler checklist")
        self.assertEqual(parse("pinterest", "pinterest_empty.html")["status"], "ok")

    def test_threads(self):
        s = parse("threads", "threads_scheduled.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(s["items"][0]["scheduled_for"], "2026-10-01T11:22:00-07:00")

    def test_youtube_studio(self):
        s = parse("youtube_studio", "youtube_studio.html")
        self.assertEqual(s["status"], "ok")
        self.assertEqual(len(s["items"]), 1)
        self.assertEqual(s["items"][0]["video_id"], "abcDEF12345")
        self.assertEqual(s["items"][0]["caption"], "Six apps, one queue")


if __name__ == "__main__":
    unittest.main()
