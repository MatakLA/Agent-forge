#!/usr/bin/env python3
"""Every dashboard translation is a JSON object of strings with a language name, no key the English file lacks, and the same
{placeholders} as the English text."""
import json
import os
import re
import unittest

LOCALES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "static", "locales")
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def load(name):
    with open(os.path.join(LOCALES, name), encoding="utf-8") as f:
        return json.load(f)


class Locales(unittest.TestCase):
    def test_every_file(self):
        english = load("en.json")
        files = sorted(f for f in os.listdir(LOCALES) if f.endswith(".json"))
        self.assertIn("en.json", files)
        for name in files:
            data = load(name)
            with self.subTest(locale=name):
                self.assertIsInstance(data, dict)
                self.assertTrue(data.get("lang.name"), "missing lang.name")
                self.assertTrue(all(isinstance(v, str) for v in data.values()), "all values must be strings")
                self.assertEqual(sorted(set(data) - set(english)), [], "keys that en.json does not have")
                for key, text in data.items():
                    if key in english:
                        self.assertEqual(sorted(PLACEHOLDER.findall(text)), sorted(PLACEHOLDER.findall(english[key])),
                                         "placeholders differ from English in " + key)


if __name__ == "__main__":
    unittest.main()
