#!/usr/bin/env python3
"""Unit tests for the seed-repo bookmarks CLI tool.
Public interface: run via unittest discover or plain python3 test_bookmarks.py.
Invariants:
- Uses BOOKMARKS_FILE with temporary files to avoid touching user home directory.
- Exercises CLI invocations via subprocess with Python standard library only.
- Validates add/list round-trips, tagless entries, and insertion order preservation.
"""

import sys
import traceback

# Top-level smoke import check: print traceback and exit non-zero on failure
try:
    import json
    import os
    import subprocess
    import tempfile
    import unittest
except Exception:
    traceback.print_exc()
    sys.exit(1)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKMARKS_BIN = os.path.join(REPO_ROOT, "bin", "bookmarks")


class TestBookmarksCLI(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_path = self.temp_file.name
        self.temp_file.close()

    def tearDown(self):
        if os.path.exists(self.temp_path):
            try:
                os.remove(self.temp_path)
            except OSError:
                pass

    def _run_bookmarks(self, *args):
        cmd = [sys.executable, BOOKMARKS_BIN, *args]
        env = {**os.environ, "BOOKMARKS_FILE": self.temp_path}
        return subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_add_and_list_with_tags(self):
        add_res = self._run_bookmarks("add", "https://example.com", "--tag", "python", "--tag", "cli")
        assert add_res.returncode == 0
        assert add_res.stdout == ""

        list_res = self._run_bookmarks("list")
        assert list_res.returncode == 0
        assert list_res.stdout == "https://example.com\tpython,cli\n"

    def test_add_and_list_tagless(self):
        add_res = self._run_bookmarks("add", "https://example.org")
        assert add_res.returncode == 0
        assert add_res.stdout == ""

        list_res = self._run_bookmarks("list")
        assert list_res.returncode == 0
        assert list_res.stdout == "https://example.org\t\n"

    def test_preserves_insertion_order(self):
        add1 = self._run_bookmarks("add", "https://first.example.com", "--tag", "tag1")
        assert add1.returncode == 0
        assert add1.stdout == ""

        add2 = self._run_bookmarks("add", "https://second.example.com", "--tag", "tag2", "--tag", "tag3")
        assert add2.returncode == 0
        assert add2.stdout == ""

        list_res = self._run_bookmarks("list")
        assert list_res.returncode == 0
        assert list_res.stdout == "https://first.example.com\ttag1\nhttps://second.example.com\ttag2,tag3\n"

    def test_empty_list_initially(self):
        list_res = self._run_bookmarks("list")
        assert list_res.returncode == 0
        assert list_res.stdout == ""

    def test_creates_file_if_missing(self):
        if os.path.exists(self.temp_path):
            os.remove(self.temp_path)
        add_res = self._run_bookmarks("add", "https://created.example.com")
        assert add_res.returncode == 0
        assert os.path.exists(self.temp_path)

        list_res = self._run_bookmarks("list")
        assert list_res.returncode == 0
        assert list_res.stdout == "https://created.example.com\t\n"

    def test_unknown_subcommand_fails(self):
        res = self._run_bookmarks("bogus")
        assert res.returncode != 0
        assert "error" in res.stderr

    def test_missing_url_fails(self):
        res = self._run_bookmarks("add")
        assert res.returncode != 0
        assert "error" in res.stderr

    def test_no_search_subcommand(self):
        res = self._run_bookmarks("search", "python")
        assert res.returncode != 0
        assert "error" in res.stderr

    def test_script_is_executable_and_shebang(self):
        with open(BOOKMARKS_BIN, "r", encoding="utf-8") as f:
            first_line = f.readline()
        assert os.access(BOOKMARKS_BIN, os.X_OK)
        assert first_line == "#!/usr/bin/env python3\n"


if __name__ == "__main__":
    unittest.main()
