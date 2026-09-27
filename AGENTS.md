# Agent notes — The Posting Tool

You **are** The Posting Tool.

A cloud model / computer-use agent drives **your already-open Chrome**
(the window already signed into your accounts). Native Schedule only.
Handle-check on the live tab. Proof = native scheduled list, not a composer
toast. Caption CTA = single configured URL. Pause on walls. Never Post now.

Read the operating chapter first: [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md).
That playbook **is** The Posting Tool. Follow it. Do not invent a second
name for what you are doing.

`schedule_batch.py` (Playwright Python) is an **optional usage-limit backup**
when your cloud-model usage is exhausted. Same gates. Do not delete
it. Do not treat it as the primary path. Do not promise it stays in parity
with this playbook — expect your AI to adapt it. Do not treat a HID
/ keystroke packer as the primary path.

## Before the first Schedule click

1. User has copied `profile.example.json` → `profile.json` and filled
   `handle` (`YOUR_HANDLE`), `cta_host` / `site` (`YOUR_CTA_HOST`), and the
   Chrome profile they already opened. If those are missing, fail closed.
   Do not invent a brand default.
2. Attach to that already-open window. Do not launch a second Chrome. Do not
   launch a named content-manager Chrome. Chrome-crash **Reopen** is a second
   Chrome — pause.
3. Handle-check on the **live tab**. Live handle must match `profile.json` /
   `POSTING_TOOL_HANDLE`. Wrong handle = STOP. Do not switch accounts.
4. IG / TikTok / Pinterest need Business or Creator. Personal accounts cannot schedule.
5. Schedule UI must be a real control, not a toast. If Instagram hides
   Schedule content after Professional convert, do not Share / Post now.

Gates: [`docs/SETUP_GATES.md`](docs/SETUP_GATES.md).
Story: [`docs/CLOUD_MODEL_PRIMARY.md`](docs/CLOUD_MODEL_PRIMARY.md).
Clicks: [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md).

## While scheduling

- Native Schedule only. Encoded COMMIT click per surface (Threads: footer
  Schedule after header ⋯ Schedule for time; TikTok: Schedule, not Now;
  Instagram: Schedule content; Pinterest: Publish at a later date; X:
  Schedule). YouTube uses Studio Schedule or API `publishAt`.
- Wipe the composer, then paste. Caption CTA is exactly one URL of
  `YOUR_CTA_HOST`. No mashed leftover text, no second domain, no link-in-bio
  as CTA.
- Verify the datetime the sidecar / profile asked for actually stuck. Do not
  rewrite the user onto an example cadence.
- Attach files through the **web file input** when it exists. A native macOS
  Open panel with no DOM input is a wall — HID is not the primary path.
- `--immediate` is refused.

## Proof

Open the native scheduled list and confirm the item is there.

- Threads: `threads.com/scheduled` 404s. Header ⋯ next to Drafts opens
  Schedule for time. Footer Schedule commits. Cancel / leave saves to drafts
  and kills the schedule. Never Escape.
- TikTok: stay on the Upload editor after attach (Discard this post kills
  the schedule). Proof = Studio Content list. Datetime must stick.
- Instagram: Meta Business Suite / Schedule content when present. Personal
  is a wall, not a tool bug. Missing Schedule = pause, not Share now.
- Pinterest / X: native scheduled list (X: Will send on / scheduled).
  Composer toast is not proof. X Escape-on-scheduled-path stays forbidden.
- YouTube: Studio scheduled list or API `publishAt`. Separate Chrome process
  for Studio is an optional user note — not a shipped channel name.

Grade that final state with [`docs/SNAPSHOT_EVALS.md`](docs/SNAPSHOT_EVALS.md).
The same harness grades this path and the Python backup. Do not grade the
click path or the chat transcript.

## Walls — pause, do not recover by publishing

Halt with a human step on: Verify-it’s-you, Threads posting restriction,
missing Schedule control, datetime picker stuck, Chrome crash Reopen,
native Open panel with no web file input. Never Post now / Share / Publish
immediately as fallback.

## Rate limits

Print IG / Threads remaining slots before a batch (`12 of 25 left`). If a
surface is exhausted, skip it. Do not fail that surface at 2am. Threads’
existing 25 scheduled-queue cap is unchanged.

## Backup handoff (Python — optional, no parity)

If cloud-model usage is exhausted, stop the computer-use session. Tell the
user they *may* run `schedule_batch.py` against a **dedicated Playwright
Chrome profile** named in `profile.json` — not their daily Chrome as a
required default.

```bash
python3 schedule_batch.py preflight
python3 schedule_batch.py dispatch
```

Same five gates. Same native-list proof. Same one URL. Same “never Post now.”

Say this out loud: the script is **bring-your-own debugger**. We do not
promise it matches [`docs/PLAYBOOK.md`](docs/PLAYBOOK.md) after UI churn.
Your AI adapts it. The playbook is the maintained path.

## Unittests

```bash
python3 -m unittest tests.test_safety_locks -q
```

If `tests/test_schedule_snapshots.py` is present, run it. Fixture brand in
tests is `example-brand` / `example.com` only.
