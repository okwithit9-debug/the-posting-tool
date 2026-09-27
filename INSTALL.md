# Posting Tool — optional Python backup (install notes)

**The Posting Tool** starts at [`README.md`](README.md) and
[`docs/PLAYBOOK.md`](docs/PLAYBOOK.md): a cloud model / computer-use agent
on your already-open Chrome.

This file is **only** the Playwright Python path (`schedule_batch.py`).
Use it when cloud-model usage is exhausted.

**Note:** this is an optional backup, not the primary path. We do **not** promise Python parity with
[`docs/PLAYBOOK.md`](docs/PLAYBOOK.md). Expect your AI to adapt selectors
and account wiring when the UI moves (bring-your-own debugger). Do not
treat paths later in this file as required defaults.

Copy `profile.example.json` to `profile.json` and fill `YOUR_HANDLE`,
`YOUR_CTA_HOST`, and your Chrome / Playwright profile fields. Python uses
a dedicated Playwright profile — daily Chrome is not a required default.

One-time setup for the backup path. After this, a Python dispatch is
hands-free **if** the selectors still match. When they do not, adapt —
do not assume this tree was updated the same day as the playbook.

## 1. Install Python dependencies

From the repo root:

```bash
pip3 install -r requirements.txt
python3 -m playwright install chromium
```

What this installs:
- `google-auth*` + `google-api-python-client` — YouTube API
- `praw` — Reddit API (paused until you add API credentials)
- `playwright` + `playwright-stealth` — browser automation for X / TikTok / IG / Threads / Pinterest
- A Chromium runtime that Playwright drives (separate from your daily Chrome)

## 2. Log in to each platform in the Playwright profile

Python uses its own Playwright Chrome profile (default `~/.posting_tool_chrome`,
override in `profile.json`). Log in once, by hand, inside that profile:

```bash
python3 setup_playwright_logins.py
```

(or double-click `scripts/macos/bs_login.command`). This opens the Playwright
Chrome window and walks you through a manual login on X, Threads, TikTok,
Instagram, and Pinterest as `YOUR_HANDLE`. Sessions persist in the profile;
re-run it for any platform whose session expires.

Some platforms (X in particular) may detect automation on the login form.
If a login freezes, log in the same way again later, or use the primary
agent-driven path in [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md) for that platform.
This repo intentionally does **not** ship a tool that copies cookies out of
your daily Chrome.

YouTube is API-routed — authorize once with `python3 setup_youtube_auth.py`
(credentials go in `config.json`; see `config.example.json`).
Reddit is paused by default — add API credentials to `config.json` and
remove `"reddit"` from `paused_platforms` in `topics.json`.

## 3. Install the LaunchAgent

```bash
./scripts/macos/install_launchd.sh
```

This installs the LaunchAgent plist from `launchd/` and starts it. The LaunchAgent fires when:

- Any file changes in `.dispatch_queue/` (the manual / "Posting Tool Go" trigger)
- Or on a repeating interval in **your** timezone (the example six-hour wall clock is not a required default)

## How to dispatch from now on

**Manual trigger (instant dispatch):**

From the repo root, drop a file in `.dispatch_queue/`:

```bash
date > .dispatch_queue/trigger.txt
```

That's the entire "Posting Tool Go" command. The LaunchAgent picks it up within a second and runs `schedule_batch.py dispatch`.

**Watching what happened:**

```bash
tail -F logs/launchd.out.log
```

Or check `posted/<today>/` for finalized receipts and `failed/` for anything that errored.

## Per-dispatch behavior

For each video in the inbox (capped by `--max-videos 80`, default 3h spacing across 10-day horizon):

| Platform | Path | Schedules native? |
|---|---|---|
| YouTube | API (videos.insert + publishAt) | ✅ |
| X | Playwright (x.com/compose/post) | ✅ |
| Threads | Playwright (threads.com) | ⚠ historical note below |
| TikTok | Playwright (tiktokstudio/upload) | ✅ ≤10 days |
| Instagram | Playwright (instagram.com/reels/create) | ✅ |
| Pinterest | Playwright (pinterest.com/pin-builder) | ✅ ≤14 days |
| Reddit | API (PRAW) — currently paused | ❌ posts immediately when active |

**Current behavior:** `--immediate` is refused. Native Schedule only.
Older notes in this tree that mention falling back to an immediate post
when a scheduler is missing are **not** current behavior. Missing
Schedule is a wall — pause, do not Post now. See
[`docs/PLAYBOOK.md`](docs/PLAYBOOK.md) and
[`docs/SETUP_GATES.md`](docs/SETUP_GATES.md).

## When things go wrong

- **A Playwright poster fails**: the platform's screenshot lands in `logs/screenshots/<platform>_<basename>_<label>_<ts>.png`. The video moves to `failed/` with the error in the receipt JSON. Look at the screenshot to see what selector broke — then adapt. That adaptation is expected; this is the backup path.
- **A Chrome session expires** (logged out): re-run `setup_playwright_logins.py` for that platform only — open the script and comment out the others, or just walk through all 5 again.
- **TikTok flags the session**: stealth helps but isn't bulletproof. If TikTok consistently fails, run the dispatch headed (without `POSTING_TOOL_HEADLESS=1`) so you can see what's happening — the Chrome window stays open during the run.
- **YouTube API token expired**: re-run `python3 setup_youtube_auth.py`.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/<the-plist-this-repo-installed>
rm ~/Library/LaunchAgents/<the-plist-this-repo-installed>
```

Use the filename `install_launchd.sh` actually wrote. Do not treat any
branded plist name in `launchd/` as your identity.

## File map (post-install)

```
<repo-root>/
├── INSTALL.md                               ← you are here (backup only)
├── docs/PLAYBOOK.md                         ← The Posting Tool (primary)
├── inbox/                                   ← producers drop video + sidecar JSON
├── posted/<YYYY-MM-DD>/                     ← finalized + receipts
├── failed/                                  ← anything that errored
├── .in_flight/                              ← partial receipts mid-dispatch
├── .dispatch_queue/                         ← drop a file here to trigger
├── logs/launchd.{out,err}.log               ← LaunchAgent stdout/stderr
├── logs/schedule_batch.log                  ← Python-side dispatch log
├── logs/screenshots/                        ← Playwright failure screenshots
├── platforms/_chrome.py                     ← shared Playwright launcher (stealth, persistent profile)
├── platforms/{youtube,reddit}.py            ← API-routed
├── platforms/{x,threads,tiktok,instagram,pinterest}.py  ← Playwright-routed
├── schedule_batch.py                        ← entrypoint (CLI subcommands: plan/record/finalize/api-post/dispatch)
├── setup_playwright_logins.py               ← one-time login walker
├── setup_youtube_auth.py                    ← one-time YouTube OAuth
├── profile.example.json / config.example.json / .env.example  ← copy + fill
├── scripts/macos/                          ← optional macOS launchers (.command) + install_launchd.sh
└── launchd/                                 ← LaunchAgent plist source
```
