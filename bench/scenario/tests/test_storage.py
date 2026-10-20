"""Tests for storage.Store: ids, ordering, filtering, update/delete and failure on a corrupt file."""
import datetime as dt
import unittest

import helpers
from storage import StoreError


class StoreTest(unittest.TestCase):
    def test_add_assigns_increasing_ids_that_are_not_reused(self):
        with helpers.TempStore() as t:
            s = t.store()
            a, b = s.add(helpers.entry()), s.add(helpers.entry())
            self.assertEqual((a.id, b.id), (1, 2))
            s.delete(b.id)
            self.assertEqual(s.add(helpers.entry()).id, 3)

    def test_all_is_ordered_by_start(self):
        with helpers.TempStore() as t:
            s = t.fill()
            self.assertEqual([e.start for e in s.all()], sorted(e.start for e in helpers.sample_entries()))

    def test_since_filters_by_day(self):
        with helpers.TempStore() as t:
            s = t.fill()
            self.assertEqual([e.label for e in s.since(dt.date(2024, 3, 7))], ["inbox", "ui"])

    def test_update_and_delete(self):
        with helpers.TempStore() as t:
            s = t.fill()
            self.assertEqual(s.update(1, minutes=10).minutes, 10)
            self.assertEqual(s.by_id(1).minutes, 10)
            self.assertTrue(s.delete(1))
            self.assertFalse(s.delete(1))
            self.assertIsNone(s.by_id(1))
            with self.assertRaises(StoreError):
                s.update(99, minutes=5)

    def test_invalid_update_is_refused_and_leaves_store_intact(self):
        with helpers.TempStore() as t:
            s = t.fill()
            with self.assertRaises(ValueError):
                s.update(1, minutes=0)
            self.assertEqual(s.by_id(1).minutes, 90)

    def test_missing_file_is_empty_and_corrupt_file_raises(self):
        with helpers.TempStore() as t:
            self.assertEqual(t.store().all(), [])
            with open(t.path, "w") as f:
                f.write("{nope")
            with self.assertRaises(StoreError):
                t.store().all()
            with self.assertRaises(StoreError):
                t.store().add(helpers.entry())


if __name__ == "__main__":
    unittest.main()
