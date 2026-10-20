"""Tests for tags: normalising, parsing free text and reading an entry's tags."""
import unittest

import helpers
import tags


class TagsTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(tags.normalize_tag("  #Back End "), "back-end")

    def test_normalize_rejects_garbage(self):
        for raw in ["", "#", "a" * 31, "ü", "-x"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    tags.normalize_tag(raw)

    def test_parse_tags_dedupes_and_keeps_order(self):
        self.assertEqual(tags.parse_tags("Backend, #api; backend ,review"), ["backend", "api", "review"])
        self.assertEqual(tags.parse_tags(""), [])

    def test_entry_tags_reports_untagged(self):
        self.assertEqual(tags.entry_tags(helpers.entry(tags=["x"])), ["x"])
        self.assertEqual(tags.entry_tags(helpers.entry()), [tags.UNTAGGED])

    def test_all_tags_sorted_and_unique(self):
        self.assertEqual(tags.all_tags(helpers.sample_entries()), ["(untagged)", "api", "backend", "frontend", "review"])


if __name__ == "__main__":
    unittest.main()
