#!/usr/bin/env python3
"""Tests for Foreman merge path (bin/lib/foreman/merge.py): crash recovery after merge,
requeue-gate round counting and task isolation, externally-merged PR handling,
null head tolerance in already_merged_pr, conflict resolution for shared files,
and control character safety in gate output and review feedback.

Public interface: runs via unittest runner or directly as a test script.
Invariants: crash recovery prevents double merges/PRs; 4-round regate cap blocks
at exactly 4 rounds; external upstream merges are detected; non-union shared files conflict;
gate output and review feedback containing NUL or ANSI control chars never crash subprocess argv.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import sbx
from unittest.mock import patch
from foreman import ctx, merge, store

# Smoke import check: ensures modules are loaded cleanly without silent failure
for _mod in (sbx, ctx, merge, store):
    assert _mod is not None, f"Module {_mod} failed to load"



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
        assert t1_cur["status"] == "blocked"
        self.assertEqual(t1_cur["regates"], 4)
        assert t1_cur["regates"] == 4
        self.assertIn("after 4 tries", t1_cur.get("blocked_why", ""))

        # Second task: verify round counter belongs strictly to that task
        t2 = dict(id="0002-t2", history=[], regates=0, status="gate")
        merge.save_task(t2)
        merge.requeue_gate(t2, "busy")
        t2_cur = merge.live_task(t2)
        assert t2_cur["status"] == "gate"
        self.assertEqual(t2_cur["status"], "gate", "Task 2 must not be affected by Task 1's budget")
        assert t2_cur["status"] == "gate"
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
        assert t_after["status"] == "done"
        self.assertEqual(t_after["pr"], 88)
        assert t_after["pr"] == 88
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
        assert t_owner_after["status"] == "done"
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
        t_after = None
        try:
            fn = merge.step_upstream_merge(t)
            self.assertTrue(callable(fn))
            fn()
            t_after = merge.live_task(t)
        finally:
            upstream.sync_main = old_sync_main

        self.assertEqual(t_after["status"], "done")
        assert t_after["status"] == "done"
        self.assertEqual(t_after["pr"], 15)
        assert t_after["pr"] == 15
        self.assertEqual(len(synced_args), 1)
        assert len(synced_args) == 1

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
        assert pr_num == 3

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
        assert rb_res == "conflict"

    def test_auto_rebase_non_union_py_conflict(self):
        """Probe 1: conflict in a non-union .py file reports conflict and leaves shared clone clean without .git/rebase-merge."""
        bare = os.path.join(self.tmp, "forge_py.git")
        work = os.path.join(self.tmp, "work_py")
        git_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def run_git(d, *args):
            r = subprocess.run(["git", *args], cwd=d, capture_output=True, text=True, env=git_env)
            if r.returncode != 0:
                raise RuntimeError(f"git {args} failed: {r.stderr}")
            return r.stdout.strip()

        run_git(self.tmp, "init", "-q", "--bare", "-b", "main", bare)
        run_git(self.tmp, "clone", "-q", bare, work)
        with open(os.path.join(work, "logic.py"), "w") as f:
            f.write("def value():\n    return 'base'\n")
        run_git(work, "add", "-A")
        run_git(work, "commit", "-qm", "initial logic")
        run_git(work, "push", "-q", "origin", "main")

        # Branch changes logic.py line 2
        run_git(work, "checkout", "-q", "-b", "feat/0041-py-conflict")
        with open(os.path.join(work, "logic.py"), "w") as f:
            f.write("def value():\n    return 'branch'\n")
        run_git(work, "commit", "-qam", "branch logic update")
        run_git(work, "push", "-q", "origin", "feat/0041-py-conflict")

        # Main also changes logic.py line 2
        run_git(work, "checkout", "-q", "main")
        with open(os.path.join(work, "logic.py"), "w") as f:
            f.write("def value():\n    return 'main'\n")
        run_git(work, "commit", "-qam", "main logic update")
        run_git(work, "push", "-q", "origin", "main")

        sbx.FORGE_URL = "file://" + self.tmp
        sbx.FORGE_REPO = "forge_py"
        rb_res = merge.auto_rebase("feat/0041-py-conflict")
        self.assertEqual(rb_res, "conflict")
        assert rb_res == "conflict"

        rebase_dir = os.path.join(ctx.FD, "rebase")
        self.assertFalse(os.path.exists(os.path.join(rebase_dir, ".git", "rebase-merge")))
        assert not os.path.exists(os.path.join(rebase_dir, ".git", "rebase-merge"))
        self.assertFalse(os.path.exists(os.path.join(rebase_dir, ".git", "rebase-apply")))
        assert not os.path.exists(os.path.join(rebase_dir, ".git", "rebase-apply"))

        st = subprocess.run(["git", "status", "--porcelain"], cwd=rebase_dir, capture_output=True, text=True)
        self.assertEqual(st.stdout.strip(), "", "shared clone must be clean after aborted rebase")
        assert st.stdout.strip() == ""

    def test_auto_rebase_union_jsonl_append_conflict(self):
        """Probe 2: conflict in a union (.jsonl) file appends both lines without data loss and stays syntactically valid."""
        ctx.C["SHARED_FILES"] = "records.jsonl"
        bare = os.path.join(self.tmp, "forge_union.git")
        work = os.path.join(self.tmp, "work_union")
        git_env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def run_git(d, *args):
            r = subprocess.run(["git", *args], cwd=d, capture_output=True, text=True, env=git_env)
            if r.returncode != 0:
                raise RuntimeError(f"git {args} failed: {r.stderr}")
            return r.stdout.strip()

        run_git(self.tmp, "init", "-q", "--bare", "-b", "main", bare)
        run_git(self.tmp, "clone", "-q", bare, work)
        with open(os.path.join(work, "records.jsonl"), "w") as f:
            f.write(json.dumps({"id": "base", "val": 0}) + "\n")
        run_git(work, "add", "-A")
        run_git(work, "commit", "-qm", "initial records")
        run_git(work, "push", "-q", "origin", "main")

        # Branch appends line
        run_git(work, "checkout", "-q", "-b", "feat/0041-union")
        with open(os.path.join(work, "records.jsonl"), "a") as f:
            f.write(json.dumps({"id": "branch_entry", "val": 1}) + "\n")
        run_git(work, "commit", "-qam", "branch append")
        run_git(work, "push", "-q", "origin", "feat/0041-union")

        # Main appends different line
        run_git(work, "checkout", "-q", "main")
        with open(os.path.join(work, "records.jsonl"), "a") as f:
            f.write(json.dumps({"id": "main_entry", "val": 2}) + "\n")
        run_git(work, "commit", "-qam", "main append")
        run_git(work, "push", "-q", "origin", "main")

        sbx.FORGE_URL = "file://" + self.tmp
        sbx.FORGE_REPO = "forge_union"
        rb_res = merge.auto_rebase("feat/0041-union")
        self.assertEqual(rb_res, "rebased")
        assert rb_res == "rebased"

        # Verify content on remote branch
        run_git(work, "fetch", "-q", "origin")
        run_git(work, "checkout", "-q", "feat/0041-union")
        run_git(work, "reset", "-q", "--hard", "origin/feat/0041-union")
        with open(os.path.join(work, "records.jsonl")) as f:
            content = f.read()

        lines = [l for l in content.strip().splitlines() if l.strip()]
        parsed = [json.loads(l) for l in lines]
        parsed_ids = {p["id"] for p in parsed}

        self.assertIn("base", parsed_ids)
        self.assertIn("branch_entry", parsed_ids, "branch line must be preserved in union rebase")
        self.assertIn("main_entry", parsed_ids, "main line must be preserved in union rebase")
        assert "branch_entry" in parsed_ids
        assert "main_entry" in parsed_ids

        rebase_dir = os.path.join(ctx.FD, "rebase")
        self.assertFalse(os.path.exists(os.path.join(rebase_dir, ".git", "rebase-merge")))
        assert not os.path.exists(os.path.join(rebase_dir, ".git", "rebase-merge"))
        st = subprocess.run(["git", "status", "--porcelain"], cwd=rebase_dir, capture_output=True, text=True)
        self.assertEqual(st.stdout.strip(), "")
        assert st.stdout.strip() == ""

    def test_requeue_gate_cap_and_no_silent_merge(self):
        """Probe 3: requeue_gate past retry cap blocks with clear reason and is never silently merged."""
        t = dict(id="0041-cap", history=[], regates=0, status="gate", branch="feat/0041-cap", attempts=0)
        merge.save_task(t)

        for i in range(1, 6):
            merge.requeue_gate(t, f"failure round {i}")
            t_cur = merge.live_task(t)
            if i < 4:
                self.assertEqual(t_cur["status"], "gate")
            else:
                self.assertEqual(t_cur["status"], "blocked")
                assert t_cur["status"] == "blocked"

        self.assertEqual(t_cur["status"], "blocked")
        assert t_cur["status"] == "blocked"
        self.assertIn("after 4 tries", t_cur.get("blocked_why", ""))
        assert "after 4 tries" in t_cur.get("blocked_why", "")

        # Calling gate or merge steps on a blocked task must never silently merge it
        fn_gate = merge.step_gate_review_merge(t_cur)
        if callable(fn_gate):
            fn_gate()
        fn_merge = merge.do_merge(t_cur, "lead", "feedback", "gate tail")
        if callable(fn_merge):
            fn_merge()
        merge.finish(t_cur, "silent merge attempt", "lead", "ok", 999)

        t_final = merge.live_task(t)
        self.assertEqual(t_final["status"], "blocked", "blocked task must never be transitioned to done")
        assert t_final["status"] == "blocked"

    def test_step_gate_review_merge_resumability_and_restart(self):
        """Probe 4: restart mid-step leaves no stale PHASE and resumes to terminal/clearly-retryable state."""
        from foreman import jobs
        t = dict(id="0041-resumable", history=[], status="gate", branch="feat/0041-resumable",
                 role="backend-developer", goal="G1", title="resumability test", attempts=0,
                 spec="test spec", acceptance=[])
        merge.save_task(t)

        merge.auto_rebase = lambda b: "uptodate"
        merge.already_merged_pr = lambda b: None
        merge.refresh_repo = lambda: None

        def interrupted_sh(cmd, *a, **kw):
            if "review-branch" in cmd[0]:
                cur = merge.live_task(t)
                cur["diff"] = {"added": 10, "removed": 2}
                merge.save_task(cur)
                raise RuntimeError("simulated kill during test container execution")
            return (0, "", "")

        merge.sh = interrupted_sh
        try:
            merge.step_gate_review_merge(t)
        except RuntimeError:
            pass

        # Invariant: PHASE tracking must not remain dangling without an active job
        self.assertNotIn(t["id"], jobs.PHASE, "PHASE must be cleared on mid-step exception")
        assert t["id"] not in jobs.PHASE

        # Second call simulates supervisor resuming the step
        def successful_sh(cmd, *a, **kw):
            if "diff" in cmd:
                return (0, "+code\n", "")
            if "review-branch" in cmd[0]:
                return (0, "gate tests passed\n", "")
            if "open-pr" in cmd[0]:
                return (0, "opened PR #41\n", "")
            if "merge-pr" in cmd[0]:
                return (0, "merged\n", "")
            return (0, "", "")

        merge.sh = successful_sh
        merge.llm = lambda *a, **kw: ("{\"verdict\": \"approve\"}", "lead")
        merge.upstream_obj = lambda: None
        merge.total_lines_after_merge = lambda files: 25
        ctx.C["AUTO_MERGE"] = 1

        fn_resume = merge.step_gate_review_merge(merge.live_task(t))
        self.assertTrue(callable(fn_resume))
        fn_resume()

        t_resumed = merge.live_task(t)
        self.assertEqual(t_resumed["status"], "done")
        assert t_resumed["status"] == "done"
        self.assertNotIn(t["id"], jobs.PHASE)
        assert t["id"] not in jobs.PHASE

        # Also test resumability for a task stuck in 'merging' with no PR reference
        t_merg = dict(id="0041-stuck-merg", history=[], status="merging", branch="feat/0041-stuck-merg",
                      role="backend-developer", goal="G1", title="stuck merging test", upstream_pr=None)
        merge.save_task(t_merg)
        merge.upstream_obj = lambda: None

        fn_up = merge.step_upstream_merge(t_merg)
        self.assertTrue(callable(fn_up))
        fn_up()

        t_merg_after = merge.live_task(t_merg)
        self.assertEqual(t_merg_after["status"], "gate", "stuck merging task with no PR reference must route back to gate")
        assert t_merg_after["status"] == "gate"
        self.assertNotIn(t_merg["id"], jobs.PHASE)
        assert t_merg["id"] not in jobs.PHASE

    def test_requeue_gate_ignores_blocked_task(self):
        """Invariant: requeue_gate returns immediately for blocked tasks without altering status, regates, or history."""
        # Case 1: blocked task with regates < 4 must not be unblocked back to 'gate'
        t1 = dict(id="0051-blk1", history=[], regates=1, status="blocked", blocked_why="rejected by owner")
        merge.save_task(t1)
        merge.requeue_gate(t1, "stray regate attempt")
        t1_after = merge.live_task(t1)
        self.assertEqual(t1_after["status"], "blocked")
        assert t1_after["status"] == "blocked"
        self.assertEqual(t1_after["regates"], 1)
        assert t1_after["regates"] == 1
        self.assertEqual(len(t1_after["history"]), 0)
        assert len(t1_after["history"]) == 0

        # Case 2: blocked task after hitting retry cap must not record further regates
        t2 = dict(id="0052-blk2", history=[dict(outcome="regate")], regates=4, status="blocked",
                  blocked_why="approved but could not be merged after 4 tries")
        merge.save_task(t2)
        merge.requeue_gate(t2, "another failure")
        t2_after = merge.live_task(t2)
        self.assertEqual(t2_after["status"], "blocked")
        assert t2_after["status"] == "blocked"
        self.assertEqual(t2_after["regates"], 4)
        assert t2_after["regates"] == 4
        self.assertEqual(len(t2_after["history"]), 1)
        assert len(t2_after["history"]) == 1

    def test_finish_ignores_blocked_task(self):
        """Invariant: finish returns immediately for blocked tasks and never marks them done or sets pr."""
        t = dict(id="0053-blk-finish", history=[], regates=4, status="blocked",
                 blocked_why="blocked after repeated failures")
        merge.save_task(t)
        merge.finish(t, "unauthorized finish", "reviewer", "good", 123)
        t_after = merge.live_task(t)
        self.assertEqual(t_after["status"], "blocked")
        assert t_after["status"] == "blocked"
        self.assertNotIn("pr", t_after)
        assert "pr" not in t_after
        self.assertEqual(len(t_after["history"]), 0)
        assert len(t_after["history"]) == 0

    def test_step_upstream_merge_no_pr_no_upstream_requeues_gate(self):
        """Invariant: step_upstream_merge on task in merging with no PR reference and no upstream requeues to gate."""
        t = dict(id="0054-no-pr-no-up", history=[], status="merging", branch="feat/0054-test", upstream_pr=None)
        merge.save_task(t)
        merge.upstream_obj = lambda: None

        fn = merge.step_upstream_merge(t)
        self.assertTrue(callable(fn))
        assert callable(fn)
        fn()

        t_after = merge.live_task(t)
        self.assertEqual(t_after["status"], "gate")
        assert t_after["status"] == "gate"
        self.assertEqual(t_after["regates"], 1)
        assert t_after["regates"] == 1
        self.assertTrue(any("in merging state with no PR reference and upstream unavailable" in h.get("why", "") for h in t_after["history"]))
        assert any("in merging state with no PR reference and upstream unavailable" in h.get("why", "") for h in t_after["history"])

    def test_feedback_and_gate_control_characters_safety(self):
        """Regression test for control characters (NUL, ANSI escape, bell, backspace) in feedback/gate_tail:
        asserts no crash and no raw NUL in any subprocess-bound argv across rework, requeue_gate, await_owner,
        do_merge, and merge_upstream."""
        adversarial_fb = "hello\x00world \x1b[31mred\x1b[0m \x07bell \x08backspace"
        adversarial_gate = "gate tests:\x00null_error \x1b[32mgreen\x1b[0m \x07alert"

        # Intercept and record all subprocess calls made via merge.sh
        invoked_commands = []

        def recording_sh(cmd, *a, **kw):
            invoked_commands.append(list(cmd))
            cmd_str = " ".join(cmd)
            if "open-pr" in cmd_str:
                return (0, "opened PR #77\n", "")
            if "merge-pr" in cmd_str:
                return (0, "merged\n", "")
            if "forgejo-api" in cmd_str:
                return (0, "[]", "")
            if "review-branch" in cmd_str:
                return (2, adversarial_gate, "")
            return (0, "", "")

        merge.sh = recording_sh

        # 1. Test rework() with NUL and ANSI escape characters
        t = dict(id="0113-ctrl", history=[], status="gate", branch="feat/0113-ctrl",
                 role="security-developer", goal="G20261012", title="test control chars", attempts=0)
        merge.save_task(t)

        merge.rework(t, adversarial_fb, adversarial_gate)
        t_rework = merge.live_task(t)
        self.assertEqual(t_rework["status"], "pending")
        assert t_rework["status"] == "pending"
        self.assertEqual(t_rework["feedback"], adversarial_fb)
        assert t_rework["feedback"] == adversarial_fb
        self.assertEqual(t_rework["gate_tail"], adversarial_gate)
        assert t_rework["gate_tail"] == adversarial_gate

        # 2. Test requeue_gate() with adversarial why
        merge.requeue_gate(t, adversarial_fb)
        t_regate = merge.live_task(t)
        self.assertEqual(t_regate["status"], "gate")
        assert t_regate["status"] == "gate"
        self.assertEqual(t_regate["regates"], 1)
        assert t_regate["regates"] == 1

        # 3. Test await_owner() with adversarial feedback and gate_tail
        merge.await_owner(t, "merge", "auditor", adversarial_fb, adversarial_gate)
        t_owner = merge.live_task(t)
        self.assertEqual(t_owner["status"], "approval")
        assert t_owner["status"] == "approval"
        self.assertEqual(t_owner["merge_feedback"], adversarial_fb[:800])
        assert t_owner["merge_feedback"] == adversarial_fb[:800]
        self.assertEqual(t_owner["gate_tail"], adversarial_gate[-8000:])
        assert t_owner["gate_tail"] == adversarial_gate[-8000:]

        # 4. Test do_merge() with adversarial feedback and gate_tail
        merge.already_merged_pr = lambda b: None
        merge.upstream_obj = lambda: None
        ctx.C["AUTO_MERGE"] = 1

        fn_merge = merge.do_merge(t, "auditor", adversarial_fb, adversarial_gate)
        self.assertTrue(callable(fn_merge))
        assert callable(fn_merge)
        fn_merge()

        t_merged = merge.live_task(t)
        self.assertEqual(t_merged["status"], "done")
        assert t_merged["status"] == "done"
        self.assertEqual(t_merged["pr"], 77)
        assert t_merged["pr"] == 77

        # Verify PR body file written to disk
        pr_md_path = os.path.join(ctx.FD, "tasks", t["id"] + ".pr.md")
        self.assertTrue(os.path.isfile(pr_md_path))
        assert os.path.isfile(pr_md_path)
        with open(pr_md_path, "r", encoding="utf-8") as f:
            pr_body_content = f.read()
        self.assertIn("hello\x00world", pr_body_content)
        assert "hello\x00world" in pr_body_content

        # 5. Test merge_upstream() with adversarial feedback and gate_tail
        class FakeUpstream:
            kind = "github"
            c = {"branch": "main", "repo": "test/repo"}

            def __init__(self):
                self.calls = []

            def open_pr(self, branch, title, body):
                self.calls.append((branch, title, body))
                return {"number": 88, "url": "https://github.com/test/repo/pull/88"}

        fake_up = FakeUpstream()
        import upstream
        old_push_branch = getattr(upstream, "push_branch", None)
        upstream.push_branch = lambda up, fd, br: None
        try:
            t_up = dict(id="0113-up", history=[], status="gate", branch="feat/0113-up",
                        role="security-developer", goal="G20261012", title="test upstream control chars", attempts=0)
            merge.save_task(t_up)
            fn_up = merge.merge_upstream(t_up, fake_up, "auditor", adversarial_fb, adversarial_gate)
            self.assertTrue(callable(fn_up))
            assert callable(fn_up)
            fn_up()

            t_up_after = merge.live_task(t_up)
            self.assertEqual(t_up_after["status"], "merging")
            assert t_up_after["status"] == "merging"
            self.assertEqual(len(fake_up.calls), 1)
            assert len(fake_up.calls) == 1
            self.assertIn("hello\x00world", fake_up.calls[0][2])
            assert "hello\x00world" in fake_up.calls[0][2]
        finally:
            if old_push_branch is not None:
                upstream.push_branch = old_push_branch

        # 6. Verify that subprocess calls derived from this flow never contain raw NUL in argv
        self.assertTrue(len(invoked_commands) > 0, "expected at least one subprocess call during do_merge")
        assert len(invoked_commands) > 0
        nul_in_argv = [arg for cmd in invoked_commands for arg in cmd if "\x00" in str(arg)]
        self.assertEqual(nul_in_argv, [], f"raw NUL byte found in subprocess argv: {nul_in_argv}")
        assert not nul_in_argv, "subprocess argv derived from gate/review path must never contain raw NUL byte"



if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("foreman merge tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
