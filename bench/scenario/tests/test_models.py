"""Tests for models.Entry: validation, derived dates and the dict round trip."""
import unittest

import helpers
from models import Entry


class EntryTest(unittest.TestCase):
    def test_round_trip_keeps_every_field(self):
        e = helpers.entry(tags=["a", "b"], note="n", id=7)
        self.assertEqual(Entry.from_dict(e.to_dict()), e)

    def test_day_and_end_derive_from_start(self):
        e = helpers.entry("2024-03-04T23:30", 45)
        self.assertEqual(e.day.isoformat(), "2024-03-04")
        self.assertEqual(e.end.strftime("%Y-%m-%dT%H:%M"), "2024-03-05T00:15")

    def test_rejects_bad_values(self):
        for kwargs in ({"minutes": 0}, {"minutes": -5}, {"minutes": 1.5}, {"label": "  "}, {"start": "2024-03-04"}, {"start": "tomorrow"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    helpers.entry(**kwargs)

    def test_label_is_stripped(self):
        self.assertEqual(helpers.entry(label="  api  ").label, "api")


if __name__ == "__main__":
    unittest.main()
