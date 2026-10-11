#!/usr/bin/env python3
"""Tests for bin/lib/foreman/loop.py state-machine behavior:
Stuck-task, double-dispatch, orphan recovery, and lost-goal defects.
Public interface: standalone script run via `python3 tests/test_foreman_loop.py`.
Important invariants:
- Tasks with overlapping files or duplicate keys are never double-dispatched in a pass.
- Orphaned runs are accurately detected without false negatives or false positives.
- Completed, cancelled, or blocked goals leave active state rather than lingering indefinitely.
"""
import builtins, os, shutil, sys, tempfile, unittest
from unittest.mock import patch, MagicMock

# Set test environment before any sbx or foreman imports
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-loop-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import sbx
from foreman import ctx, loop, store, util

bad = 0


def check(name, ok):
    global bad
    bad += not ok
    print(("ok   " if ok else "FAIL ") + name)


def reset_state():
    """Clear tasks, goals, and state in the throwaway runs directory."""
    shutil.rmtree(T, ignore_errors=True)
    os.makedirs(os.path.join(ctx.FD, "tasks"), exist_ok=True)
    os.makedirs(os.path.join(ctx.FD, "goals"), exist_ok=True)
    loop.INFLIGHT.clear()
    loop.SINCE.clear()
    loop.PHASE.clear()
    loop.COOLDOWN.clear()
    loop.RESERVED.clear()


# -----------------------------------------------------------------------------
# Test 1: Double dispatch - overlapping files within one pass
# -----------------------------------------------------------------------------
reset_state()
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
     patch.object(loop, "sh", side_effect=fake_sh), \
     patch.object(sbx, "quota_verdict", return_value=(True, "")):
    loop.dispatch_ready()

tasks_after = {t["id"]: t for t in store.tasks()}
# Invariant: dispatch_ready must not dispatch two tasks touching the same files in the same pass
check("dispatch_ready: no double dispatch for tasks with overlapping files",
      tasks_after["0001-t1"]["status"] == "running" and tasks_after["0002-t2"]["status"] == "pending")


# -----------------------------------------------------------------------------
# Test 2: Double dispatch - duplicate task key within one pass or already running
# -----------------------------------------------------------------------------
reset_state()
store.save_goal(dict(id="G1", text="goal 1", status="active"))
t1 = dict(id="0001-t1", key="duplicate-key", title="Task 1", goal="G1", status="pending", attempts=0,
          files=["a.py"], role="developer", branch="feat/0001-t1", spec="spec 1", depends_on=[])
t2 = dict(id="0002-t2", key="duplicate-key", title="Task 2", goal="G1", status="pending", attempts=0,
          files=["b.py"], role="developer", branch="feat/0002-t2", spec="spec 2", depends_on=[])
store.save_task(t1)
store.save_task(t2)

with patch.object(loop, "workers", return_value=["worker-1", "worker-2"]), \
     patch.object(loop, "worker_busy", return_value=False), \
     patch.object(loop, "sh", side_effect=fake_sh), \
     patch.object(sbx, "quota_verdict", return_value=(True, "")):
    loop.dispatch_ready()

tasks_after = {t["id"]: t for t in store.tasks()}
# Invariant: dispatch_ready must not dispatch duplicate task keys across workers
check("dispatch_ready: no double dispatch for tasks with the same key",
      tasks_after["0001-t1"]["status"] == "running" and tasks_after["0002-t2"]["status"] == "pending")


# -----------------------------------------------------------------------------
# Test 3: Orphan detection - missing/empty/corrupted pid file (false negative)
# -----------------------------------------------------------------------------
reset_state()
run_orphan = os.path.join(sbx.RUNS, "20260101T000000Z-worker-3")
os.makedirs(run_orphan, exist_ok=True)
# No pid file created, no finished file created
# Invariant: a run without a finished file and without a running supervisor is an orphan
check("orphaned: missing pid file is detected as orphan (not stuck forever)",
      loop.orphaned(run_orphan) is True)


# -----------------------------------------------------------------------------
# Test 4: Orphan detection - supervisor process died / recycled pid (false negative)
# -----------------------------------------------------------------------------
reset_state()
run_dead = os.path.join(sbx.RUNS, "20260101T000000Z-worker-4")
os.makedirs(run_dead, exist_ok=True)
open(os.path.join(run_dead, "pid"), "w").write("999999998")

orig_open = builtins.open
def proc_fail_open(path, *args, **kwargs):
    if "/proc/" in str(path):
        raise OSError("Permission denied")
    return orig_open(path, *args, **kwargs)

# Mock alive=True (PID recycled to an unrelated process or inaccessible) but reading /proc cmdline raises OSError
with patch.object(sbx, "alive", return_value=True), \
     patch("builtins.open", side_effect=proc_fail_open):
    # Invariant: an inaccessible or non-supervisor process is detected as an orphan
    check("orphaned: dead supervisor with recycled pid/OSError is detected as orphan",
          loop.orphaned(run_dead) is True)


# -----------------------------------------------------------------------------
# Test 5: Lost goals - goal with all cancelled tasks leaves active state
# -----------------------------------------------------------------------------
reset_state()
g1 = dict(id="G20261011-000001", text="Test goal 1", status="active", rounds=1)
store.save_goal(g1)
t1 = dict(id="0001-t1", goal="G20261011-000001", title="Task 1", history=[], status="cancelled", files=["a.py"], role="developer", branch="feat/0001-t1")
t2 = dict(id="0002-t2", goal="G20261011-000001", title="Task 2", history=[], status="cancelled", files=["b.py"], role="developer", branch="feat/0002-t2")
store.save_task(t1)
store.save_task(t2)

with patch.object(loop, "llm_available", return_value=True):
    loop.advance()

goals_after = {g["id"]: g for g in store.goals()}
# Invariant: a goal must leave active state when all its tasks are cancelled
check("advance: goal with all cancelled tasks leaves active state and becomes cancelled",
      goals_after["G20261011-000001"]["status"] == "cancelled")


# -----------------------------------------------------------------------------
# Test 6: Non-existent goal - task referencing missing goal id is cancelled safely
# -----------------------------------------------------------------------------
reset_state()
t_lost = dict(id="0003-lost", goal="G-nonexistent", title="Task Lost", history=[], status="pending", files=["c.py"], role="developer", branch="feat/0003-lost")
store.save_task(t_lost)

with patch.object(loop, "llm_available", return_value=True):
    loop.advance()

tasks_after = {t["id"]: t for t in store.tasks()}
# Invariant: a task referencing a non-existent goal must not linger pending or crash lookups
check("advance: task referencing non-existent goal is cancelled safely",
      tasks_after["0003-lost"]["status"] == "cancelled")


# Cleanup
shutil.rmtree(T, ignore_errors=True)
print("foreman loop tests: " + ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
