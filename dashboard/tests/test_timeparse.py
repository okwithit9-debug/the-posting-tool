import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from posting_dashboard.timeparse import parse_human_datetime
from tests.helpers import NOW

TZ = ZoneInfo("Etc/GMT+7")


class TimeParseTests(unittest.TestCase):
    def p(self, s):
        return parse_human_datetime(s, now=NOW, tz=TZ)

    def test_full_us_date_with_time(self):
        dt, d = self.p("Will send on Thu, Oct 1, 2026 at 11:22 AM")
        self.assertEqual(dt, datetime(2026, 10, 1, 11, 22, tzinfo=TZ))
        self.assertFalse(d)

    def test_pm_and_noon_midnight(self):
        self.assertEqual(self.p("Oct 1, 2026 at 12:05 PM")[0].hour, 12)
        self.assertEqual(self.p("Oct 1, 2026 at 12:05 AM")[0].hour, 0)
        self.assertEqual(self.p("Oct 1, 2026, 3:22 p.m.")[0].hour, 15)

    def test_date_only(self):
        dt, d = self.p("Publishing on Oct 3, 2026")
        self.assertTrue(d)
        self.assertEqual(dt.date().isoformat(), "2026-10-03")

    def test_no_year_rolls_forward(self):
        dt, _ = self.p("Jan 4 at 9:00 AM")
        self.assertEqual(dt.year, 2027)
        dt, _ = self.p("Fri, Oct 2 at 10:35 AM")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute), (2026, 10, 2, 10, 35))

    def test_relative(self):
        dt, _ = self.p("Tomorrow at 11:22 AM")
        self.assertEqual((dt.month, dt.day, dt.hour), (10, 1, 11))

    def test_iso_with_offset(self):
        dt, _ = self.p("2026-10-01T18:22:00Z")
        self.assertEqual(dt.astimezone(TZ).hour, 11)

    def test_garbage_is_none(self):
        self.assertEqual(self.p("sometime soon"), (None, False))
        self.assertEqual(self.p("Oct 1, 2026 at 13:22 PM"), (None, False))
        self.assertEqual(self.p(""), (None, False))


if __name__ == "__main__":
    unittest.main()


class AmbiguityTests(unittest.TestCase):
    def test_missing_ampm_is_unknown_not_date_only(self):
        self.assertEqual(parse_human_datetime("Oct 1, 2026 at 11:22", now=NOW, tz=TZ), (None, False))

    def test_24h_time_after_date(self):
        dt, d = parse_human_datetime("Oct 1, 2026 18:22", now=NOW, tz=TZ)
        self.assertEqual((dt.hour, dt.minute, d), (18, 22, False))
