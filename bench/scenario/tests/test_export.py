"""Tests for export: CSV layout, JSON content and unknown formats."""
import json
import unittest

import helpers
import export


class ExportTest(unittest.TestCase):
    def test_csv_has_header_and_joined_tags(self):
        lines = export.to_csv(helpers.sample_entries()).splitlines()
        self.assertEqual(lines[0], "id,start,minutes,label,tags,note")
        self.assertEqual(lines[1], "1,2024-03-04T09:00,90,api,backend;api,")
        self.assertEqual(len(lines), 6)

    def test_csv_quotes_commas(self):
        text = export.to_csv([helpers.entry(note="a, b")])
        self.assertIn('"a, b"', text)

    def test_json_round_trips(self):
        data = json.loads(export.to_json(helpers.sample_entries()))
        self.assertEqual([d["id"] for d in data], [1, 2, 3, 4, 5])

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            export.export([], "xml")


if __name__ == "__main__":
    unittest.main()
