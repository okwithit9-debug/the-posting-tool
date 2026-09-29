# The Posting Tool: "Scheduled Queue" dashboard, scoping plan

Status: **built** in `dashboard/` (this folder). This is the original scoping plan; where the
build differs, see [`README.md`](README.md#differences-from-planmd).

---

## 1. Goal

Add one local page that lists **every currently scheduled post** across **X, Threads, TikTok,
Instagram, Pinterest, and YouTube**. Each row shows:

| Field | Source |
|---|---|
| Platform | receipt / ledger key |
| Scheduled time (shown in `profile.json` timezone) | `scheduled_for` (ISO with offset) |
| Caption preview | sidecar `captions.<platform>.caption` / title / description |
| Media thumbnail | local video file (first frame) |
| Status | ledger status plus optional native-list sync (see §5) |

Non-goals for v1: editing, rescheduling, or cancelling from the dashboard; publishing of any kind;
Reddit (it is paused and has no scheduler); cloud hosting; accounts; telemetry.

---

## 2. What the repo already has (findings)

**Stack.** Python 3.11+ with Playwright (`playwright`, `playwright-stealth`), plus
`google-api-python-client` for YouTube and `praw`. There is no web framework, no JS build, and no database.
State is JSON files on disk. macOS launchers are in `scripts/macos/` and there is a LaunchAgent in `launchd/`.

**Two scheduling paths:**
1. **Primary:** a computer-use agent follows `docs/PLAYBOOK.md` / `AGENTS.md` in the already-open
   Chrome. Proof is the platform's native scheduled list, graded by `docs/SNAPSHOT_EVALS.md`.
2. **Backup:** `schedule_batch.py dispatch` calls `platforms/<name>.py:post()` through Playwright,
   or through the YouTube Data API using `publishAt`.

**Local artifacts a dashboard can read today, with no new writes needed:**

| Artifact | Written by | Contents |
|---|---|---|
| `posted/<YYYY-MM-DD>/<basename>.posted.json` | `schedule_batch.finalize()` (~L408–482) | `{basename, started_at, finalized_at, platforms:{<p>:{status, scheduled_for, url, error, proof, composer, board, topic, video_id, via, at, quota_skipped…}}}` |
| `posted/<date>/<basename>.mp4` + `<basename>.json` (sidecar) | moved there by `finalize()` | the media plus all captions |
| `failed/<basename>.posted.json` (+ `.mp4`, `.json`) | `finalize()` when any platform failed | **may still contain platforms with `status: scheduled`** (partial success) |
| `failed/<basename>.posted.json.previous` | `retry-failed` | audit only. Ignore it. |
| `.in_flight/<basename>.json` | `record_result()` (L376) | partial, mid-dispatch results |
| `logs/screenshots/<platform>_<basename>_<label>_<ts>.png` | each `platforms/*._shot()` | includes proof shots such as `scheduled_on_content_list` (TikTok), `native_scheduled_list_open` / `scheduled` (Threads), `submitted` (Pinterest) |
| `logs/schedule_batch.log` | `schedule_batch.log()` | text log |
| `state.json` | `update_state()` | `last_scheduled_for`, `rotation_index`, `last_posted_at` |
| `platforms/_preflight.daily_remaining_map()` | computed | IG/Threads "12 of 25 left" counters |

Receipt `status` values are `scheduled | success | failed | skipped`.

There is **no `SCOREBOARD.json`** or other scoreboard file in either public repo. `state.json` and the
receipts are the only state.

**Gaps that matter for a dashboard:**
- **The agent path writes no receipts.** The `schedule_batch.py` docstring describes
  `record` and `finalize` subcommands for agents, but `PLAYBOOK.md` and `AGENTS.md` never tell the agent to call
  them. Posts scheduled by the primary path are therefore invisible to local state. That is the biggest gap.
- `_shot()` returns the screenshot path, but it is **not saved in the receipt**, so proof screenshots can only be
  found by filename glob.
- Receipts are written once and never updated. After `scheduled_for` passes, the receipt still says
  `scheduled`, and nothing records a native-side cancel or reschedule.
- Some legacy result shapes need a warning in the UI, e.g. Pinterest/X `status: "success"` +
  `scheduling_skipped: true`, which means "went out immediately".
- `schedule_batch._count_future_threads_scheduled()` (L489) is already a small "ledger query".
  It should move into the new shared module so the dashboard and dispatch count the same way.

**Guardrails already in the repo that the dashboard must respect:** attach-only Chrome (no second
Chrome), never Post now, never Escape on the X or Threads scheduled paths, Threads `/scheduled` returns 404,
TikTok "Discard this post" kills a schedule, and no cookie copying. `CONTRIBUTING.md` bans personal data, and
`tests/test_safety_locks.py::RepoIdentityTests` scans the tree for it.

---

## 3. How each platform's scheduled queue can be READ

These findings were verified against official docs and specs on 2026-09-29. Summary: **only YouTube has a
free, official, sanctioned way to read your scheduled queue.** For the other five, the realistic
sources are (a) the tool's own ledger and (b) reading the native scheduled-list page in the
logged-in browser, which is the same page the tool already uses as proof.

| Platform | Official API read of scheduled queue? | Browser native list (read-only) | Recommendation |
|---|---|---|---|
| **YouTube** | **Yes.** `channels.list(mine=true)` gives the uploads playlist, then `playlistItems.list`, then `videos.list(part=snippet,status)`. Scheduled means `status.privacyStatus=private` and `status.publishAt` in the future. Each call costs 1 quota unit out of the 10,000/day read pool. Uploads have used a separate bucket since 2026-06-01. The OAuth token the tool already creates (`youtube.py` scopes include `youtube`) is enough, and `youtube.readonly` would also work. Videos scheduled in Studio show up here too. | Studio → Content, filtered by Visibility = Scheduled. **Shorts are listed under the separate "Shorts" tab.** | **API sync** (Phase 2). Use Studio only as a fallback. |
| **X** | **Not with the standard API.** X API v2 has no scheduled-post read or write. Pricing is pay-per-use: `POST /2/tweets` costs $0.015, or $0.20 with a URL, and "owned reads" cost $0.001 per resource. The **X Ads API** does have `GET accounts/:account_id/scheduled_tweets` (with `user_id`, `scheduled_at`, `scheduled_status`, `text`), but it needs approved Ads API access and an ads account. **Unverified:** whether posts scheduled in the x.com composer appear in that list. | `x.com/compose/post/unsent/scheduled` (already `SURFACE_CONTRACTS["x"]["proof_url"]`). Shows "Will send on …". | **Ledger plus browser sync.** Ads API is an optional later spike only if the user already has Ads API access. |
| **Threads** | **No.** The Threads API publishes immediately (container, then `threads_publish`). There is no publish-time parameter and no scheduled-list endpoint. `GET /me/threads` returns published posts only. | No URL (`threads.com/scheduled` returns 404). Open the composer, then the header ⋯ next to Drafts, then the scheduled list. Existing helper: `threads._threads_native_scheduled_list_has_caption()`. This reader carries the most risk because it opens the composer, and leaving a non-empty composer saves a draft. | **Ledger first.** Browser sync last, only with an empty composer, and never press Escape. |
| **TikTok** | **No.** The Content Posting API (Direct Post `/v2/post/publish/video/init/`) has no schedule field. Display API `/v2/video/list/` returns **public** videos only. | `tiktok.com/tiktokstudio/content`, already used for proof in `tiktok.py` (~L1086). Scheduled items show a scheduled badge and time. | **Ledger plus browser sync.** |
| **Instagram** | **No read of the scheduled queue.** Meta's Content Publishing docs cover publishing only. They mention scheduling only as something *your app* does ("if your app allows app users to schedule posts"), and the limit is 100 API-published posts per 24h. There is no endpoint that reads the Meta Business Suite Planner. Third-party "list scheduled" APIs only return their own queues. | Meta Business Suite Planner (`business.facebook.com/latest/planner`; Meta moves this often, and it sometimes appears under Content). There is also an instagram.com "Scheduled content" entry, which `instagram._ig_native_scheduled_list_has_caption()` already clicks. Needs an asset/business id (`config.json` → `instagram.business_suite_*`). | **Ledger plus browser sync** (Planner list view). |
| **Pinterest** | **No.** The official v5 OpenAPI spec (`pinterest/api-description` v5.28.0) `PinCreate` has no `publish_at` or scheduling field, and `GET /pins` has no scheduled filter. Some third-party blogs claim `publish_at` exists, but the official spec does not support that. | Profile → Created → **Scheduled Pins** section. Exact placement still needs a live check (Pinterest's own cap is 100 scheduled pins and a 14-day window, per repo comments). | **Ledger plus browser sync.** |

Costs and auth, stated honestly:
- YouTube: free, using the existing OAuth client in `config.json`. Unverified Google projects still work for owner reads.
- X Ads API: needs an application, approval, and an ads account (a card may be required even with $0 spend). Not recommended for v1.
- Everything else: no API cost. Browser sync reuses the logged-in session, follows the same attach-only
  rules as scheduling, and touches the same ToS surface the tool already uses. The user stays responsible, as the README says.

---

## 4. Proposed architecture

```
          scheduling (unchanged)                         read side (new)
┌──────────────────────────────────┐        ┌──────────────────────────────────────┐
│ agent (PLAYBOOK) ─► schedule_batch│        │ dashboard/index.py  (normalize)      │
│   record/finalize (+new flags)    │──┐     │   reads: ledger/*.jsonl, receipts,   │
│ python dispatch ─► platforms/*.py │  │     │   sidecars, screenshots, snapshots   │
└──────────────────────────────────┘  │     │ dashboard/server.py (127.0.0.1 only) │
                                       ▼     │   /            → static/index.html   │
                        ledger/ledger.jsonl ─►│   /api/queue   → JSON rows           │
                        (append-only events)  │   /media/...   → allow-listed files  │
                                       ▲     └──────────────────────────────────────┘
┌──────────────────────────────────┐  │
│ sync (optional, read-only)        │──┘  writes ledger/snapshots/<platform>_<ts>.json
│  youtube: Data API                │     + a "reconciled" event per item
│  others: attach to Chrome, read   │
│  native list, never click commit  │
│  agent can do it too → import JSON│
└──────────────────────────────────┘
```

Principles:
- **Self-hosted, stdlib only.** The server uses `http.server`, and the page is one HTML file with vanilla JS and CSS.
  There is **no CDN, no fonts, no analytics, and no outbound requests** from the page. It binds to `127.0.0.1` only.
- **Read-only.** The dashboard never calls `post()`, never opens a composer for writing, and has no
  buttons that change platform state. The only action is "Sync now" (Phase 3), which only reads.
- **One normalized row model** (`QueueItem`): `id`, `platform`, `basename`, `scheduled_for`,
  `caption_preview` (the first ~140 characters; the full caption stays in the local sidecar), `media_path`,
  `proof_screenshot`, `proof_kind`, `source` (`agent` | `python` | `sync`), `ledger_status`,
  `native_status`, `last_seen_native_at`, `display_status`.
- **Display status** is derived like this:
  - `Verified`: in the ledger and seen in the native list at the last sync
  - `Scheduled (unverified)`: in the ledger, with no sync yet or a stale sync
  - `Missing on platform ⚠`: in the ledger with a future time, but absent from a fresh native list
  - `Untracked`: in the native list but not in the ledger (scheduled by hand)
  - `Due / likely published`: `scheduled_for` has passed
  - `Failed` and `Skipped (quota)`: shown in a separate collapsed section
  - `Went out immediately ⚠`: legacy `success` + `scheduling_skipped`
- **Reconcile key:** platform + `scheduled_for` within ±2 min + the first 40 characters of the caption. This is the
  same 40-character snippet the existing proof helpers use. YouTube matches on `video_id` when present.
- **Thumbnails:** MVP uses `<video preload="metadata" src="/media/…#t=0.5">`, so the browser renders the first
  frame with no ffmpeg dependency. Later, cache a JPG with ffmpeg if it is installed.
- **Safety of `/media`:** serve only files that resolve under `posted/`, `failed/`, `inbox/`,
  `logs/screenshots/`, or `ledger/snapshots/`. Block path traversal.
- **Concurrency:** sync takes `ledger/.sync.lock` and refuses to run while `.in_flight/` is non-empty
  or a dispatch is running, so it never fights dispatch for the one Chrome.

---

## 5. Phases, files, and effort

Effort assumes one developer who knows the repo. The estimates are rough.

### Phase 0: MVP read-only dashboard over existing receipts (about 1–1.5 days)
Zero changes to the scheduling code. This immediately covers everything the Python path scheduled.

Add:
- `dashboard/__init__.py`
- `dashboard/index.py`: globs `posted/**/*.posted.json`, `failed/*.posted.json`,
  `.in_flight/*.json`. Joins each receipt with its sidecar (same dir, `<basename>.json`), media
  (`<basename>.mp4`), and newest matching `logs/screenshots/<platform>_<basename>_*.png`. Emits
  `QueueItem`s for the 6 platforms only. Default window: now −24h to now +14d. Uses
  `load_user_profile().zoneinfo()` for display.
- `dashboard/server.py`: `python3 -m dashboard` serves on `127.0.0.1:8765`. It has `--port`, `--open`, and
  `--static out.html`, which writes a single self-contained HTML file for users who want no server.
- `dashboard/static/index.html`: list view grouped by day, 6 platform filter chips, a status filter,
  and an IG/Threads "N of 25 left" header from `daily_remaining_map()`. Clicking a row shows the full caption
  and the proof screenshot.
- `tests/test_dashboard_index.py`: fixture receipts under a temp dir using `example-brand` /
  `example.com` only. Covers partial-success in `failed/`, `.previous` ignored, the legacy
  `scheduling_skipped` warning, timezone display, and the path-traversal guard.

Change:
- `README.md` / `INSTALL.md`: add a short "Dashboard" section.
- `.gitignore`: nothing yet.

### Phase 1: the ledger, which covers the primary (agent) path (about 1–1.5 days)
Add:
- `platforms/_ledger.py`: `append_event(dict)` writes to `ledger/ledger.jsonl`, append-only with one
  JSON object per line and fsync. It also provides `iter_events()`, `current_queue()` (folding events to latest state per
  `(basename, platform)`), and `count_future_scheduled(platform)`, which replaces
  `_count_future_threads_scheduled`. Event fields: `event` (`scheduled|failed|skipped|reconciled|
  missing|untracked`), `basename`, `platform`, `scheduled_for`, `caption_preview`, `media_path`,
  `proof_kind`, `proof_screenshot`, `url`, `video_id`, `source`, `recorded_at`.
- `ledger backfill` subcommand, which seeds the ledger from existing receipts so nothing is lost.

Change:
- `schedule_batch.py`:
  - `record_result()` also calls `_ledger.append_event()`.
  - The `record` subparser gains `--caption-file/--caption`, `--media`, `--proof-screenshot`, and
    `--proof-kind`, so the agent can pass them.
  - Add subcommands `ledger backfill` and `ledger show`.
  - Threads cap counting switches to `_ledger.count_future_scheduled("threads")`, falling back to receipts.
- `platforms/{x,threads,tiktok,instagram,pinterest,youtube_studio}.py`: include the proof
  `_shot(...)` return value as `proof_screenshot` in the success dict. This is a one-line change per surface
  and does not change behavior.
- `docs/PLAYBOOK.md` + `AGENTS.md`: add a **"Record it"** step after list proof:
  `python3 schedule_batch.py record <basename> <platform> --status scheduled --scheduled-for <iso>
  --proof-screenshot <path>`, then `finalize`. Without this step the dashboard cannot see agent-scheduled posts.
- `.gitignore`: add `ledger/`.
- `dashboard/index.py`: prefer ledger events and fall back to receipts.
- Tests: ledger fold logic, and backfill that is idempotent when run twice.

### Phase 2: YouTube API sync (about 0.5–1 day)
Add `sync/__init__.py`, `sync/youtube_api.py` (reuses the `youtube.py` credential loader and
token file; paginates the uploads playlist; keeps `private` + future `publishAt`; writes
`ledger/snapshots/youtube_<ts>.json` + `reconciled/untracked/missing` events), and
`python3 -m sync youtube`. Cost: about 3 quota units per 50 videos. Test with mocked API responses.

### Phase 3: read-only browser sync for X, TikTok, Instagram, Pinterest (about 3–4 days total)
Add:
- `sync/_readonly.py`: a wrapper around the Playwright page that **only** allows `goto`, scrolling,
  text/attribute extraction, screenshots, and clicks on an explicit allow-list of *navigation*
  labels (for example "Scheduled", a Visibility filter). It refuses anything in
  `FORBIDDEN_COMMIT_LABELS`, plus "Schedule", "Publish", "Delete", "Unschedule", "Edit", and
  "Discard". It never presses Escape. It checks for walls via `scan_page_for_wall()` and a handle match via
  `read_live_handle()`, and fails closed.
- `sync/readers/{x,tiktok,instagram,pinterest,youtube_studio}.py`, each exposing
  `read_scheduled(page) -> list[NativeItem(platform, scheduled_for, caption_snippet, thumb?)]`.
  Attach through `platforms._chrome.chrome_session()` using the same attach-only rules.
- `sync/__main__.py`: `python3 -m sync [--platforms x,tiktok,...]`. Takes the sync lock, writes a
  snapshot and screenshot per platform, and appends reconcile events.
- `sync/import_snapshot.py`: `python3 -m sync import <file.json>`, so the **primary agent** can do
  the same read by following a new PLAYBOOK "Sync (read-only)" chapter and hand the dashboard a JSON snapshot
  in the documented schema. This keeps the playbook as the maintained path.
- Dashboard: a "Sync now" button (POST to localhost that spawns `python3 -m sync`) and a "last synced
  N min ago" badge per platform.
- Tests: reader parsers against saved, redacted HTML fixtures (no browser in CI, matching
  SNAPSHOT_EVALS policy), and read-only-wrapper refusal tests.

Per-reader notes:
- **X:** after reading, leave the modal list by navigating to `x.com/home`, not by pressing Escape.
  Never click a list item, because that opens the editing composer.
- **TikTok:** read only on `/tiktokstudio/content`, never on `/upload`.
- **Instagram:** read the Planner list view and filter to the IG asset. Requires the `config.json` business ids.
- **Pinterest:** locate the Scheduled Pins section on the profile. Verify the selectors live first.
- **YouTube Studio:** fallback only for when the API isn't configured. Check both the Videos and Shorts tabs.

### Phase 4: Threads browser sync plus polish (about 1.5–2 days, optional)
- `sync/readers/threads.py`: open the composer **only when it is empty**, then header ⋯ → the scheduled
  list, read it, and close with the composer's own close control while it is still empty. Abort if any
  text or media is present. Never navigate to `/scheduled` and never press Escape.
- Calendar (week) view, CSV export, a cached ffmpeg JPG thumbnail, and a `launchd` example to run
  YouTube sync hourly.

**Totals:** MVP (Phase 0 + 1) is about 2.5–3 days. Adding YouTube API gives about 3.5 days. Full
native reconciliation for all 6 platforms is about 8–10 days.

---

## 6. Recommended MVP

Phases 0 + 1, plus Phase 2 if the YouTube token is already set up:
1. `python3 -m dashboard` lists every post the tool has scheduled across the 6 platforms, with time,
   caption preview, first-frame thumbnail, proof screenshot, and status. The data comes from receipts plus the new ledger.
2. The agent path records into the ledger through a one-line PLAYBOOK step.
3. YouTube rows are confirmed against the real queue through the official API.
4. The other five show "Scheduled (unverified)" with the proof screenshot the tool captured at schedule
   time. That screenshot is the same evidence SNAPSHOT_EVALS already treats as proof. Live reconciliation for
   those five comes in Phase 3.

---

## 7. Open decisions for the user

1. **Server or static file?** The recommendation is a tiny `127.0.0.1` stdlib server, which enables the Sync button later,
   plus a `--static` export. Alternative: static HTML only, with no server at all.
2. **Where the ledger lives.** The recommendation is a gitignored `ledger/ledger.jsonl` at the repo root. Alternative:
   under `logs/` (but people delete logs) or `~/.posting_tool/`.
3. **Make "record" mandatory in the PLAYBOOK?** This is required for agent-scheduled posts to appear. It
   adds one shell step per (video, platform).
4. **Enable browser sync at all, and which Chrome?** It must attach to the same single Chrome the
   profile names, and it cannot run during a dispatch. Should it be opt-in per platform (recommended), and
   should it run from Python, from the agent via snapshot import, or both?
5. **Threads sync:** worth the composer-open risk, or ledger-only for Threads?
6. **X Ads API:** skip it (recommended), or spike it if the user already has Ads API access? Whether
   composer-scheduled posts appear in `GET scheduled_tweets` is unverified.
7. **Time window and history:** default now −24h to +14d? Include published history or not?
8. **Thumbnails:** browser first frame (no deps, recommended) or an ffmpeg-generated JPG cache?
9. **Read-only forever?** v1 has no cancel or reschedule. Should a later version add native-UI "open this
   item" deep links (safe) or real edits (much riskier, and it would need its own gates)?
10. **Caption storage in the ledger:** preview only (recommended; the full caption stays in the sidecar) or the full text?

---

## 8. Risks

- Native list DOMs change often. Readers are "bring-your-own debugger", like `schedule_batch.py`.
  Keep them small and per-platform, and pin them with fixtures.
- A sync that misreads can show a false "Missing ⚠". Mitigations: show a snapshot screenshot next to
  every Missing row, and never act on it automatically.
- Session walls (Verify-it's-you) during sync pause just like scheduling and mark the platform
  "sync blocked". They are never "recovered" by clicking through.
- Privacy: the page is local-only, but captions and thumbnails are visible to anyone on the machine.
  Binding to 127.0.0.1 and having no telemetry is the whole story; say so in the README.

## 9. Sources checked (2026-09-29)
- YouTube Data API: Videos resource (`status.publishAt`), `playlistItems.list` (1 unit), and implementation
  guide (uploads playlist). Quota changes per TimeToPost summary (2025-12-04 and 2026-06-01).
- Pinterest: official OpenAPI `pinterest/api-description` v5.28.0 (`PinCreate`, `GET /pins` params).
- Meta: Instagram Platform Content Publishing docs (rate limit 100/24h; no scheduled-queue read).
  Threads Posts docs (container then publish; 250/24h).
- TikTok: Content Posting API Direct Post reference (updated 2026-08-24) and `/v2/video/list/`
  (public videos only, updated 2026-08-04).
- X: Ads API Creatives reference (`GET accounts/:account_id/scheduled_tweets`) and the X API
  pay-per-use pricing and changelog (Owned Reads $0.001; POST $0.015 / $0.20 with URL).
