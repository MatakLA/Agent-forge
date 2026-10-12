#!/usr/bin/env python3
"""Tests for foreman settings validation and configuration persistence.
Public interface: run via python3 tests/test_foreman_settings.py or unittest discovery.
Invariants:
- settings_apply validates all keys in one pass and returns every error.
- write_conf prevents newline and unescaped '=' config injection.
- path traversal and arbitrary file references are rejected across project settings.
- multi-select values like NOTIFY_EVENTS reject unknown options.
"""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-settings-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

from foreman import ctx
from foreman.settings import EDITABLE, settings_apply, settings_get, write_conf


class TestForemanSettings(unittest.TestCase):
    """Unit tests for settings_apply validation and write_conf persistence."""

    def setUp(self):
        """Set up an isolated environment with temporary etc and config files."""
        self.test_dir = tempfile.mkdtemp(prefix="af-settings-run-")
        self.etc_dir = os.path.join(self.test_dir, "etc")
        os.makedirs(self.etc_dir, exist_ok=True)
        self.foreman_conf = os.path.join(self.etc_dir, "foreman.conf")
        self.limits_conf = os.path.join(self.etc_dir, "limits.conf")
        with open(self.foreman_conf, "w") as f:
            f.write("# foreman config\nCLAUDE_MODEL=sonnet\nAUTOPILOT=1\n")
        with open(self.limits_conf, "w") as f:
            f.write("# limits config\nMAX_PARALLEL=4\n")
        self._orig_root = ctx.ROOT
        self._orig_pdir = ctx.PDIR
        ctx.ROOT = self.test_dir
        ctx.PDIR = ""

    def tearDown(self):
        """Restore ctx paths and remove temporary directory."""
        ctx.ROOT = self._orig_root
        ctx.PDIR = self._orig_pdir
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_write_conf_safe_against_newline_injection(self):
        """write_conf must raise ValueError when a value contains a newline, preventing line injection."""
        target = os.path.join(self.etc_dir, "boundary.conf")
        with open(target, "w") as f:
            f.write("AUTOPILOT=1\n")

        with self.assertRaises(ValueError):
            write_conf(target, {"AUTOPILOT": "1\nAUTO_MERGE=0"})

        content = open(target).read()
        self.assertNotIn("AUTO_MERGE", content)
        self.assertEqual(content.strip(), "AUTOPILOT=1")
        assert "AUTO_MERGE" not in content and content.strip() == "AUTOPILOT=1"

    def test_write_conf_safe_against_key_equals_injection(self):
        """write_conf must raise ValueError when a key contains '=' or newlines, preventing config corruption."""
        target = os.path.join(self.etc_dir, "key_boundary.conf")
        with open(target, "w") as f:
            f.write("VALID_KEY=initial\n")

        with self.assertRaises(ValueError):
            write_conf(target, {"INJECTED=KEY": "bad_value"})

        with self.assertRaises(ValueError):
            write_conf(target, {"KEY\nINJECTED": "bad_value"})

        content = open(target).read()
        self.assertNotIn("INJECTED", content)
        self.assertEqual(content.strip(), "VALID_KEY=initial")
        assert "INJECTED" not in content and content.strip() == "VALID_KEY=initial"

    def test_settings_apply_rejects_path_traversal(self):
        """settings_apply must reject path traversal and arbitrary files in VISION_FILE, PROJECT_DIR, and LOCAL_TZ."""
        changes = {
            "VISION_FILE": "../vision.md",
            "PROJECT_DIR": "../..",
            "LOCAL_TZ": "/etc/passwd",
        }
        errs = settings_apply(changes)
        self.assertIn("VISION_FILE", errs)
        self.assertIn("PROJECT_DIR", errs)
        self.assertIn("LOCAL_TZ", errs)
        assert "VISION_FILE" in errs and "PROJECT_DIR" in errs and "LOCAL_TZ" in errs

    def test_settings_apply_returns_all_errors_in_one_pass(self):
        """settings_apply must collect and return errors for every invalid key simultaneously."""
        changes = {
            "CLAUDE_MODEL": "invalid model with spaces",
            "MAX_PARALLEL": "999",
            "UNKNOWN_KEY": "some_value",
        }
        errs = settings_apply(changes)
        self.assertEqual(errs.get("CLAUDE_MODEL"), "invalid value")
        self.assertEqual(errs.get("MAX_PARALLEL"), "number 1-64")
        self.assertEqual(errs.get("UNKNOWN_KEY"), "not editable")
        self.assertEqual(len(errs), 3)
        assert len(errs) == 3

    def test_notify_events_rejects_unknown_kind(self):
        """NOTIFY_EVENTS multi-select must reject unknown kinds while accepting valid ones."""
        errs_bad = settings_apply({"NOTIFY_EVENTS": "goal_done,unknown_kind"})
        self.assertIn("NOTIFY_EVENTS", errs_bad)

        errs_bad_single = settings_apply({"NOTIFY_EVENTS": "bogus"})
        self.assertIn("NOTIFY_EVENTS", errs_bad_single)

        errs_good = settings_apply({"NOTIFY_EVENTS": "goal_done,task_blocked,approval"})
        self.assertEqual(errs_good, {})
        content = open(self.foreman_conf).read()
        self.assertIn("NOTIFY_EVENTS=goal_done,task_blocked,approval", content)
        assert "NOTIFY_EVENTS=goal_done,task_blocked,approval" in content

    def test_settings_apply_routes_scopes_correctly(self):
        """settings_apply writes project keys to foreman.conf and limits to limits.conf."""
        valid_changes = {
            "CLAUDE_MODEL": "opus",
            "MAX_PARALLEL": "8",
            "AUTOPILOT": "0",
        }
        errs = settings_apply(valid_changes)
        self.assertEqual(errs, {})

        foreman_content = open(self.foreman_conf).read()
        limits_content = open(self.limits_conf).read()

        self.assertIn("CLAUDE_MODEL=opus", foreman_content)
        self.assertIn("AUTOPILOT=0", foreman_content)
        self.assertNotIn("MAX_PARALLEL", foreman_content)

        self.assertIn("MAX_PARALLEL=8", limits_content)
        self.assertNotIn("CLAUDE_MODEL", limits_content)
        self.assertNotIn("AUTOPILOT", limits_content)
        assert "CLAUDE_MODEL=opus" in foreman_content and "MAX_PARALLEL=8" in limits_content

    def test_settings_help_text_restart_accuracy(self):
        """Every EDITABLE key must have HELP text in settings.js stating it applies live, and restart keys must state so."""
        import re
        js_path = os.path.join(ROOT, "web", "static", "js", "settings.js")
        js = open(js_path).read()

        help_match = re.search(r"const HELP = \{(.*?)\};", js, re.DOTALL)
        self.assertIsNotNone(help_match)
        help_map = dict(re.findall(r"([A-Z0-9_]+):\s*\x27((?:[^\x27\\]|\\.)*)\x27", help_match.group(1)))

        label_match = re.search(r"const LABEL = \{(.*?)\};", js, re.DOTALL)
        self.assertIsNotNone(label_match)
        label_map = dict(re.findall(r"([A-Z0-9_]+):\s*\x27((?:[^\x27\\]|\\.)*)\x27", label_match.group(1)))

        for k in EDITABLE:
            self.assertIn(k, help_map, f"missing HELP for {k}")
            self.assertIn("applies live", help_map[k].lower(), f"{k} does not state applies live")
            self.assertIn(k, label_map, f"missing LABEL for {k}")
            self.assertTrue(len(label_map[k]) > 0, f"empty label for {k}")

        self.assertIn("restart", help_map["FORGE_URL"].lower())
        self.assertIn("restart", help_map["FORGE_REPO"].lower())
        self.assertIn("ORPHAN_LIMIT", help_map)
        self.assertIn("ORPHAN_WARN", help_map)
        self.assertIn("before the task is blocked", help_map["ORPHAN_LIMIT"])
        self.assertIn("before a warning", help_map["ORPHAN_WARN"])
        assert len(help_map) >= len(EDITABLE) and "before the task is blocked" in help_map["ORPHAN_LIMIT"] and "before a warning" in help_map["ORPHAN_WARN"]

    def test_orphan_settings_help_text(self):
        """help_map contains non-empty, correctly-phrased help text for ORPHAN_LIMIT and ORPHAN_WARN."""
        import re
        js_path = os.path.join(ROOT, "web", "static", "js", "settings.js")
        js = open(js_path).read()

        help_match = re.search(r"const HELP = \{(.*?)\};", js, re.DOTALL)
        self.assertIsNotNone(help_match)
        help_map = dict(re.findall(r"([A-Z0-9_]+):\s*\x27((?:[^\x27\\]|\\.)*)\x27", help_match.group(1)))

        self.assertIn("ORPHAN_LIMIT", help_map)
        self.assertIn("ORPHAN_WARN", help_map)
        self.assertTrue(len(help_map["ORPHAN_LIMIT"]) > 0)
        self.assertTrue(len(help_map["ORPHAN_WARN"]) > 0)
        self.assertIn("before the task is blocked", help_map["ORPHAN_LIMIT"])
        self.assertIn("before a warning", help_map["ORPHAN_WARN"])
        self.assertTrue("consecutive" in help_map["ORPHAN_LIMIT"].lower() and "orphan" in help_map["ORPHAN_LIMIT"].lower())
        self.assertTrue("consecutive" in help_map["ORPHAN_WARN"].lower() and "orphan" in help_map["ORPHAN_WARN"].lower())
        assert "before the task is blocked" in help_map["ORPHAN_LIMIT"] and "before a warning" in help_map["ORPHAN_WARN"]

    def test_orphan_settings_get_defaults_and_scopes(self):
        """settings_get exposes ORPHAN_LIMIT and ORPHAN_WARN with default values 5 and 3 in project scope."""
        res = settings_get()
        self.assertIn("ORPHAN_LIMIT", res["scopes"])
        self.assertIn("ORPHAN_WARN", res["scopes"])
        self.assertEqual(res["scopes"]["ORPHAN_LIMIT"], "project")
        self.assertEqual(res["scopes"]["ORPHAN_WARN"], "project")
        self.assertEqual(res["values"]["ORPHAN_LIMIT"], 5)
        self.assertEqual(res["values"]["ORPHAN_WARN"], 3)
        assert res["scopes"]["ORPHAN_LIMIT"] == "project" and res["values"]["ORPHAN_LIMIT"] == 5

    def test_orphan_settings_apply_valid_and_persistence(self):
        """settings_apply accepts valid integers for ORPHAN_LIMIT and ORPHAN_WARN and writes them to project config."""
        errs = settings_apply({"ORPHAN_LIMIT": "5", "ORPHAN_WARN": "3"})
        self.assertEqual(errs, {})
        foreman_content = open(self.foreman_conf).read()
        self.assertIn("ORPHAN_LIMIT=5", foreman_content)
        self.assertIn("ORPHAN_WARN=3", foreman_content)
        limits_content = open(self.limits_conf).read()
        self.assertNotIn("ORPHAN_LIMIT", limits_content)
        self.assertNotIn("ORPHAN_WARN", limits_content)

        # boundary tests: 1 and 100
        errs_bounds = settings_apply({"ORPHAN_LIMIT": "1", "ORPHAN_WARN": "100"})
        self.assertEqual(errs_bounds, {})
        foreman_content = open(self.foreman_conf).read()
        self.assertIn("ORPHAN_LIMIT=1", foreman_content)
        self.assertIn("ORPHAN_WARN=100", foreman_content)
        assert "ORPHAN_LIMIT=1" in foreman_content and "ORPHAN_WARN=100" in foreman_content

    def test_orphan_settings_apply_rejects_invalid_values(self):
        """settings_apply rejects non-integer and out-of-range values for ORPHAN_LIMIT and ORPHAN_WARN."""
        # non-integers
        for bad in ["abc", "-1", "3.5", "", " 0x10 "]:
            errs = settings_apply({"ORPHAN_LIMIT": bad, "ORPHAN_WARN": bad})
            self.assertEqual(errs.get("ORPHAN_LIMIT"), "number 1-100", f"failed to reject {bad!r} for ORPHAN_LIMIT")
            self.assertEqual(errs.get("ORPHAN_WARN"), "number 1-100", f"failed to reject {bad!r} for ORPHAN_WARN")

        # out-of-range boundaries (0 and 101)
        errs_zero = settings_apply({"ORPHAN_LIMIT": "0", "ORPHAN_WARN": "0"})
        self.assertEqual(errs_zero.get("ORPHAN_LIMIT"), "number 1-100")
        self.assertEqual(errs_zero.get("ORPHAN_WARN"), "number 1-100")

        errs_high = settings_apply({"ORPHAN_LIMIT": "101", "ORPHAN_WARN": "101"})
        self.assertEqual(errs_high.get("ORPHAN_LIMIT"), "number 1-100")
        self.assertEqual(errs_high.get("ORPHAN_WARN"), "number 1-100")

        foreman_content = open(self.foreman_conf).read()
        self.assertNotIn("ORPHAN_LIMIT=0", foreman_content)
        self.assertNotIn("ORPHAN_LIMIT=101", foreman_content)
        assert errs_zero["ORPHAN_LIMIT"] == "number 1-100" and errs_high["ORPHAN_LIMIT"] == "number 1-100"



if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman settings tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
