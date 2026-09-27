"""
setup_youtube_auth.py — one-time YouTube OAuth flow.

Run this ONCE from the project root after installing requirements:

    pip3 install -r requirements.txt
    python3 setup_youtube_auth.py

A browser tab will open asking you to authorize the YouTube account
(Clipitmotions). After consent, a token is cached at
tokens/youtube_token.json and the dispatch can use the API path
(`schedule_batch.py api-post <basename> youtube --scheduled-for <iso>`).

If `tokens/youtube_token.json` already exists and is valid, this script
just reports success without re-prompting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_ROOT / "config.json"
TOKEN_DIR = PROJECT_ROOT / "tokens"


def main() -> int:
    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found", file=sys.stderr)
        return 1
    cfg = json.loads(CONFIG_FILE.read_text()).get("youtube") or {}
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    token_file = cfg.get("token_file", "tokens/youtube_token.json")
    if not client_id or not client_secret:
        print("ERROR: youtube.client_id / client_secret missing in config.json", file=sys.stderr)
        return 1

    # Lazy-import so missing google libs produce a helpful message instead of a
    # bare traceback.
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        print(f"ERROR: google API libraries not installed: {e}", file=sys.stderr)
        print("Run: pip3 install -r requirements.txt", file=sys.stderr)
        return 1

    SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube",
    ]
    token_path = PROJECT_ROOT / token_file
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        print(f"Token already valid at {token_path}")
        ok = _verify(creds, build)
        return 0 if ok else 2

    if creds and creds.expired and creds.refresh_token:
        print("Refreshing existing token...")
        creds.refresh(Request())
    else:
        print("Authorizing — your browser will open. Pick the Clipitmotions YouTube account.")
        client_config = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
        creds = flow.run_local_server(port=8080)

    TOKEN_DIR.mkdir(exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"Token saved to {token_path}")

    return 0 if _verify(creds, build) else 2


def _verify(creds, build) -> bool:
    try:
        yt = build("youtube", "v3", credentials=creds)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            print("WARN: token works but no channel was returned for this account.")
            return False
        ch = items[0]["snippet"]
        print(f"Authorized as channel: {ch.get('title')}  (id: {items[0]['id']})")
        return True
    except Exception as e:
        print(f"ERROR verifying token: {type(e).__name__}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    sys.exit(main())
