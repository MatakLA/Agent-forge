"""Tests for clock: date and duration parsing, formatting, week/month helpers and the pinned clock."""
import datetime as dt
import os
import unittest

import helpers  # noqa: F401  (puts the project on sys.path)
import clock


class ParseDurationTest(unittest.TestCase):
    def test_accepted_forms(self):
        for text, minutes in [("90", 90), ("90m", 90), ("2h", 120), ("1h30m", 90), (" 1H05M ", 65)]:
            with self.subTest(text=text):
                self.assertEqual(clock.parse_duration(text), minutes)

    def test_rejected_forms(self):
        for text in ["", "0", "0m", "abc", "1.5h", "h", "-5"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    clock.parse_duration(text)


class ParseDateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(clock.parse_date("2024-02-29"), dt.date(2024, 2, 29))

    def test_invalid(self):
        for text in ["2024-2-9", "2023-02-29", "02/03/2024", "", "2024-13-01"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    clock.parse_date(text)


class FormatAndCalendarTest(unittest.TestCase):
    def test_format_minutes(self):
        self.assertEqual([clock.format_minutes(m) for m in (0, 5, 60, 65, 600)], ["0m", "5m", "1h", "1h05m", "10h"])

    def test_week_and_month_start(self):
        self.assertEqual(clock.week_start(dt.date(2024, 3, 10)), dt.date(2024, 3, 4))
        self.assertEqual(clock.month_start(dt.date(2024, 3, 31)), dt.date(2024, 3, 1))

    def test_date_range_is_inclusive(self):
        days = list(clock.date_range(dt.date(2024, 2, 28), dt.date(2024, 3, 1)))
        self.assertEqual([d.day for d in days], [28, 29, 1])

    def test_pinned_clock(self):
        old = dict(os.environ)
        try:
            os.environ["TASKLOG_TODAY"] = "2024-03-04"
            os.environ["TASKLOG_NOW"] = "2024-03-04T10:15"
            self.assertEqual(clock.today(), dt.date(2024, 3, 4))
            self.assertEqual(clock.now_iso(), "2024-03-04T10:15")
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
