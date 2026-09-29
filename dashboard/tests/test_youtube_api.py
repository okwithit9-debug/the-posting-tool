import tempfile
import unittest
from pathlib import Path

from posting_dashboard.sync.youtube_api import items_from_videos, read_scheduled
from tests.helpers import NOW


class _Req:
    def __init__(self, resp):
        self.resp = resp

    def execute(self):
        return self.resp


class FakeYouTube:
    def __init__(self, videos):
        self.videos_data = videos
        self.calls = []

    def channels(self):
        outer = self

        class C:
            def list(self, **kw):
                outer.calls.append(("channels", kw))
                return _Req({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]})
        return C()

    def playlistItems(self):  # noqa: N802
        outer = self

        class P:
            def list(self, **kw):
                outer.calls.append(("playlistItems", kw))
                return _Req({"items": [{"contentDetails": {"videoId": v["id"]}} for v in outer.videos_data]})
        return P()

    def videos(self):
        outer = self

        class V:
            def list(self, **kw):
                outer.calls.append(("videos", kw))
                ids = kw["id"].split(",")
                return _Req({"items": [v for v in outer.videos_data if v["id"] in ids]})
        return V()


VIDEOS = [
    {"id": "sched1", "snippet": {"title": "Six apps, one queue"},
     "status": {"privacyStatus": "private", "publishAt": "2026-10-05T16:00:00Z"}},
    {"id": "past1", "snippet": {"title": "Went live"},
     "status": {"privacyStatus": "private", "publishAt": "2026-09-01T16:00:00Z"}},
    {"id": "pub1", "snippet": {"title": "Public"}, "status": {"privacyStatus": "public"}},
    {"id": "priv1", "snippet": {"title": "Private, no schedule"}, "status": {"privacyStatus": "private"}},
]


class YouTubeApiTests(unittest.TestCase):
    def test_items_only_future_private_publishat(self):
        items = items_from_videos(VIDEOS, now=NOW)
        self.assertEqual([i["video_id"] for i in items], ["sched1"])
        self.assertEqual(items[0]["url"], "https://studio.youtube.com/video/sched1/edit")

    def test_read_with_fake_service_is_read_only(self):
        fake = FakeYouTube(VIDEOS)
        snap = read_scheduled(Path("."), now=NOW, service=fake)
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(len(snap["items"]), 1)
        self.assertEqual({c[0] for c in fake.calls}, {"channels", "playlistItems", "videos"})

    def test_not_configured_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            snap = read_scheduled(Path(d), now=NOW)
        self.assertEqual(snap["status"], "unknown")
        self.assertIn("not", snap["note"])


if __name__ == "__main__":
    unittest.main()
