# The Posting Tool

This playbook **is** The Posting Tool.

A cloud model / computer-use agent drives the **already-open Chrome** signed
into your brand and uses each surface’s **native Schedule**. Handle-check on
the live tab. Proof is the **native scheduled list**, not a composer toast.

Copy `profile.example.json` to `profile.json` and fill:

- `handle` → `YOUR_HANDLE`
- `cta_host` / `site` → `YOUR_CTA_HOST` (e.g. `example.com`)
- `chrome.profile_directory` / `chrome.profile_name` → the Chrome profile
  that is already open and signed into your brand

There is no shipped brand handle, CTA host, Chrome profile name, machine
path, or mandated timezone cadence.

## Scope

| This is The Posting Tool | This is not |
|---|---|
| Agent + already-open Chrome + native Schedule + handle-check + list proof | A second Chrome, a named content-manager Chrome, or Chrome-crash **Reopen** |
| Pause on walls | Post now / Share / Publish immediately as “recovery” |
| One URL of `YOUR_CTA_HOST` after wiping the composer | Leftover mashed text, a second domain, or “link in bio” as the CTA |
| This playbook, the maintained path | Python parity with every UI change |

`schedule_batch.py` is an **optional usage-limit backup** for when cloud-model
usage is exhausted. Expect your AI to adapt that script to your accounts and
to UI churn. Python parity is **not** promised; the playbook is the maintained path.
HID / keystroke playback of a native macOS Open panel is a wall / exception,
not the primary path.

Gates that apply to both paths: [`SETUP_GATES.md`](SETUP_GATES.md).
How to grade the list: [`SNAPSHOT_EVALS.md`](SNAPSHOT_EVALS.md).
Agent contract: [`../AGENTS.md`](../AGENTS.md).

---

## Before the first Schedule click

1. `profile.json` exists and is filled. Missing `handle`, `cta_host`, or
   Chrome profile fields = fail closed. Do not invent a brand default.
2. Attach to the already-open Chrome signed into your brand. Do not launch a
   second Chrome. Do not launch a named content-manager Chrome. Chrome-crash
   **Reopen** is a second Chrome — pause.
3. Handle-check on the **live tab** (the account chip / profile URL for that
   surface). Live handle must match `profile.json` / `POSTING_TOOL_HANDLE`.
   Wrong handle = STOP. Do not switch accounts.
4. Instagram, TikTok, and Pinterest need Business or Creator. Personal is
   not a tool bug. A Verify-it’s-you wall, a Threads posting restriction, and
   a laggy or hidden Schedule control after an Instagram Professional
   convert are also **not tool bugs** — they are walls. Pause.
5. Schedule UI must be a real control, not a toast. If the control is
   missing, pause. Never Post now.

## Chrome attach (CDP / already-open window)

Drive the window that is already signed in. Do not launch a second
**Google Chrome**.

**Same-process lock.** Google Chrome locks the shared `user-data-dir` for
every profile in that process. If another Chrome profile (or another window
of the same Google Chrome) already holds that directory, Playwright /
remote debugging **cannot** attach Profile N at `chrome.cdp`. That is a
wall, not a reason to open another Chrome.

Recovery:

- Quit **that** Google Chrome (all windows of that process), then reopen
  only the brand profile named in `profile.json` and attach to it, **or**
- Attach to the already-open brand window (the primary path).

Do **not** “fix” this by opening a second Google Chrome, a Chrome-crash
**Reopen**, or a named content-manager Chrome.

**Separate AI browsers are fine.** Codex / ChatGPT Computer Use / other
agent browsers that use their **own** `user-data-dir` are not the same
Google Chrome process and do not take this lock. They are not a second
Google Chrome in the sense used here.

## Global rules (every surface)

- **Attach only.** Drive the window that is already signed in. Do not create
  a profile. Do not guess a directory name.
- **Handle-check the live tab** immediately before Schedule. Re-check if you
  changed tabs.
- **One URL CTA.** Wipe the composer, then paste. Caption CTA is exactly one
  URL of `YOUR_CTA_HOST`. No mashed leftover text, no second domain, no
  link-in-bio as CTA.
- **Native Schedule only.** `--immediate` is refused. Never Post now / Share
  now / Publish immediately / Now.
- **Datetime must stick.** The picker must show the slot you asked for
  before you commit. Do not rewrite the user onto an example cadence.
- **Walls pause.** Verify-it’s-you, posting restriction, missing Schedule
  control, datetime picker stuck, Chrome crash Reopen → halt with a human
  step. Never publish-now to “recover.”
- **Proof is the list.** Snapshot the native scheduled list for that
  surface. A toast, a closed composer, a spinner, or “I clicked Schedule”
  is not proof.

  | Surface | Proof (open this list) | Not proof |
  |---|---|---|
  | Threads | Header ⋯ next to **Drafts** → scheduled list | Toast, composer, `threads.com/scheduled` (404) |
  | X | Drafts / unsent scheduled (`Will send on`) | Toast, closed composer |
  | Instagram | **Meta Business Suite Planner** / Schedule content row | Toast, Share spinner |
  | TikTok | Studio **Content** list | Toast, Upload editor still open |
  | Pinterest | **Scheduled Pins** list | Toast |
  | YouTube | Studio scheduled list or API `publishAt` | Studio toast |
- **Rate limits.** Print remaining Instagram / Threads slots before a batch
  (e.g. `12 of 25 left`). Exhausted surfaces are skipped — they do not fail
  at 2am. Threads’ existing 25 scheduled-queue cap is unchanged.

## File attach

Prefer the **web file input** when the composer exposes one (`<input
type="file">`). A computer-use agent or Playwright `set_input_files` on
that DOM control is the primary path.

If the site only opens a **native macOS Open** panel, treat that as a wall /
exception. HID / keystroke playback of that panel is **not** the primary
path. Pause and tell the user: pick the file once, or expose a web file
input. Do not invent a keystroke packer to “finish the job.”

---

## Encoded COMMIT clicks (per surface)

These are the encoded COMMIT clicks and proof lists. UI chrome moves; the
rule does not: Schedule (not now), list proof (not toast), pause on walls.

### Threads

1. Open the already-signed-in Threads tab. Handle-check the live profile.
2. **Video first.** Attach the clip via the web file input. Confirm the
   preview is that file before any caption work.
3. Set **community / topic** (`Community or topic`) from the sidecar, then
   wipe and paste the caption (one `YOUR_CTA_HOST` URL).
4. Header **⋯** next to Drafts opens **Schedule** / **Schedule for time**.
   That kebab is the schedule UI. `threads.com/scheduled` and
   `threads.net/scheduled` **404** — do not navigate there for proof.
5. Set the requested datetime. Confirm it stuck in the picker.
6. Footer **Schedule** commits. That is the encoded COMMIT click.
7. **Never Escape.** Cancel / leave / “Save to drafts?” **kills** the
   schedule (the item lands in drafts, not the scheduled list).
8. **Proof:** header ⋯ next to Drafts → **Drafts scheduled** list. The
   item is there at the requested time with the one-URL caption. Toast is
   not proof.

If the 25-queue cap modal appears (“Can't schedule thread” / “up to 25
threads”), skip Threads for this batch. Do not Post now.

A Threads **posting restriction** is a platform wall. It is **not** the
same as a missing Schedule control. Neither one is a tool bug. Pause. Do
not Post now.

### X

1. Open the already-signed-in X compose tab. Handle-check the live account.
2. Attach the clip via the web file input. Video must stay attached.
3. Wipe, then paste the caption (one `YOUR_CTA_HOST` URL).
4. Open **native Schedule** (calendar / Schedule post). Set the requested
   datetime. Confirm it stuck.
5. Confirm the schedule (the modal’s Schedule / Confirm). Never **Post now**.
6. **Never Escape** on the scheduled path. Escape (and the modal X) opens
   “Save post?” — Save sends to drafts; Discard kills the composer. If you
   must dismiss a broken picker, toggle the schedule icon off rather than
   Escape.
7. **Proof:** Drafts / unsent scheduled (`Will send on`). Match time +
   first caption line + media. A “will be sent” toast alone is not batch
   proof.

### Instagram

1. Handle-check the live Instagram asset. Business or Creator required.
   Personal accounts cannot schedule.
2. Prefer **Meta Business Suite → Schedule content** / Planner when that
   Schedule control is present for the connected Instagram asset. If Suite
   is not signed into this brand, use the web composer only when **Schedule
   content** is a real control.
3. Attach the clip via the web file input. Wipe, then paste (one
   `YOUR_CTA_HOST` URL). Do not add a second domain.
4. Encoded COMMIT is **Schedule content** (or Schedule). Set the requested
   datetime and confirm it stuck.
5. **Missing Schedule = pause.** After a Professional convert, Instagram
   sometimes hides Schedule content or the switch lags. That is a wall.
   **Verify-it’s-you** after convert is the same class of wall. Neither is
   a tool bug. Do **not** Share / Share now / Post now.
6. **Proof:** **Meta Business Suite Planner** (Schedule content row) for
   that Reel at that time. Toast and the Share spinner are not proof.

### TikTok Studio

1. Open TikTok Studio Upload on the already-signed-in Chrome. Handle-check.
   Creator / Business required.
2. Attach the clip via the web file input. **Stay on the Upload editor.**
   Navigating away after attach opens **Discard this post**, which kills
   the schedule.
3. Wipe, then paste the caption (one `YOUR_CTA_HOST` URL).
4. When to post → **Schedule**, not **Now**. Set the requested datetime.
   The picker must show that exact minute before you commit. An example
   even-hour slot is **not** a user default — only “the datetime you asked
   for did not stick.”
5. Encoded COMMIT is **Schedule**.
6. **Proof:** Studio **Content** list (the scheduled item). The Upload
   editor still being open, or a toast, is not proof.

### Pinterest

1. Handle-check the live profile. Business account required.
2. Attach the clip via the web file input. Wipe, then paste title /
   description / destination (one `YOUR_CTA_HOST` URL).
3. Encoded COMMIT is **Publish at a later date**. Never **Publish
   immediately**.
4. Set the requested datetime. Confirm it stuck.
5. **Proof:** **scheduled Pins** list. Toast is not proof.

### YouTube

1. Handle-check Studio (or the API channel) on the account you configured.
   We do not ship a channel name.
2. **Studio Schedule** (Visibility → Schedule radio → date/time → Schedule)
   **or** Data API `publishAt`. Either is valid native Schedule.
3. Confirm the requested datetime stuck (Studio picker or the API body’s
   `publishAt`).
4. **Proof:** Studio’s scheduled list, or the API response showing
   `publishAt` on that video. A Studio toast is not proof.
5. **Optional user note:** Studio is a heavy Polymer app. If it fights the
   same Chrome process you use for the other surfaces, some users open
   Studio in a **separate Chrome process / user-data-dir** that is still
   *their* already-signed-in brand — not a second window of the same
   Google Chrome (that takes the CDP lock above), not a mystery profile,
   and not a channel we name. If you cannot attach cleanly, pause. Do not
   Post now.

---

## Walls — pause, do not recover by publishing

Halt with a human step on:

- Verify-it’s-you / verify your identity (**not a tool bug**)
- Threads posting restriction (**not** “missing Schedule”; **not a tool bug**)
- Missing Schedule control (including IG Schedule content hidden or laggy
  after Professional convert — **not a tool bug**)
- Datetime picker stuck / requested minute did not stick
- Chrome crash **Reopen** (that Reopen is a second Chrome)
- Same-process Chrome `user-data-dir` lock (quit **that** Google Chrome;
  do not open a second Chrome)
- Native macOS Open panel with no web file input (HID is not the primary)
- Wrong live handle

Never Post now / Share / Publish immediately as fallback.

## Grade the list

Open the native scheduled list and apply [`SNAPSHOT_EVALS.md`](SNAPSHOT_EVALS.md).
PASS = the item is on the list, the caption is one `YOUR_CTA_HOST` URL, and
the requested datetime stuck. FAIL = halt (never Post now). SKIP = exhausted
Instagram / Threads day.

Do not grade a conversation. Do not grade a click path.

## Optional Python backup

When cloud-model usage is exhausted, stop the computer-use session. You may
run `schedule_batch.py` against a **dedicated Playwright Chrome profile**
named in `profile.json`. Daily Chrome is not a required default for that
path.

```bash
python3 schedule_batch.py preflight
python3 schedule_batch.py dispatch
```

Same five gates. Same “never Post now.” Same native-list proof.

This script is **bring-your-own debugger**. UI churn will break selectors.
Expect your AI to adapt it to your accounts. We do **not** promise it stays
in parity with this playbook. Install notes (backup only):
[`../INSTALL.md`](../INSTALL.md).
