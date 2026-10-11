#!/usr/bin/env python3
"""Foreman state persistence and locking tests: concurrency, crash-safety, boundary values, and injection coverage.
Public interface: run directly via python3 tests/test_foreman_store.py or unittest discovery.
Important invariants:
- state_tx: concurrent transactions serialize read-modify-write cycles without lost updates across threads.
- jsave: atomic writes via temp file and os.replace ensure crash-safety; failed writes leave target file intact.
- jload: unparseable or missing files safely return default without unhandled exceptions.
- event/tasks/goals: robust against injection payloads (SQLi, cmd injection, null bytes, unicode).
"""
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

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

    def test_scenario_1_lost_update(self):
        """Scenario 1: Two threads each do 25 increments (50 total) under state_tx() with forced interleaving (20 runs)."""
        final_val = 0
        for run_i in range(20):
            final_val = self._run_deterministic_lost_update(n_rounds=25)
            self.assertEqual(final_val, 50, f"Run {run_i}: expected 50, got {final_val}")
        assert final_val == 50

    def test_scenario_2_jsave_crash_safety(self):
        """Scenario 2: Failure partway through jsave leaves previous content intact via atomic replace."""
        test_path = os.path.join(ctx.FD, "test_file.json")
        initial_data = {"key": "original_valid_data", "counter": 1}
        store.jsave(test_path, initial_data)
        self.assertEqual(store.jload(test_path), initial_data)

        # 1. Monkeypatch json.dump to raise partway through writing
        def broken_dump(obj, fp, *args, **kwargs):
            fp.write('{"partial": "corrupted_content')
            fp.flush()
            raise IOError("Simulated crash/power-cut during file write")

        with mock.patch("json.dump", side_effect=broken_dump):
            with self.assertRaises(IOError):
                store.jsave(test_path, {"key": "new_corrupted_data", "counter": 2})

        # Destination file must remain unchanged
        recovered_dump = store.jload(test_path, default={})
        self.assertEqual(recovered_dump, initial_data, "jsave crash corrupted destination file")

        # 2. Monkeypatch os.replace to raise before replacement completes
        def broken_replace(src, dst):
            raise OSError("Simulated crash during atomic replace")

        with mock.patch("os.replace", side_effect=broken_replace):
            with self.assertRaises(OSError):
                store.jsave(test_path, {"key": "another_uncommitted_update", "counter": 3})

        recovered_replace = store.jload(test_path, default={})
        self.assertEqual(recovered_replace, initial_data, "os.replace failure corrupted destination file")

        assert recovered_dump == initial_data

    def test_security_injection_payloads(self):
        """Security: Injection payloads (SQLi, cmd injection, null bytes, unicode) are stored cleanly."""
        payloads = [
            "'; DROP TABLE users;--",
            "$(id)",
            "`reboot`",
            "hello\x00world",
            "🚀🔥 unicode payload with newlines\nsecond line\r\nthird",
        ]
        raw_data = {}
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

    def test_security_path_traversal_behavior(self):
        """Security: Document behavior and confinement of task and goal IDs."""
        normal_task = dict(id="0001-normal", title="Normal Task", spec="ok")
        store.save_task(normal_task)
        normal_path = os.path.join(ctx.FD, "tasks", "0001-normal.json")
        self.assertTrue(os.path.exists(normal_path))

        # Document finding VULN-STORE-01: save_task allows unconfined path traversal in ID
        traversal_task = dict(id="../../escaped_task", title="Traversal", spec="pwn")
        store.save_task(traversal_task)
        escaped_file = os.path.normpath(os.path.join(ctx.FD, "..", "escaped_task.json"))
        # Direct filesystem verification documents whether traversal succeeded
        escaped_exists = os.path.exists(escaped_file)
        self.assertTrue(escaped_exists, "File was written via path traversal")
        if escaped_exists:
            os.remove(escaped_file)

        # new_goal_id with custom suffix
        gid = store.new_goal_id(suffix="-custom")
        self.assertTrue(gid.startswith("G"))
        assert os.path.exists(normal_path)

    def test_boundary_fuzzing_and_error_handling(self):
        """Boundary: jload missing files, malformed task files, next_task_number, notify."""
        # Non-existent file returns default
        missing = os.path.join(ctx.FD, "does_not_exist.json")
        self.assertIsNone(store.jload(missing))
        self.assertEqual(store.jload(missing, default={"fallback": True}), {"fallback": True})

        # Corrupted / invalid JSON file returns default safely without unhandled exception
        bad_json_path = os.path.join(ctx.FD, "bad_syntax.json")
        with open(bad_json_path, "w", encoding="utf-8") as f:
            f.write("{invalid_json: true")
        self.assertEqual(store.jload(bad_json_path, default={"fallback": 1}), {"fallback": 1})

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
        store.notify("questions", text="Clarification needed")
        store.notify("quota_out", text="Quota exhausted")
        store.notify("auth", text="Auth failed")
        store.notify("approval", text="Approval needed")
        store.notify("custom_kind", None, "custom text")

        # goals() on empty vs populated
        self.assertEqual(store.goals(), [])
        g = dict(id="G20261011-120000", text="Goal 1", status="new")
        store.save_goal(g)
        self.assertEqual(len(store.goals()), 1)

        assert next_num >= 1


if __name__ == "__main__":
    unittest.main()
