import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from posting_dashboard.locks import PostingRunActive, browser_check_guard
from posting_dashboard.snapshots import read_snapshot
from posting_dashboard.sync import runner
from tests.helpers import cfg_for

HOLDER = """
import fcntl, sys, time
f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)
"""


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg, self.root = cfg_for(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_refuses_when_dispatch_lock_is_held(self):
        lock = self.cfg.dispatch_lock_path
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "held")
            with self.assertRaises(PostingRunActive):
                with browser_check_guard(lock, self.cfg.runtime_path("in_flight")):
                    pass
        finally:
            proc.kill()
            proc.wait()
            proc.stdout.close()

    def test_refuses_when_in_flight_has_receipts(self):
        (self.cfg.runtime_path("in_flight") / "demo-clip.json").write_text("{}")
        with self.assertRaises(PostingRunActive):
            with browser_check_guard(self.cfg.dispatch_lock_path, self.cfg.runtime_path("in_flight")):
                pass

    def test_guard_holds_lock_and_cleans_up(self):
        lock = self.cfg.dispatch_lock_path
        with browser_check_guard(lock, self.cfg.runtime_path("in_flight")):
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_threads_and_studio_disabled(self):
        cfg, _ = cfg_for(Path(self.tmp.name), checks={"x": {"enabled": False}, "tiktok": {"enabled": False},
                                                       "instagram": {"enabled": False},
                                                       "pinterest": {"enabled": False},
                                                       "youtube_api": {"enabled": False}})
        res = runner.run_sync(cfg, log=lambda *_: None)
        self.assertEqual(res["threads"]["status"], "disabled")
        self.assertEqual(res["youtube_studio"]["status"], "disabled")

    def test_browser_checks_blocked_during_posting_run(self):
        cfg, _ = cfg_for(Path(self.tmp.name), checks={"youtube_api": {"enabled": False}})
        (cfg.runtime_path("in_flight") / "demo-clip.json").write_text("{}")
        with mock.patch.object(runner, "open_page") as op:
            res = runner.run_sync(cfg, log=lambda *_: None)
            op.assert_not_called()
        for c in ("x", "tiktok", "instagram", "pinterest"):
            self.assertEqual(res[c]["status"], "blocked")
            self.assertIn("posting run active", res[c]["note"])
        self.assertEqual(read_snapshot(cfg.snapshots_dir, "x")["status"], "blocked")

    def test_reader_exception_becomes_error_snapshot(self):
        cfg, _ = cfg_for(Path(self.tmp.name), checks={"youtube_api": {"enabled": False},
                                                       "tiktok": {"enabled": False},
                                                       "instagram": {"enabled": False},
                                                       "pinterest": {"enabled": False}})

        class Sess:
            def __enter__(self):
                return object()

            def __exit__(self, *a):
                return False

        def boom(*a, **k):
            raise RuntimeError("selector moved")

        with mock.patch.object(runner, "open_page", return_value=Sess()), \
                mock.patch.dict(runner.READERS, {"x": boom}):
            res = runner.run_sync(cfg, log=lambda *_: None)
        self.assertEqual(res["x"]["status"], "error")
        self.assertFalse(cfg.dispatch_lock_path.exists())


if __name__ == "__main__":
    unittest.main()


class BrowserModeTests(unittest.TestCase):
    def test_default_has_no_cdp_url_and_never_launches(self):
        from posting_dashboard.config import DEFAULTS
        self.assertEqual(DEFAULTS["browser"], {"cdp_url": None})
        import posting_dashboard.sync.browser as b
        src = Path(b.__file__).read_text()
        self.assertNotIn("launch(", src)
        self.assertNotIn("launch_persistent_context", src)

    def test_cdp_url_from_profile_json(self):
        with tempfile.TemporaryDirectory() as td:
            cfg, root = cfg_for(Path(td))
            self.assertEqual(cfg.cdp_url, "")
            (root / "profile.json").write_text(json.dumps(
                {"handle": "example-brand", "chrome": {"cdp": "http://127.0.0.1:9999"}}))
            from posting_dashboard.config import load_config
            cfg2 = load_config(Path(td) / "config.json")
            self.assertEqual(cfg2.cdp_url, "http://127.0.0.1:9999")
            self.assertEqual(cfg2.expected_handles["x"], "example-brand")

    def test_no_cdp_url_is_unavailable(self):
        from posting_dashboard.sync.browser import BrowserUnavailable, open_page
        with self.assertRaises(BrowserUnavailable) as cm:
            with open_page(""):
                pass
        self.assertIn("no CDP URL", str(cm.exception))

    def test_cdp_nothing_listening_is_unavailable(self):
        from posting_dashboard.sync.browser import BrowserUnavailable, open_page
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.skipTest("playwright not installed")
        with self.assertRaises(BrowserUnavailable):
            with open_page("http://127.0.0.1:9"):
                pass
