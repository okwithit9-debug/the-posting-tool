# Scheduled queue dashboard

A local, read-only page that lists every post The Posting Tool has scheduled
on **X, Threads, TikTok, Instagram, Pinterest and YouTube**. Each row shows
the platform, the time (in the `profile.json` timezone), a 200-character
caption preview, a thumbnail from the video's first frame, the native proof
screenshot, an "Open on platform" link when the URL is known, and a status:

| Status | Meaning |
|---|---|
| **verified** | A fresh native-list check (6 hours or newer) shows this item at this time. |
| **missing** | Recorded here, a fresh readable native list was checked, and the item is not on it. |
| **not tracked by the tool** | On the native list, but the tool has no record of it. |
| **scheduled (unverified)** | Recorded, but there is no fresh usable check (disabled, blocked, unknown, stale or ambiguous). |

The history toggle adds published, failed, skipped and immediate rows.

![Dashboard with example data](docs/dashboard-demo.png)

*(The screenshot uses generated `example-brand` demo data.)*

Design notes and API research: [`PLAN.md`](PLAN.md).

## Quick start

```bash
cd dashboard
python3 -m posting_dashboard backfill      # import posted/ + failed/ receipts once (idempotent)
python3 -m posting_dashboard serve         # http://127.0.0.1:8765
```

It needs Python 3.10+. The page uses only the standard library. Sync uses
the Playwright already in `requirements.txt`, and the YouTube check uses the
Google API client already in `requirements.txt`.

```bash
python3 -m posting_dashboard queue [--history]      # print the queue as JSON
python3 -m posting_dashboard sync [--only x,tiktok] # native-list checks (same as "Sync now")
python3 -m posting_dashboard record ...             # add a ledger entry (see below)
```

You can try it with fake data:

```bash
python3 demo/make_demo.py --out /tmp/posting-demo   # prints a config path
python3 -m posting_dashboard --config /tmp/posting-demo/config.json serve
```

## Where the data comes from

- **Receipts:** `schedule_batch.py` writes these to `posted/<date>/*.posted.json`
  and `failed/*.posted.json`, and they are also read from `.in_flight/`. Sidecar
  captions and media sit next to them. Proof screenshots come from
  `logs/screenshots/`.
- **Ledger:** `logs/ledger.jsonl` is append-only and never committed. For each
  `(basename, platform)`, the latest event wins. The agent path (see
  [`docs/PLAYBOOK.md`](../docs/PLAYBOOK.md)) writes no receipts, so after
  every native-list PASS it runs the **Record it** step:

  ```bash
  python3 -m posting_dashboard record <basename> <platform> \
    --status scheduled --scheduled-for 2026-10-01T11:00:00-07:00 \
    --caption-file <sidecar.json or caption.txt> --media <video> \
    --proof-screenshot <native-list screenshot>
  ```

  Use `--status failed --error "..."` for a miss and `--status cancelled` when
  a scheduled item is removed. Add `--url` when you know the native URL and
  `--video-id` for YouTube. `--scheduled-for` must include a UTC offset.
- **Native lists:** Sync writes a snapshot to `logs/dashboard/snapshots/`.

## Configuration

Copy [`config.example.json`](config.example.json) to `dashboard/config.json`
(gitignored), or pass `--config`. Handles, timezone and the CDP URL come from
the repo's `profile.json` (`handle` / `handles`, `timezone`, `chrome.cdp`).

- **Browser:** attach-only. Start your own Chrome, already signed in to your
  accounts, with `--remote-debugging-port`, then set `browser.cdp_url` or
  `profile.json` `chrome.cdp`. Sync opens **one extra tab**, reads, and
  closes only that tab. The dashboard never launches Chrome. With no URL,
  or nothing listening, every browser check is `blocked`.
- **Checks:** X, TikTok, Instagram (Meta Business Suite Planner) and
  Pinterest are **on**. Threads is **off**, and even when on it only logs
  unless `affects_status: true`. The YouTube Data API check is **on** and
  skips if there is no token in `config.json` `youtube.token_file`. The
  YouTube Studio browser check is **off**.
- **Window:** last 24 hours to 14 days ahead.
- **`urls`:** overrides the native-list URLs (Meta moves the Planner).

## Read-only guarantees

- Sync refuses to start while a posting run is active, meaning any file is
  in `.in_flight/` or the optional dispatch lock (`.dispatch.lock`) is held.
  It holds that lock for the whole check. A separate `.sync.lock` stops two
  syncs from running at once.
- Every page goes through `ReadOnlyPage`:
  - Only URLs on a per-platform allow-list are opened.
  - Only navigation labels are clicked. Labels like Schedule, Post,
    Publish, Share, Discard, Delete, Save, Edit, Confirm and Upload are
    refused.
  - **No keyboard input at all**, so no Escape on X.
  - To leave a page it navigates away and never closes dialogs.
- TikTok only visits `tiktokstudio/content`, never the upload page.
- Threads only opens the scheduled list when the composer is empty.
  Otherwise it stops without touching anything.
- YouTube uses the Data API with your existing token and never starts an
  OAuth flow.
- The server binds to `127.0.0.1` and checks the `Host` header. POSTs need an
  `X-Posting-Dashboard: 1` header, and media is served only from the
  receipt, screenshot and snapshot folders.

### Best-effort parsers

The native-list selectors in `posting_dashboard/sync/parsers/` were written
against saved example pages, so expect UI churn. They fail closed:

- A login wall gives `blocked`.
- A wrong handle, unreadable times or an unrecognized page gives `unknown`.
- An empty list only counts when a positive empty-state marker is found.

An unknown result never marks a row missing. Selector fixes with scrubbed
fixtures are welcome (see [`../CONTRIBUTING.md`](../CONTRIBUTING.md)).

## Tests

```bash
cd dashboard
python3 -m unittest discover -s tests -t .
```

The tests cover:

- time parsing
- the ledger
- parsers (13 example fixtures)
- reconcile
- the queue index
- the YouTube API (with a fake service)
- the read-only guard
- locks and the runner
- the server
- a headless-Chrome reader flow where every request is answered from
  fixtures (skipped without Playwright)

A scrub test also fails if a personal email or local path appears in this
folder.

## Differences from PLAN.md

- The ledger CLI is `python3 -m posting_dashboard record`, not new
  `schedule_batch.py` flags, so the dispatcher is unchanged.
- The browser is attach-only over CDP. The plan's option to reuse the
  dispatcher's Playwright profile was dropped.
- The YouTube Studio browser check is off by default because the Data API
  covers it.

## Privacy

Nothing leaves your machine. There is no telemetry and no remote assets.
Fixtures and the demo use a fake `example-brand` handle and `example.com`.
The ledger, `config.json` and snapshots stay local and are gitignored.
