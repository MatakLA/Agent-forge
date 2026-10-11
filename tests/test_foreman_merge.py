#!/usr/bin/env python3
"""Tests for Foreman merge path (bin/lib/foreman/merge.py): crash recovery after merge,
requeue-gate round counting and task isolation, externally-merged PR handling,
null head tolerance in already_merged_pr, and conflict resolution for shared files.

Public interface: runs via unittest runner or directly as a test script.
Invariants: crash recovery prevents double merges/PRs; 4-round regate cap blocks
at exactly 4 rounds; external upstream merges are detected; non-union shared files conflict.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import sbx
from unittest.mock import patch
from foreman import ctx, merge, store


class TestForemanMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="af-test-merge-")
        os.environ["SANDBOX_TEST"] = "1"
        os.environ["SANDBOX_RUNS_DIR"] = os.path.join(self.tmp, "runs")
        os.environ["SANDBOX_SECRETS"] = os.path.join(self.tmp, "secrets")
        os.makedirs(os.environ["SANDBOX_SECRETS"], exist_ok=True)
        with open(os.path.join(os.environ["SANDBOX_SECRETS"], "orchestrator.token"), "w") as f:
            f.write("token\n")
        with open(os.path.join(os.environ["SANDBOX_SECRETS"], "monitor.token"), "w") as f:
            f.write("token\n")

        self.old_fd = ctx.FD
        self.old_repo = ctx.REPO
        self.old_c = dict(ctx.C)
        self.old_sh = merge.sh
        self.old_already_merged_pr = merge.already_merged_pr
        self.old_upstream_obj = merge.upstream_obj
        self.old_forge_url = sbx.FORGE_URL
        self.old_forge_repo = sbx.FORGE_REPO

        self.old_sbx_secrets = sbx.SECRETS
        sbx.SECRETS = os.path.join(self.tmp, "secrets")
        self.old_sbx_runs = sbx.RUNS
        sbx.RUNS = os.path.join(self.tmp, "runs")

        ctx.FD = os.path.join(self.tmp, "foreman")
        ctx.REPO = os.path.join(ctx.FD, "repo")
        os.makedirs(os.path.join(ctx.FD, "tasks"), exist_ok=True)

        def _silent_event(msg, **kw):
            os.makedirs(ctx.FD, exist_ok=True)
            with open(os.path.join(ctx.FD, "events.jsonl"), "a") as f:
                f.write(json.dumps(dict(at=sbx.now(), msg=msg, **kw), ensure_ascii=False) + "\n")

        self._p_store_event = patch.object(store, "event", side_effect=_silent_event)
        self._p_merge_event = patch.object(merge, "event", side_effect=_silent_event)
        self._p_store_event.start()
        self._p_merge_event.start()

    def tearDown(self):
        self._p_store_event.stop()
        self._p_merge_event.stop()
        ctx.FD = self.old_fd
        ctx.REPO = self.old_repo
        ctx.C = self.old_c
        sbx.SECRETS = self.old_sbx_secrets
        sbx.RUNS = self.old_sbx_runs
        merge.sh = self.old_sh
        merge.already_merged_pr = self.old_already_merged_pr
        merge.upstream_obj = self.old_upstream_obj
        sbx.FORGE_URL = self.old_forge_url
        sbx.FORGE_REPO = self.old_forge_repo
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_requeue_gate_cap_and_task_isolation(self):
        """Invariant: 4-round cap blocks after exactly 4 rounds (not 3 or 5), isolated per task."""
        t1 = dict(id="0001-t1", history=[], regates=0, status="gate")
        merge.save_task(t1)

        # Rounds 1, 2, 3 should requeue in status 'gate'
        for i in range(1, 4):
            merge.requeue_gate(t1, f"main moved {i}")
            t1_cur = merge.live_task(t1)
            self.assertEqual(t1_cur["status"], "gate", f"Round {i} should remain in status gate")
            self.assertEqual(t1_cur["regates"], i)

        # Round 4: must block immediately after exactly 4 rounds
        merge.requeue_gate(t1_cur, "main moved 4")
        t1_cur = merge.live_task(t1)
        assert t1_cur["status"] == "blocked"
        self.assertEqual(t1_cur["status"], "blocked", "Round 4 must block the task")
        self.assertEqual(t1_cur["regates"], 4)
        self.assertIn("after 4 tries", t1_cur.get("blocked_why", ""))

        # Second task: verify round counter belongs strictly to that task
        t2 = dict(id="0002-t2", history=[], regates=0, status="gate")
        merge.save_task(t2)
        merge.requeue_gate(t2, "busy")
        t2_cur = merge.live_task(t2)
        assert t2_cur["status"] == "gate"
        self.assertEqual(t2_cur["status"], "gate", "Task 2 must not be affected by Task 1's budget")
        self.assertEqual(t2_cur["regates"], 1)

    def test_do_merge_crash_recovery_no_duplicate(self):
        """Invariant: crash recovery in do_merge and step_owner_merge detects prior merge and finishes."""
        t = dict(id="0010-rec", history=[], branch="feat/0010-rec", status="gate",
                 role="backend-developer", goal="G1", title="test recovery")
        merge.save_task(t)

        merge.already_merged_pr = lambda branch: 88 if branch == "feat/0010-rec" else None
        called_sh = []
        merge.sh = lambda *a, **kw: (called_sh.append(a) or (0, "", ""))

        fn = merge.do_merge(t, "reviewer", "good", "gate log")
        self.assertTrue(callable(fn))
        fn()
        t_after = merge.live_task(t)
        assert t_after["status"] == "done"
        self.assertEqual(t_after["status"], "done")
        self.assertEqual(t_after["pr"], 88)
        self.assertEqual(called_sh, [], "do_merge must not call open-pr or merge-pr when already merged")

        # Also verify step_owner_merge crash recovery
        t_owner = dict(id="0011-own", history=[], branch="feat/0010-rec", status="merge-ready",
                       role="backend-developer", goal="G1", title="test recovery owner", merge_reviewer="owner")
        merge.save_task(t_owner)
        fn_owner = merge.step_owner_merge(t_owner)
        self.assertTrue(callable(fn_owner))
        fn_owner()
        t_owner_after = merge.live_task(t_owner)
        assert t_owner_after["status"] == "done"
        self.assertEqual(t_owner_after["status"], "done")
        self.assertEqual(t_owner_after["pr"], 88)

    def test_step_upstream_merge_externally_merged(self):
        """Invariant: an upstream PR merged externally between polls is detected and finished cleanly."""
        t = dict(id="0020-ext", history=[], branch="feat/0020-ext", status="merging",
                 upstream_pr={"number": 15, "url": "https://github.com/org/repo/pull/15"},
                 merge_reviewer="lead", merge_feedback="approved")
        merge.save_task(t)

        class FakeUpstream:
            kind = "github"
            state_call_count = 0
            def pr_state(self, num):
                self.state_call_count += 1
                if self.state_call_count == 1:
                    return {"state": "open", "mergeable": True, "why": ""}
                # After merge fails or is re-queried, report as merged externally
                return {"state": "merged", "mergeable": False, "why": ""}
            def merge(self, num):
                return False, "HTTP 405: Pull Request is not mergeable"
            def find_pr(self, branch, state="open"):
                return {"number": 15, "state": "merged"}

        fake_up = FakeUpstream()
        merge.upstream_obj = lambda: fake_up

        import upstream
        old_sync_main = upstream.sync_main
        synced_args = []
        upstream.sync_main = lambda up, fd: synced_args.append((up, fd)) or "synced"
        try:
            fn = merge.step_upstream_merge(t)
            self.assertTrue(callable(fn))
            fn()
            t_after = merge.live_task(t)
            assert t_after["status"] == "done"
            self.assertEqual(t_after["status"], "done")
            self.assertEqual(t_after["pr"], 15)
            self.assertEqual(len(synced_args), 1)
        finally:
            upstream.sync_main = old_sync_main

    def test_already_merged_pr_tolerates_null_head(self):
        """Invariant: already_merged_pr safely ignores PRs with null head and finds merged PR."""
        merge.upstream_obj = lambda: None
        closed_prs = [
            {"number": 1, "head": None, "merged": False},
            {"number": 2, "head": {"ref": "feat/other"}, "merged": True},
            {"number": 3, "head": {"ref": "feat/my-target"}, "merged": True},
        ]
        merge.sh = lambda cmd, **kw: (0, json.dumps(closed_prs), "") if "pulls?state=closed" in cmd[2] else (1, "", "")
        pr_num = merge.already_merged_pr("feat/my-target")
        assert pr_num == 3
        self.assertEqual(pr_num, 3)

    def test_auto_rebase_non_union_shared_file_conflict(self):
        """Invariant: conflicts in shared files that do not have UNION_EXT (.jsonl/.md/.txt/.csv) abort as conflict."""
        ctx.C["SHARED_FILES"] = "tools/registry.json"
        bare = os.path.join(self.tmp, "forge.git")
        work = os.path.join(self.tmp, "work")
        git_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def run_git(d, *args):
            r = subprocess.run(["git", *args], cwd=d, capture_output=True, text=True, env=git_env)
            if r.returncode != 0:
                raise RuntimeError(f"git {args} failed: {r.stderr}")
            return r.stdout.strip()

        run_git(self.tmp, "init", "-q", "--bare", "-b", "main", bare)
        run_git(self.tmp, "clone", "-q", bare, work)
        os.makedirs(os.path.join(work, "tools"), exist_ok=True)
        with open(os.path.join(work, "tools", "registry.json"), "w") as f:
            f.write('{"key": "base"}\n')
        run_git(work, "add", "-A")
        run_git(work, "commit", "-qm", "initial commit")
        run_git(work, "push", "-q", "origin", "main")

        # Branch with conflicting change
        run_git(work, "checkout", "-q", "-b", "feat/0001-conflict")
        with open(os.path.join(work, "tools", "registry.json"), "w") as f:
            f.write('{"key": "branch_change"}\n')
        run_git(work, "commit", "-qam", "branch commit")
        run_git(work, "push", "-q", "origin", "feat/0001-conflict")

        # Main with conflicting change
        run_git(work, "checkout", "-q", "main")
        with open(os.path.join(work, "tools", "registry.json"), "w") as f:
            f.write('{"key": "main_change"}\n')
        run_git(work, "commit", "-qam", "main commit")
        run_git(work, "push", "-q", "origin", "main")

        sbx.FORGE_URL = "file://" + self.tmp
        sbx.FORGE_REPO = "forge"
        rb_res = merge.auto_rebase("feat/0001-conflict")
        assert rb_res == "conflict"
        self.assertEqual(rb_res, "conflict")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman merge tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
