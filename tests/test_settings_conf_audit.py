#!/usr/bin/env python3
"""Regression tests verifying example conf settings coverage against EDITABLE and FILE_ONLY.

Public interface: run via pytest or python3 tests/test_settings_conf_audit.py.
Invariants:
- Every KEY= line in etc/*.example.conf is accounted for in EDITABLE or FILE_ONLY.
- No key is double-covered across EDITABLE and FILE_ONLY (disjoint sets).
- Adding an unhandled key to any example conf file fails the test suite.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
bin_lib = os.path.join(ROOT, "bin", "lib")
if bin_lib not in sys.path:
    sys.path.insert(0, bin_lib)

from bin.lib.foreman.settings import EDITABLE, FILE_ONLY


def parse_conf_keys(filepath):
    """Extract configuration keys from non-comment, non-blank lines matching KEY=VALUE."""
    keys = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^([A-Za-z0-9_]+)\s*=", stripped)
            if match:
                keys.append(match.group(1))
    return keys


class TestSettingsConfAudit(unittest.TestCase):
    """Test suite ensuring all settings in example conf files are covered."""

    def test_foreman_example_conf_keys_covered(self):
        """Every key in etc/foreman.example.conf must be covered by EDITABLE | FILE_ONLY."""
        conf_path = os.path.join(os.path.dirname(__file__), "..", "etc", "foreman.example.conf")
        keys = parse_conf_keys(conf_path)
        assert len(keys) > 0, f"Expected non-empty keys parsed from {conf_path}"
        covered_set = set(EDITABLE) | set(FILE_ONLY)
        uncovered = [k for k in keys if k not in covered_set]
        assert not uncovered, f"Uncovered keys in foreman.example.conf: {uncovered}"
        for key in keys:
            assert key in covered_set, f"Key '{key}' in foreman.example.conf is not in EDITABLE or FILE_ONLY"
        assert set(keys).issubset(covered_set)

    def test_forge_example_conf_keys_covered(self):
        """Every key in etc/forge.example.conf must be covered by EDITABLE | FILE_ONLY."""
        conf_path = os.path.join(os.path.dirname(__file__), "..", "etc", "forge.example.conf")
        keys = parse_conf_keys(conf_path)
        assert len(keys) > 0, f"Expected non-empty keys parsed from {conf_path}"
        covered_set = set(EDITABLE) | set(FILE_ONLY)
        uncovered = [k for k in keys if k not in covered_set]
        assert not uncovered, f"Uncovered keys in forge.example.conf: {uncovered}"
        for key in keys:
            assert key in covered_set, f"Key '{key}' in forge.example.conf is not in EDITABLE or FILE_ONLY"
        assert set(keys).issubset(covered_set)

    def test_limits_example_conf_keys_covered(self):
        """Every key in etc/limits.example.conf must be covered by EDITABLE | FILE_ONLY."""
        conf_path = os.path.join(os.path.dirname(__file__), "..", "etc", "limits.example.conf")
        keys = parse_conf_keys(conf_path)
        assert len(keys) > 0, f"Expected non-empty keys parsed from {conf_path}"
        covered_set = set(EDITABLE) | set(FILE_ONLY)
        uncovered = [k for k in keys if k not in covered_set]
        assert not uncovered, f"Uncovered keys in limits.example.conf: {uncovered}"
        for key in keys:
            assert key in covered_set, f"Key '{key}' in limits.example.conf is not in EDITABLE or FILE_ONLY"
        assert set(keys).issubset(covered_set)

    def test_editable_and_file_only_no_overlap(self):
        """EDITABLE and FILE_ONLY must be disjoint sets with no duplicate keys."""
        overlap = set(EDITABLE) & set(FILE_ONLY)
        assert set(EDITABLE) & set(FILE_ONLY) == set(), f"Keys double-covered in EDITABLE and FILE_ONLY: {overlap}"


if __name__ == "__main__":
    unittest.main()
