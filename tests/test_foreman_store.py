#!/usr/bin/env python3
"""Foreman state persistence and locking tests: concurrency, crash-safety, reentrancy, and injection hardening.
Public interface: run directly via python3 tests/test_foreman_store.py or unittest discovery.
Invariants:
- state_tx: concurrent transactions serialize read-modify-write cycles without lost updates.
- jsave/jload: atomic writes via temp file and replace; corrupted files raise rather than masquerading as empty.
- save_task/save_goal: path traversal is blocked; concurrently deleted files are not resurrected with stale state.
- locked/state_tx reentrancy: nested calls within the same thread do not deadlock and preserve state.
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

# Set test environment before any sbx or foreman imports
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-store-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = T
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    import sbx
    from foreman import ctx, store
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)


class TestForemanStore(unittest.TestCase):
    """Integration and security test suite for bin/lib/foreman/store.py."""

    def setUp(self):
        """Prepare a throwaway foreman directory."""
        self.tmp = tempfile.mkdtemp(prefix="af-store-test-")
        self.old_fd = ctx.FD
        ctx.FD = os.path.join(self.tmp, "foreman")
        os.makedirs(os.path.join(ctx.FD, "tasks"), exist_ok=True)
        os.makedirs(os.path.join(ctx.FD, "goals"), exist_ok=True)

    def tearDown(self):
        """Clean up throwaway foreman directory and restore context."""
        ctx.FD = self.old_fd
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_deterministic_lost_update(self, n_rounds=25):
        """Run two threads executing n_rounds each (total 2*n_rounds) with deterministic interleaving."""
        store.gsave({"x": 0})
        rounds = [(threading.Event(), threading.Event()) for _ in range(n_rounds)]
        a_round = 0

        orig_gstate = store.gstate
        def hook_gstate():
            res = orig_gstate()
            if threading.current_thread().name == "T1":
                b_ready, _ = rounds[a_round]
                assert b_ready.wait(timeout=3.0), f"Round {a_round}: T2 was not ready"
            return res

        store.gstate = hook_gstate

        def w1():
            nonlocal a_round
            for i in range(n_rounds):
                a_round = i
                with store.state_tx() as st:
                    st["x"] = st.get("x", 0) + 1
                _, b_finished = rounds[i]
                assert b_finished.wait(timeout=3.0), f"Round {i}: T2 did not finish"

        def w2():
            for i in range(n_rounds):
                b_ready, b_finished = rounds[i]
                b_ready.set()
                with store.state_tx() as st:
                    st["x"] = st.get("x", 0) + 1
                b_finished.set()

        try:
            t1 = threading.Thread(target=w1, name="T1")
            t2 = threading.Thread(target=w2, name="T2")
            t1.start()
            t2.start()
            t1.join(timeout=10.0)
            t2.join(timeout=10.0)
            self.assertFalse(t1.is_alive(), "T1 timed out or deadlocked")
            self.assertFalse(t2.is_alive(), "T2 timed out or deadlocked")
        finally:
            store.gstate = orig_gstate

        final_st = store.gstate()
        return final_st.get("x")

    def test_scenario_1_lost_update_deterministic_interleaving(self):
        """Scenario 1: Two threads each do 25 increments (50 total) under state_tx() with forced interleaving."""
        final_val = self._run_deterministic_lost_update(25)
        self.assertEqual(final_val, 50, f"Expected final x=50, got {final_val}")
        assert final_val == 50

    def test_scenario_1_passes_20_consecutive_runs(self):
        """Scenario 1 criterion: Verify lost-update test passes reliably 20 runs in a row."""
        all_passed = True
        for run_i in range(20):
            val = self._run_deterministic_lost_update(5)  # 10 increments per run, 20 runs
            if val != 10:
                all_passed = False
                break
        self.assertTrue(all_passed, f"Failed at run {run_i}")
        assert all_passed is True

    def test_scenario_2_jsave_crash_safety_and_corruption(self):
        """Scenario 2: Partway failure during jsave leaves previous content intact; corrupted JSON raises."""
        test_path = os.path.join(ctx.FD, "test_file.json")
        initial_data = {"key": "original_valid_data", "counter": 1}
        store.jsave(test_path, initial_data)
        self.assertEqual(store.jload(test_path), initial_data)

        # Monkeypatch json.dump to raise partway through writing
        real_dump = json.dump
        def broken_dump(obj, fp, *args, **kwargs):
            fp.write('{"partial": "corrupt')
            fp.flush()
            raise IOError("Simulated power-cut / kill partway through write")

        with unittest.mock.patch("json.dump", side_effect=broken_dump):
            with self.assertRaises(IOError):
                store.jsave(test_path, {"key": "stale_in_progress", "counter": 2})

        # jload on test_path must return previous valid content
        recovered = store.jload(test_path)
        self.assertEqual(recovered, initial_data, "jsave crash corrupted the destination file")

        # Now test a corrupted / half-written file directly
        corrupt_path = os.path.join(ctx.FD, "corrupt_data.json")
        with open(corrupt_path, "w", encoding="utf-8") as f:
            f.write('{"truncated": "no closing brace"')

        # Must raise catchable JSONDecodeError, NEVER silently return empty default dict
        with self.assertRaises(json.JSONDecodeError):
            store.jload(corrupt_path, default={})

        assert recovered == initial_data

    def test_scenario_3_save_task_and_goal_concurrent_delete(self):
        """Scenario 3: save_task/save_goal does not resurrect a deleted task/goal with stale data."""
        # 1. Test task concurrent delete
        t = dict(id="0001-sample", title="Sample Task", status="pending", attempts=0)
        saved = store.save_task(t)
        self.assertTrue(saved)
        task_path = os.path.join(ctx.FD, "tasks", t["id"] + ".json")
        self.assertTrue(os.path.exists(task_path))

        # Thread B deletes the task file
        os.remove(task_path)
        self.assertFalse(os.path.exists(task_path))

        # Thread A attempts to save its in-memory task
        res_task = store.save_task(t)
        self.assertFalse(res_task, "save_task should return False when task was deleted")
        self.assertFalse(os.path.exists(task_path), "save_task resurrected a deleted task file!")
        self.assertEqual([x["id"] for x in store.tasks()], [], "Deleted task appeared in tasks()")

        # 2. Test goal concurrent delete
        g = dict(id="G20261011-120000", text="Sample Goal", status="new")
        saved_g = store.save_goal(g)
        self.assertTrue(saved_g)
        goal_path = os.path.join(ctx.FD, "goals", g["id"] + ".json")
        self.assertTrue(os.path.exists(goal_path))

        # Thread B deletes the goal file
        os.remove(goal_path)
        self.assertFalse(os.path.exists(goal_path))

        # Thread A attempts to save its in-memory goal
        res_goal = store.save_goal(g)
        self.assertFalse(res_goal, "save_goal should return False when goal was deleted")
        self.assertFalse(os.path.exists(goal_path), "save_goal resurrected a deleted goal file!")
        self.assertEqual([x["id"] for x in store.goals()], [], "Deleted goal appeared in goals()")

        assert not os.path.exists(task_path)

    def test_scenario_4_locked_and_state_tx_reentrancy(self):
        """Scenario 4: Reentrant state_tx() and locked() in the same thread works without deadlock."""
        def reentrant_work():
            with store.state_tx() as st1:
                st1["outer"] = 1
                with store.state_tx() as st2:
                    st2["inner"] = 2

        # Bound test execution with timeout to fail loudly on deadlock
        th = threading.Thread(target=reentrant_work)
        th.start()
        th.join(timeout=3.0)
        self.assertFalse(th.is_alive(), "Reentrant state_tx() deadlocked!")

        # Verify both outer and inner updates are preserved
        final_st = store.gstate()
        self.assertEqual(final_st.get("outer"), 1)
        self.assertEqual(final_st.get("inner"), 2)

        # Also test bare locked() reentrancy
        def reentrant_locked():
            with store.locked():
                with store.locked():
                    pass

        th_lock = threading.Thread(target=reentrant_locked)
        th_lock.start()
        th_lock.join(timeout=3.0)
        self.assertFalse(th_lock.is_alive(), "Reentrant locked() deadlocked!")

        assert final_st.get("inner") == 2

    def test_security_path_traversal_prevention(self):
        """Security: save_task, save_goal, and new_goal_id reject path traversal payloads."""
        traversal_task = dict(id="../../escaped_task", title="Pwn")
        with self.assertRaises(ValueError):
            store.save_task(traversal_task)

        traversal_goal = dict(id="../../escaped_goal", text="Pwn")
        with self.assertRaises(ValueError):
            store.save_goal(traversal_goal)

        escaped_file = os.path.normpath(os.path.join(ctx.FD, "..", "escaped_task.json"))
        self.assertFalse(os.path.exists(escaped_file), "File was written outside tasks directory!")

        # new_goal_id sanitizes suffix
        gid = store.new_goal_id(suffix="/../../evil_suffix")
        self.assertNotIn("/", gid)
        self.assertNotIn("..", gid)
        assert not os.path.exists(escaped_file)

    def test_security_injection_payloads(self):
        """Security: Injection payloads (SQLi, cmd injection, null bytes, unicode) are stored cleanly."""
        payloads = [
            "'; DROP TABLE users;--",
            "$(id)",
            "`reboot`",
            "hello\x00world",
            "🚀🔥 unicode payload with newlines\nsecond line\r\nthird",
        ]
        for idx, payload in enumerate(payloads):
            tid = f"00{idx + 10:02d}-inject"
            t = dict(id=tid, title=payload, spec=payload)
            store.save_task(t)

            # Direct storage verification (read raw file from filesystem, bypass jload)
            raw_path = os.path.join(ctx.FD, "tasks", f"{tid}.json")
            self.assertTrue(os.path.exists(raw_path))
            with open(raw_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            self.assertEqual(raw_data["spec"], payload)

            # Test event log injection
            store.event(payload, extra=payload)

        # Check raw events.jsonl
        events_path = os.path.join(ctx.FD, "events.jsonl")
        self.assertTrue(os.path.exists(events_path))
        with open(events_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertTrue(len(lines) >= len(payloads))

        assert raw_data["spec"] == payloads[-1]

    def test_boundary_fuzzing_and_error_handling(self):
        """Boundary: jload missing files, malformed task files, next_task_number, notify."""
        # Non-existent file returns default
        missing = os.path.join(ctx.FD, "does_not_exist.json")
        self.assertIsNone(store.jload(missing))
        self.assertEqual(store.jload(missing, default={"fallback": True}), {"fallback": True})

        # tasks() skips non-dict files and unparseable files without crashing
        bad_task_path = os.path.join(ctx.FD, "tasks", "0099-bad.json")
        with open(bad_task_path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")  # valid JSON but list, not dict
        self.assertNotIn("0099-bad", [t["id"] for t in store.tasks()])

        # next_task_number computation
        next_num = store.next_task_number()
        self.assertIsInstance(next_num, int)

        # notify() accepts various kinds and types without raising
        store.notify("goal_done", {"text": "done", "summary": "sum"})
        store.notify("task_blocked", {"id": "0001", "blocked_why": "why"})
        store.notify("custom_kind", None, "custom text")

        assert next_num >= 1


if __name__ == "__main__":
    unittest.main()
