import json
import tempfile
import unittest
from pathlib import Path

from posting_dashboard.ledger import (append_event, backfill, caption_preview, current_state,
                                  iter_events, make_event)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "logs" / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def ev(self, **kw):
        base = dict(basename="demo-clip-1", platform="x", status="scheduled",
                    scheduled_for="2026-10-01T11:22:00-07:00", caption="hello", source="agent")
        base.update(kw)
        return make_event(**base)

    def test_caption_preview_is_capped_at_200(self):
        e = self.ev(caption="word " * 200)
        self.assertLessEqual(len(e["caption_preview"]), 200)
        self.assertEqual(caption_preview("a\n\n b"), "a b")

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.ev(platform="reddit")
        with self.assertRaises(ValueError):
            self.ev(scheduled_for=None)
        with self.assertRaises(ValueError):
            self.ev(scheduled_for="2026-10-01T11:22:00")  # naive: refuse to guess
        with self.assertRaises(ValueError):
            self.ev(basename="../etc/passwd")
        with self.assertRaises(ValueError):
            self.ev(status="posted")

    def test_append_and_fold_latest_wins(self):
        append_event(self.path, self.ev(recorded_at="2026-09-30T10:00:00+00:00"))
        append_event(self.path, self.ev(status="failed", scheduled_for=None, error="picker stuck",
                                        recorded_at="2026-09-30T11:00:00+00:00"))
        append_event(self.path, self.ev(platform="tiktok", recorded_at="2026-09-30T10:30:00+00:00"))
        state = current_state(iter_events(self.path))
        self.assertEqual(state[("demo-clip-1", "x")]["event"], "failed")
        self.assertEqual(state[("demo-clip-1", "tiktok")]["event"], "scheduled")

    def test_corrupt_lines_are_skipped(self):
        append_event(self.path, self.ev())
        with open(self.path, "a") as f:
            f.write("{not json\n\n[1,2]\n" + json.dumps({"platform": "myspace", "basename": "x"}) + "\n")
        self.assertEqual(len(list(iter_events(self.path))), 1)

    def test_backfill_is_idempotent(self):
        evs = [self.ev(source="receipt-backfill"), self.ev(platform="pinterest", source="receipt-backfill")]
        self.assertEqual(backfill(self.path, evs), 2)
        self.assertEqual(backfill(self.path, evs), 0)
        self.assertEqual(len(list(iter_events(self.path))), 2)


if __name__ == "__main__":
    unittest.main()
