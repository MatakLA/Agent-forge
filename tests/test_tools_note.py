#!/usr/bin/env python3
"""Tests for sbx.missing_tools_note: the prompt block that stops agents hunting for tools their container lacks.

Public interface: python3 tests/test_tools_note.py
Invariants: nothing missing -> empty string; only tools in PROBE_TOOLS are named; the text forbids searching and installing.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "lib"))
import sbx


class ToolsNote(unittest.TestCase):
    def test_empty_when_nothing_missing(self):
        self.assertEqual(sbx.missing_tools_note([]), "")
        self.assertEqual(sbx.missing_tools_note(["gcc"]), "")

    def test_names_missing_tools_and_forbids_search(self):
        n = sbx.missing_tools_note(["node", "npm", "gcc"])
        self.assertIn("Not installed: node, npm.", n)
        self.assertNotIn("gcc", n)
        self.assertIn("Do not look for them", n)


if __name__ == "__main__":
    unittest.main()
