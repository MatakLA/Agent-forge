#!/usr/bin/env python3
"""The worker's self-check (SELF_CHECK_PY in bin/run-supervisor) must give a verdict whatever bytes the branch and the suite contain:
a crash there is swallowed and counts as a pass. Runs the same script the supervisor pipes into the worker, without Docker, in a
throwaway clone. Run: python3 tests/test_self_check.py"""
import json, os, pathlib, re, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GIT = ["git", "-c", "user.email=o@o", "-c", "user.name=o"]


def script():
    sup = pathlib.Path(ROOT, "bin", "run-supervisor").read_text()
    return pathlib.Path(ROOT, "bin", "lib", "quality.py").read_text() + "\n" + re.search(r'SELF_CHECK_PY = r"""(.*?)"""', sup, re.S).group(1)


def git(cwd, *args):
    subprocess.run(GIT + list(args), cwd=cwd, check=True, capture_output=True)


class SelfCheckBytes(unittest.TestCase):
    def check(self, committed=None, untracked=None, test_cmd="true"):
        """Verdict of the self-check for a branch with these files (name -> bytes) on top of a green main."""
        with tempfile.TemporaryDirectory() as d:
            origin, work = os.path.join(d, "origin.git"), os.path.join(d, "work")
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
            subprocess.run(["git", "clone", "-q", origin, work], check=True, capture_output=True)
            pathlib.Path(work, "util.py").write_text('"""Util."""\nX = 1\n')
            git(work, "add", "-A")
            git(work, "commit", "-qm", "base")
            git(work, "push", "-q", "origin", "HEAD:main")
            git(work, "fetch", "-q")
            git(work, "checkout", "-q", "-b", "feat", "origin/main")
            for files, commit in ((committed or {}, True), (untracked or {}, False)):
                for name, data in files.items():
                    pathlib.Path(work, name).write_bytes(data)
                if commit and files:
                    git(work, "add", "-A")
                    git(work, "commit", "-qm", "branch")
            env = dict(os.environ, AF_TEST_CMD=test_cmd, AF_SELF_CHECK_MODE="full", AF_SC_CONF='{"quality": "warn"}')
            r = subprocess.run([sys.executable, "-"], input=script().encode(), cwd=work, env=env, capture_output=True, timeout=120)
            err = r.stderr.decode("utf-8", "replace")
            self.assertEqual(r.returncode, 0, "the self-check crashed:\n" + err[-1500:])
            return json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])

    def test_latin1_test_file_is_read_not_a_crash(self):
        res = self.check(committed={"test_latin.py": b"# caf\xe9\ndef test_f():\n    assert 1 == 1\n"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["problems"], [])

    def test_latin1_test_file_is_still_scanned_for_skips(self):
        for kind in ("committed", "untracked"):
            res = self.check(**{kind: {"test_latin.py": b"# caf\xe9\nimport pytest\n\n@pytest.mark.skip\ndef test_f():\n    pass\n"}})
            self.assertFalse(res["ok"], (kind, res))
            self.assertTrue(any("test_latin.py contains pytest.mark.skip" in p for p in res["problems"]), (kind, res))

    def test_latin1_source_file_does_not_stop_the_quality_findings(self):
        res = self.check(committed={"legacy.py": b'# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\n'})
        self.assertTrue(res["ok"], res)
        self.assertFalse([h for h in res["hints"] if "could not run" in h], res)

    def test_suite_output_that_is_not_utf8_is_reported(self):
        res = self.check(test_cmd=r"printf 'caf\351\n'; exit 1")
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["rc"], 1)
        self.assertIn("caf", res["tail"])


if __name__ == "__main__":
    unittest.main()
