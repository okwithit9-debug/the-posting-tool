# The Posting Tool — cloud-model primary, Python optional

This is the short story.

**The Posting Tool** is the cloud model / computer-use agent on your
already-open Chrome, following [`PLAYBOOK.md`](PLAYBOOK.md). That playbook
**is** the tool.

Gates live in [`SETUP_GATES.md`](SETUP_GATES.md).
Agents read [`../AGENTS.md`](../AGENTS.md).

## 1. The Posting Tool (primary)

A cloud model drives the **your already-open Chrome** — the window already
signed into their brand accounts.

- Native Schedule only. Never Post now.
- Handle-check on the live tab. Wrong handle = STOP. Do not switch accounts.
- Proof = the native scheduled list, not a composer toast.
- Caption CTA = one URL from `profile.json` (`YOUR_HANDLE`, `YOUR_CTA_HOST`).
- Pause on walls. Do not publish-now to “recover.”
- Do not launch a second Chrome.
- Prefer the web file input. Native macOS Open / HID is a wall, not primary.

The user fills `profile.json` (from `profile.example.json`). No shipped
brand handle, CTA host, or Chrome profile name.

## 2. Optional backup — Playwright Python

When cloud-model usage is exhausted, the user *may* use `schedule_batch.py`.

- Same gates as The Posting Tool (preflight / native-list proof / one URL /
  rate limits). `--immediate` is still refused.
- Chrome for this path is a **dedicated Playwright profile** configured in
  `profile.json`. Daily Chrome is not a required default.
- Python stays in the repo. It is **not** the primary path. It is **not**
  promised to stay in parity with [`PLAYBOOK.md`](PLAYBOOK.md). Expect the
  your AI to adapt it (bring-your-own debugger).

```bash
python3 schedule_batch.py preflight
python3 schedule_batch.py dispatch
```

## Grade both paths the same way

[`SNAPSHOT_EVALS.md`](SNAPSHOT_EVALS.md) is the grade-the-list harness for
The Posting Tool and the Python backup. PASS means the native list shows
the item, the caption is one configured URL, and the requested datetime
stuck. FAIL means halt — never Post now. SKIP means an exhausted IG/Threads
day, not a 2am failure.

## Not the primary path

HID / keystroke playback is not the primary path. A second Chrome is never
the recovery. A shipped brand identity is never the default. Python parity
is never the promise.
