# Contributing

Thanks for helping. The Posting Tool is free and open source (MIT).

## Ground rules

- **Native Schedule only.** Never add a "Post now" / immediate-publish
  fallback. A missing scheduler is a wall: pause and report.
- **No second Chrome.** The agent path attaches to the Chrome you already
  have open; the Python backup uses its own Playwright profile.
- **No personal data.** No real handles, emails, machine paths, profile
  numbers, IDs, tokens, or cookies in code, docs, tests, or screenshots.
  Use `YOUR_HANDLE`, `yourdomain.com`, `example.com`, `Profile 1`.
- **No credential scraping.** Do not add code that copies cookies or
  passwords out of a daily browser profile.

## Workflow

1. Fork and create a branch.
2. Make your change. UI selectors break often; keep fixes small and
   per-platform (`platforms/<name>.py`).
3. Run the tests:

   ```bash
   python3 -m unittest tests.test_safety_locks -v
   ```

   `RepoIdentityTests` fails if personal paths/emails/profile numbers slip in.
4. Update `docs/PLAYBOOK.md` if agent-facing behavior changes.
5. Open a pull request describing the platform, the UI change, and how
   you verified the item appeared in the platform's native scheduled list.

## Reporting issues

Include the platform, what the UI showed (redact your handle), and the
receipt JSON from `failed/` if using the Python backup.
