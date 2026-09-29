"""Local dashboard server (stdlib only). Binds 127.0.0.1; refuses others.

Routes:
  GET  /                 the page
  GET  /api/queue        rows (``?history=1`` includes past/failed/skipped)
  GET  /api/sync/status  whether a sync is running + last log lines
  POST /api/sync         start read-only checks (subprocess)
  GET  /media?p=<path>   file under an allow-listed runtime folder only
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import Config
from .index import build_queue

STATIC = Path(__file__).resolve().parent / "static"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def safe_media_path(cfg: Config, raw: str) -> Optional[Path]:
    if not raw:
        return None
    try:
        p = Path(raw).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not p.is_file():
        return None
    for root in cfg.media_roots():
        try:
            r = root.resolve()
        except OSError:
            continue
        if p == r or r in p.parents:
            return p
    return None


class SyncJob:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.lines: list[str] = []
        self.lock = threading.Lock()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, config_path: Optional[str]) -> bool:
        with self.lock:
            if self.running():
                return False
            cmd = [sys.executable, "-m", "posting_dashboard"]
            if config_path:
                cmd += ["--config", config_path]
            cmd.append("sync")
            self.lines = []
            self.proc = subprocess.Popen(
                cmd, cwd=str(Path(__file__).resolve().parent.parent),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            threading.Thread(target=self._pump, daemon=True).start()
            return True

    def _pump(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())
            self.lines = self.lines[-60:]

    def status(self) -> dict:
        return {"running": self.running(),
                "exit_code": None if self.proc is None else self.proc.poll(),
                "log": self.lines[-30:]}


def make_handler(cfg: Config, config_path: Optional[str], job: SyncJob):
    class Handler(BaseHTTPRequestHandler):
        server_version = "posting-dashboard"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ALLOWED_HOSTS

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self'; media-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"))

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/queue":
                hist = (q.get("history") or ["0"])[0] in ("1", "true")
                return self._json(200, build_queue(cfg, show_history=hist))
            if u.path == "/api/sync/status":
                return self._json(200, job.status())
            if u.path == "/media":
                p = safe_media_path(cfg, (q.get("p") or [""])[0])
                if p is None:
                    return self._send(404, b"not found", "text/plain")
                return self._send_file(p)
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def _send_file(self, p: Path) -> None:
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            size = p.stat().st_size
            rng = self.headers.get("Range") or ""
            start, end = 0, size - 1
            partial = False
            if rng.startswith("bytes="):
                try:
                    a, b = rng[6:].split(",")[0].split("-")
                    if a:
                        start = int(a)
                        end = int(b) if b else size - 1
                    else:
                        start = max(0, size - int(b))
                    end = min(end, size - 1)
                    partial = start <= end
                except ValueError:
                    partial = False
            if rng and not partial:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(p, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_POST(self):  # noqa: N802
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            if self.headers.get("X-Posting-Dashboard") != "1":
                return self._send(403, b"missing header", "text/plain")
            if urlparse(self.path).path == "/api/sync":
                started = job.start(config_path)
                return self._json(202 if started else 409,
                                  {"started": started, **job.status()})
            return self._send(404, b"not found", "text/plain")

    return Handler


def serve(cfg: Config, config_path: Optional[str] = None, *, open_browser: bool = False) -> None:
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind to a non-loopback host; the dashboard is local-only")
    job = SyncJob()
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), make_handler(cfg, config_path, job))
    url = f"http://127.0.0.1:{cfg.port}/"
    print(f"Scheduled-queue dashboard on {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
