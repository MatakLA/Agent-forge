#!/usr/bin/env python3
"""Foreman loop state-machine tests for worker dispatch, orphan recovery, and goal lifecycle.
Public interface: run directly via python3 tests/test_foreman_loop.py or unittest discovery.
Invariants:
- dispatch_ready: prevents double dispatch across idle workers for overlapping files or duplicate keys.
- orphaned: detects dead supervisors without false negatives (missing pid) or false positives (alive runs).
- advance: active goals leave active state once all tasks terminate; tasks for missing goals are safely cancelled.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Set test environment before any sbx or foreman imports
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-loop-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import sbx
from foreman import ctx, loop, pool, store, util


class TestForemanLoop(unittest.TestCase):
    """Unit tests for the foreman state-machine: dispatch, orphan recovery, and goal lifecycle."""

    def setUp(self):
        """Prepare a clean throwaway runs directory and reset in-memory foreman caches."""
        shutil.rmtree(T, ignore_errors=True)
        os.makedirs(os.path.join(ctx.FD, "tasks"), exist_ok=True)
        os.makedirs(os.path.join(ctx.FD, "goals"), exist_ok=True)
        loop.INFLIGHT.clear()
        loop.SINCE.clear()
        loop.PHASE.clear()
        loop.COOLDOWN.clear()
        loop.RESERVED.clear()
        loop.LAST_DISPATCHED.clear()
        loop.QUOTA_CACHE.clear()
        if hasattr(loop, "DISPATCHING"):
            loop.DISPATCHING.clear()
            loop.DISPATCHING_FILES.clear()
            loop.DISPATCHING_WORKERS.clear()

        # Silence live event printing to sys.stderr during test runs
        def _silent_event(msg, **kw):
            os.makedirs(ctx.FD, exist_ok=True)
            with open(os.path.join(ctx.FD, "events.jsonl"), "a") as f:
                f.write(json.dumps(dict(at=sbx.now(), msg=msg, **kw), ensure_ascii=False) + "\n")

        self._p_store_event = patch.object(store, "event", side_effect=_silent_event)
        self._p_loop_event = patch.object(loop, "event", side_effect=_silent_event)
        self._p_store_event.start()
        self._p_loop_event.start()

    def tearDown(self):
        """Stop mock patchers and clean up throwaway state."""
        self._p_store_event.stop()
        self._p_loop_event.stop()
        shutil.rmtree(T, ignore_errors=True)

    def test_dispatch_ready_no_double_dispatch_overlapping_files(self):
        """Invariant: dispatch_ready must not dispatch tasks with overlapping files to multiple workers in one pass."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=0,
                  files=["common.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        t2 = dict(id="0002-t2", key="k2", title="Task 2", goal="G1", status="pending", attempts=0,
                  files=["common.py"], role="developer", branch="feat/0002-t2", spec="spec 2", depends_on=[])
        store.save_task(t1)
        store.save_task(t2)

        def fake_sh(cmd, timeout=300, env=None):
            w = cmd[1]
            run_dir = os.path.join(sbx.RUNS, f"20260101T000000Z-{w}")
            os.makedirs(run_dir, exist_ok=True)
            return 0, f"dispatched to {w} -> {run_dir} (ok)", ""

        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", side_effect=fake_sh) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            loop.dispatch_ready()

        self.assertEqual(mock_sh.call_count, 1)
        assert mock_sh.call_count == 1
        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0001-t1"]["status"], "running")
        assert tasks_after["0001-t1"]["status"] == "running"
        self.assertEqual(tasks_after["0002-t2"]["status"], "pending")
        assert tasks_after["0002-t2"]["status"] == "pending"

    def test_dispatch_ready_no_double_dispatch_duplicate_keys(self):
        """Invariant: dispatch_ready must not dispatch duplicate task keys across workers within one pass."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="duplicate-key", title="Task 1", goal="G1", status="pending", attempts=0,
                  files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        t2 = dict(id="0002-t2", key="duplicate-key", title="Task 2", goal="G1", status="pending", attempts=0,
                  files=["b.py"], role="developer", branch="feat/0002-t2", spec="spec 2", depends_on=[])
        store.save_task(t1)
        store.save_task(t2)

        def fake_sh(cmd, timeout=300, env=None):
            w = cmd[1]
            run_dir = os.path.join(sbx.RUNS, f"20260101T000000Z-{w}")
            os.makedirs(run_dir, exist_ok=True)
            return 0, f"dispatched to {w} -> {run_dir} (ok)", ""

        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", side_effect=fake_sh) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            loop.dispatch_ready()

        self.assertEqual(mock_sh.call_count, 1)
        assert mock_sh.call_count == 1
        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0001-t1"]["status"], "running")
        assert tasks_after["0001-t1"]["status"] == "running"
        self.assertEqual(tasks_after["0002-t2"]["status"], "pending")
        assert tasks_after["0002-t2"]["status"] == "pending"

    def test_orphaned_missing_pid_detected_as_orphan(self):
        """Invariant: a run without a finished file and missing/corrupted pid is detected as an orphan (no false negative)."""
        run_orphan = os.path.join(sbx.RUNS, "20260101T000000Z-worker-3")
        os.makedirs(run_orphan, exist_ok=True)
        # No finished file, no pid file created
        is_orphan = loop.orphaned(run_orphan)
        self.assertTrue(is_orphan)
        assert is_orphan is True

    def test_orphaned_dead_run_detected_as_orphan(self):
        """Invariant: a run that has not finished and whose supervisor is not running is detected as an orphan."""
        run_dead = os.path.join(sbx.RUNS, "20260101T000000Z-worker-4")
        os.makedirs(run_dead, exist_ok=True)
        with patch.object(sbx, "is_running", return_value=False) as mock_is_running:
            is_orphan = loop.orphaned(run_dead)
            self.assertTrue(mock_is_running.called)
            assert mock_is_running.called
            self.assertTrue(is_orphan)
            assert is_orphan is True

    def test_orphaned_alive_run_not_treated_as_orphan(self):
        """Invariant: an actively running run is never treated as an orphan (false-positive guard against double dispatch)."""
        run_alive = os.path.join(sbx.RUNS, "20260101T000000Z-worker-5")
        os.makedirs(run_alive, exist_ok=True)
        with patch.object(sbx, "is_running", return_value=True) as mock_is_running:
            is_orphan = loop.orphaned(run_alive)
            self.assertTrue(mock_is_running.called)
            assert mock_is_running.called
            self.assertFalse(is_orphan)
            assert is_orphan is False

    def test_orphaned_finished_run_not_treated_as_orphan(self):
        """Invariant: a finished run is never treated as an orphan."""
        run_finished = os.path.join(sbx.RUNS, "20260101T000000Z-worker-6")
        os.makedirs(run_finished, exist_ok=True)
        open(os.path.join(run_finished, "finished"), "w").write("ok\n")
        is_orphan = loop.orphaned(run_finished)
        self.assertFalse(is_orphan)
        assert is_orphan is False

    def test_advance_goal_leaves_active_when_all_tasks_cancelled(self):
        """Invariant: a goal must leave active state and transition to cancelled when all its tasks are cancelled."""
        g1 = dict(id="G20261011-000001", text="Test goal 1", status="active", rounds=1)
        store.save_goal(g1)
        t1 = dict(id="0001-t1", goal="G20261011-000001", title="Task 1", history=[], status="cancelled",
                  files=["a.py"], role="developer", branch="feat/0001-t1")
        t2 = dict(id="0002-t2", goal="G20261011-000001", title="Task 2", history=[], status="cancelled",
                  files=["b.py"], role="developer", branch="feat/0002-t2")
        store.save_task(t1)
        store.save_task(t2)

        with patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        goals_after = {g["id"]: g for g in store.goals()}
        self.assertEqual(goals_after["G20261011-000001"]["status"], "cancelled")
        assert goals_after["G20261011-000001"]["status"] == "cancelled"

    def test_advance_goal_leaves_active_when_all_tasks_blocked_or_cancelled(self):
        """Invariant: a goal must leave active state and transition to blocked when all its tasks are blocked or cancelled."""
        g2 = dict(id="G20261011-000002", text="Test goal 2", status="active", rounds=1)
        store.save_goal(g2)
        t1 = dict(id="0004-t1", goal="G20261011-000002", title="Task 1", history=[], status="blocked",
                  files=["a.py"], role="developer", branch="feat/0004-t1")
        t2 = dict(id="0005-t2", goal="G20261011-000002", title="Task 2", history=[], status="cancelled",
                  files=["b.py"], role="developer", branch="feat/0005-t2")
        store.save_task(t1)
        store.save_task(t2)

        with patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        goals_after = {g["id"]: g for g in store.goals()}
        self.assertEqual(goals_after["G20261011-000002"]["status"], "blocked")
        assert goals_after["G20261011-000002"]["status"] == "blocked"

    def test_advance_cancels_task_referencing_nonexistent_goal(self):
        """Invariant: a task referencing a non-existent goal is cancelled safely and does not linger pending."""
        t_lost = dict(id="0003-lost", goal="G-nonexistent", title="Task Lost", history=[], status="pending",
                      files=["c.py"], role="developer", branch="feat/0003-lost")
        store.save_task(t_lost)

        with patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0003-lost"]["status"], "cancelled")
        assert tasks_after["0003-lost"]["status"] == "cancelled"

    def test_feed_idle_workers_does_not_duplicate_backlog_goals(self):
        """Invariant: feed_idle_workers claims backlog items sequentially without double dispatching."""
        backlog_path = os.path.join(ctx.FD, "backlog.json")
        store.jsave(backlog_path, ["Goal A", "Goal B"])

        with patch.object(loop, "workers", return_value=["worker-1"]), \
             patch.object(loop, "worker_busy", return_value=False):
            loop.feed_idle_workers()

        remaining_backlog = store.jload(backlog_path, [])
        created_goals = store.goals()
        self.assertEqual(len(created_goals), 1)
        assert len(created_goals) == 1
        self.assertEqual(created_goals[0]["text"], "Goal A")
        assert created_goals[0]["text"] == "Goal A"
        self.assertEqual(remaining_backlog, ["Goal B"])
        assert remaining_backlog == ["Goal B"]

    def test_dispatch_ready_no_double_dispatch_racing_ticks(self):
        """Scenario 1: call dispatch_ready() twice in a row simulating racing ticks; task not given to 2 workers nor in pending & running."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=0,
                  files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        store.save_task(t1)

        calls = []
        def fake_sh(cmd, timeout=300, env=None):
            w = cmd[1]
            tid = os.path.basename(cmd[2]).replace(".md", "")
            calls.append((tid, w))
            if len(calls) == 1:
                # Simulate second tick racing before first run is persisted
                loop.dispatch_ready()
            run_dir = os.path.join(sbx.RUNS, f"20260101T000000Z-{w}")
            os.makedirs(run_dir, exist_ok=True)
            return 0, f"dispatched to {w} -> {run_dir} (ok)", ""

        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", side_effect=fake_sh) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            loop.dispatch_ready()

        self.assertEqual(len(calls), 1)
        assert len(calls) == 1
        self.assertEqual(calls[0][0], "0001-t1")
        assert calls[0][0] == "0001-t1"
        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0001-t1"]["status"], "running")
        assert tasks_after["0001-t1"]["status"] == "running"
        pending_ids = [t["id"] for t in store.tasks() if t["status"] == "pending"]
        running_ids = [t["id"] for t in store.tasks() if t["status"] == "running"]
        self.assertNotIn("0001-t1", pending_ids)
        assert "0001-t1" not in pending_ids
        self.assertIn("0001-t1", running_ids)
        assert "0001-t1" in running_ids

    def test_dispatch_ready_file_overlap_bypass_racing_calls(self):
        """Scenario 2: two file-overlapping ready tasks must not be dispatched together even across racing calls."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=0,
                  files=["common.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        t2 = dict(id="0002-t2", key="k2", title="Task 2", goal="G1", status="pending", attempts=0,
                  files=["common.py"], role="developer", branch="feat/0002-t2", spec="spec 2", depends_on=[])
        store.save_task(t1)
        store.save_task(t2)

        calls = []
        def fake_sh(cmd, timeout=300, env=None):
            w = cmd[1]
            tid = os.path.basename(cmd[2]).replace(".md", "")
            calls.append((tid, w))
            if len(calls) == 1:
                # Simulate second tick racing before first run is persisted
                loop.dispatch_ready()
            run_dir = os.path.join(sbx.RUNS, f"20260101T000000Z-{w}")
            os.makedirs(run_dir, exist_ok=True)
            return 0, f"dispatched to {w} -> {run_dir} (ok)", ""

        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", side_effect=fake_sh) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            loop.dispatch_ready()

        self.assertEqual(len(calls), 1)
        assert len(calls) == 1
        self.assertEqual(calls[0][0], "0001-t1")
        assert calls[0][0] == "0001-t1"
        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0001-t1"]["status"], "running")
        assert tasks_after["0001-t1"]["status"] == "running"
        self.assertEqual(tasks_after["0002-t2"]["status"], "pending")
        assert tasks_after["0002-t2"]["status"] == "pending"

    def test_dispatch_ready_paused_goal_never_dispatched(self):
        """Scenario 3: a task whose goal has state 'paused' must never be returned or dispatched by dispatch_ready()."""
        store.save_goal(dict(id="G1", text="goal 1", state="paused"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=0,
                  files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        store.save_task(t1)

        with patch.object(loop, "workers", return_value=["worker-1"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", return_value=(0, "dispatched -> run (ok)", "")) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            dispatched = loop.dispatch_ready()

        self.assertEqual(mock_sh.call_count, 0)
        assert mock_sh.call_count == 0
        self.assertEqual(dispatched or [], [])
        assert dispatched == [] or dispatched is None
        tasks_after = {t["id"]: t for t in store.tasks()}
        self.assertEqual(tasks_after["0001-t1"]["status"], "pending")
        assert tasks_after["0001-t1"]["status"] == "pending"

    def test_orphaned_run_detection_and_safeguards(self):
        """Scenario 4: detect dead runs without supervisor as orphaned; do not flag recent runs or runs of finished tasks."""
        # Case A: RUN_DIR_RE matching run, task running in meta, no live supervisor -> orphaned
        run_dead = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_dead, exist_ok=True)
        t_active = dict(id="0001-t1", goal="G1", status="running", files=["a.py"], role="developer", branch="feat/0001-t1")
        store.save_task(t_active)
        json.dump(dict(task="0001-t1", status="running", worker="worker-1", dispatched="2026-10-10T00:00:00Z"),
                  open(os.path.join(run_dead, "meta.json"), "w"))
        open(os.path.join(run_dead, "pid"), "w").write("999999")
        with patch.object(sbx, "alive", return_value=False), \
             patch.object(sbx, "is_running", return_value=False):
            is_dead_orphan = loop.orphaned(run_dead)
            self.assertTrue(is_dead_orphan)
            assert is_dead_orphan

        # Case B: Genuinely in progress (started seconds ago, matching live markers) -> NOT orphaned
        run_live = os.path.join(sbx.RUNS, "20261011T120001Z-worker-2")
        os.makedirs(run_live, exist_ok=True)
        json.dump(dict(task="0001-t1", status="running", worker="worker-2", dispatched=sbx.now()),
                  open(os.path.join(run_live, "meta.json"), "w"))
        open(os.path.join(run_live, "started"), "w").write(sbx.now() + "\n")
        open(os.path.join(run_live, "pid"), "w").write("12345")
        with patch.object(sbx, "alive", return_value=True), \
             patch.object(sbx, "is_running", return_value=True):
            is_live_orphan = loop.orphaned(run_live)
            self.assertFalse(is_live_orphan)
            assert not is_live_orphan

        # Case C: Run of a different, already-finished task -> must NOT be re-flagged orphaned
        run_old_finished = os.path.join(sbx.RUNS, "20261011T120002Z-worker-3")
        os.makedirs(run_old_finished, exist_ok=True)
        t_done = dict(id="0002-t2", goal="G1", status="done", files=["b.py"], role="developer", branch="feat/0002-t2")
        store.save_task(t_done)
        json.dump(dict(task="0002-t2", status="running", worker="worker-3", dispatched="2026-10-10T00:00:00Z"),
                  open(os.path.join(run_old_finished, "meta.json"), "w"))
        with patch.object(sbx, "is_running", return_value=False):
            is_old_orphan = loop.orphaned(run_old_finished)
            self.assertFalse(is_old_orphan)
            assert not is_old_orphan

        # Case D: Directory name not matching RUN_DIR_RE -> NOT orphaned
        run_invalid = os.path.join(sbx.RUNS, "invalid-dir-format")
        os.makedirs(run_invalid, exist_ok=True)
        with patch.object(sbx, "is_running", return_value=False):
            is_invalid_orphan = loop.orphaned(run_invalid)
            self.assertFalse(is_invalid_orphan)
            assert not is_invalid_orphan

        assert is_dead_orphan and not is_live_orphan and not is_old_orphan and not is_invalid_orphan

    def test_recover_orphan_restores_dispatchability_and_worker_usability(self):
        """Scenario 5: recovered task is dispatchable and worker is immediately usable in pool.py."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 spec="spec 1", depends_on=[])
        store.save_task(t)
        pool.LAST_DISPATCHED["worker-1"] = 1790000000.0

        loop.recover_orphan(t)

        recovered = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(recovered["status"], "pending")
        assert recovered["status"] == "pending"
        self.assertIsNone(recovered.get("worker"))
        assert recovered.get("worker") is None
        self.assertIsNone(recovered.get("run"))
        assert recovered.get("run") is None

        # Worker is usable immediately according to pool.py
        self.assertFalse(pool.worker_busy("worker-1"))
        assert not pool.worker_busy("worker-1")
        self.assertNotIn("worker-1", pool.LAST_DISPATCHED)
        assert "worker-1" not in pool.LAST_DISPATCHED

        # Assert task can be dispatched immediately to worker-1
        with patch.object(loop, "workers", return_value=["worker-1"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", return_value=(0, f"dispatched to worker-1 -> {run_orphan} (ok)", "")), \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            dispatched = loop.dispatch_ready()

        self.assertEqual(len(dispatched or []), 1)
        assert len(dispatched or []) == 1
        dispatched_task = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(dispatched_task["status"], "running")
        assert dispatched_task["status"] == "running"
        self.assertEqual(dispatched_task["worker"], "worker-1")
        assert dispatched_task["worker"] == "worker-1"

    def test_feed_idle_workers_accounts_for_planning_and_backlog_exhaustion(self):
        """Scenario 6: feed_idle_workers accounts for planning tasks and leaves idle workers alone when backlog exhausted."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending",
                  files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
        t2 = dict(id="0002-t2", key="k2", title="Task 2", goal="G1", status="planning",
                  files=["b.py"], role="developer", branch="feat/0002-t2", spec="spec 2", depends_on=[])
        t3 = dict(id="0003-t3", key="k3", title="Task 3", goal="G1", status="planning",
                  files=["c.py"], role="developer", branch="feat/0003-t3", spec="spec 3", depends_on=[])
        store.save_task(t1)
        store.save_task(t2)
        store.save_task(t3)

        backlog_path = os.path.join(ctx.FD, "backlog.json")
        store.jsave(backlog_path, ["Goal Backlog 1", "Goal Backlog 2"])

        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False):
            loop.feed_idle_workers()

        # 1 ready + 2 planning = 3 coming >= 2 idle slots: must not pull extra backlog goals
        remaining_backlog = store.jload(backlog_path, [])
        self.assertEqual(remaining_backlog, ["Goal Backlog 1", "Goal Backlog 2"])
        assert remaining_backlog == ["Goal Backlog 1", "Goal Backlog 2"]
        self.assertEqual(len(store.goals()), 1)
        assert len(store.goals()) == 1

        # Workers are not marked busy without an actual run starting
        self.assertFalse(pool.worker_busy("worker-1"))
        assert not pool.worker_busy("worker-1")
        self.assertFalse(pool.worker_busy("worker-2"))
        assert not pool.worker_busy("worker-2")
        self.assertNotIn("worker-1", pool.RESERVED)
        assert "worker-1" not in pool.RESERVED
        self.assertNotIn("worker-2", pool.RESERVED)
        assert "worker-2" not in pool.RESERVED

        # Exhaust backlog and assert idle workers are left alone (no retry loop or busy marks)
        store.jsave(backlog_path, [])
        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
             patch.object(loop, "worker_busy", return_value=False):
            loop.feed_idle_workers()

        self.assertEqual(len(store.goals()), 1)
        assert len(store.goals()) == 1
        self.assertFalse(pool.worker_busy("worker-1"))
        assert not pool.worker_busy("worker-1")
        self.assertFalse(pool.worker_busy("worker-2"))
        assert not pool.worker_busy("worker-2")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman loop tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
