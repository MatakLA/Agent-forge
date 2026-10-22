#!/usr/bin/env python3
"""Docker-free tests of bin/review-branch (the gate): a gate that breaks must say so, never look like a pass.

Python ends an uncaught exception with exit 1, and exit 1 is what the gate uses for "warnings" - the foreman lets those through to
the review and the merge. So every crash, timeout or missing Docker has to end as exit 3 (the gate broke) or exit 2 (red), and the
things that used to crash it (a deleted test file, a test file with a syntax error, a diff that is not UTF-8) must pass through.
A local bare repo stands in for the forge and a tiny shell script stands in for `docker`, so this needs neither Docker nor a model.
Run: python3 tests/test_review_branch_failclosed.py"""
import os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.environ.get("REVIEW_BRANCH") or os.path.join(ROOT, "bin", "review-branch")      # REVIEW_BRANCH: point at another copy (to prove a test catches the old bug)
GIT = shutil.which("git")
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
[ "$1" = run ] || exit 0
case "$*" in
  */mutate.py*)
    case "$FAKE_MUTATE" in
      killed) echo '{"name": "m1", "result": "KILLED", "tail": "1 failed"}'; exit 0 ;;
      survived) echo '{"name": "m1", "result": "SURVIVED", "tail": "1 passed"}'; exit 0 ;;
      partial) echo '{"name": "m1", "result": "KILLED", "tail": "1 failed"}'; echo "container broke" >&2; exit 1 ;;
      *) echo "container broke" >&2; exit 1 ;;
    esac ;;
esac
case "$FAKE_TESTS" in
  fail) echo "1 failed, 0 passed in 0.01s"; exit 1 ;;
  hang) exec sleep 30 ;;
  daemon) echo "docker: Cannot connect to the Docker daemon at unix:///var/run/docker.sock." >&2; exit 125 ;;
  *) echo "1 passed in 0.01s"; exit 0 ;;
esac
"""


GREEN = {"good.py": '"""Good."""\n\ndef double(x):\n    return x * 2\n',
         "test_good.py": "import good\n\ndef test_double():\n    assert good.double(2) == 4\n",
         "util.py": '"""Util."""\nimport good\n\ndef clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n\ndef twice(x):\n    return good.double(x)\n'}
ALLOWED = "good.py,test_good.py,util.py"


class TestReviewBranchFailClosed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="af-test-gate-")
        cls.bare = os.path.join(cls.tmp, "forge", "sandbox", "playground.git")
        cls.work = os.path.join(cls.tmp, "w")
        cls.secrets = os.path.join(cls.tmp, "secrets")
        cls.fake = os.path.join(cls.tmp, "fakebin")
        cls.only_git = os.path.join(cls.tmp, "onlygit")
        cls.scratch = os.path.join(cls.tmp, "scratch")          # TMPDIR of the gate: nothing may stay in it
        for d in (cls.secrets, cls.fake, cls.only_git, cls.scratch, cls.work, os.path.dirname(cls.bare)):
            os.makedirs(d, exist_ok=True)
        open(os.path.join(cls.secrets, "monitor.token"), "w").write("t\n")
        with open(os.path.join(cls.fake, "docker"), "w") as f:
            f.write(FAKE_DOCKER)
        os.chmod(os.path.join(cls.fake, "docker"), 0o755)
        os.symlink(GIT, os.path.join(cls.only_git, "git"))
        cls.env = dict(os.environ, GIT_AUTHOR_NAME="worker-1", GIT_AUTHOR_EMAIL="w@w", GIT_COMMITTER_NAME="worker-1", GIT_COMMITTER_EMAIL="w@w",
                       GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        cls.git("init", "-q", "--bare", "-b", "main", cls.bare, cwd=cls.tmp)
        cls.git("init", "-q", "-b", "main", cwd=cls.work)
        cls.put("util.py", '"""Util."""\n\ndef clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n')
        cls.put("test_util.py", "import util\n\ndef test_clamp():\n    assert util.clamp(5, 0, 3) == 3\n")
        cls.git("add", "-A", cwd=cls.work)
        cls.git("commit", "-qm", "base", cwd=cls.work)
        cls.git("remote", "add", "origin", cls.bare, cwd=cls.work)
        cls.git("push", "-q", "origin", "main", cwd=cls.work)
        cls.branch("feat/green", GREEN)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def git(cls, *args, cwd):
        r = subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, env=cls.env)
        if r.returncode:
            raise RuntimeError(f"git {args}: {r.stderr}")
        return r.stdout

    @classmethod
    def put(cls, name, content):
        with open(os.path.join(cls.work, name), "wb" if isinstance(content, bytes) else "w") as f:
            f.write(content)

    @classmethod
    def branch(cls, name, changes, removes=()):
        cls.git("checkout", "-q", "-f", "-B", name, "main", cwd=cls.work)
        cls.git("clean", "-fdq", cwd=cls.work)
        for n, c in changes.items():
            cls.put(n, c)
        for n in removes:
            cls.git("rm", "-q", n, cwd=cls.work)
        cls.git("add", "-A", cwd=cls.work)
        cls.git("commit", "-qm", name, cwd=cls.work)
        cls.git("push", "-q", "origin", name, cwd=cls.work)

    def gate(self, branch, *args, path=None, **extra):
        """Run the gate on `branch`; returns (exit code, output). `path` replaces PATH (default: the fake docker first)."""
        log = os.path.join(self.tmp, "docker.log")
        open(log, "w").close()
        env = dict(self.env, FORGE_URL="file://" + os.path.join(self.tmp, "forge"), FORGE_REPO="sandbox/playground", SANDBOX_SECRETS=self.secrets,
                   TMPDIR=self.scratch, FAKE_DOCKER_LOG=log, PATH=path or (self.fake + os.pathsep + os.environ["PATH"]))
        env.update({k: v for k, v in extra.items()})
        r = subprocess.run([sys.executable, GATE, branch, *args], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(os.listdir(self.scratch), [], "the gate left its working copy behind")
        self.docker_log = open(log).read()
        return r.returncode, r.stdout + r.stderr

    def test_deleted_test_file_is_a_warning_not_a_crash(self):
        self.branch("feat/deleted-test", {}, removes=["test_util.py"])
        rc, out = self.gate("feat/deleted-test")
        self.assertNotIn("GATE ERROR", out)
        self.assertEqual(rc, 1, out)
        self.assertIn("file deleted/renamed/copied", out)

    def test_test_file_with_a_syntax_error_is_a_warning_not_a_crash(self):
        self.branch("feat/syntax-test", {"test_broken.py": "def test_x(:\n    assert True\n"})
        rc, out = self.gate("feat/syntax-test")
        self.assertNotIn("GATE ERROR", out)
        self.assertEqual(rc, 1, out)
        self.assertIn("test_broken.py: is not valid Python (SyntaxError)", out)
        self.assertRegex(out, r"== FLAGS: .*unparsable")

    def test_a_diff_that_is_not_utf8_does_not_crash_the_gate(self):
        self.branch("feat/latin1", {"notes.csv": b"name\ncaf\xe9\n", "other.py": '"""Other."""\nX = "caf\xe9"\n'.encode("latin-1")})
        rc, out = self.gate("feat/latin1")
        self.assertNotIn("GATE ERROR", out)
        self.assertIn(rc, (0, 1), out)
        self.assertIn("== warnings:", out)

    def test_a_clean_branch_is_exit_0_and_a_red_run_is_exit_2(self):
        rc, out = self.gate("feat/green", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 0, out)
        rc, out = self.gate("feat/green", FAKE_TESTS="fail")
        self.assertEqual(rc, 2, out)
        self.assertNotIn("GATE ERROR", out)

    def test_a_hanging_suite_is_red_with_a_marker_and_the_container_is_removed(self):
        rc, out = self.gate("feat/green", FAKE_TESTS="hang", GATE_TIMEOUT="2", GATE_FAST="0")
        self.assertEqual(rc, 2, out)
        self.assertIn("TIMEOUT: the test suite did not finish within 2 s (GATE_TIMEOUT)", out)
        self.assertRegex(self.docker_log, r"(?m)^rm -f review-")

    def test_a_docker_daemon_that_is_down_is_red_with_its_message(self):
        rc, out = self.gate("feat/green", FAKE_TESTS="daemon")
        self.assertEqual(rc, 2, out)
        self.assertIn("Cannot connect to the Docker daemon", out)

    def test_missing_docker_is_exit_3_never_exit_1(self):
        rc, out = self.gate("feat/green", path=self.only_git)
        self.assertEqual(rc, 3, out)
        self.assertIn("== GATE ERROR: the gate itself broke (FileNotFoundError", out)
        self.assertIn("NOT a pass", out)
        self.assertNotIn("== warnings:", out)

    def test_a_missing_token_file_is_exit_3(self):
        token = os.path.join(self.secrets, "monitor.token")
        os.rename(token, token + ".off")
        try:
            rc, out = self.gate("feat/green")
        finally:
            os.rename(token + ".off", token)
        self.assertEqual(rc, 3, out)
        self.assertIn("== GATE ERROR", out)

    def test_a_branch_that_was_not_pushed_is_exit_2(self):
        rc, out = self.gate("feat/never-pushed")
        self.assertEqual(rc, 2, out)
        self.assertIn("could not fetch the branch", out)

    def test_mutation_check_without_a_result_is_a_visible_warning(self):
        spec = os.path.join(self.tmp, "spec.json")
        open(spec, "w").write('[{"name": "m1", "file": "good.py", "old": "x * 2", "new": "x * 3"}]')
        rc, out = self.gate("feat/green", "--mutations", spec, FAKE_MUTATE="broken", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 1, out)
        self.assertIn("the mutation check gave no result (exit 1): container broke - not verified", out)
        rc, out = self.gate("feat/green", "--mutations", spec, FAKE_MUTATE="killed", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 0, out)
        self.assertIn("KILLED", out)
        rc, out = self.gate("feat/green", "--mutations", spec, FAKE_MUTATE="survived", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 1, out)
        self.assertIn("SURVIVED", out)

    def test_mutation_check_that_stops_early_says_how_far_it_got(self):
        spec = os.path.join(self.tmp, "spec2.json")
        open(spec, "w").write('[{"name": "m1", "file": "good.py", "old": "x * 2", "new": "x * 3"}, {"name": "m2", "file": "good.py", "old": "x * 2", "new": "x * 4"}]')
        rc, out = self.gate("feat/green", "--mutations", spec, FAKE_MUTATE="partial", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 1, out)
        self.assertIn("KILLED", out)
        self.assertIn("the mutation check stopped after 1 of 2 mutants (exit 1): container broke - the rest is not verified", out)
        rc, out = self.gate("feat/green", "--mutations", spec, FAKE_MUTATE="killed", GATE_ALLOWED_FILES=ALLOWED)
        self.assertEqual(rc, 1, out)
        self.assertIn("stopped after 1 of 2 mutants (exit 0)", out)


if __name__ == "__main__":
    result = unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout), exit=False)
    if result.result.wasSuccessful():
        print("review-branch fail-closed tests: ALL OK")
        sys.exit(0)
    sys.exit(1)
