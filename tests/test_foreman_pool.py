#!/usr/bin/env python3
"""Tests for bin/lib/foreman/pool.py: budget and quota gates in the worker pool.

Public interface under test:
- api_budget_left(w): install, project, and worker caps enforced together (AND logic).
- has_quota(rows, family, floor): boundary handling and fail-open unknown quota.
- pool_quota_out(): distinguishing short cooldowns from true quota exhaustion.
- order_by_quota(ws, pct_of, last_used): least-recently-used tiebreaker.
"""
import json, os, shutil, sys, tempfile, time, unittest, warnings

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
        # Clear ledger and cooldowns before each test
        os.makedirs(os.environ["SANDBOX_RUNS_DIR"], exist_ok=True)
        ledger_path = os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl")
        if os.path.exists(ledger_path):
            os.remove(ledger_path)
        with open(ledger_path, "w") as f:
            pass  # Ensure ledger file exists
        if hasattr(pool, "COOLDOWN"):
            pool.COOLDOWN.clear()
        if hasattr(pool, "QUOTA_CACHE"):
            pool.QUOTA_CACHE.clear()
        if hasattr(pool, "WORKER_RT"):
            pool.WORKER_RT.clear()
        ctx.C["PROJECT_DAILY_BUDGET_USD"] = 0.0
        ctx.C["PROJECT_MONTHLY_BUDGET_USD"] = 0.0
        self._orig_workers = pool.workers
        self._orig_api_budget_left = pool.api_budget_left
        self._orig_runtime_of = pool.runtime_of

    def tearDown(self):
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
        with open(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"), "a") as f:
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
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self._append_ledger(9.0, worker="worker-1", project="")
        res2 = pool.api_budget_left("worker-1")
        self.assertFalse(
            res2,
            "Worker exceeding its own worker cap must be refused",
        )
        assert res2 is False

        # Case 3: Project cap and worker cap have headroom,
        # but install-wide cap ($100.0) is exceeded ($99.0 spent).
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self._append_ledger(99.0, worker="worker-other", project="other-project")
        res3 = pool.api_budget_left("worker-1")
        self.assertFalse(
            res3,
            "Worker must be refused when install-wide cap is exceeded",
        )
        assert res3 is False

        # Case 4: All caps satisfied: project ($0 spent), worker ($0 spent), install ($0 spent).
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
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
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
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
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self._append_ledger(9.0, worker="worker-1", project="")
        res_multi = pool.pool_quota_out()
        self.assertFalse(
            res_multi,
            "Pool with at least one eligible API worker must not report quota-out",
        )
        assert res_multi is False



if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("pool tests: ALL OK")
        sys.exit(0)
    sys.exit(1)

