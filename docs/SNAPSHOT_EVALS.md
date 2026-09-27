# How to grade “did it actually schedule”

This is the grade-the-list harness for **both** paths:

1. **The Posting Tool (primary)** — cloud model / computer-use on the
   your already-open Chrome, following [`PLAYBOOK.md`](PLAYBOOK.md)
2. **Optional backup** — `schedule_batch.py` (Playwright Python, dedicated
   profile, no parity promise)

Grade **snapshots**, not conversations and not click paths.

Construct the state a surface can reach (caption, requested slot, live handle,
whether the native scheduled list shows the item, whether a wall is up). Attach
a test action (`schedule`, `Post now`, `Cancel`, `--immediate`, …). Grade the
**final state**: what a native scheduled list, caption, or halt would show.

```bash
python3 -m unittest tests.test_safety_locks -q
```

If `tests/test_schedule_snapshots.py` is in the tree, run that too. Cases are
unittest methods, not a new agent. No browser is launched in CI.

## User identity is yours

Copy `profile.example.json` to `profile.json` and set `handle`
(`YOUR_HANDLE`), `cta_host` (`YOUR_CTA_HOST`), and the Chrome profile you
already opened. The eval fixture brand is `example-brand` / `example.com`
only. There is no shipped brand handle, CTA host, or Chrome profile name.

## PASS (native list)

A snapshot **PASS** means all of these are true on the constructed final state:

1. **Caption** shown on the list is exactly one URL of the configured `YOUR_CTA_HOST`.
2. **Datetime** on the list matches the requested slot (the picker stuck).
3. **Proof** is an item on that surface’s native scheduled list.

Composer toasts, a closed composer, and `threads.com/scheduled` (404) are not
proof. Threads proof is the header ⋯ list next to Drafts. TikTok proof is the
Studio Content list. X proof is Will send on / scheduled. YouTube proof is
Studio’s scheduled list or API `publishAt`.

## FAIL (halt — never Post now)

The harness fails closed and **never** falls through to Post now / Share now /
Publish now / `--immediate`:

| Final state | Grade |
|---|---|
| Mashed leftover caption, second domain, or link-in-bio as CTA | FAIL |
| Composer toast counted as proof, or composer closed with no list item | FAIL |
| Post now / Share now / Publish now / `--immediate` | FAIL |
| Live handle ≠ configured handle | FAIL (STOP; do not switch accounts) |
| Threads Cancel/leave (Save to drafts) and no list item | FAIL |
| TikTok picker shows a different minute than requested | FAIL (do not commit). An example slot is **not** a user default — only “the datetime you asked for did not stick.” |
| Schedule control missing | FAIL / halt — not Share now |
| Verify-it’s-you, posting restriction, Chrome crash Reopen (a second Chrome) | FAIL / halt |

## SKIP (do not fail at 2am)

If Instagram or Threads daily slots are exhausted, **skip that surface**. The
batch does not abort. Remaining slots print first (`12 of 25 left`).

## Surfaces

`x`, `threads`, `instagram`, `tiktok`, `pinterest`, and `youtube` (API
`publishAt` / Studio Schedule). Each PASS case has a matching negative.

When the Python grader module is present (`platforms/_schedule_snapshots.py`),
it calls the existing safety-lock / preflight / native-list proof helpers. It
does not drive platform clickers. A computer-use agent grades the same way:
open the native list and apply this rubric. Encoded clicks live in
[`PLAYBOOK.md`](PLAYBOOK.md).
