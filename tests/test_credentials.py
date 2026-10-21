#!/usr/bin/env python3
"""Regression tests for credential handling security in bin/secret-set, worker/git-credential-forgejo and bin/forgejo-api.

Public interface:
- Run directly: python3 tests/test_credentials.py
- Run via unittest: python3 -m unittest tests/test_credentials.py

Important invariants:
- bin/secret-set writes secrets with mode 0600 and never echoes the secret value.
- worker/git-credential-forgejo reads tokens from files, never accepts tokens in argv,
  and suppresses tracing so tokens never appear in debug/stderr logs.
- bin/forgejo-api passes tokens in Authorization headers, rejects tokens in URL query strings,
  and never logs tokens to runs/ files.
"""
import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSecretSetCredentialHandling(unittest.TestCase):
    """Test security constraints and file permission safety in bin/secret-set."""

    def test_secret_set_creates_file_with_mode_0600_even_if_tmp_preexists_loose(self):
        """bin/secret-set must ensure mode 0600 even if a .tmp file pre-existed with loose permissions (e.g. 0666)."""
        ret = -1
        target_mode = 0
        target_exists = False
        with tempfile.TemporaryDirectory() as td:
            # Point sandbox secrets directory to temporary directory
            secret_name = "anthropic.key"
            tmp_file = os.path.join(td, secret_name + ".tmp")
            target_file = os.path.join(td, secret_name)

            # Adversarial setup: pre-create .tmp with loose permissions 0o666
            with open(tmp_file, "w") as f:
                f.write("old-garbage")
            os.chmod(tmp_file, 0o666)

            env = os.environ.copy()
            env["HOME"] = td
            env.update(SANDBOX_TEST="1", SANDBOX_RUNS_DIR=os.path.join(td, "runs"))   # the audit line must not land in this checkout
            secrets_dir = os.path.join(td, ".config", "sandbox", "secrets")
            env["SANDBOX_SECRETS"] = secrets_dir
            os.makedirs(secrets_dir, mode=0o700, exist_ok=True)
            tmp_in_secrets = os.path.join(secrets_dir, secret_name + ".tmp")
            target_in_secrets = os.path.join(secrets_dir, secret_name)

            # Pre-create .tmp inside actual secrets dir with loose 0o666 permissions
            with open(tmp_in_secrets, "w") as f:
                f.write("pre-existing-tmp")
            os.chmod(tmp_in_secrets, 0o666)

            cmd = [os.environ.get("PY", "python3"), os.path.join(ROOT, "bin", "secret-set"), secret_name]

            adversarial_secret = "test-adversarial-secret-value-9999"
            proc = subprocess.run(
                cmd,
                input=adversarial_secret,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, f"secret-set failed: {proc.stderr}")
            ret = proc.returncode
            target_exists = os.path.exists(target_in_secrets)
            if target_exists:
                target_mode = stat.S_IMODE(os.stat(target_in_secrets).st_mode)
            self.assertTrue(target_exists)
            self.assertEqual(oct(target_mode), oct(0o600))
        assert ret == 0
        assert target_exists is True
        assert target_mode == 0o600

    def test_secret_set_never_prints_secret_value(self):
        """bin/secret-set must echo only a confirmation, never the secret value itself."""
        proc_ret = -1
        has_confirmation = False
        leaked_in_stdout = True
        leaked_in_stderr = True
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["HOME"] = td
            env.update(SANDBOX_TEST="1", SANDBOX_RUNS_DIR=os.path.join(td, "runs"))   # the audit line must not land in this checkout
            secret_value = "super-confidential-token-do-not-leak-12345"
            cmd = [os.environ.get("PY", "python3"), os.path.join(ROOT, "bin", "secret-set"), "openai.key"]
            proc = subprocess.run(
                cmd,
                input=secret_value,
                capture_output=True,
                text=True,
                env=env,
            )
            proc_ret = proc.returncode
            has_confirmation = "stored openai.key" in proc.stdout
            leaked_in_stdout = secret_value in proc.stdout
            leaked_in_stderr = secret_value in proc.stderr
            self.assertEqual(proc.returncode, 0)
            self.assertIn("stored openai.key", proc.stdout)
            self.assertNotIn(secret_value, proc.stdout, "Secret value was printed to stdout!")
            self.assertNotIn(secret_value, proc.stderr, "Secret value was printed to stderr!")
            audit = open(os.path.join(td, "runs", "ALERT")).read()
            self.assertIn("secret openai.key stored", audit, "storing a secret must leave an audit line (the name only)")
            self.assertNotIn(secret_value, audit, "Secret value reached the audit log!")
        assert proc_ret == 0
        assert has_confirmation is True
        assert leaked_in_stdout is False
        assert leaked_in_stderr is False

    def test_secret_set_rejects_invalid_values_without_leakage(self):
        """bin/secret-set must reject invalid values without leaking value in error output."""
        proc_ret = -1
        leaked_in_stdout = True
        leaked_in_stderr = True
        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env["HOME"] = td
            env.update(SANDBOX_TEST="1", SANDBOX_RUNS_DIR=os.path.join(td, "runs"))   # the audit line must not land in this checkout
            invalid_secret = "token with spaces"
            cmd = [os.environ.get("PY", "python3"), os.path.join(ROOT, "bin", "secret-set"), "gemini.key"]
            proc = subprocess.run(
                cmd,
                input=invalid_secret,
                capture_output=True,
                text=True,
                env=env,
            )
            proc_ret = proc.returncode
            leaked_in_stdout = invalid_secret in proc.stdout
            leaked_in_stderr = invalid_secret in proc.stderr
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn(invalid_secret, proc.stdout)
            self.assertNotIn(invalid_secret, proc.stderr)
        assert proc_ret == 2
        assert leaked_in_stdout is False
        assert leaked_in_stderr is False


class TestGitCredentialForgejoSecurity(unittest.TestCase):
    """Test credential leakage protections in worker/git-credential-forgejo."""

    def test_git_credential_no_token_in_xtrace_or_stderr(self):
        """worker/git-credential-forgejo must not leak tokens to stderr when run under bash -x (xtrace)."""
        proc_ret = -1
        token_in_stdout = False
        token_in_stderr = True
        with tempfile.TemporaryDirectory() as td:
            dummy_token = "forgejo-test-token-secret-xyz-8888"
            token_file = os.path.join(td, "forgejo-token")
            with open(token_file, "w") as f:
                f.write(dummy_token + "\n")

            env = os.environ.copy()
            env["FORGEJO_TOKEN_FILE"] = token_file
            env["FORGEJO_USER"] = "worker-1"

            # Execute git-credential-forgejo with bash -x to simulate debug/tracing
            script_path = os.path.join(ROOT, "worker", "git-credential-forgejo")
            proc = subprocess.run(
                ["bash", "-x", script_path, "get"],
                input="protocol=http\nhost=forgejo:3000\n\n",
                capture_output=True,
                text=True,
                env=env,
            )
            proc_ret = proc.returncode
            token_in_stdout = f"password={dummy_token}" in proc.stdout
            token_in_stderr = dummy_token in proc.stderr
            self.assertEqual(proc.returncode, 0)
            self.assertIn(f"password={dummy_token}", proc.stdout)
            self.assertNotIn(dummy_token, proc.stderr, f"Token leaked in stderr trace: {proc.stderr}")
        assert proc_ret == 0
        assert token_in_stdout is True
        assert token_in_stderr is False

    def test_git_credential_tolerates_verbose_flags_without_logging_token(self):
        """worker/git-credential-forgejo must accept -v/--verbose flags without logging token."""
        all_passed = True
        with tempfile.TemporaryDirectory() as td:
            dummy_token = "forgejo-test-token-secret-verbose-7777"
            token_file = os.path.join(td, "forgejo-token")
            with open(token_file, "w") as f:
                f.write(dummy_token + "\n")

            env = os.environ.copy()
            env["FORGEJO_TOKEN_FILE"] = token_file

            script_path = os.path.join(ROOT, "worker", "git-credential-forgejo")
            for flag in ["-v", "--verbose", "-d"]:
                proc = subprocess.run(
                    ["bash", script_path, flag, "get"],
                    input="protocol=http\nhost=forgejo:3000\n\n",
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if proc.returncode != 0 or f"password={dummy_token}" not in proc.stdout or dummy_token in proc.stderr:
                    all_passed = False
                self.assertEqual(proc.returncode, 0)
                self.assertIn(f"password={dummy_token}", proc.stdout)
                self.assertNotIn(dummy_token, proc.stderr)
        assert all_passed is True

    def test_git_credential_never_accepts_token_from_argv(self):
        """worker/git-credential-forgejo must receive token from a file, never via argv."""
        proc_ret = -1
        file_token_in_stdout = False
        argv_token_leaked = True
        with tempfile.TemporaryDirectory() as td:
            file_token = "correct-file-token-1111"
            argv_token = "adversarial-argv-token-2222"
            token_file = os.path.join(td, "forgejo-token")
            with open(token_file, "w") as f:
                f.write(file_token + "\n")

            env = os.environ.copy()
            env["FORGEJO_TOKEN_FILE"] = token_file

            script_path = os.path.join(ROOT, "worker", "git-credential-forgejo")
            # Try to pass token in argv
            proc = subprocess.run(
                ["bash", script_path, "get", argv_token],
                input="protocol=http\nhost=forgejo:3000\n\n",
                capture_output=True,
                text=True,
                env=env,
            )
            proc_ret = proc.returncode
            file_token_in_stdout = f"password={file_token}" in proc.stdout
            argv_token_leaked = argv_token in proc.stdout or argv_token in proc.stderr
            self.assertEqual(proc.returncode, 0)
            self.assertIn(f"password={file_token}", proc.stdout)
            self.assertNotIn(argv_token, proc.stdout)
            self.assertNotIn(argv_token, proc.stderr)
        assert proc_ret == 0
        assert file_token_in_stdout is True
        assert argv_token_leaked is False

    def test_git_credential_only_serves_internal_forgejo_host(self):
        """worker/git-credential-forgejo must only serve credentials for internal forgejo:3000."""
        proc_ret = -1
        stdout_empty = False
        with tempfile.TemporaryDirectory() as td:
            dummy_token = "forgejo-token-host-test"
            token_file = os.path.join(td, "forgejo-token")
            with open(token_file, "w") as f:
                f.write(dummy_token + "\n")

            env = os.environ.copy()
            env["FORGEJO_TOKEN_FILE"] = token_file

            script_path = os.path.join(ROOT, "worker", "git-credential-forgejo")
            proc = subprocess.run(
                ["bash", script_path, "get"],
                input="protocol=http\nhost=external-host.com:3000\n\n",
                capture_output=True,
                text=True,
                env=env,
            )
            proc_ret = proc.returncode
            stdout_empty = (proc.stdout.strip() == "")
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout.strip(), "")
        assert proc_ret == 0
        assert stdout_empty is True


class TestForgejoApiSecurity(unittest.TestCase):
    """Test token protections in bin/forgejo-api."""

    def test_forgejo_api_rejects_token_in_url_query_string(self):
        """bin/forgejo-api must reject requests attempting to pass tokens in URL query strings."""
        script_path = os.path.join(ROOT, "bin", "forgejo-api")
        env = os.environ.copy()
        env["FORGE_REPO"] = "sandbox/playground"
        bad_urls = [
            "/repos/sandbox/playground/pulls?token=leak_token_123",
            "/repos/sandbox/playground/pulls?access_token=leak_token_456",
            "/repos/sandbox/playground/pulls?state=open&secret=leak_token_789",
        ]
        all_rejected = True
        for url in bad_urls:
            proc = subprocess.run(
                [os.environ.get("PY", "python3"), script_path, "GET", url],
                capture_output=True,
                text=True,
                env=env,
            )
            if proc.returncode != 2 or "tokens must not be passed in URL query strings" not in proc.stderr:
                all_rejected = False
            self.assertEqual(proc.returncode, 2, f"Expected rejection for {url}")
            self.assertIn("tokens must not be passed in URL query strings", proc.stderr)
        assert all_rejected is True

    def test_forgejo_api_source_code_uses_authorization_header_not_query(self):
        """bin/forgejo-api must use Authorization header and not query string for authentication."""
        with open(os.path.join(ROOT, "bin", "forgejo-api"), "r", encoding="utf-8") as f:
            api_script = f.read()
        has_auth_header = 'headers={"Authorization": "token " + tok}' in api_script
        has_query_token = "?token=" in api_script
        has_query_access_token = "?access_token=" in api_script
        self.assertIn('headers={"Authorization": "token " + tok}', api_script)
        self.assertNotIn("?token=", api_script)
        self.assertNotIn("?access_token=", api_script)
        assert has_auth_header is True
        assert has_query_token is False
        assert has_query_access_token is False

    def test_runs_files_do_not_contain_secrets(self):
        """No secret tokens or private keys must be written to files under runs/."""
        runs_dir = os.path.join(ROOT, "runs")
        leak_count = 0
        if os.path.isdir(runs_dir):
            token_patterns = [
                re.compile(r"sk-" + r"[a-zA-Z0-9]{20,}"),
                re.compile(r"ghp_" + r"[a-zA-Z0-9]{20,}"),
                re.compile(r"glpat-" + r"[a-zA-Z0-9]{20,}"),
            ]
            for root, _, files in os.walk(runs_dir):
                for file in files:
                    filepath = os.path.join(root, file)
                    if os.path.islink(filepath) or file.endswith(".lock"):
                        continue
                    try:
                        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        for pattern in token_patterns:
                            if pattern.search(content):
                                leak_count += 1
                                self.assertIsNone(
                                    pattern.search(content),
                                    f"Potential token leak found in {filepath}",
                                )
                    except (PermissionError, IsADirectoryError):
                        continue
        assert leak_count == 0


if __name__ == "__main__":
    unittest.main()
