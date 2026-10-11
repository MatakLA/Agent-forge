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
from foreman.settings import settings_apply, write_conf


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


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman settings tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
