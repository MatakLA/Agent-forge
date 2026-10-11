#!/usr/bin/env python3
"""Hermetic install smoke tests for fresh Agent-Forge dashboard startup.
Public interface: run directly via python3 tests/test_install_smoke.py or unittest.
Invariants:
- Uses isolated layout in temp directory with SANDBOX_TEST=1 and no real Docker.
- Verifies web/server.py starts and answers /api/status with valid JSON.
- Verifies static web assets are served at / with text/html content-type.
- Cleans up background server processes and temporary directories on tearDown.
"""
import sys
import traceback

# Top-level smoke import check: print traceback and exit non-zero on failure
try:
    import json
    import os
    import shutil
    import socket
    import subprocess
    import tempfile
    import time
    import unittest
    import urllib.error
    import urllib.request
except Exception:
    traceback.print_exc()
    sys.exit(1)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestInstallSmoke(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="af-smoke-py-")
        self.runs_dir = os.path.join(self.tmp, "runs")
        os.makedirs(os.path.join(self.tmp, "etc"), exist_ok=True)
        os.makedirs(self.runs_dir, exist_ok=True)
        os.makedirs(os.path.join(self.tmp, "bin"), exist_ok=True)

        # Copy minimal files to mimic install layout
        shutil.copytree(os.path.join(ROOT, "web"), os.path.join(self.tmp, "web"))
        shutil.copytree(os.path.join(ROOT, "bin", "lib"), os.path.join(self.tmp, "bin", "lib"))
        shutil.copy(os.path.join(ROOT, "bin", "foreman"), os.path.join(self.tmp, "bin", "foreman"))
        os.chmod(os.path.join(self.tmp, "bin", "foreman"), 0o755)

        for cfg in ("limits", "foreman", "forge"):
            ex = os.path.join(ROOT, "etc", f"{cfg}.example.conf")
            if os.path.exists(ex):
                shutil.copy(ex, os.path.join(self.tmp, "etc", f"{cfg}.conf"))
            elif os.path.exists(os.path.join(ROOT, "etc", f"{cfg}.conf")):
                shutil.copy(os.path.join(ROOT, "etc", f"{cfg}.conf"), os.path.join(self.tmp, "etc", f"{cfg}.conf"))

        tr = os.path.join(ROOT, "etc", "team-rules.example.md")
        if os.path.exists(tr):
            shutil.copy(tr, os.path.join(self.tmp, "etc", "team-rules.md"))

        self.port = _free_port()
        self.env = dict(
            os.environ,
            SANDBOX_TEST="1",
            SANDBOX_RUNS_DIR=self.runs_dir,
            FORGE_DIR=self.tmp,
            BIND_PORT=str(self.port),
            DASHBOARD_TOKEN="",
        )
        self.proc = subprocess.Popen(
            [sys.executable, "web/server.py"],
            cwd=self.tmp,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def tearDown(self):
        if hasattr(self, "proc") and self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
        if hasattr(self, "tmp") and os.path.exists(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _poll_status(self, timeout=10.0):
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Server exited early with code {self.proc.returncode}")
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/status")
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    return resp.status, dict(resp.headers), resp.read().decode("utf-8")
            except Exception as e:
                last_err = e
                time.sleep(0.15)
        raise TimeoutError(f"Server at port {self.port} did not answer within {timeout}s: {last_err}")

    def test_server_starts(self):
        """Invariant: web/server.py starts and answers /api/status with JSON containing ok or foreman."""
        status, headers, body = self._poll_status(timeout=10.0)
        self.assertEqual(status, 200)
        assert status == 200
        data = json.loads(body)
        self.assertIsInstance(data, dict)
        assert isinstance(data, dict)
        has_key = "ok" in data or "foreman" in data
        self.assertTrue(has_key)
        assert has_key is True

    def test_static_served(self):
        """Invariant: after server starts, GET / returns 200 and Content-Type: text/html."""
        self._poll_status(timeout=10.0)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
        self.assertEqual(status, 200)
        assert status == 200
        self.assertIn("text/html", ctype)
        assert "text/html" in ctype
        self.assertGreater(len(body), 0)
        assert len(body) > 0

    def test_shell_smoke_script(self):
        """Invariant: tests/test_install_smoke.sh executes cleanly and exits 0."""
        sh_path = os.path.join(ROOT, "tests", "test_install_smoke.sh")
        res = subprocess.run(["bash", sh_path], cwd=ROOT, capture_output=True, text=True, timeout=25)
        self.assertEqual(res.returncode, 0, f"Script stderr: {res.stderr}\nstdout: {res.stdout}")
        assert res.returncode == 0
        assert "ALL OK" in res.stdout

    def test_unreachable_server_rejected(self):
        """Invariant: polling an unreachable port fails rather than reporting success."""
        unused_port = _free_port()
        failed = False
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{unused_port}/api/status")
            with urllib.request.urlopen(req, timeout=0.5):
                pass
        except Exception:
            failed = True
        self.assertTrue(failed)
        assert failed is True


if __name__ == "__main__":
    unittest.main()
