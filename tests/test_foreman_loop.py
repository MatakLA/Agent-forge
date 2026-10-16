#!/usr/bin/env python3
"""Foreman loop state-machine tests for worker dispatch, orphan recovery, and goal lifecycle.
Public interface: run directly via python3 tests/test_foreman_loop.py or unittest discovery.
Invariants:
- dispatch_ready: prevents double dispatch across idle workers for overlapping files or duplicate keys.
- orphaned: detects dead supervisors without false negatives (missing pid) or false positives (alive runs).
- advance: active goals leave active state once all tasks terminate; tasks for missing goals are safely cancelled.
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
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
from foreman import ctx, loop, merge, pool, store, util


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
        loop.STALL.update(since=0.0, told=False)
        loop.STARTUP_GRACE_DONE = False
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

    def test_dispatch_ready_requeued_task_with_stale_worker_is_dispatched(self):
        """A task sent back for rework / after a quota stop keeps its old 'worker' field; it must still be dispatched."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t1 = dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=1, worker="worker-1",
                  run="/old/run", files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[],
                  feedback="rework please")
        store.save_task(t1)

        with patch.object(loop, "workers", return_value=["worker-1"]), \
             patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", return_value=(0, "dispatched to worker-1 -> run (ok)", "")) as mock_sh, \
             patch.object(sbx, "quota_verdict", return_value=(True, "")):
            dispatched = loop.dispatch_ready()

        self.assertEqual(mock_sh.call_count, 1)
        self.assertEqual(len(dispatched or []), 1)
        after = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(after["status"], "running")
        self.assertEqual(after["worker"], "worker-1")

    def test_dispatch_refused_for_unreadable_quota_rests_only_that_worker(self):
        """bin/dispatch could not read /usage (503): that worker rests 3 min, the other free worker still gets a task."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        for i in (1, 2):
            store.save_task(dict(id=f"000{i}-t{i}", key=f"k{i}", title=f"Task {i}", goal="G1", status="pending", attempts=0,
                                 files=[f"f{i}.py"], role="developer", branch=f"feat/000{i}-t{i}", spec="s", depends_on=[]))
        refusal = (3, "", "REFUSED: cannot determine quota (/usage unreadable or no matching rows) - refusing (fail closed)")
        ok = (0, "dispatched to worker-2 -> run (ok)", "")
        with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "sh", side_effect=[refusal, ok]) as mock_sh, patch.object(sbx, "quota_verdict", return_value=(True, "")):
            dispatched = loop.dispatch_ready()
        self.assertEqual(mock_sh.call_count, 2)
        self.assertEqual(len(dispatched or []), 1)
        rested = [w for w in ("worker-1", "worker-2") if w in loop.COOLDOWN]
        self.assertEqual(len(rested), 1)
        self.assertLessEqual(loop.COOLDOWN[rested[0]][0] - time.time(), 185)

    def test_check_stall_tells_once_when_ready_task_and_free_worker_do_not_meet(self):
        """Ready task + free worker + nothing dispatched for STALL_SEC -> one event and one notification; a dispatch or a quota rest clears it."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        store.save_task(dict(id="0001-t1", key="k1", title="Task 1", goal="G1", status="pending", attempts=0, files=["a.py"],
                             role="developer", branch="feat/0001-t1", spec="s", depends_on=[]))
        with patch.object(loop, "workers", return_value=["worker-1"]), patch.object(loop, "worker_busy", return_value=False), \
             patch.object(loop, "notify") as mock_notify:
            loop.check_stall([])
            self.assertEqual(mock_notify.call_count, 0)                       # just started to wait
            loop.STALL["since"] = time.time() - loop.STALL_SEC - 5
            loop.check_stall([])
            loop.check_stall([])
            self.assertEqual(mock_notify.call_count, 1)                       # told once, not every tick
            self.assertEqual(mock_notify.call_args[0][0], "stalled")
            self.assertIn("0001-t1", mock_notify.call_args[1]["text"])
            self.assertIn("STALLED", open(os.path.join(ctx.FD, "events.jsonl")).read())
            loop.check_stall(["0001-t1"])                                     # something was dispatched: all clear
            self.assertEqual(loop.STALL, {"since": 0.0, "told": False})
            loop.STALL["since"] = time.time() - loop.STALL_SEC - 5
            loop.COOLDOWN["worker-1"] = (time.time() + 600, "quota low")      # the only worker rests: waiting for quota is not a stall
            loop.check_stall([])
            self.assertEqual(loop.STALL["since"], 0.0)
            self.assertEqual(mock_notify.call_count, 1)

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

    def test_recover_orphan_increments_streak_and_captures_log_tail_after_startup_grace(self):
        """Invariant: recover_orphan increments orphan_streak and captures log tail for no-progress orphans after startup grace."""
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        log_content = "\n".join([f"line {i}" for i in range(50)])
        with open(os.path.join(run_orphan, "supervisor.log"), "w") as f:
            f.write(log_content)
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1")
        store.save_task(t)

        loop.STARTUP_GRACE_DONE = True
        loop.recover_orphan(t)

        recovered = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(recovered["orphan_streak"], 1)
        self.assertIn("line 49", recovered["orphan_log_tail"])
        self.assertNotIn("line 5\n", recovered["orphan_log_tail"])
        assert recovered["status"] == "pending"
        assert recovered["orphan_streak"] == 1
        assert "line 49" in recovered["orphan_log_tail"]

    def test_recover_orphan_startup_grace_exemption_does_not_increment_counter(self):
        """Invariant: orphans discovered in first pass after startup do not increment or reset orphan_streak."""
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 orphan_streak=2)
        store.save_task(t)

        loop.STARTUP_GRACE_DONE = False
        loop.recover_orphan(t)

        recovered = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(recovered["orphan_streak"], 2)
        assert recovered["status"] == "pending"
        assert recovered["orphan_streak"] == 2

    def test_advance_startup_grace_processes_all_first_pass_orphans_then_sets_done(self):
        """Invariant: advance exempts all orphans in the first scan pass, then marks STARTUP_GRACE_DONE=True."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        run_1 = os.path.join(sbx.RUNS, "20261011T120001Z-worker-1")
        run_2 = os.path.join(sbx.RUNS, "20261011T120002Z-worker-2")
        os.makedirs(run_1, exist_ok=True)
        os.makedirs(run_2, exist_ok=True)
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_1, "meta.json"), "w"))
        json.dump(dict(task="0002-t2", worker="worker-2"), open(os.path.join(run_2, "meta.json"), "w"))

        t1 = dict(id="0001-t1", goal="G1", status="running", worker="worker-1", run=run_1, files=["a.py"], role="dev", orphan_streak=1)
        t2 = dict(id="0002-t2", goal="G1", status="running", worker="worker-2", run=run_2, files=["b.py"], role="dev", orphan_streak=2)
        store.save_task(t1)
        store.save_task(t2)

        loop.STARTUP_GRACE_DONE = False
        with patch.object(sbx, "is_running", return_value=False), \
             patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        self.assertTrue(loop.STARTUP_GRACE_DONE)
        t1_after = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        t2_after = store.jload(os.path.join(ctx.FD, "tasks", "0002-t2.json"))
        self.assertEqual(t1_after["orphan_streak"], 1)
        self.assertEqual(t2_after["orphan_streak"], 2)
        self.assertEqual(t1_after["status"], "pending")
        self.assertEqual(t2_after["status"], "pending")
        assert loop.STARTUP_GRACE_DONE is True
        assert t1_after["orphan_streak"] == 1
        assert t2_after["orphan_streak"] == 2

    def test_recover_orphan_progress_resets_orphan_streak(self):
        """Invariant: run that ran longer than ORPHAN_PROGRESS_SEC before being orphaned resets orphan_streak to 0."""
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        started_iso = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(run_orphan, "started"), "w") as f:
            f.write(started_iso + "\n")
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 orphan_streak=4, orphan_log_tail="previous crash log")
        store.save_task(t)

        loop.STARTUP_GRACE_DONE = True
        loop.recover_orphan(t)

        recovered = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(recovered["orphan_streak"], 0)
        self.assertEqual(recovered["orphan_log_tail"], "")
        assert recovered["status"] == "pending"
        assert recovered["orphan_streak"] == 0
        assert recovered["orphan_log_tail"] == ""

    def test_advance_real_supervisor_result_resets_orphan_streak(self):
        """Invariant: real supervisor result in advance resets orphan_streak and clears orphan_log_tail."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        run_done = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_done, exist_ok=True)
        open(os.path.join(run_done, "finished"), "w").write(sbx.now() + "\n")
        open(os.path.join(run_done, "exitcode"), "w").write("0\n")
        json.dump(dict(task="0001-t1", worker="worker-1", reason="ok", tokens=1000),
                  open(os.path.join(run_done, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_done, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 orphan_streak=3, orphan_log_tail="old error log")
        store.save_task(t)

        with patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        task_after = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(task_after["status"], "gate")
        self.assertEqual(task_after["orphan_streak"], 0)
        self.assertEqual(task_after["orphan_log_tail"], "")
        assert task_after["status"] == "gate"
        assert task_after["orphan_streak"] == 0
        assert task_after["orphan_log_tail"] == ""

    def test_orphan_warn_threshold_notifies_once_and_leaves_pending(self):
        """Invariant: reaching ORPHAN_WARN sends task_blocked notification once and leaves task pending."""
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 orphan_streak=2)
        store.save_task(t)

        loop.STARTUP_GRACE_DONE = True
        with patch.object(store, "notify") as mock_notify:
            loop.recover_orphan(t)

        recovered = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(recovered["status"], "pending")
        self.assertEqual(recovered["orphan_streak"], 3)
        mock_notify.assert_called_once_with(
            "task_blocked",
            obj=recovered,
            text=f"task 0001-t1 is close to the orphan limit (3/{ctx.C['ORPHAN_LIMIT']} consecutive crashes with no progress)"
        )
        assert recovered["status"] == "pending"
        assert recovered["orphan_streak"] == 3
        assert mock_notify.call_count == 1

        with patch.object(store, "notify") as mock_notify_2:
            loop.recover_orphan(recovered)
        self.assertEqual(mock_notify_2.call_count, 0)
        assert mock_notify_2.call_count == 0

    def test_orphan_limit_blocks_task_with_captured_tail_and_no_requeue(self):
        """Invariant: reaching ORPHAN_LIMIT blocks the task via merge.block with log tail and does not re-queue."""
        run_orphan = os.path.join(sbx.RUNS, "20261011T120000Z-worker-1")
        os.makedirs(run_orphan, exist_ok=True)
        with open(os.path.join(run_orphan, "supervisor.log"), "w") as f:
            f.write("FATAL: segmentation fault in runner\nexit code 139\n")
        json.dump(dict(task="0001-t1", worker="worker-1"), open(os.path.join(run_orphan, "meta.json"), "w"))

        t = dict(id="0001-t1", goal="G1", status="running", worker="worker-1",
                 run=run_orphan, attempts=0, files=["a.py"], role="developer", branch="feat/0001-t1",
                 orphan_streak=4, history=[])
        store.save_task(t)

        loop.STARTUP_GRACE_DONE = True
        with patch.object(merge, "block", wraps=merge.block) as spy_block:
            loop.recover_orphan(t)

        task_after = store.jload(os.path.join(ctx.FD, "tasks", "0001-t1.json"))
        self.assertEqual(task_after["status"], "blocked")
        self.assertEqual(task_after["orphan_streak"], 5)
        self.assertTrue(task_after["blocked_why"].startswith("orphan limit reached: "))
        self.assertIn("FATAL: segmentation fault in runner", task_after["blocked_why"])
        self.assertIn("FATAL: segmentation fault in runner", task_after["orphan_log_tail"])
        self.assertTrue(spy_block.called)
        assert task_after["status"] == "blocked"
        assert task_after["orphan_streak"] == 5
        assert task_after["blocked_why"].startswith("orphan limit reached: ")
        assert "FATAL: segmentation fault in runner" in task_after["blocked_why"]

    def test_capture_orphan_log_tail_fallback_and_truncation(self):
        """Invariant: supervisor.log preferred, stderr.log fallback if supervisor.log empty, truncated to 40 lines/4000 chars."""
        run_fallback = os.path.join(sbx.RUNS, "20261011T120000Z-fallback")
        os.makedirs(run_fallback, exist_ok=True)
        with open(os.path.join(run_fallback, "supervisor.log"), "w") as f:
            f.write("   \n")
        with open(os.path.join(run_fallback, "stderr.log"), "w") as f:
            f.write("Error from worker stderr\n")
        tail_fallback = loop.capture_orphan_log_tail(run_fallback)
        self.assertEqual(tail_fallback, "Error from worker stderr")
        assert tail_fallback == "Error from worker stderr"

        run_long = os.path.join(sbx.RUNS, "20261011T120001Z-long")
        os.makedirs(run_long, exist_ok=True)
        with open(os.path.join(run_long, "supervisor.log"), "w") as f:
            f.write("X" * 150 * 50)
        tail_long = loop.capture_orphan_log_tail(run_long)
        self.assertTrue(len(tail_long) <= 4000)
        self.assertTrue(len(tail_long.splitlines()) <= 40)
        assert len(tail_long) <= 4000
        assert len(tail_long.splitlines()) <= 40

    def test_orphan_recovery_five_consecutive_cycles_block_with_log_tail(self):
        """Invariant: 5 consecutive no-progress orphan recoveries block the task with reason starting 'orphan limit reached: ' and log tail."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))
        t_id = "0121-block"
        task = dict(id=t_id, goal="G1", status="pending", attempts=0, files=["core.py"],
                    role="developer", branch="feat/0121-block", history=[], orphan_streak=0)
        store.save_task(task)

        loop.STARTUP_GRACE_DONE = True

        for cycle in range(1, 6):
            run_dir = os.path.join(sbx.RUNS, f"20261012T00000{cycle}Z-worker-1")
            os.makedirs(run_dir, exist_ok=True)
            with open(os.path.join(run_dir, "supervisor.log"), "w") as f:
                f.write(f"runner initialization\nworker cycle {cycle}\nfatal segmentation fault in runner\nexit code 139\n")
            with open(os.path.join(run_dir, "meta.json"), "w") as f:
                json.dump(dict(task=t_id, worker="worker-1"), f)

            current_task = store.jload(os.path.join(ctx.FD, "tasks", f"{t_id}.json"))
            current_task.update(status="running", run=run_dir, worker="worker-1")
            store.save_task(current_task)

            loop.recover_orphan(current_task)

            saved = store.jload(os.path.join(ctx.FD, "tasks", f"{t_id}.json"))
            if cycle < 5:
                self.assertEqual(saved["status"], "pending")
                self.assertEqual(saved["orphan_streak"], cycle)
                self.assertIn("fatal segmentation fault", saved["orphan_log_tail"])
                self.assertNotIn("worker", saved)
                self.assertNotIn("run", saved)
            else:
                self.assertEqual(saved["status"], "blocked")
                self.assertEqual(saved["orphan_streak"], 5)
                self.assertTrue(saved["blocked_why"].startswith("orphan limit reached: "))
                self.assertIn("fatal segmentation fault", saved["blocked_why"])
                self.assertIn("fatal segmentation fault", saved["orphan_log_tail"])

        final_task = store.jload(os.path.join(ctx.FD, "tasks", f"{t_id}.json"))
        self.assertEqual(final_task["status"], "blocked")
        self.assertEqual(final_task["orphan_streak"], 5)
        self.assertTrue(final_task["blocked_why"].startswith("orphan limit reached: "))
        self.assertIn("fatal segmentation fault", final_task["blocked_why"])
        self.assertIn("fatal segmentation fault", final_task["orphan_log_tail"])
        assert final_task["status"] == "blocked"
        assert final_task["orphan_streak"] == 5
        assert final_task["blocked_why"].startswith("orphan limit reached: ")
        assert "fatal segmentation fault" in final_task["blocked_why"]
        assert "fatal segmentation fault" in final_task["orphan_log_tail"]

    def test_orphan_recovery_progress_resets_streak_for_both_duration_and_supervisor_result(self):
        """Invariant: progress events (run duration > ORPHAN_PROGRESS_SEC or real supervisor result) reset orphan_streak to 0."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))

        # Progress Case 1: Run lasting longer than ORPHAN_PROGRESS_SEC (90 s) resets streak to 0
        run_long = os.path.join(sbx.RUNS, "20261012T010000Z-worker-1")
        os.makedirs(run_long, exist_ok=True)
        started_iso = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=150)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(run_long, "started"), "w") as f:
            f.write(started_iso + "\n")
        with open(os.path.join(run_long, "meta.json"), "w") as f:
            json.dump(dict(task="0121-progress-dur", worker="worker-1"), f)

        t_dur = dict(id="0121-progress-dur", goal="G1", status="running", worker="worker-1",
                     run=run_long, attempts=0, files=["a.py"], role="developer",
                     orphan_streak=4, orphan_log_tail="stale log from previous crash")
        store.save_task(t_dur)

        loop.STARTUP_GRACE_DONE = True
        loop.recover_orphan(t_dur)

        saved_dur = store.jload(os.path.join(ctx.FD, "tasks", "0121-progress-dur.json"))
        self.assertEqual(saved_dur["status"], "pending")
        self.assertEqual(saved_dur["orphan_streak"], 0)
        self.assertEqual(saved_dur["orphan_log_tail"], "")

        # Non-progress Case: Short run (<= ORPHAN_PROGRESS_SEC, e.g. 20 s) does NOT reset streak
        run_short = os.path.join(sbx.RUNS, "20261012T010001Z-worker-1")
        os.makedirs(run_short, exist_ok=True)
        started_short = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(os.path.join(run_short, "started"), "w") as f:
            f.write(started_short + "\n")
        with open(os.path.join(run_short, "meta.json"), "w") as f:
            json.dump(dict(task="0121-short", worker="worker-1"), f)

        t_short = dict(id="0121-short", goal="G1", status="running", worker="worker-1",
                       run=run_short, attempts=0, files=["b.py"], role="developer",
                       orphan_streak=2, orphan_log_tail="previous log")
        store.save_task(t_short)

        loop.recover_orphan(t_short)
        saved_short = store.jload(os.path.join(ctx.FD, "tasks", "0121-short.json"))
        self.assertEqual(saved_short["status"], "pending")
        self.assertEqual(saved_short["orphan_streak"], 3)

        # Progress Case 2: Real supervisor result in advance() resets streak to 0 and clears log tail
        run_sup = os.path.join(sbx.RUNS, "20261012T010002Z-worker-2")
        os.makedirs(run_sup, exist_ok=True)
        with open(os.path.join(run_sup, "finished"), "w") as f:
            f.write(sbx.now() + "\n")
        with open(os.path.join(run_sup, "exitcode"), "w") as f:
            f.write("0\n")
        with open(os.path.join(run_sup, "meta.json"), "w") as f:
            json.dump(dict(task="0121-progress-sup", worker="worker-2", reason="ok", tokens=800), f)

        t_sup = dict(id="0121-progress-sup", goal="G1", status="running", worker="worker-2",
                     run=run_sup, attempts=0, files=["c.py"], role="developer",
                     orphan_streak=4, orphan_log_tail="previous crash log",
                     not_before=time.time() + 3600)
        store.save_task(t_sup)

        with patch.object(loop, "llm_available", return_value=True), \
             patch.object(sbx, "is_running", return_value=False):
            loop.advance()

        saved_sup = store.jload(os.path.join(ctx.FD, "tasks", "0121-progress-sup.json"))
        self.assertEqual(saved_sup["status"], "gate")
        self.assertEqual(saved_sup["orphan_streak"], 0)
        self.assertEqual(saved_sup["orphan_log_tail"], "")

        assert saved_dur["orphan_streak"] == 0
        assert saved_dur["orphan_log_tail"] == ""
        assert saved_dur["status"] == "pending"
        assert saved_short["orphan_streak"] == 3
        assert saved_sup["orphan_streak"] == 0
        assert saved_sup["orphan_log_tail"] == ""
        assert saved_sup["status"] == "gate"

    def test_orphan_recovery_startup_grace_exemption_and_transition(self):
        """Invariant: STARTUP_GRACE_DONE=False exempts all initial orphans from streak increment, transitioning to True."""
        store.save_goal(dict(id="G1", text="goal 1", status="active"))

        # Task 1: Direct recover_orphan during startup grace
        run_1 = os.path.join(sbx.RUNS, "20261012T020001Z-worker-1")
        os.makedirs(run_1, exist_ok=True)
        with open(os.path.join(run_1, "meta.json"), "w") as f:
            json.dump(dict(task="0121-grace-1", worker="worker-1"), f)
        t1 = dict(id="0121-grace-1", goal="G1", status="running", worker="worker-1",
                  run=run_1, attempts=0, files=["x.py"], role="developer", orphan_streak=2)
        store.save_task(t1)

        loop.STARTUP_GRACE_DONE = False
        loop.recover_orphan(t1)
        t1_after = store.jload(os.path.join(ctx.FD, "tasks", "0121-grace-1.json"))
        self.assertEqual(t1_after["status"], "pending")
        self.assertEqual(t1_after["orphan_streak"], 2)

        # Task 2: advance() first pass with STARTUP_GRACE_DONE=False exempts task with streak=4
        run_2 = os.path.join(sbx.RUNS, "20261012T020002Z-worker-2")
        os.makedirs(run_2, exist_ok=True)
        with open(os.path.join(run_2, "supervisor.log"), "w") as f:
            f.write("crash log\n")
        with open(os.path.join(run_2, "meta.json"), "w") as f:
            json.dump(dict(task="0121-grace-2", worker="worker-2"), f)
        t2 = dict(id="0121-grace-2", goal="G1", status="running", worker="worker-2",
                  run=run_2, attempts=0, files=["y.py"], role="developer", orphan_streak=4, history=[])
        store.save_task(t2)

        loop.STARTUP_GRACE_DONE = False
        with patch.object(sbx, "is_running", return_value=False), \
             patch.object(loop, "llm_available", return_value=True):
            loop.advance()

        self.assertTrue(loop.STARTUP_GRACE_DONE)
        t2_first_pass = store.jload(os.path.join(ctx.FD, "tasks", "0121-grace-2.json"))
        self.assertEqual(t2_first_pass["status"], "pending")
        self.assertEqual(t2_first_pass["orphan_streak"], 4)

        # Subsequent orphan recovery (now that STARTUP_GRACE_DONE=True) increments streak 4 -> 5 and blocks task
        run_2_subsequent = os.path.join(sbx.RUNS, "20261012T020003Z-worker-2")
        os.makedirs(run_2_subsequent, exist_ok=True)
        with open(os.path.join(run_2_subsequent, "supervisor.log"), "w") as f:
            f.write("second crash log: unrecoverable\n")
        with open(os.path.join(run_2_subsequent, "meta.json"), "w") as f:
            json.dump(dict(task="0121-grace-2", worker="worker-2"), f)

        t2_second = dict(t2_first_pass)
        t2_second.update(status="running", run=run_2_subsequent, worker="worker-2")
        store.save_task(t2_second)

        loop.recover_orphan(t2_second)
        t2_post_grace = store.jload(os.path.join(ctx.FD, "tasks", "0121-grace-2.json"))
        self.assertEqual(t2_post_grace["status"], "blocked")
        self.assertEqual(t2_post_grace["orphan_streak"], 5)
        self.assertTrue(t2_post_grace["blocked_why"].startswith("orphan limit reached: "))
        self.assertIn("second crash log", t2_post_grace["blocked_why"])

        assert t1_after["orphan_streak"] == 2
        assert t1_after["status"] == "pending"
        assert t2_first_pass["orphan_streak"] == 4
        assert t2_first_pass["status"] == "pending"
        assert loop.STARTUP_GRACE_DONE is True
        assert t2_post_grace["orphan_streak"] == 5
        assert t2_post_grace["status"] == "blocked"
        assert t2_post_grace["blocked_why"].startswith("orphan limit reached: ")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman loop tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
