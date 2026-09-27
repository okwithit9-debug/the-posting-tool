"""
Per-platform Playwright posters.

Each module exposes:

    def post(video_path: pathlib.Path, captions: dict) -> dict:
        '''
        Post `video_path` to this platform using the caption block `captions`
        from the sidecar JSON.

        Returns a dict shaped like one of:
            {"status": "success", "url": "https://..."}
            {"status": "failed",  "error": "human-readable reason"}
            {"status": "skipped", "error": "reason for skipping"}

        post_next.py adds an "at" timestamp automatically if missing.
        '''

All posters share a Chrome profile at:
    ~/Library/Application Support/Posting Tool/chrome-profile/
(see chrome_profile.py for the helper)
"""
