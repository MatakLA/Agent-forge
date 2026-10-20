#!/usr/bin/env python3
"""Tests for bin/lib/quality.py: test-ratio measurement and the unwired-file check. Run: python3 tests/test_quality.py"""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
import quality


class Quality(unittest.TestCase):
    def test_ratio_splits_tests_from_code(self):
        self.assertEqual(quality.test_ratio(["10\t0\tapp/a.py", "30\t2\ttests/test_a.py", "5\t0\tweb/x.test.js", "-\t-\timg.png"]), (35, 10))

    def test_unreferenced_finds_only_unused_sources(self):
        with tempfile.TemporaryDirectory() as d:
            for p, body in {"pkg/used.py": "x=1\n", "pkg/lonely.py": "y=1\n", "pkg/main.py": "import used\n",
                            "web/panel.js": "//\n", "web/index.html": '<script src="panel.js">', "tests/test_lonely.py": "import lonely\n"}.items():
                os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
                open(os.path.join(d, p), "w").write(body)
            subprocess.run("git init -q && git add -A", shell=True, cwd=d, check=True)
            self.assertEqual(quality.unreferenced(d, ["pkg/used.py", "pkg/lonely.py", "pkg/main.py", "web/panel.js"]), ["pkg/lonely.py"])


if __name__ == "__main__":
    unittest.main()
