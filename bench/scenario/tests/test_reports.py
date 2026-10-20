"""Tests for reports: the totals, rankings and renderers used by `tasklog report`."""
import datetime as dt
import unittest

import helpers
import reports

D = dt.date


class TotalsTest(unittest.TestCase):
    def setUp(self):
        self.es = helpers.sample_entries()

    def test_period_totals(self):
        self.assertEqual(reports.daily_totals(self.es), {D(2024, 3, 4): 135, D(2024, 3, 5): 120, D(2024, 3, 7): 30, D(2024, 3, 11): 60})
        self.assertEqual(reports.weekly_totals(self.es), {D(2024, 3, 4): 285, D(2024, 3, 11): 60})
        self.assertEqual(reports.monthly_totals(self.es), {D(2024, 3, 1): 345})

    def test_minutes_by_label_orders_by_minutes_then_name(self):
        self.assertEqual(list(reports.minutes_by_label(self.es).items()), [("api", 210), ("ui", 60), ("review", 45), ("inbox", 30)])

    def test_minutes_by_label_with_key(self):
        self.assertEqual(reports.minutes_by_label(self.es, key=lambda e: e.day.weekday())[0], 195)

    def test_rankings(self):
        self.assertEqual([e.id for e in reports.longest_entries(self.es, 2)], [3, 1])
        self.assertEqual([e.id for e in reports.shortest_entries(self.es, 1)], [4])
        self.assertEqual(reports.top_labels(self.es, 1), [("api", 210)])

    def test_empty_input_never_raises(self):
        self.assertEqual(reports.daily_totals([]), {})
        self.assertEqual(reports.average_per_day([]), 0.0)
        self.assertIsNone(reports.busiest_day([]))
        self.assertEqual(reports.percent_breakdown([]), {})
        self.assertEqual(reports.build_report([], "summary"), "No entries.")


class ShapeTest(unittest.TestCase):
    def setUp(self):
        self.es = helpers.sample_entries()

    def test_averages_and_extremes(self):
        self.assertEqual(reports.average_per_day(self.es), 86.25)
        self.assertAlmostEqual(reports.average_per_day(self.es, only_active_days=False), 345 / 8)
        self.assertEqual(reports.median_entry_length(self.es), 60.0)
        self.assertEqual(reports.busiest_day(self.es), (D(2024, 3, 4), 135))
        self.assertEqual(reports.quietest_day(self.es), (D(2024, 3, 7), 30))
        self.assertEqual(reports.busiest_week(self.es), (D(2024, 3, 4), 285))

    def test_streaks_and_gaps(self):
        self.assertEqual(reports.streaks(self.es), [(D(2024, 3, 4), D(2024, 3, 5), 2), (D(2024, 3, 7), D(2024, 3, 7), 1), (D(2024, 3, 11), D(2024, 3, 11), 1)])
        self.assertEqual(reports.longest_streak(self.es)[2], 2)
        self.assertEqual(reports.current_streak(self.es, D(2024, 3, 12)), 1)
        self.assertEqual(reports.current_streak(self.es, D(2024, 3, 20)), 0)
        gaps = reports.idle_gaps(self.es, 60)
        self.assertEqual([g[2] for g in gaps], [150])

    def test_overlapping_entries(self):
        a, b = helpers.entry("2024-03-04T09:00", 60, id=1), helpers.entry("2024-03-04T09:30", 60, id=2)
        self.assertEqual(reports.overlapping([a, b]), [(a, b)])
        self.assertEqual(reports.overlapping(self.es), [])

    def test_breakdowns(self):
        self.assertEqual(reports.percent_breakdown(self.es)["api"], 60.9)
        self.assertEqual(reports.weekday_profile(self.es), [195, 120, 0, 30, 0, 0, 0])
        self.assertEqual(len(reports.hour_profile(self.es)), 24)
        gap = [helpers.entry("2024-03-04T09:00", 10, "a"), helpers.entry("2024-03-18T09:00", 20, "a")]
        self.assertEqual(reports.label_trend(gap, "a"), [(D(2024, 3, 4), 10), (D(2024, 3, 11), 0), (D(2024, 3, 18), 20)])
        self.assertEqual(reports.compare_periods(self.es, D(2024, 3, 7)), {"before": 255, "after": 90, "change_percent": -64.7})

    def test_goals_and_smoothing(self):
        self.assertEqual(reports.goal_progress(self.es, 600, D(2024, 3, 4), D(2024, 3, 10)), 47.5)
        self.assertEqual(reports.weeks_meeting_goal(self.es, 200), [D(2024, 3, 4)])
        self.assertEqual(reports.rolling_average(self.es, 2)[0], (D(2024, 3, 4), 67.5))
        self.assertEqual([reports.round_minutes(m, 15) for m in (1, 7, 8, 22, 23)], [0, 0, 15, 15, 30])
        self.assertEqual(reports.inactive_days(self.es, D(2024, 3, 4), D(2024, 3, 8)), [D(2024, 3, 6), D(2024, 3, 8)])

    def test_merge_adjacent_joins_touching_entries_of_one_label(self):
        es = [helpers.entry("2024-03-04T09:00", 30, "a"), helpers.entry("2024-03-04T09:30", 30, "a"), helpers.entry("2024-03-04T11:00", 30, "a")]
        self.assertEqual(reports.merge_adjacent(es), [("a", "2024-03-04T09:00", 60), ("a", "2024-03-04T11:00", 30)])


class RenderTest(unittest.TestCase):
    def test_render_table_aligns_and_trims(self):
        text = reports.render_table(["A", "N"], [("x", 5), ("long", 123)], "lr")
        self.assertEqual(text.splitlines(), ["A       N", "x       5", "long  123"])

    def test_histogram_scales_to_biggest(self):
        self.assertEqual(reports.histogram([10, 5, 0, 1], width=10), ["#" * 10, "#" * 5, "", "#"])

    def test_build_report_kinds_and_unknown(self):
        es = helpers.sample_entries()
        self.assertTrue(reports.build_report(es, "summary").startswith("Entries:        5"))
        self.assertIn("2024-03-04", reports.build_report(es, "day"))
        self.assertEqual(reports.build_report(es, "month").splitlines()[1], "2024-03  5h45m")
        with self.assertRaises(ValueError):
            reports.build_report(es, "nope")


if __name__ == "__main__":
    unittest.main()
