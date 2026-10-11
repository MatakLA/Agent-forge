"""Security regression tests for bin/lib/sbx.py and bin/dispatch.
Public interface: run via unittest runner or directly with python3 tests/test_sbx_security.py.
Invariants:
- Argument regexes reject shell metacharacters, control characters, and path separators.
- Path helpers reject directory traversal sequences (..) and separators, returning safe paths.
- Project configurations reject malicious TEST_CMD and GATE_IMAGE entries, failing safe.
- Dispatch preflight enforces worker naming, model, and role formats before container execution.
"""
import inspect
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "lib"))
import sbx

# Top-level smoke-import check: fail fast with clear traceback if imports fail
assert sbx is not None
assert hasattr(sbx, "PROJECT_RE")
assert hasattr(sbx, "MODEL_RE")
assert hasattr(sbx, "ROLE_RE")
assert hasattr(sbx, "WORKER_RE")
assert hasattr(sbx, "SAFE_ARG")
assert hasattr(sbx, "ks_path")
assert hasattr(sbx, "ledger_path")
assert hasattr(sbx, "run_path")
assert hasattr(sbx, "secret_path")
assert hasattr(sbx, "test_cmd")
assert hasattr(sbx, "gate_image")


class TestSbxSecurity(unittest.TestCase):
    """Regression test suite for argv injection, path traversal, and validation guards."""

    def test_project_re_rejects_shell_metacharacters(self):
        """Invariant: PROJECT_RE rejects shell metacharacters and path separators."""
        self.assertFalse(bool(sbx.PROJECT_RE.fullmatch("proj;rm -rf /")))
        assert not sbx.PROJECT_RE.fullmatch("proj;rm -rf /")
        self.assertFalse(bool(sbx.PROJECT_RE.fullmatch("../proj")))
        assert not sbx.PROJECT_RE.fullmatch("../proj")
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("playground")))
        assert sbx.PROJECT_RE.fullmatch("playground")
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
            "proj.1",
            "my.project",
            "a.b",
            "project.conf",
        ]
        for p in evil_projects:
            self.assertFalse(bool(sbx.PROJECT_RE.fullmatch(p)), f"PROJECT_RE must reject {p!r}")
            assert not sbx.PROJECT_RE.fullmatch(p)
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("agent-forge")))
        assert sbx.PROJECT_RE.fullmatch("agent-forge")
        self.assertTrue(bool(sbx.PROJECT_RE.fullmatch("p1")))
        assert sbx.PROJECT_RE.fullmatch("p1")

    def test_project_re_rejects_dots(self):
        """Invariant: PROJECT_RE disallows dot characters to prevent extension and traversal confusion."""
        self.assertNotIn(".", sbx.PROJECT_RE.pattern, "PROJECT_RE pattern must not allow dots")
        assert "." not in sbx.PROJECT_RE.pattern
        self.assertFalse(bool(sbx.PROJECT_RE.fullmatch("proj.1")), "PROJECT_RE must reject dot in project name")
        assert not sbx.PROJECT_RE.fullmatch("proj.1")
        self.assertFalse(bool(sbx.PROJECT_RE.fullmatch("my.project")), "PROJECT_RE must reject dot in project name")
        assert not sbx.PROJECT_RE.fullmatch("my.project")
        self.assertFalse(bool(sbx.PROJECT_RE.fullmatch("a.b")), "PROJECT_RE must reject dot in project name")
        assert not sbx.PROJECT_RE.fullmatch("a.b")
        for dot_proj in ["proj.1", "my.project", "a.b", "dot.test", "project.conf"]:
            self.assertFalse(bool(sbx.PROJECT_RE.fullmatch(dot_proj)))
            assert not sbx.PROJECT_RE.fullmatch(dot_proj)
            with self.assertRaises(ValueError, msg=f"ks_path({dot_proj!r}) must raise ValueError"):
                sbx.ks_path(dot_proj)
            with self.assertRaises(ValueError, msg=f"ledger_path({dot_proj!r}) must raise ValueError"):
                sbx.ledger_path(dot_proj)
            with self.assertRaises(ValueError, msg=f"project_conf({dot_proj!r}) must raise ValueError"):
                sbx.project_conf(dot_proj)
            with self.assertRaises(ValueError, msg=f"project_pool({dot_proj!r}) must raise ValueError"):
                sbx.project_pool(dot_proj)

    def test_check_project_traversal_guard_independent_of_regex(self):
        """Invariant: _check_project contains an explicit traversal guard for '..', '/', and '\\'."""
        src = inspect.getsource(sbx._check_project)
        self.assertIn('".." in p', src, "_check_project must explicitly check '..' in p")
        assert '".." in p' in src
        self.assertIn('"/" in p', src, "_check_project must explicitly check '/' in p")
        assert '"/" in p' in src
        self.assertTrue('"\\\\" in p' in src or r'"\\" in p' in src, "_check_project must explicitly check '\\' in p")
        assert ('"\\\\" in p' in src or r'"\\" in p' in src)
        with patch.object(sbx, "PROJECT_RE", re.compile(r".*")):
            raised = False
            try:
                sbx._check_project("..")
            except ValueError:
                raised = True
            self.assertTrue(raised, "_check_project must reject '..' via explicit traversal guard even if PROJECT_RE matches")
            assert raised
            for traversal in ["..", "../evil", "a/b", "a\\b", "/etc", "test..name"]:
                with self.assertRaises(ValueError, msg=f"_check_project({traversal!r}) must raise ValueError"):
                    sbx._check_project(traversal)
                with self.assertRaises(ValueError, msg=f"ks_path({traversal!r}) must raise ValueError"):
                    sbx.ks_path(traversal)
                with self.assertRaises(ValueError, msg=f"ledger_path({traversal!r}) must raise ValueError"):
                    sbx.ledger_path(traversal)
                with self.assertRaises(ValueError, msg=f"project_conf({traversal!r}) must raise ValueError"):
                    sbx.project_conf(traversal)
                with self.assertRaises(ValueError, msg=f"project_pool({traversal!r}) must raise ValueError"):
                    sbx.project_pool(traversal)

    def test_model_re_rejects_shell_metacharacters(self):
        """Invariant: MODEL_RE rejects shell metacharacters and control characters."""
        self.assertFalse(bool(sbx.MODEL_RE.fullmatch("gemini; rm -rf /")))
        assert not sbx.MODEL_RE.fullmatch("gemini; rm -rf /")
        self.assertFalse(bool(sbx.MODEL_RE.fullmatch("model$(whoami)")))
        assert not sbx.MODEL_RE.fullmatch("model$(whoami)")
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("gemini-3.8-flash-high")))
        assert sbx.MODEL_RE.fullmatch("gemini-3.8-flash-high")
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
            self.assertFalse(bool(sbx.MODEL_RE.fullmatch(m)), f"MODEL_RE must reject {m!r}")
            assert not sbx.MODEL_RE.fullmatch(m)
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("claude-sonnet-4-6")))
        assert sbx.MODEL_RE.fullmatch("claude-sonnet-4-6")
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("gpt-4o")))
        assert sbx.MODEL_RE.fullmatch("gpt-4o")
        self.assertTrue(bool(sbx.MODEL_RE.fullmatch("openai/gpt-4o")))
        assert sbx.MODEL_RE.fullmatch("openai/gpt-4o")

    def test_role_re_rejects_shell_metacharacters(self):
        """Invariant: ROLE_RE rejects shell metacharacters, path traversal, and out-of-bound lengths."""
        self.assertFalse(bool(sbx.ROLE_RE.fullmatch("role;rm -rf /")))
        assert not sbx.ROLE_RE.fullmatch("role;rm -rf /")
        self.assertFalse(bool(sbx.ROLE_RE.fullmatch("role..evil")))
        assert not sbx.ROLE_RE.fullmatch("role..evil")
        self.assertFalse(bool(sbx.ROLE_RE.fullmatch("a" * 42)))
        assert not sbx.ROLE_RE.fullmatch("a" * 42)
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("a" * 41)))
        assert sbx.ROLE_RE.fullmatch("a" * 41)
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("developer")))
        assert sbx.ROLE_RE.fullmatch("developer")
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
            "-role",
        ]
        for r in evil_roles:
            self.assertFalse(bool(sbx.ROLE_RE.fullmatch(r)), f"ROLE_RE must reject {r!r}")
            assert not sbx.ROLE_RE.fullmatch(r)
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("tester")))
        assert sbx.ROLE_RE.fullmatch("tester")
        self.assertTrue(bool(sbx.ROLE_RE.fullmatch("security-tester")))
        assert sbx.ROLE_RE.fullmatch("security-tester")

    def test_worker_re_rejects_shell_metacharacters(self):
        """Invariant: WORKER_RE only accepts worker-<digits>."""
        self.assertFalse(bool(sbx.WORKER_RE.fullmatch("worker-1; evil")))
        assert not sbx.WORKER_RE.fullmatch("worker-1; evil")
        self.assertFalse(bool(sbx.WORKER_RE.fullmatch("../worker-1")))
        assert not sbx.WORKER_RE.fullmatch("../worker-1")
        self.assertTrue(bool(sbx.WORKER_RE.fullmatch("worker-1")))
        assert sbx.WORKER_RE.fullmatch("worker-1")
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
            self.assertFalse(bool(sbx.WORKER_RE.fullmatch(w)), f"WORKER_RE must reject {w!r}")
            assert not sbx.WORKER_RE.fullmatch(w)
        self.assertTrue(bool(sbx.WORKER_RE.fullmatch("worker-42")))
        assert sbx.WORKER_RE.fullmatch("worker-42")

    def test_safe_arg_rejects_shell_metacharacters(self):
        """Invariant: SAFE_ARG rejects command separators and shell expansions."""
        self.assertFalse(bool(sbx.SAFE_ARG.fullmatch("arg;evil")))
        assert not sbx.SAFE_ARG.fullmatch("arg;evil")
        self.assertFalse(bool(sbx.SAFE_ARG.fullmatch("arg$(id)")))
        assert not sbx.SAFE_ARG.fullmatch("arg$(id)")
        self.assertTrue(bool(sbx.SAFE_ARG.fullmatch("safe-arg_123.txt")))
        assert sbx.SAFE_ARG.fullmatch("safe-arg_123.txt")
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
            self.assertFalse(bool(sbx.SAFE_ARG.fullmatch(a)), f"SAFE_ARG must reject {a!r}")
            assert not sbx.SAFE_ARG.fullmatch(a)

    def test_ks_path_rejects_path_traversal(self):
        """Invariant: ks_path rejects project ids with '..' or path separators."""
        raised = False
        try:
            sbx.ks_path("../../etc")
        except ValueError:
            raised = True
        self.assertTrue(raised)
        assert raised
        valid_path = sbx.ks_path("myproj")
        self.assertIn("projects/myproj/KILLSWITCH", valid_path)
        assert "projects/myproj/KILLSWITCH" in valid_path
        traversal_ids = ["../../etc", "../KILLSWITCH", "a/b", "a\\b", "..", "/abs", "proj.1", "my.project"]
        for p in traversal_ids:
            with self.assertRaises(ValueError, msg=f"ks_path({p!r}) should raise ValueError"):
                sbx.ks_path(p)

    def test_ledger_path_rejects_path_traversal(self):
        """Invariant: ledger_path rejects project ids with '..' or path separators and constructs project path."""
        raised = False
        try:
            sbx.ledger_path("../../etc")
        except ValueError:
            raised = True
        self.assertTrue(raised)
        assert raised
        self.assertEqual(sbx.ledger_path(), os.path.join(sbx.RUNS, "ledger.jsonl"))
        assert sbx.ledger_path() == os.path.join(sbx.RUNS, "ledger.jsonl")
        self.assertEqual(sbx.ledger_path("proj1"), os.path.join(sbx.RUNS, "projects", "proj1", "ledger.jsonl"))
        assert sbx.ledger_path("proj1") == os.path.join(sbx.RUNS, "projects", "proj1", "ledger.jsonl")
        traversal_ids = ["../../etc", "../ledger", "a/b", "a\\b", "..", "proj.1", "my.project"]
        for p in traversal_ids:
            with self.assertRaises(ValueError, msg=f"ledger_path({p!r}) should raise ValueError"):
                sbx.ledger_path(p)

    def test_run_path_and_run_dir_reject_path_traversal(self):
        """Invariant: run_path and run_dir reject run ids with '..' or path separators."""
        raised = False
        try:
            sbx.run_path("../../etc/passwd")
        except ValueError:
            raised = True
        self.assertTrue(raised)
        assert raised
        self.assertEqual(sbx.run_path("run-123"), os.path.join(sbx.RUNS, "run-123"))
        assert sbx.run_path("run-123") == os.path.join(sbx.RUNS, "run-123")
        self.assertEqual(sbx.run_dir("run-123"), os.path.join(sbx.RUNS, "run-123"))
        assert sbx.run_dir("run-123") == os.path.join(sbx.RUNS, "run-123")
        traversal_ids = ["../../etc/passwd", "../evil", "run/sub", "run\\sub", ".."]
        for rid in traversal_ids:
            with self.assertRaises(ValueError, msg=f"run_path({rid!r}) should raise ValueError"):
                sbx.run_path(rid)
            with self.assertRaises(ValueError, msg=f"run_dir({rid!r}) should raise ValueError"):
                sbx.run_dir(rid)

    def test_secret_path_rejects_path_traversal(self):
        """Invariant: secret_path rejects secret names with '..' or path separators."""
        raised = False
        try:
            sbx.secret_path("../../etc/shadow")
        except ValueError:
            raised = True
        self.assertTrue(raised)
        assert raised
        self.assertEqual(sbx.secret_path("token"), os.path.join(sbx.SECRETS, "token"))
        assert sbx.secret_path("token") == os.path.join(sbx.SECRETS, "token")
        traversal_names = ["../../etc/shadow", "../evil", "token/sub", "token\\sub", ".."]
        for s in traversal_names:
            with self.assertRaises(ValueError, msg=f"secret_path({s!r}) should raise ValueError"):
                sbx.secret_path(s)

    def test_project_conf_and_pool_reject_path_traversal(self):
        """Invariant: project_conf and project_pool reject traversal project names."""
        raised_conf = False
        try:
            sbx.project_conf("../../etc")
        except ValueError:
            raised_conf = True
        self.assertTrue(raised_conf)
        assert raised_conf
        raised_pool = False
        try:
            sbx.project_pool("../../etc")
        except ValueError:
            raised_pool = True
        self.assertTrue(raised_pool)
        assert raised_pool
        for p in ["../../etc", "a/b", "..", "proj.1", "my.project"]:
            with self.assertRaises(ValueError):
                sbx.project_conf(p)
            with self.assertRaises(ValueError):
                sbx.project_pool(p)

    def test_test_cmd_rejects_malicious_conf(self):
        """Invariant: test_cmd rejects project.conf TEST_CMD with shell syntax."""
        with patch.object(sbx, "project_conf", return_value={"TEST_CMD": "pytest; rm -rf /"}):
            raised = False
            try:
                sbx.test_cmd()
            except ValueError:
                raised = True
        self.assertTrue(raised)
        assert raised
        with patch.object(sbx, "project_conf", return_value={"TEST_CMD": "pytest -q"}):
            cmd_val = sbx.test_cmd()
        self.assertEqual(cmd_val, "pytest -q")
        assert cmd_val == "pytest -q"
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
        with patch.object(sbx, "project_conf", return_value={"GATE_IMAGE": "sandbox-worker:1; rm -rf /"}):
            raised = False
            try:
                sbx.gate_image()
            except ValueError:
                raised = True
        self.assertTrue(raised)
        assert raised
        with patch.object(sbx, "project_conf", return_value={"GATE_IMAGE": "sandbox-worker:1.2.11"}):
            img_val = sbx.gate_image()
        self.assertEqual(img_val, "sandbox-worker:1.2.11")
        assert img_val == "sandbox-worker:1.2.11"
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
        self.assertFalse(bool(sbx.WORKER_RE.fullmatch("worker-1; evil")))
        assert not sbx.WORKER_RE.fullmatch("worker-1; evil")
        self.assertTrue(bool(sbx.WORKER_RE.fullmatch("worker-1")))
        assert sbx.WORKER_RE.fullmatch("worker-1")
        dispatch_bin = os.path.join(sbx.ROOT, "bin", "dispatch")
        r = subprocess.run([sys.executable, dispatch_bin, "--check", "worker-1; evil"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, "bin/dispatch --check must exit 2 on invalid worker name")
        assert r.returncode == 2
        self.assertIn("invalid worker name", r.stderr)
        assert "invalid worker name" in r.stderr

    def test_dispatch_rejects_malicious_model_env(self):
        """Invariant: bin/dispatch rejects DISPATCH_MODEL containing shell metacharacters."""
        self.assertFalse(bool(sbx.MODEL_RE.fullmatch("model; rm -rf /")))
        assert not sbx.MODEL_RE.fullmatch("model; rm -rf /")
        self.assertFalse(bool(sbx.ROLE_RE.fullmatch("role; evil")))
        assert not sbx.ROLE_RE.fullmatch("role; evil")
        dispatch_bin = os.path.join(sbx.ROOT, "bin", "dispatch")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tf:
            tf.write("# Task test\nSome content\n")
            dummy_task = tf.name
        try:
            env = dict(os.environ, DISPATCH_MODEL="model; rm -rf /", SANDBOX_TEST="1")
            r = subprocess.run([sys.executable, dispatch_bin, "worker-1", dummy_task],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 3, "bin/dispatch must refuse malformed DISPATCH_MODEL with exit code 3")
            self.assertIn("REFUSED: invalid DISPATCH_MODEL", r.stderr)
        finally:
            if os.path.exists(dummy_task):
                os.remove(dummy_task)
        assert r.returncode == 3
        assert "REFUSED: invalid DISPATCH_MODEL" in r.stderr


if __name__ == "__main__":
    unittest.main()
