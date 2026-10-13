#!/usr/bin/env python3
"""Tests for per-runtime cost/quota classifier cost_model() in bin/lib/runtimes.py.

Public interface:
- Run directly: python3 tests/test_runtime_cost_model.py
- Run via unittest: python3 -m unittest tests/test_runtime_cost_model.py

Important invariants:
- cost_model(rt) returns a dict with exactly two keys: 'kind' and 'detail'.
- 'kind' is one of {"metered", "quota", "unknown"}.
- 'detail' is a non-empty human-readable explanation.
- opencode, qwen-code, goose, and anthropic-compat return kind == 'unknown',
  each with a distinct detail string naming that specific runtime.
- agy returns kind == 'quota' (subscription tracked via agy /usage).
- claude, codex, gemini, compat, aider return kind == 'metered' (API token billing).
- Unknown runtimes not in RUNTIMES raise ValueError.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

import runtimes


class TestRuntimeCostModel(unittest.TestCase):
    """Unit tests for cost_model() classifier."""

    def test_all_runtimes_covered_and_dict_shape(self):
        """Every key in RUNTIMES must return a dict with exactly {'kind', 'detail'}."""
        self.assertGreater(len(runtimes.RUNTIMES), 0)
        valid_kinds = {"metered", "quota", "unknown"}
        for rt in runtimes.RUNTIMES:
            res = runtimes.cost_model(rt)
            self.assertIsInstance(res, dict, f"cost_model({rt!r}) must return a dict")
            self.assertEqual(
                set(res.keys()),
                {"kind", "detail"},
                f"cost_model({rt!r}) must return exactly keys ('kind', 'detail'), got {set(res.keys())}",
            )
            self.assertIn(
                res["kind"],
                valid_kinds,
                f"cost_model({rt!r}) kind must be in {valid_kinds}, got {res['kind']!r}",
            )
            self.assertIsInstance(res["detail"], str, f"cost_model({rt!r}) detail must be a string")
            self.assertTrue(len(res["detail"].strip()) > 0, f"cost_model({rt!r}) detail must be non-empty")

    def test_exact_kind_for_every_runtime(self):
        """Every RUNTIMES key must assert its exact kind in aggregate."""
        expected_kinds = {
            "agy": "quota",
            "claude": "metered",
            "codex": "metered",
            "gemini": "metered",
            "compat": "metered",
            "aider": "metered",
            "opencode": "unknown",
            "goose": "unknown",
            "qwen-code": "unknown",
            "anthropic-compat": "unknown",
        }
        self.assertEqual(set(expected_kinds.keys()), set(runtimes.RUNTIMES.keys()))
        for rt, expected_kind in expected_kinds.items():
            res = runtimes.cost_model(rt)
            self.assertEqual(
                res["kind"],
                expected_kind,
                f"cost_model({rt!r}) expected kind {expected_kind!r}, got {res['kind']!r}",
            )

    # Individual unit tests for each RUNTIMES key asserting its exact kind
    def test_kind_agy(self):
        res = runtimes.cost_model("agy")
        self.assertEqual(res["kind"], "quota")

    def test_kind_claude(self):
        res = runtimes.cost_model("claude")
        self.assertEqual(res["kind"], "metered")

    def test_kind_codex(self):
        res = runtimes.cost_model("codex")
        self.assertEqual(res["kind"], "metered")

    def test_kind_gemini(self):
        res = runtimes.cost_model("gemini")
        self.assertEqual(res["kind"], "metered")

    def test_kind_compat(self):
        res = runtimes.cost_model("compat")
        self.assertEqual(res["kind"], "metered")

    def test_kind_aider(self):
        res = runtimes.cost_model("aider")
        self.assertEqual(res["kind"], "metered")

    def test_kind_opencode(self):
        res = runtimes.cost_model("opencode")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_goose(self):
        res = runtimes.cost_model("goose")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_qwen_code(self):
        res = runtimes.cost_model("qwen-code")
        self.assertEqual(res["kind"], "unknown")

    def test_kind_anthropic_compat(self):
        res = runtimes.cost_model("anthropic-compat")
        self.assertEqual(res["kind"], "unknown")

    def test_unknown_runtimes_distinct_details_and_naming(self):
        """opencode, qwen-code, goose, and anthropic-compat return unknown with distinct detail naming the runtime."""
        unknown_rts = ["opencode", "qwen-code", "goose", "anthropic-compat"]
        details = {}
        for rt in unknown_rts:
            res = runtimes.cost_model(rt)
            self.assertEqual(res["kind"], "unknown")
            detail = res["detail"]
            self.assertNotIn(detail, details.values(), f"Detail for {rt!r} must be distinct from other unknown runtimes")
            details[rt] = detail

        # Each detail string must name that specific runtime
        self.assertIn("opencode", details["opencode"].lower())
        self.assertTrue(
            "qwen-code" in details["qwen-code"].lower() or "qwen code" in details["qwen-code"].lower(),
            f"Detail for 'qwen-code' must name the runtime: {details['qwen-code']!r}",
        )
        self.assertIn("goose", details["goose"].lower())
        self.assertIn("anthropic-compat", details["anthropic-compat"].lower())

    def test_quota_runtime_detail(self):
        """agy detail explains tracking via agy /usage."""
        res = runtimes.cost_model("agy")
        self.assertEqual(res["kind"], "quota")
        self.assertIn("agy /usage", res["detail"])

    def test_metered_runtime_detail(self):
        """Metered runtimes explain per-token billing."""
        for rt in ("claude", "codex", "gemini", "compat", "aider"):
            res = runtimes.cost_model(rt)
            self.assertEqual(res["kind"], "metered")
            self.assertTrue(
                "token" in res["detail"].lower() or "money" in res["detail"].lower(),
                f"Detail for {rt!r} should explain per-token billing: {res['detail']!r}",
            )

    def test_result_immutability(self):
        """Mutating a returned dict must not mutate subsequent calls."""
        res1 = runtimes.cost_model("claude")
        res1["kind"] = "mutated"
        res1["extra"] = True
        res2 = runtimes.cost_model("claude")
        self.assertEqual(res2["kind"], "metered")
        self.assertNotIn("extra", res2)

    def test_unknown_runtime_raises_value_error(self):
        """cost_model() raises ValueError for runtime names not in RUNTIMES."""
        with self.assertRaises(ValueError):
            runtimes.cost_model("bogus-runtime")
        with self.assertRaises(ValueError):
            runtimes.cost_model("")
        with self.assertRaises(ValueError):
            runtimes.cost_model(" ")
        with self.assertRaises(ValueError):
            runtimes.cost_model(" agy")
        with self.assertRaises(ValueError):
            runtimes.cost_model(None)
        with self.assertRaises(ValueError):
            runtimes.cost_model("unknown")
        with self.assertRaises(ValueError):
            runtimes.cost_model(123)


if __name__ == "__main__":
    unittest.main()
