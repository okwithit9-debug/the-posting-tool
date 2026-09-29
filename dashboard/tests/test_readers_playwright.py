"""End-to-end reader flow in a real headless Chrome with ALL network
requests intercepted and answered from fixture HTML (no live sites).
Skipped when Playwright or a browser is unavailable."""

import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.helpers import HANDLE, html

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

from posting_dashboard.sync.readers import read_pinterest, read_tiktok, read_x
from posting_dashboard.sync.readonly import ReadOnlyPage

TZ = ZoneInfo("Etc/GMT+7")

ROUTES = {
    "https://x.com/home": "<html><body><a href='/example-brand'>Profile</a></body></html>",
    "https://x.com/compose/post/unsent/scheduled": html("x_scheduled.html"),
    "https://www.tiktok.com/tiktokstudio/content": html("tiktok_content.html"),
    "https://www.pinterest.com/example-brand/_created/": html("pinterest_created.html"),
}


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class ReaderFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        try:
            try:
                cls.browser = cls.pw.chromium.launch(headless=True)
            except Exception:
                cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover
            cls.pw.stop()
            raise unittest.SkipTest(f"no browser: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.page = self.browser.new_page()
        self.requested = []

        def handler(route):
            url = route.request.url
            self.requested.append(url)
            body = ROUTES.get(url)
            if body is None:
                return route.fulfill(status=404, body="not found")
            return route.fulfill(status=200, body=body, content_type="text/html")

        self.page.route("**/*", handler)

    def tearDown(self):
        self.page.close()
        self.tmp.cleanup()

    def run_reader(self, fn, check):
        ro = ReadOnlyPage(self.page, check)
        snap = fn(ro, Path(self.tmp.name), tz=TZ, handle=HANDLE, urls={})
        return snap, ro

    def test_x_flow(self):
        snap, ro = self.run_reader(read_x, "x")
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(len(snap["items"]), 2)
        self.assertTrue(Path(snap["screenshot"]).is_file())
        # Left the modal by navigating (x.com/home), never a key press.
        self.assertEqual(ro.actions[-1], "goto https://x.com/home")
        self.assertFalse(any(a.startswith("click") for a in ro.actions))

    def test_tiktok_flow_never_touches_upload(self):
        snap, ro = self.run_reader(read_tiktok, "tiktok")
        self.assertEqual(snap["status"], "ok")
        self.assertFalse(any("upload" in u for u in self.requested))
        self.assertEqual(ro.actions[-1], "leave about:blank")

    def test_pinterest_flow(self):
        snap, _ = self.run_reader(read_pinterest, "pinterest")
        self.assertEqual(snap["status"], "ok")
        self.assertEqual(len(snap["items"]), 2)


if __name__ == "__main__":
    unittest.main()
