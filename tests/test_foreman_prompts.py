"""Unit tests for orchestrator planning context and orphan awareness (bin/lib/foreman/prompts.py)."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    from foreman import ctx, prompts
    from foreman.prompts import context_bundle, orphan_section, plan_prompt
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestForemanPromptsOrphanAwareness(unittest.TestCase):
    def setUp(self):
        self.orig_orphan_limit = ctx.C.get("ORPHAN_LIMIT")
        self.orig_orphan_warn = ctx.C.get("ORPHAN_WARN")
        ctx.C["ORPHAN_LIMIT"] = 5
        ctx.C["ORPHAN_WARN"] = 3

    def tearDown(self):
        if self.orig_orphan_limit is not None:
            ctx.C["ORPHAN_LIMIT"] = self.orig_orphan_limit
        if self.orig_orphan_warn is not None:
            ctx.C["ORPHAN_WARN"] = self.orig_orphan_warn

    def test_orphan_blocked_task_surfaced_with_log_tail_and_diagnose_instruction(self):
        """Invariant: tasks blocked with reason starting 'orphan limit reached: ' are surfaced with log tail and instruction."""
        tail = "FATAL: segmentation fault in runner\nexit code 139\n"
        t_blocked = dict(
            id="0001-crash",
            title="Segmentation fault task",
            status="blocked",
            role="backend-developer",
            blocked_why="orphan limit reached: 5 consecutive crash-and-orphan cycles with no progress\n--- last log ---\n" + tail,
            orphan_log_tail=tail,
            orphan_streak=5,
        )
        bundle = context_bundle([t_blocked])
        self.assertIn("## Tasks blocked (orphan limit reached)", bundle)
        self.assertIn("- Task 0001-crash: Segmentation fault task", bundle)
        self.assertIn(tail, bundle)
        self.assertIn("Diagnose the underlying cause (e.g. a crash in the worker/supervisor)", bundle)
        self.assertIn("explicitly not to just re-create an identical task that will crash the same way", bundle)

        goal = dict(id="G1", text="Sample goal")
        with patch.object(prompts, "tasks", return_value=[t_blocked]):
            prompt = plan_prompt(goal)
        self.assertIn("## Tasks blocked (orphan limit reached)", prompt)
        self.assertIn("- Task 0001-crash: Segmentation fault task", prompt)
        self.assertIn(tail, prompt)

        assert "## Tasks blocked (orphan limit reached)" in bundle
        assert "0001-crash" in bundle
        assert tail in bundle
        assert "Diagnose the underlying cause" in bundle
        assert "## Tasks blocked (orphan limit reached)" in prompt

    def test_orphan_warn_range_surfaced_as_heads_up(self):
        """Invariant: tasks in warn range (warn <= streak < limit, not yet blocked) are surfaced as early heads-up."""
        t_warn = dict(
            id="0002-warn",
            title="Flaky task approaching limit",
            status="pending",
            role="backend-developer",
            orphan_streak=3,
        )
        bundle = context_bundle([t_warn])
        self.assertIn("Early heads-up", bundle)
        self.assertIn("0002-warn", bundle)
        self.assertIn("Flaky task approaching limit", bundle)
        self.assertIn("3 consecutive orphan recoveries", bundle)
        self.assertNotIn("## Tasks blocked (orphan limit reached)", bundle)

        goal = dict(id="G1", text="Sample goal")
        with patch.object(prompts, "tasks", return_value=[t_warn]):
            prompt = plan_prompt(goal)
        self.assertIn("Early heads-up", prompt)
        self.assertIn("0002-warn", prompt)

        assert "Early heads-up" in bundle
        assert "0002-warn" in bundle
        assert "3 consecutive orphan recoveries" in bundle
        assert "Early heads-up" in prompt

    def test_no_orphan_history_produces_no_orphan_section(self):
        """Invariant: tasks with no orphan history produce no orphan section in the prompt."""
        normal_tasks = [
            dict(id="0010-t1", title="Normal task 1", status="done", role="tester"),
            dict(id="0011-t2", title="Normal task 2", status="running", role="developer", orphan_streak=0),
            dict(id="0012-t3", title="Normal task 3", status="pending", role="writer"),
            dict(id="0013-t4", title="Normal task 4", status="blocked", role="developer", blocked_why="still failing after 4 attempts"),
        ]
        bundle = context_bundle(normal_tasks)
        self.assertNotIn("## Tasks blocked (orphan limit reached)", bundle)
        self.assertNotIn("orphan limit reached: ", bundle)
        self.assertNotIn("Early heads-up", bundle)
        self.assertNotIn("Diagnose the underlying cause", bundle)

        goal = dict(id="G1", text="Sample goal")
        with patch.object(prompts, "tasks", return_value=normal_tasks):
            prompt = plan_prompt(goal)
        self.assertNotIn("## Tasks blocked (orphan limit reached)", prompt)
        self.assertNotIn("orphan limit reached: ", prompt)
        self.assertNotIn("Early heads-up", prompt)

        assert "## Tasks blocked (orphan limit reached)" not in bundle
        assert "Early heads-up" not in bundle
        assert "## Tasks blocked (orphan limit reached)" not in prompt

    def test_both_blocked_and_warn_tasks_coexist_in_prompt(self):
        """Invariant: both blocked-by-orphan and warn-range tasks appear together cleanly in the same prompt."""
        t_blocked = dict(
            id="0001-crash",
            title="Crash task",
            status="blocked",
            role="developer",
            blocked_why="orphan limit reached: 5 consecutive crash-and-orphan cycles with no progress",
            orphan_log_tail="FATAL: crash log here",
            orphan_streak=5,
        )
        t_warn = dict(
            id="0002-warn",
            title="Warn task",
            status="pending",
            role="developer",
            orphan_streak=4,
        )
        bundle = context_bundle([t_blocked, t_warn])
        self.assertIn("## Tasks blocked (orphan limit reached)", bundle)
        self.assertIn("0001-crash", bundle)
        self.assertIn("FATAL: crash log here", bundle)
        self.assertIn("Early heads-up", bundle)
        self.assertIn("0002-warn", bundle)
        self.assertIn("4 consecutive orphan recoveries", bundle)

        assert "0001-crash" in bundle
        assert "0002-warn" in bundle
        assert "FATAL: crash log here" in bundle
        assert "Early heads-up" in bundle

    def test_completed_or_cancelled_tasks_ignored(self):
        """Invariant: completed or cancelled tasks with past orphan info do not trigger orphan warnings or blocks."""
        t_done = dict(
            id="0003-done",
            title="Done task",
            status="done",
            role="developer",
            orphan_streak=4,
            blocked_why="orphan limit reached: 5 ...",
        )
        t_cancelled = dict(
            id="0004-cancel",
            title="Cancelled task",
            status="cancelled",
            role="developer",
            orphan_streak=3,
            blocked_why="orphan limit reached: 5 ...",
        )
        section = orphan_section([t_done, t_cancelled])
        self.assertEqual(section, "")

        assert section == ""

    def test_custom_orphan_thresholds_honored(self):
        """Invariant: custom ORPHAN_WARN and ORPHAN_LIMIT settings in ctx.C are respected."""
        ctx.C["ORPHAN_WARN"] = 2
        ctx.C["ORPHAN_LIMIT"] = 4
        t_warn = dict(
            id="0005-custom",
            title="Custom warn task",
            status="pending",
            role="developer",
            orphan_streak=2,
        )
        section = orphan_section([t_warn])
        self.assertIn("0005-custom", section)
        self.assertIn("2 consecutive orphan recoveries", section)
        self.assertIn("warn threshold: 2", section)
        self.assertIn("blocks at 4", section)

        assert "0005-custom" in section
        assert "warn threshold: 2" in section

    def test_orphan_log_tail_fallback_from_blocked_why(self):
        """Invariant: if orphan_log_tail is empty on task dict, tail is extracted from blocked_why."""
        t_blocked = dict(
            id="0006-fallback",
            title="Fallback log tail task",
            status="blocked",
            role="developer",
            blocked_why="orphan limit reached: 5 consecutive cycles\n--- last log ---\nRECOVERED ERROR LOG",
            orphan_log_tail="",
            orphan_streak=5,
        )
        section = orphan_section([t_blocked])
        self.assertIn("RECOVERED ERROR LOG", section)

        assert "RECOVERED ERROR LOG" in section

    def test_orphan_edge_cases_empty_and_missing_fields(self):
        """Invariant: empty list, non-dict, missing title, and non-integer streak handle gracefully."""
        self.assertEqual(orphan_section([]), "")
        self.assertEqual(orphan_section([None, "not a dict", 123]), "")

        t_no_title = dict(
            id="0007-notitle",
            status="blocked",
            blocked_why="orphan limit reached: 5 ...",
            orphan_log_tail="some tail",
        )
        section = orphan_section([t_no_title])
        self.assertIn("0007-notitle", section)
        self.assertIn("some tail", section)

        t_bad_streak = dict(
            id="0008-badstreak",
            status="pending",
            orphan_streak="not-a-number",
        )
        self.assertEqual(orphan_section([t_bad_streak]), "")

        assert orphan_section([]) == ""
        assert "0007-notitle" in section


if __name__ == "__main__":
    unittest.main()
