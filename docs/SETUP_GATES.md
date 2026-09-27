# What a setup now refuses

These gates ship with **The Posting Tool**. They apply to **both** paths:

1. **The Posting Tool (primary)** — a cloud model / computer-use agent on
   your already-open Chrome, following
   [`PLAYBOOK.md`](PLAYBOOK.md) and [`../AGENTS.md`](../AGENTS.md).
2. **Optional backup** — `schedule_batch.py` (Playwright Python) when
   cloud-model usage is exhausted. Dedicated Playwright Chrome profile via
   `profile.json`, not daily Chrome as a required default. No Python-parity
   promise — expect your AI to adapt the script (bring-your-own
   debugger). The playbook is the maintained path.

A batch will not schedule unless the gates pass. A successful schedule is
proven on the **native scheduled list**, not a composer toast.

Copy `profile.example.json` to `profile.json` and fill the placeholders
(`YOUR_HANDLE`, `YOUR_CTA_HOST`, the Chrome profile name you already opened).
There is no shipped brand handle, CTA host, or Chrome profile name.

**Primary Chrome:** the window already signed into your brand
accounts. Do not launch a second Chrome or a named content-manager Chrome.

**Same-process lock:** Google Chrome locks the shared `user-data-dir` for
every profile in that process. If another profile in the **same** Google
Chrome already holds the directory, Playwright / remote debugging cannot
attach Profile N. Recovery is quit **that** Google Chrome (then reopen
only the named brand profile and attach), or attach to the already-open
brand window. Do **not** open a second Google Chrome. Separate AI browsers
(Codex / ChatGPT Computer Use) with their **own** `user-data-dir` are
fine — they are not that lock.

**Backup Chrome:** a dedicated Playwright profile named in `profile.json`.
Daily Chrome is not required for the Python path.

| Gate | Used to silently do | Now refuses |
|---|---|---|
| **1. Preflight** | Run as whoever was signed in. Personal IG looked "broken". Missing Schedule still hit Share. | Live handle must match the handle in `profile.json` / `POSTING_TOOL_HANDLE`. Wrong handle = STOP, no account switch. Missing configured handle = fail closed. IG / TikTok / Pinterest need Business or Creator — Personal accounts cannot schedule. Verify-it's-you and a laggy / hidden Schedule control after an Instagram Professional convert are walls, **not tool bugs**. A Threads posting restriction is a platform wall — it is **not** “missing Schedule” and **not** a tool bug. Schedule UI must be a real control, not a toast. If Instagram hides Schedule content after Professional convert, it will not Post now. |
| **2. Proof** | Composer toast or a closed composer counted as scheduled. | Proof is the native scheduled list. Threads: `threads.com/scheduled` 404s — header ⋯ next to Drafts opens Schedule for time; footer Schedule commits; proof = **Drafts scheduled** list. Cancel/leave saves to drafts and kills the schedule. Never Escape. TikTok Studio: stay on the Upload editor after attach (Discard this post kills the schedule). When to post → Schedule (not Now). Verify the datetime the sidecar/profile asked for actually stuck. Proof = Studio Content list. Instagram: **Meta Business Suite Planner**. Pinterest: **scheduled Pins**. X: Drafts / unsent scheduled (`Will send on`); never Escape on the scheduled path. Each surface has an encoded COMMIT click. See [`PLAYBOOK.md`](PLAYBOOK.md). |
| **3. One URL** | Paste could mash onto leftover composer text; extra domains / link-in-bio slipped through. | Caption CTA is a single host from `profile.json` (`YOUR_CTA_HOST`). Composer is wiped before paste. No mashed links, no link-in-bio-as-CTA, no extra domains. |
| **4. Pause on walls** | Post now / Share / Publish immediately when Schedule was missing or a challenge appeared. | Halt with a human step on: Verify-it's-you (not a tool bug), Threads posting restriction (not missing Schedule, not a tool bug), missing Schedule control (including IG lag after convert — not a tool bug), datetime picker stuck, Chrome crash Reopen (that Reopen is a second Chrome), same-process Chrome `user-data-dir` lock (quit **that** Google Chrome; do not open a second Chrome), native macOS Open panel with no web file input. Never Post now as fallback. HID is not the primary path. |
| **5. Rate limits** | Keep dispatching until IG/Threads failed overnight. | IG and Threads ~25/day, configurable via `daily_caps`. Remaining slots print before dispatch (e.g. `12 of 25 left`). Exhausted surfaces are skipped — they do not fail at 2am. Threads' existing 25 scheduled-queue cap is unchanged. |

`--immediate` is refused. Native Schedule only.

The tool verifies the datetime **you** asked for stuck. It does not
rewrite every user onto an example cadence or a shipped timezone.

Check without posting:

```bash
# after filling profile.json (optional Python backup path)
python3 schedule_batch.py preflight
```

How a user or agent grades “did it actually schedule” (snapshots, not
conversations; same rubric for both paths): [`SNAPSHOT_EVALS.md`](SNAPSHOT_EVALS.md).
Operating chapter: [`PLAYBOOK.md`](PLAYBOOK.md).
Agent pointers: [`../AGENTS.md`](../AGENTS.md).
