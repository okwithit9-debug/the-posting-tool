import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from posting_dashboard.server import SyncJob, make_handler, safe_media_path
from tests.helpers import cfg_for, write_receipt

DASHBOARD = Path(__file__).resolve().parent.parent


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg, cls.root = cfg_for(Path(cls.tmp.name))
        write_receipt(cls.root / "posted/2026-09-30", "demo-clip-1",
                      {"x": {"status": "scheduled", "scheduled_for": "2030-01-01T11:22:00-07:00"}})
        (Path(cls.tmp.name) / "secret.txt").write_text("nope")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.cfg, None, SyncJob()))
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def get(self, path, host=None, method="GET", headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=headers or {})
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_page_and_queue(self):
        code, body, _ = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Sync now", body)
        code, body, _ = self.get("/api/queue")
        self.assertEqual(code, 200)
        self.assertIn("rows", json.loads(body))

    def test_foreign_host_header_refused(self):  # DNS-rebinding guard
        self.assertEqual(self.get("/api/queue", host="evil.example.com")[0], 403)

    def test_post_needs_custom_header(self):
        self.assertEqual(self.get("/api/sync", method="POST")[0], 403)

    def test_media_allow_list(self):
        mp4 = self.root / "posted/2026-09-30/demo-clip-1.mp4"
        self.assertEqual(safe_media_path(self.cfg, str(mp4)), mp4.resolve())
        self.assertIsNone(safe_media_path(self.cfg, str(Path(self.tmp.name) / "secret.txt")))
        self.assertIsNone(safe_media_path(self.cfg, str(mp4.parent / ".." / ".." / ".." / ".." / "secret.txt")))
        self.assertEqual(self.get("/media?p=" + urllib.request.quote(str(Path(self.tmp.name) / "secret.txt")))[0], 404)
        code, body, headers = self.get("/media?p=" + urllib.request.quote(str(mp4)), headers={"Range": "bytes=0-3"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 4)

    def test_refuses_non_loopback_bind(self):
        from posting_dashboard.server import serve
        self.cfg.raw["host"] = "0.0.0.0"
        try:
            with self.assertRaises(SystemExit):
                serve(self.cfg)
        finally:
            self.cfg.raw["host"] = "127.0.0.1"


class IdentityTests(unittest.TestCase):
    """No personal paths, emails, or profile numbers in the dashboard tree."""

    PATTERNS = (
        re.compile(r"/(Users|home)/(?!<)[A-Za-z0-9._-]+/"),
        re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.(com|org|net)\b)[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
        re.compile(r"Profile\s*\d+\b"),
    )

    def test_no_identity_strings(self):
        hits = []
        for p in DASHBOARD.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix not in {".py", ".md", ".json", ".html", ".txt"}:
                continue
            if p.resolve() == Path(__file__).resolve():
                continue
            text = p.read_text(errors="ignore")
            for pat in self.PATTERNS:
                for m in pat.finditer(text):
                    hits.append(f"{p.relative_to(DASHBOARD)}: {m.group(0)!r}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
