"""Security regression tests for bin/lib/sbx.py and bin/dispatch.
Public test cases asserting regex validation of fields, rejection of path traversal
in path-building helpers, safe rejection of malicious TEST_CMD / GATE_IMAGE,
and prevention of command/argv injection. Invariants: no shell=True, no traversal.
"""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "lib"))
import sbx


class TestSbxSecurity(unittest.TestCase):
    """Regression test suite for argv injection, path traversal, and validation guards."""

    def test_project_re_rejects_shell_metacharacters(self):
        """Invariant: PROJECT_RE rejects shell metacharacters and path separators."""
        evil_projects = [
            "proj;rm -rf /",
            "proj$(whoami)",
            "proj`id`",
            "proj|evil",
            "proj&evil",
            "proj>out",
            "proj<in",
            "../proj",
            "proj/sub",
            "proj\\win",
            "proj space",
            "proj\nline",
        ]
        for p in evil_projects:
            self.assertFalse(
                bool(sbx.PROJECT_RE.fullmatch(p)),
                f"PROJECT_RE should have rejected crafted project: {p!r}"
            )
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("playground")))
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("agent-forge")))
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("p1")))

    def test_model_re_rejects_shell_metacharacters(self):
        """Invariant: MODEL_RE rejects shell metacharacters and control characters."""
        evil_models = [
            "gemini; rm -rf /",
            "model$(whoami)",
            "model`id`",
            "model|cat /etc/passwd",
            "model&",
            "model>out",
            "model<in",
            "model\nevil",
            "model\tevil",
            "model space",
            "\"model\"",
            "'model'",
        ]
        for m in evil_models:
            self.assertFalse(
                bool(sbx.MODEL_RE.fullmatch(m)),
                f"MODEL_RE should have rejected crafted model: {m!r}"
            )
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("gemini-3.8-flash-high")))
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("claude-sonnet-4-6")))
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("gpt-4o")))
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("openai/gpt-4o")))
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("qwen3-coder:30b")))

    def test_role_re_rejects_shell_metacharacters(self):
        """Invariant: ROLE_RE rejects shell metacharacters and path traversal."""
        evil_roles = [
            "role;rm -rf /",
            "role$(whoami)",
            "role`id`",
            "role|evil",
            "role&",
            "role/developer",
            "role..evil",
            "role space",
            "role\n",
        ]
        for r in evil_roles:
            self.assertFalse(
                bool(sbx.ROLE_RE.fullmatch(r)),
                f"ROLE_RE should have rejected crafted role: {r!r}"
            )
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("developer")))
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("tester")))
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("security-tester")))
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("backend-developer")))

    def test_worker_re_rejects_shell_metacharacters(self):
        """Invariant: WORKER_RE only accepts worker-<digits>."""
        evil_workers = [
            "worker-1; evil",
            "worker-$(whoami)",
            "worker-1|evil",
            "worker-1`id`",
            "worker-",
            "worker-abc",
            "../worker-1",
            "worker-1/sub",
        ]
        for w in evil_workers:
            self.assertFalse(
                bool(sbx.WORKER_RE.fullmatch(w)),
                f"WORKER_RE should have rejected crafted worker: {w!r}"
            )
        self.assertTrue(bool(sbx.WORKER_RE.fullmatch("worker-1")))
        self.assertTrue(bool(sbx.WORKER_RE.fullmatch("worker-42")))

    def test_safe_arg_rejects_shell_metacharacters(self):
        """Invariant: SAFE_ARG rejects command separators and shell expansions."""
        evil_args = [
            "arg;evil",
            "arg$(id)",
            "arg`id`",
            "arg|evil",
            "arg&",
            "arg>out",
            "arg<in",
            "arg'quote",
            "arg\"quote",
            "arg\nnewline",
        ]
        for a in evil_args:
            self.assertFalse(
                bool(sbx.SAFE_ARG.fullmatch(a)),
                f"SAFE_ARG should have rejected shell argument: {a!r}"
            )

    def test_ks_path_rejects_path_traversal(self):
        """Invariant: ks_path rejects project ids with '..' or path separators."""
        traversal_ids = ["../../etc", "../KILLSWITCH", "a/b", "a\\b", "..", "/abs"]
        for p in traversal_ids:
            with self.assertRaises(ValueError, msg=f"ks_path({p!r}) should raise ValueError"):
                sbx.ks_path(p)

    def test_ledger_path_rejects_path_traversal(self):
        """Invariant: ledger_path rejects project ids with '..' or path separators."""
        traversal_ids = ["../../etc", "../ledger", "a/b", "a\\b", ".."]
        for p in traversal_ids:
            with self.assertRaises(ValueError, msg=f"ledger_path({p!r}) should raise ValueError"):
                sbx.ledger_path(p)

    def test_run_path_and_run_dir_reject_path_traversal(self):
        """Invariant: run_path and run_dir reject run ids with '..' or path separators."""
        traversal_ids = ["../../etc/passwd", "../evil", "run/sub", "run\\sub", ".."]
        for rid in traversal_ids:
            with self.assertRaises(ValueError, msg=f"run_path({rid!r}) should raise ValueError"):
                sbx.run_path(rid)
            with self.assertRaises(ValueError, msg=f"run_dir({rid!r}) should raise ValueError"):
                sbx.run_dir(rid)

    def test_secret_path_rejects_path_traversal(self):
        """Invariant: secret_path rejects secret names with '..' or path separators."""
        traversal_names = ["../../etc/shadow", "../evil", "token/sub", "token\\sub", ".."]
        for s in traversal_names:
            with self.assertRaises(ValueError, msg=f"secret_path({s!r}) should raise ValueError"):
                sbx.secret_path(s)

    def test_project_conf_and_pool_reject_path_traversal(self):
        """Invariant: project_conf and project_pool reject traversal project names."""
        for p in ["../../etc", "a/b", ".."]:
            with self.assertRaises(ValueError):
                sbx.project_conf(p)
            with self.assertRaises(ValueError):
                sbx.project_pool(p)

    def test_test_cmd_rejects_malicious_conf(self):
        """Invariant: test_cmd rejects project.conf TEST_CMD with shell syntax."""
        evil_cmds = [
            "pytest; rm -rf /",
            "pytest && curl evil.com",
            "pytest | cat",
            "pytest $(whoami)",
            "pytest `id`",
            "pytest > /tmp/out",
        ]
        for cmd in evil_cmds:
            with patch.object(sbx, "project_conf", return_value={"TEST_CMD": cmd}):
                with self.assertRaises(ValueError, msg=f"test_cmd with {cmd!r} should raise ValueError"):
                    sbx.test_cmd()

    def test_gate_image_rejects_malicious_conf(self):
        """Invariant: gate_image rejects project.conf GATE_IMAGE with shell syntax."""
        evil_images = [
            "sandbox-worker:1; rm -rf /",
            "sandbox-worker:$(id)",
            "sandbox-worker:`whoami`",
            "image|evil",
            "image space",
        ]
        for img in evil_images:
            with patch.object(sbx, "project_conf", return_value={"GATE_IMAGE": img}):
                with self.assertRaises(ValueError, msg=f"gate_image with {img!r} should raise ValueError"):
                    sbx.gate_image()

    def test_dispatch_check_rejects_invalid_worker(self):
        """Invariant: bin/dispatch --check rejects invalid worker names."""
        dispatch_bin = os.path.join(sbx.ROOT, "bin", "dispatch")
        r = subprocess.run([sys.executable, dispatch_bin, "--check", "worker-1; evil"],
                           capture_output=True, text=True)
        self.assertIn(r.returncode, (2, 3), "bin/dispatch --check must reject invalid worker name")

    def test_dispatch_rejects_malicious_model_env(self):
        """Invariant: bin/dispatch rejects DISPATCH_MODEL containing shell metacharacters."""
        dispatch_bin = os.path.join(sbx.ROOT, "bin", "dispatch")
        dummy_task = os.path.join(sbx.ROOT, "tests", "fixtures", "dummy-task.md")
        os.makedirs(os.path.dirname(dummy_task), exist_ok=True)
        with open(dummy_task, "w") as f:
            f.write("# Task test\nSome content\n")
        try:
            env = dict(os.environ, DISPATCH_MODEL="model; rm -rf /", SANDBOX_TEST="1")
            r = subprocess.run([sys.executable, dispatch_bin, "worker-1", dummy_task],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 3, "bin/dispatch must refuse malformed DISPATCH_MODEL with exit code 3")
            self.assertIn("REFUSED", r.stderr)
        finally:
            if os.path.exists(dummy_task):
                os.remove(dummy_task)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout)
    result = unittest.main(testRunner=runner, exit=False)
    if result.result.wasSuccessful():
        print("sbx security tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
