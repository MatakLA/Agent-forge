#!/usr/bin/env python3
"""Tests for bin/lib/foreman/pool.py: worker pool management, liveness, dispatch rotation, and cooldown expiry.

Public interface under test:
- api_budget_left(w): install, project, and worker caps enforced together (AND logic).
- has_quota(rows, family, floor): boundary handling and fail-open unknown quota.
- pool_quota_out(): distinguishing short cooldowns from true quota exhaustion.
- order_by_quota(ws, pct_of, last_used): least-recently-used tiebreaker.
- worker_busy(w): liveness verification against orphaned run directories.
- is_cooling_down(w, now): expired cooldown pruning and quota cache invalidation.
"""
import datetime as dt, json, os, shutil, sys, tempfile, time, unittest, warnings
from unittest.mock import patch

warnings.filterwarnings("ignore", category=ResourceWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-pool-test-")

os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = os.path.join(T, "runs")
os.environ["LIMITS_FILE"] = os.path.join(T, "limits.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

try:
    import sbx
    import runtimes
    from foreman import ctx
    import foreman.pool as pool
except ImportError as e:
    sys.stderr.write(f"FATAL: failed to import foreman.pool: {e}\n")
    sys.exit(1)


class TestForemanPool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(os.path.join(T, "runs"), exist_ok=True)
        # Standard limits for testing: 2.0 per run, 100.0 install daily budget
        with open(os.environ["LIMITS_FILE"], "w") as f:
            f.write("API_RUN_BUDGET_USD=2.0\n")
            f.write("API_DAILY_BUDGET_USD=100.0\n")
            f.write("API_MONTHLY_BUDGET_USD=0.0\n")
            f.write("WORKER_DAILY_BUDGET_USD=worker-1=10.0,worker-2=10.0\n")
            f.write("QUOTA_MIN_5H_PCT=25\n")
            f.write("QUOTA_MIN_WEEK_PCT=15\n")
            f.write("QUOTA_MODEL_FILTER=Gemini\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(T, ignore_errors=True)

    def setUp(self):
        try:
            assert pool is not None
        except Exception as e:
            sys.stderr.write(f"setUp import validation failed: {e}\n")
            raise
        self.tmp = tempfile.mkdtemp(prefix="af-pool-run-test-")
        self.orig_runs = sbx.RUNS
        sbx.RUNS = self.tmp
        os.environ["SANDBOX_RUNS_DIR"] = self.tmp
        ledger_path = sbx.ledger_path()
        if os.path.exists(ledger_path):
            os.remove(ledger_path)
        with open(ledger_path, "w") as f:
            pass  # Ensure ledger file exists
        pool.WORKER_RT.clear()
        pool.RESERVED.clear()
        pool.QUOTA_CACHE.clear()
        pool.COOLDOWN.clear()
        pool.LAST_DISPATCHED.clear()
        pool.LAST_QUOTA_MSG.clear()
        ctx.C["PROJECT_DAILY_BUDGET_USD"] = 0.0
        ctx.C["PROJECT_MONTHLY_BUDGET_USD"] = 0.0
        self._orig_workers = pool.workers
        self._orig_api_budget_left = pool.api_budget_left
        self._orig_runtime_of = pool.runtime_of

    def tearDown(self):
        sbx.RUNS = self.orig_runs
        shutil.rmtree(self.tmp, ignore_errors=True)
        pool.workers = self._orig_workers
        pool.api_budget_left = self._orig_api_budget_left
        pool.runtime_of = self._orig_runtime_of

    def _append_ledger(self, cost_usd, worker, project=""):
        entry = {
            "date": sbx.today(),
            "cost_usd": float(cost_usd),
            "worker": worker,
            "project": project,
        }
        with open(sbx.ledger_path(), "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_api_budget_left_all_caps_enforced(self):
        """Install, project, and worker caps must ALL be enforced together (AND, not OR).

        A worker under its own cap but over the project cap must still be refused.
        """
        # Run cost is $2.0. Install cap is $100.0. Worker cap is $10.0.
        # Set project cap to $5.0 in ctx.C.
        ctx.C["PROJECT_DAILY_BUDGET_USD"] = 5.0

        # Case 1: Worker-1 has spent $0 (under worker cap $10.0),
        # but another worker has spent $4.0 for this project.
        # Next run ($2.0) would make project spend $6.0 > $5.0.
        self._append_ledger(4.0, worker="worker-2", project="")
        # Worker-1 is under its own cap ($0 + $2 <= $10), but project exceeds ($4 + $2 > $5).
        res1 = pool.api_budget_left("worker-1")
        self.assertFalse(
            res1,
            "Worker under its own cap but over the project cap must be refused",
        )
        assert res1 is False

        # Case 2: Project cap has headroom ($5.0 cap, $0 spent),
        # but worker-1 has spent $9.0 (next run $2.0 -> $11.0 > $10.0 worker cap).
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        self._append_ledger(9.0, worker="worker-1", project="")
        res2 = pool.api_budget_left("worker-1")
        self.assertFalse(
            res2,
            "Worker exceeding its own worker cap must be refused",
        )
        assert res2 is False

        # Case 3: Project cap and worker cap have headroom,
        # but install-wide cap ($100.0) is exceeded ($99.0 spent).
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        self._append_ledger(99.0, worker="worker-other", project="other-project")
        res3 = pool.api_budget_left("worker-1")
        self.assertFalse(
            res3,
            "Worker must be refused when install-wide cap is exceeded",
        )
        assert res3 is False

        # Case 4: All caps satisfied: project ($0 spent), worker ($0 spent), install ($0 spent).
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        res4 = pool.api_budget_left("worker-1")
        self.assertTrue(
            res4,
            "Worker must be allowed when all applicable caps have headroom",
        )
        assert res4 is True

    def test_has_quota_boundary_at_exact_floor(self):
        """has_quota must allow a worker at exactly floor% remaining (boundary: >= vs >)."""
        floor = 2.0
        # Exactly at floor% (2.0%) remaining in 5-hour window -> quota meets floor, allowed
        rows_at_floor = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 2.0},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        res1 = pool.has_quota(rows_at_floor, "gemini", floor=floor)
        self.assertTrue(
            res1,
            "Exactly floor% remaining must count as having quota (not exhausted)",
        )
        assert res1 is True

        # Below floor% (1.9%) remaining in 5-hour window -> exhausted, refused
        rows_below_floor = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 1.9},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        res2 = pool.has_quota(rows_below_floor, "gemini", floor=floor)
        self.assertFalse(
            res2,
            "Below floor% remaining must count as exhausted",
        )
        assert res2 is False

    def test_has_quota_unknown_quota_fails_open(self):
        """Unknown quota (family absent from rows or empty rows) fails open (allowed)."""
        # Family entirely absent from rows (only Gemini reported, checking Claude)
        rows_gemini_only = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 50.0},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        res1 = pool.has_quota(rows_gemini_only, "claude")
        self.assertTrue(
            res1,
            "Absent model family must fail open (allowed)",
        )
        assert res1 is True

        # Entirely empty rows
        res2 = pool.has_quota([], "claude")
        self.assertTrue(
            res2,
            "Empty quota rows must fail open (allowed)",
        )
        assert res2 is True

    def test_pool_quota_out_cooldown_vs_exhaustion(self):
        """A pool where every worker is merely in a short cooldown must not be reported as quota-out."""
        pool.workers = lambda: ["worker-1"]
        pool.runtime_of = lambda w: "agy"
        pool.WORKER_RT["worker-1"] = "agy"
        now = time.time()

        healthy_rows = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 80.0, "reset": "tomorrow"},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 80.0, "reset": "next week"},
        ]
        exhausted_rows = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 5.0, "reset": "tomorrow"},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 5.0, "reset": "next week"},
        ]

        # Case 1: Worker has healthy quota, but is paused in cooldown with "quota low" reason
        pool.QUOTA_CACHE["worker-1"] = (now, healthy_rows)
        pool.COOLDOWN["worker-1"] = (now + 60, "quota low: brief cooldown")
        res1 = pool.pool_quota_out()
        self.assertFalse(
            res1,
            "Worker with healthy quota must not report pool quota-out even if in cooldown",
        )
        assert res1 is False

        # Case 2: Worker in a short cooldown (<= 300s) even with quota low and unreadable/empty rows
        pool.QUOTA_CACHE["worker-1"] = (now, [])
        pool.COOLDOWN["worker-1"] = (now + 60, "quota low: brief 60s pause")
        res2 = pool.pool_quota_out()
        self.assertFalse(
            res2,
            "Short cooldown (<= 300s) must not be reported as pool quota-out",
        )
        assert res2 is False

        # Case 3: Worker in cooldown for non-quota reason (e.g. rate limit, container restart)
        pool.QUOTA_CACHE["worker-1"] = (now, [])
        pool.COOLDOWN["worker-1"] = (now + 600, "rate limit: cooling down")
        res3 = pool.pool_quota_out()
        self.assertFalse(
            res3,
            "Non-quota cooldown must not be reported as pool quota-out",
        )
        assert res3 is False

        # Case 4: True exhaustion: long cooldown (> 300s), quota low reason, exhausted rows
        # Mock api_budget_left to False so it does not short-circuit before exhaustion check
        pool.api_budget_left = lambda w="": False
        pool.QUOTA_CACHE["worker-1"] = (now, exhausted_rows)
        pool.COOLDOWN["worker-1"] = (now + 1800, "quota low: weekly limit exhausted")
        res4 = pool.pool_quota_out()
        self.assertTrue(
            res4,
            "True exhaustion (> 300s with low quota) must be reported as quota-out",
        )
        assert res4 is True

    def test_order_by_quota_lru_tiebreak(self):
        """Identical quota percentage ties must be broken by least-recently-used (smallest timestamp)."""
        ws = ["worker-1", "worker-2", "worker-3"]
        # worker-1 and worker-2 tied at 80%, worker-3 at 50%
        pct_of = lambda w: 80.0 if w in ("worker-1", "worker-2") else 50.0

        # worker-2 used at t=1000 (longer ago), worker-1 used at t=2000 (more recent)
        last_used = {"worker-1": 2000, "worker-2": 1000, "worker-3": 500}
        ordered = pool.order_by_quota(ws, pct_of, last_used)
        self.assertEqual(
            ordered,
            ["worker-2", "worker-1", "worker-3"],
            "On tie, least-recently-used worker (smallest timestamp) must come first",
        )
        assert ordered == ["worker-2", "worker-1", "worker-3"]

        # Worker never used (not in last_used -> 0) comes before worker with positive timestamp
        last_used_partial = {"worker-1": 2000}
        ordered_partial = pool.order_by_quota(["worker-1", "worker-2"], pct_of, last_used_partial)
        self.assertEqual(
            ordered_partial,
            ["worker-2", "worker-1"],
            "Worker never used (0) must precede previously used worker on tied quota",
        )
        assert ordered_partial == ["worker-2", "worker-1"]

    def test_pool_quota_out_api_worker_budget_conjunction(self):
        """pool_quota_out must check each API worker's specific budget (api_budget_left(w)), not just global api_budget_left()."""
        pool.workers = lambda: ["worker-1"]
        pool.runtime_of = lambda w: "claude"

        # Case 1: worker-1 has spent $9.0. Next run costs $2.0 -> $11.0 > $10.0 (worker cap).
        # Global install cap ($100.0) has plenty of headroom.
        # api_budget_left() (without args) is True, but api_budget_left("worker-1") is False.
        self._append_ledger(9.0, worker="worker-1", project="")
        self.assertTrue(pool.api_budget_left(), "Global install cap must have headroom")
        assert pool.api_budget_left() is True
        self.assertFalse(pool.api_budget_left("worker-1"), "Worker cap must be exceeded")
        assert pool.api_budget_left("worker-1") is False

        # pool_quota_out() must report True (pool is out of quota/budget because worker-1 cannot run)
        res_exhausted = pool.pool_quota_out()
        self.assertTrue(
            res_exhausted,
            "API worker exceeding its worker cap must cause pool_quota_out to return True",
        )
        assert res_exhausted is True

        # Case 2: worker-1 has spent only $5.0. Next run costs $2.0 -> $7.0 <= $10.0.
        # Both global cap and worker cap have headroom.
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        self._append_ledger(5.0, worker="worker-1", project="")
        self.assertTrue(pool.api_budget_left("worker-1"), "Worker-1 has headroom")
        assert pool.api_budget_left("worker-1") is True

        res_available = pool.pool_quota_out()
        self.assertFalse(
            res_available,
            "API worker within its worker cap must allow pool dispatch (pool_quota_out is False)",
        )
        assert res_available is False

        # Case 3: Multiple API workers: worker-1 is over cap ($9 spent), worker-2 is under cap ($0 spent).
        # pool_quota_out must return False because worker-2 can still run.
        pool.workers = lambda: ["worker-1", "worker-2"]
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        self._append_ledger(9.0, worker="worker-1", project="")
        res_multi = pool.pool_quota_out()
        self.assertFalse(
            res_multi,
            "Pool with at least one eligible API worker must not report quota-out",
        )
        assert res_multi is False

        # Case 4: API worker has budget, but is currently cooling down.
        # pool_quota_out must return True because no eligible worker can start a run.
        pool.workers = lambda: ["worker-1"]
        pool.runtime_of = lambda w: "claude"
        pool.WORKER_RT["worker-1"] = "claude"
        pool.COOLDOWN.clear()
        if os.path.exists(sbx.ledger_path()):
            os.remove(sbx.ledger_path())
        self._append_ledger(5.0, worker="worker-1", project="")
        self.assertTrue(pool.api_budget_left("worker-1"), "Worker-1 has headroom")
        assert pool.api_budget_left("worker-1") is True

        now = time.time()
        pool.COOLDOWN["worker-1"] = (now + 600.0, "rate limit: 429 cooling down")
        self.assertTrue(pool.is_cooling_down("worker-1", now=now), "Worker-1 must be cooling down")
        assert pool.is_cooling_down("worker-1", now=now) is True

        res_cooling = pool.pool_quota_out()
        self.assertTrue(
            res_cooling,
            "API worker with budget but in cooldown must cause pool_quota_out to return True",
        )
        assert res_cooling is True

        # Case 5: Multiple API workers: worker-1 has budget but is cooling down,
        # worker-2 has exhausted its budget. Neither can start a run.
        pool.workers = lambda: ["worker-1", "worker-2"]
        pool.WORKER_RT["worker-2"] = "claude"
        self._append_ledger(9.0, worker="worker-2", project="")
        self.assertFalse(pool.api_budget_left("worker-2"), "Worker-2 has no budget")
        assert pool.api_budget_left("worker-2") is False

        res_multi_cooling = pool.pool_quota_out()
        self.assertTrue(
            res_multi_cooling,
            "Pool with cooling-down and exhausted API workers must return True",
        )
        assert res_multi_cooling is True

    def test_pool_quota_out_api_worker_cooldown_guard(self):
        """Invariant: api_budget_left guard in pool_quota_out must verify the worker is not cooling down."""
        pool.workers = lambda: ["worker-1"]
        pool.runtime_of = lambda w: "claude"
        pool.WORKER_RT["worker-1"] = "claude"
        pool.COOLDOWN.clear()
        now = time.time()

        # Worker-1 has sufficient budget (0 spent)
        self.assertTrue(pool.api_budget_left("worker-1"), "Worker-1 has budget")
        assert pool.api_budget_left("worker-1") is True

        # When worker-1 is in cooldown, pool_quota_out must not treat it as available
        pool.COOLDOWN["worker-1"] = (now + 600.0, "rate limit: cooling down")
        self.assertTrue(pool.is_cooling_down("worker-1", now=now))
        assert pool.is_cooling_down("worker-1", now=now) is True

        res = pool.pool_quota_out()
        self.assertTrue(
            res,
            "API worker with budget in cooldown must cause pool_quota_out to return True",
        )
        assert res is True

    def test_worker_busy_after_orphaned_run(self):
        """Invariant: worker_busy must not report busy forever once a run is no longer live."""
        w = "worker-1"
        orphaned_dir = os.path.join(self.tmp, f"20261011T120000Z-{w}")
        os.makedirs(orphaned_dir, exist_ok=True)

        # Case 1: Run directory left behind by crashed supervisor with no pid file
        self.assertFalse(pool.worker_busy(w), "Orphaned run with no pid must not be marked busy")
        assert not pool.worker_busy(w)

        # Case 2: Run directory with pid of dead process (crashed supervisor)
        pid_file = os.path.join(orphaned_dir, "pid")
        with open(pid_file, "w") as f:
            f.write("999999\n")
        self.assertFalse(pool.worker_busy(w), "Dead supervisor pid must not mark worker busy")
        assert not pool.worker_busy(w)

        # Case 3: Finished run directory must not be marked busy
        with open(os.path.join(orphaned_dir, "finished"), "w") as f:
            f.write("1\n")
        self.assertFalse(pool.worker_busy(w), "Finished run must not mark worker busy")
        assert not pool.worker_busy(w)

        # Case 4: Actively running supervisor process must report busy
        with patch.object(sbx, "is_running", return_value=True):
            self.assertTrue(pool.worker_busy(w), "Active run supervisor must report worker busy")
            assert pool.worker_busy(w)

    def test_starvation_rotation_order_by_quota(self):
        """Invariant: order_by_quota rotates dispatches across tied workers without starvation."""
        ws = ["worker-1", "worker-2", "worker-3"]
        pct_of = lambda w: 100.0
        last_dispatched = {}
        history = []
        now = 1700000000.0

        # Simulate 12 consecutive task dispatches
        for i in range(12):
            ordered = pool.order_by_quota(ws, pct_of, last_dispatched)
            chosen = ordered[0]
            history.append(chosen)
            last_dispatched[chosen] = now + i

        # Assert perfect round-robin rotation
        expected = [
            "worker-1", "worker-2", "worker-3",
            "worker-1", "worker-2", "worker-3",
            "worker-1", "worker-2", "worker-3",
            "worker-1", "worker-2", "worker-3",
        ]
        self.assertEqual(history, expected, "Dispatch must rotate across all 3 workers")
        assert history == expected

        # Assert no worker is starved for more than 2 turns
        for w in ws:
            indices = [idx for idx, chosen in enumerate(history) if chosen == w]
            self.assertEqual(len(indices), 4, f"Worker {w} should have been dispatched exactly 4 times")
            for j in range(len(indices) - 1):
                gap = indices[j + 1] - indices[j] - 1
                self.assertLessEqual(gap, 2, f"Worker {w} was starved for {gap} turns")
                assert gap <= 2

        # Assert order_by_quota handles None quota and missing timestamps gracefully
        none_pct = pool.order_by_quota(["worker-1", "worker-2"], lambda w: None, {})
        self.assertEqual(len(none_pct), 2, "order_by_quota must handle None quota without raising")
        assert len(none_pct) == 2

    def test_cooldown_not_expiring_stale_entry(self):
        """Invariant: expired cooldowns must not keep worker cooling down forever (no stale entries)."""
        w = "worker-1"
        now = time.time()
        past_epoch = now - 60.0

        # Set a past cooldown
        pool.COOLDOWN[w] = (past_epoch, "quota low: rate limit")
        pool.QUOTA_CACHE[w] = (past_epoch, [{"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 0.0}])

        # Assert worker is treated as available again (not cooling down)
        self.assertFalse(pool.is_cooling_down(w, now=now), "Past cooldown must be treated as available")
        assert not pool.is_cooling_down(w, now=now)

        # Assert expired entry was pruned from COOLDOWN
        self.assertNotIn(w, pool.COOLDOWN, "Expired cooldown entry must be pruned from COOLDOWN")
        assert w not in pool.COOLDOWN

        # Assert stale QUOTA_CACHE was cleared upon cooldown expiry
        self.assertNotIn(w, pool.QUOTA_CACHE, "Stale quota cache must be purged on cooldown expiry")
        assert w not in pool.QUOTA_CACHE

        # Boundary test: exact equality (until == now) is treated as expired (available)
        pool.COOLDOWN[w] = (now, "quota low: edge boundary")
        self.assertFalse(pool.is_cooling_down(w, now=now), "Exact expiration boundary must count as available")
        assert not pool.is_cooling_down(w, now=now)

        # Future cooldown (until > now) must be recognized as cooling down
        pool.COOLDOWN[w] = (now + 300.0, "quota low: cooling")
        self.assertTrue(pool.is_cooling_down(w, now=now), "Future cooldown must report cooling down")
        assert pool.is_cooling_down(w, now=now)

    def test_api_budget_worker_busy_stale_run_surfaces_reason(self):
        """Invariant: worker out of budget with stale run must not silently block pool and surfaces accurate reason."""
        w = "worker-1"
        pool.workers = lambda: [w]
        pool.WORKER_RT[w] = "claude"

        # Stale run directory left behind by dead supervisor
        stale_run = os.path.join(self.tmp, f"20261011T120000Z-{w}")
        os.makedirs(stale_run, exist_ok=True)

        # Stale run must not mark worker busy
        self.assertFalse(pool.worker_busy(w), "Stale run must not mark worker busy")
        assert not pool.worker_busy(w)

        # Simulate out-of-budget worker where worker cap is exceeded
        lim = sbx.load_limits()
        lim["WORKER_DAILY_BUDGET_USD"] = f"{w}=10.0"
        lim["API_RUN_BUDGET_USD"] = 2.0
        lim["API_DAILY_BUDGET_USD"] = 100.0

        with patch.object(sbx, "load_limits", return_value=lim), \
             patch.object(sbx, "read_ledger", return_value=[]), \
             patch.object(sbx, "running_api_runs", return_value=[]), \
             patch.object(sbx, "api_spent", side_effect=lambda led, window="day", project=None, worker=None: 9.0 if worker == w else 9.0):

            # Worker has exhausted its worker-specific cap ($9 spent + $2 next run > $10 cap)
            has_budget = pool.api_budget_left(w)
            self.assertFalse(has_budget, "Worker exceeding its cap must not have budget")
            assert not has_budget

            # pool_quota_out must check worker-specific budget and report quota/budget exhausted
            quota_out = pool.pool_quota_out()
            self.assertTrue(quota_out, "Exhausted worker budget must cause pool_quota_out to return True")
            assert quota_out

            # At least one accurate reason must be surfaced by budget_check
            ok, msg = sbx.budget_check(w, lim)
            self.assertFalse(ok)
            self.assertIn(w, msg, "Accurate worker failure reason must be surfaced")
            assert not ok and w in msg

        # Invariant: has_quota floor check must strictly enforce floor (not weakened)
        rows_below_floor = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 1.0},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        self.assertFalse(pool.has_quota(rows_below_floor, "gemini", floor=2.0), "pct < floor must be rejected")
        assert not pool.has_quota(rows_below_floor, "gemini", floor=2.0)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("pool tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
