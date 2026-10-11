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

import sbx
from foreman import ctx, pool


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
        # Clear ledger and cooldowns before each test
        ledger_path = os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl")
        if os.path.exists(ledger_path):
            os.remove(ledger_path)
        pool.COOLDOWN.clear()
        pool.QUOTA_CACHE.clear()
        pool.WORKER_RT.clear()
        ctx.C["PROJECT_DAILY_BUDGET_USD"] = 0.0
        ctx.C["PROJECT_MONTHLY_BUDGET_USD"] = 0.0

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
        self.assertFalse(
            pool.api_budget_left("worker-1"),
            "Worker under its own cap but over the project cap must be refused",
        )

        # Case 2: Project cap has headroom ($5.0 cap, $0 spent),
        # but worker-1 has spent $9.0 (next run $2.0 -> $11.0 > $10.0 worker cap).
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self._append_ledger(9.0, worker="worker-1", project="")
        self.assertFalse(
            pool.api_budget_left("worker-1"),
            "Worker exceeding its own worker cap must be refused",
        )

        # Case 3: Project cap and worker cap have headroom,
        # but install-wide cap ($100.0) is exceeded ($99.0 spent).
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self._append_ledger(99.0, worker="worker-other", project="other-project")
        self.assertFalse(
            pool.api_budget_left("worker-1"),
            "Worker must be refused when install-wide cap is exceeded",
        )

        # Case 4: All caps satisfied: project ($0 spent), worker ($0 spent), install ($0 spent).
        os.remove(os.path.join(os.environ["SANDBOX_RUNS_DIR"], "ledger.jsonl"))
        self.assertTrue(
            pool.api_budget_left("worker-1"),
            "Worker must be allowed when all applicable caps have headroom",
        )

    def test_has_quota_boundary_at_exact_floor(self):
        """has_quota must allow a worker at exactly floor% remaining (boundary: >= vs >)."""
        floor = 2.0
        # Exactly at floor% (2.0%) remaining in 5-hour window -> quota meets floor, allowed
        rows_at_floor = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 2.0},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        self.assertTrue(
            pool.has_quota(rows_at_floor, "gemini", floor=floor),
            "Exactly floor% remaining must count as having quota (not exhausted)",
        )

        # Below floor% (1.9%) remaining in 5-hour window -> exhausted, refused
        rows_below_floor = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 1.9},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        self.assertFalse(
            pool.has_quota(rows_below_floor, "gemini", floor=floor),
            "Below floor% remaining must count as exhausted",
        )

    def test_has_quota_unknown_quota_fails_open(self):
        """Unknown quota (family absent from rows or empty rows) fails open (allowed)."""
        # Family entirely absent from rows (only Gemini reported, checking Claude)
        rows_gemini_only = [
            {"model": "Gemini Models", "label": "Five Hour Limit Remaining", "pct": 50.0},
            {"model": "Gemini Models", "label": "Weekly Limit Remaining", "pct": 50.0},
        ]
        self.assertTrue(
            pool.has_quota(rows_gemini_only, "claude"),
            "Absent model family must fail open (allowed)",
        )

        # Entirely empty rows
        self.assertTrue(
            pool.has_quota([], "claude"),
            "Empty quota rows must fail open (allowed)",
        )

    def test_pool_quota_out_cooldown_vs_exhaustion(self):
        """A pool where every worker is merely in a short cooldown must not be reported as quota-out."""
        pool.workers = lambda: ["worker-1"]
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
        self.assertFalse(
            pool.pool_quota_out(),
            "Worker with healthy quota must not report pool quota-out even if in cooldown",
        )

        # Case 2: Worker in a short cooldown (<= 300s) even with quota low and unreadable/empty rows
        pool.QUOTA_CACHE["worker-1"] = (now, [])
        pool.COOLDOWN["worker-1"] = (now + 60, "quota low: brief 60s pause")
        self.assertFalse(
            pool.pool_quota_out(),
            "Short cooldown (<= 300s) must not be reported as pool quota-out",
        )

        # Case 3: Worker in cooldown for non-quota reason (e.g. rate limit, container restart)
        pool.QUOTA_CACHE["worker-1"] = (now, [])
        pool.COOLDOWN["worker-1"] = (now + 600, "rate limit: cooling down")
        self.assertFalse(
            pool.pool_quota_out(),
            "Non-quota cooldown must not be reported as pool quota-out",
        )

        # Case 4: True exhaustion: long cooldown (> 300s), quota low reason, exhausted rows
        pool.QUOTA_CACHE["worker-1"] = (now, exhausted_rows)
        pool.COOLDOWN["worker-1"] = (now + 1800, "quota low: weekly limit exhausted")
        self.assertTrue(
            pool.pool_quota_out(),
            "True exhaustion (> 300s with low quota) must be reported as quota-out",
        )

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
            "On tie, least-recently-used worker (smaller timestamp) must come first",
        )

        # Worker never used (not in last_used -> 0) comes before worker with positive timestamp
        last_used_partial = {"worker-1": 2000}
        ordered_partial = pool.order_by_quota(["worker-1", "worker-2"], pct_of, last_used_partial)
        self.assertEqual(
            ordered_partial,
            ["worker-2", "worker-1"],
            "Worker never used (0) must precede previously used worker on tied quota",
        )


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestForemanPool)
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=1)
    res = runner.run(suite)
    print("pool tests: " + ("ALL OK" if res.wasSuccessful() else f"{len(res.failures) + len(res.errors)} FAILED"))
    sys.exit(0 if res.wasSuccessful() else 1)
