#!/usr/bin/env python3
"""Tests for orphan warning UI and serialization contract (Task 0123).
Public interface: run directly via python3 tests/test_orphan_warning_ui.py or unittest discovery.
Invariants:
- A task with orphan_streak between ORPHAN_WARN and ORPHAN_LIMIT and not blocked has orphan_warning=True.
- A task with orphan_streak below ORPHAN_WARN has orphan_warning=False.
- A blocked task has orphan_warning=False regardless of orphan_streak.
- Both dashboard_json() and task_detail() serialize orphan_streak, orphan_warning, and orphan_limit.
- web/static/js/tasks.js renders the dynamic streak count badge on board cards and in task detail sheet.
"""
import sys

try:
    import json
    import os
    import re
    import shutil
    import tempfile
    import unittest
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.environ.setdefault("LIMITS_FILE", os.path.join(ROOT, "etc", "limits.example.conf"))
    if os.path.join(ROOT, "bin", "lib") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
    import sbx
    sbx.LIMITS_FILE = os.environ["LIMITS_FILE"]
    from foreman import ctx, report, store
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestOrphanWarningUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orphan-warn-test-")
        self.old_fd = ctx.FD
        ctx.FD = self.tmp
        os.makedirs(os.path.join(self.tmp, "tasks"), exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "goals"), exist_ok=True)
        self.old_c = dict(ctx.C)
        ctx.C["ORPHAN_WARN"] = 3
        ctx.C["ORPHAN_LIMIT"] = 5
        assert os.path.isdir(self.tmp)

    def tearDown(self):
        ctx.FD = self.old_fd
        ctx.C.clear()
        ctx.C.update(self.old_c)
        shutil.rmtree(self.tmp, ignore_errors=True)
        assert not os.path.exists(self.tmp)

    def _create_task(self, tid, streak=0, status="pending", goal="G1"):
        t = {
            "id": tid,
            "goal": goal,
            "title": f"Task {tid}",
            "role": "developer",
            "status": status,
            "attempts": 0,
            "orphan_streak": streak,
            "history": [],
            "files": ["a.py"]
        }
        store.save_task(t)
        return t

    def test_api_orphan_warning_between_warn_and_limit(self):
        """Invariant: orphan_warning is True when streak is between ORPHAN_WARN and ORPHAN_LIMIT and task not blocked."""
        t_warn_3 = self._create_task("0001-warn", streak=3, status="pending")
        t_warn_4 = self._create_task("0002-warn4", streak=4, status="running")

        self.assertTrue(report.is_orphan_warning(t_warn_3))
        self.assertTrue(report.is_orphan_warning(t_warn_4))
        assert report.is_orphan_warning(t_warn_3) is True
        assert report.is_orphan_warning(t_warn_4) is True

        d = report.dashboard_json()
        tasks_map = {t["id"]: t for t in d.get("tasks", [])}
        self.assertIn("0001-warn", tasks_map)
        self.assertTrue(tasks_map["0001-warn"]["orphan_warning"])
        self.assertEqual(tasks_map["0001-warn"]["orphan_streak"], 3)
        assert tasks_map["0001-warn"]["orphan_warning"] is True
        assert tasks_map["0001-warn"]["orphan_streak"] == 3

        detail = report.task_detail("0001-warn")
        self.assertIsNotNone(detail)
        self.assertTrue(detail["task"]["orphan_warning"])
        self.assertEqual(detail["task"]["orphan_streak"], 3)
        assert detail["task"]["orphan_warning"] is True
        assert detail["task"]["orphan_streak"] == 3

    def test_api_no_orphan_warning_below_warn(self):
        """Invariant: orphan_warning is False when orphan_streak is below ORPHAN_WARN."""
        t_clean_0 = self._create_task("0003-clean0", streak=0, status="pending")
        t_clean_1 = self._create_task("0004-clean1", streak=1, status="pending")
        t_clean_2 = self._create_task("0005-clean2", streak=2, status="pending")

        self.assertFalse(report.is_orphan_warning(t_clean_0))
        self.assertFalse(report.is_orphan_warning(t_clean_1))
        self.assertFalse(report.is_orphan_warning(t_clean_2))
        assert report.is_orphan_warning(t_clean_0) is False
        assert report.is_orphan_warning(t_clean_1) is False
        assert report.is_orphan_warning(t_clean_2) is False

        d = report.dashboard_json()
        tasks_map = {t["id"]: t for t in d.get("tasks", [])}
        self.assertFalse(tasks_map["0005-clean2"]["orphan_warning"])
        self.assertEqual(tasks_map["0005-clean2"]["orphan_streak"], 2)
        assert tasks_map["0005-clean2"]["orphan_warning"] is False
        assert tasks_map["0005-clean2"]["orphan_streak"] == 2

        detail = report.task_detail("0005-clean2")
        self.assertFalse(detail["task"]["orphan_warning"])
        self.assertEqual(detail["task"]["orphan_streak"], 2)
        assert detail["task"]["orphan_warning"] is False
        assert detail["task"]["orphan_streak"] == 2

    def test_api_no_orphan_warning_when_already_blocked(self):
        """Invariant: a blocked task shows no orphan_warning even with streak >= ORPHAN_WARN."""
        t_blocked = self._create_task("0006-blk", streak=3, status="blocked")
        t_blocked_limit = self._create_task("0007-blk5", streak=5, status="blocked")

        self.assertFalse(report.is_orphan_warning(t_blocked))
        self.assertFalse(report.is_orphan_warning(t_blocked_limit))
        assert report.is_orphan_warning(t_blocked) is False
        assert report.is_orphan_warning(t_blocked_limit) is False

        d = report.dashboard_json()
        tasks_map = {t["id"]: t for t in d.get("tasks", [])}
        self.assertFalse(tasks_map["0006-blk"]["orphan_warning"])
        assert tasks_map["0006-blk"]["orphan_warning"] is False

        detail = report.task_detail("0006-blk")
        self.assertFalse(detail["task"]["orphan_warning"])
        assert detail["task"]["orphan_warning"] is False

    def test_api_no_orphan_warning_at_or_above_limit(self):
        """Invariant: orphan_warning is False when orphan_streak >= ORPHAN_LIMIT."""
        t_at_limit = self._create_task("0008-lim5", streak=5, status="pending")
        t_above_limit = self._create_task("0009-lim6", streak=6, status="pending")

        self.assertFalse(report.is_orphan_warning(t_at_limit))
        self.assertFalse(report.is_orphan_warning(t_above_limit))
        assert report.is_orphan_warning(t_at_limit) is False
        assert report.is_orphan_warning(t_above_limit) is False

        d = report.dashboard_json()
        tasks_map = {t["id"]: t for t in d.get("tasks", [])}
        self.assertFalse(tasks_map["0008-lim5"]["orphan_warning"])
        assert tasks_map["0008-lim5"]["orphan_warning"] is False

    def test_api_custom_configurable_thresholds(self):
        """Invariant: orphan_warning respects dynamic ORPHAN_WARN and ORPHAN_LIMIT from ctx.C."""
        ctx.C["ORPHAN_WARN"] = 2
        ctx.C["ORPHAN_LIMIT"] = 4
        t2 = self._create_task("0010-custom2", streak=2, status="pending")
        t1 = self._create_task("0011-custom1", streak=1, status="pending")
        t4 = self._create_task("0012-custom4", streak=4, status="pending")

        self.assertTrue(report.is_orphan_warning(t2))
        self.assertFalse(report.is_orphan_warning(t1))
        self.assertFalse(report.is_orphan_warning(t4))
        assert report.is_orphan_warning(t2) is True
        assert report.is_orphan_warning(t1) is False
        assert report.is_orphan_warning(t4) is False

        d = report.dashboard_json()
        tasks_map = {t["id"]: t for t in d.get("tasks", [])}
        self.assertTrue(tasks_map["0010-custom2"]["orphan_warning"])
        self.assertFalse(tasks_map["0011-custom1"]["orphan_warning"])
        self.assertFalse(tasks_map["0012-custom4"]["orphan_warning"])
        assert tasks_map["0010-custom2"]["orphan_warning"] is True
        assert tasks_map["0011-custom1"]["orphan_warning"] is False
        assert tasks_map["0012-custom4"]["orphan_warning"] is False

    def test_api_serialization_whitelist_and_fields(self):
        """Invariant: orphan_streak and orphan_warning are included in whitelist and output dictionaries."""
        self._create_task("0013-chk", streak=3, status="pending")
        d = report.dashboard_json()
        t_dash = next(t for t in d["tasks"] if t["id"] == "0013-chk")
        self.assertIn("orphan_streak", t_dash)
        self.assertIn("orphan_warning", t_dash)
        self.assertIn("orphan_limit", t_dash)
        assert "orphan_streak" in t_dash
        assert "orphan_warning" in t_dash
        assert "orphan_limit" in t_dash

        detail = report.task_detail("0013-chk")
        t_det = detail["task"]
        self.assertIn("orphan_streak", t_det)
        self.assertIn("orphan_warning", t_det)
        self.assertIn("orphan_limit", t_det)
        assert "orphan_streak" in t_det
        assert "orphan_warning" in t_det
        assert "orphan_limit" in t_det

    def test_ui_contract_tasks_js_badge_and_streak_interpolation(self):
        """Invariant: web/static/js/tasks.js defines orphan warning badge reflecting streak count."""
        js_path = os.path.join(ROOT, "web", "static", "js", "tasks.js")
        with open(js_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        self.assertIn("orphan_warning", js_code)
        self.assertIn("orphan_streak", js_code)
        self.assertIn("crashing repeatedly", js_code)
        self.assertIn("consecutive orphan recoveries", js_code)
        self.assertIn("orphanBadge", js_code)
        assert "orphan_warning" in js_code
        assert "orphan_streak" in js_code
        assert "crashing repeatedly" in js_code
        assert "consecutive orphan recoveries" in js_code
        assert "orphanBadge" in js_code

        # Verify badge definition contains dynamic streak count rather than hardcoded value
        self.assertRegex(js_code, r"orphan_streak")
        self.assertRegex(js_code, r"badge warn")
        assert re.search(r"orphan_streak", js_code) is not None
        assert re.search(r"badge warn", js_code) is not None

        # Verify card template includes orphan warning
        self.assertIn("orphanBadge", js_code)
        assert "orphanBadge" in js_code


if __name__ == "__main__":
    unittest.main()
