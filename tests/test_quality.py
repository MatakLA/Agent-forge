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

    def test_oversized_lists_only_long_sources(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "big.py"), "w").write("x = 1\n" * 450)
            open(os.path.join(d, "small.py"), "w").write("x = 1\n")
            self.assertEqual(quality.oversized(d, ["big.py", "small.py", "gone.py"], 400), [("big.py", 450)])

    def test_ratio_message_limits(self):
        self.assertEqual(quality.ratio_message(100, 10, 1.5), "")          # under the floor of 150 lines
        self.assertEqual(quality.ratio_message(300, 200, 1.5), "")
        self.assertIn("too many tests: 400 test lines for 100 code lines", quality.ratio_message(400, 100, 1.5))
        self.assertEqual(quality.ratio_message(900, 0), "")                # nothing but tests: not a ratio problem

    def test_outside_scope(self):
        allowed = ["web/js/dnes.js", "web/js/dnes.test.mjs"]
        ch = [("M", "web/js/dnes.js"), ("A", "web/js/dnes.test.mjs"), ("M", "web/css/dnes.css"), ("A", "web/js/test_dnes.py"),
              ("A", "web/js/dnes_extra.test.js"), ("M", "web/js/other.test.js"), ("M", "registry.json")]
        self.assertEqual(quality.outside_scope(ch, allowed, shared=["registry.json"]),
                         ["web/css/dnes.css", "web/js/dnes_extra.test.js", "web/js/other.test.js"])
        self.assertEqual(quality.outside_scope(ch, []), [])                # unrestricted task
        self.assertEqual(quality.outside_scope([("M", "a/b/registry.json")], ["x.py"], shared=["registry.json"]), [])

    def test_weakened_tests_only_on_net_loss(self):
        d = """diff --git a/t/test_a.py b/t/test_a.py
--- a/t/test_a.py
+++ b/t/test_a.py
@@ -3,3 +3,1 @@
-def test_x():
-    assert f(1) == 2
-    assert f(2) == 3
+def test_x():
+    assert f(1) == 2
diff --git a/t/test_new.py b/t/test_new.py
--- /dev/null
+++ b/t/test_new.py
@@ -0,0 +1,2 @@
+def test_y():
+    assert 1
diff --git a/t/test_b.py b/t/test_b.py
--- a/t/test_b.py
+++ b/t/test_b.py
@@ -1,1 +1,2 @@
-    assert f(1) == 2
+    assert f(1) == 3
+    assert f(2) == 3
diff --git a/web/x.test.mjs b/web/x.test.mjs
--- a/web/x.test.mjs
+++ b/web/x.test.mjs
@@ -1,2 +1,0 @@
-test('a', () => {
-  expect(a()).toBe(1);
"""
        got = {r[0]: r[1:] for r in quality.weakened_tests(d)}
        self.assertEqual(sorted(got), ["t/test_a.py", "web/x.test.mjs"])
        self.assertEqual(got["t/test_a.py"], (2, 1, 1, 1))
        self.assertEqual(got["web/x.test.mjs"], (1, 0, 1, 0))

    def _repo(self, files):
        d = tempfile.mkdtemp()
        for p, body in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
            open(os.path.join(d, p), "w").write(body)
        subprocess.run("git init -q && git add -A && git -c user.name=a -c user.email=a@a commit -qm init", shell=True, cwd=d, check=True)
        return d

    HELPER = ("def eu_offset_hours(utc):\n    start = utc.replace(month=3, day=25)\n    end = utc.replace(month=10, day=25)\n"
              "    return 2 if start <= utc < end else 1\n")

    def test_duplicate_defs_finds_copied_python_helper(self):
        d = self._repo({"tools/app_activity.py": '"""A."""\n' + self.HELPER, "tools/other.py": '"""O."""\nX = 1\n'})
        open(os.path.join(d, "tools/app_gym.py"), "w").write('"""G."""\n' + self.HELPER.replace("utc", "u") .replace("def eu_offset_hours(u)", "def eu_offset_hours(utc)") + "\n")
        open(os.path.join(d, "tools/app_body.py"), "w").write('"""B."""\ndef eu_offset_hours(x):\n    return 7\n\ndef local_thing(a):\n    b = a + 1\n    c = b * 2\n    return c\n')
        got = quality.duplicate_defs(d, [("A", "tools/app_gym.py"), ("A", "tools/app_body.py")], base="HEAD")
        self.assertEqual([(p, n, o) for p, n, o, _ in got], [("tools/app_gym.py", "eu_offset_hours", "tools/app_activity.py")])

    def test_duplicate_defs_ignores_old_names_short_and_generic(self):
        d = self._repo({"a.py": self.HELPER + "def main():\n    a = 1\n    b = 2\n    return a + b\n", "b.py": "def main():\n    a = 1\n    b = 2\n    return a + b\n" + self.HELPER})
        # b.py already defined the helper at HEAD: not new; main is a generic name
        open(os.path.join(d, "b.py"), "a").write("\n\nx = 1\n")
        self.assertEqual(quality.duplicate_defs(d, [("M", "b.py")], base="HEAD"), [])
        open(os.path.join(d, "c.py"), "w").write("def tiny_one(a):\n    return a\n")
        open(os.path.join(d, "e.py"), "w").write("def tiny_one(a):\n    return a\n")
        self.assertEqual(quality.duplicate_defs(d, [("A", "c.py")], base="HEAD"), [])

    def test_duplicate_defs_js_helper_and_untracked_copy_in_same_branch(self):
        js = "export function escapeHtml(value) {\n  return String(value)\n    .replace(/&/g, '&amp;')\n    .replace(/</g, '&lt;');\n}\n"
        d = self._repo({"web/lib.js": js, "web/readme.txt": "x"})
        open(os.path.join(d, "web/panel.js"), "w").write("import { x } from './x.js';\n" + js.replace("export ", ""))
        open(os.path.join(d, "web/panel2.js"), "w").write(js.replace("export ", "const k = 1;\n"))
        got = quality.duplicate_defs(d, [("A", "web/panel.js"), ("A", "web/panel2.js")], base="HEAD")
        self.assertEqual([(p, n, o) for p, n, o, _ in got], [("web/panel.js", "escapeHtml", "web/lib.js"), ("web/panel2.js", "escapeHtml", "web/lib.js")])
        open(os.path.join(d, "web/panel3.js"), "w").write(js.replace("export ", "").replace("escapeHtml", "escapeText"))
        open(os.path.join(d, "web/panel4.js"), "w").write(js.replace("export ", "").replace("escapeHtml", "escapeText"))
        pair = quality.duplicate_defs(d, [("A", "web/panel3.js"), ("A", "web/panel4.js")], base="HEAD")
        self.assertEqual([(p, n, o) for p, n, o, _ in pair], [("web/panel3.js", "escapeText", "web/panel4.js")])

    def test_unused_imports(self):
        src = "import os, sys\nimport json as j\nfrom a import b, c  # noqa\nfrom d import e\n__all__ = ['e']\ntry:\n    import zz\nexcept ImportError:\n    pass\nprint(sys.argv)\n"
        self.assertEqual(quality.unused_imports(src), ["j", "os"])
        self.assertEqual(quality.unused_imports("def broken(:\n"), [])

    def test_unused_imports_only_the_changes_own(self):
        before = "import os\nimport sys\nprint(sys.argv)\n"
        after = "import os\nimport sys\nimport json\nprint(sys.argv)\n"
        self.assertEqual(quality.unused_imports(after), ["json", "os"])
        self.assertEqual(quality.unused_imports(after, before), ["json"])          # os was already unused: not this change's fault

    def test_test_targets_own_tests_then_named_then_importers(self):
        d = self._repo({"pkg/calc.py": "X = 1\n", "tests/test_calc.py": "import calc\n", "tests/test_user.py": "from calc import X\n",
                        "tests/test_other.py": "import nothing\n", "pkg/main.py": "Y = 1\n", "tests/test_main.py": "import main\n",
                        "tests/conftest.py": "import calc\n", "tests/test_new.py": "def test_a():\n    pass\n"})
        got = quality.test_targets(d, [("M", "pkg/calc.py"), ("A", "tests/test_new.py"), ("M", "pkg/main.py"), ("D", "pkg/old.py"), ("M", "README.md")])
        self.assertEqual(got, ["tests/test_new.py", "tests/test_calc.py", "tests/test_user.py"])      # main is an entry stem; conftest never; other does not import calc
        self.assertEqual(quality.test_targets(d, [("M", "pkg/calc.py")], limit=1), ["tests/test_calc.py"])

    def test_file_names_that_are_not_utf8_do_not_break_the_checks(self):
        d = self._repo({"tools/app_activity.py": '"""A."""\n' + self.HELPER, "tools/other.py": '"""O."""\nX = 1\n'})
        try:
            for name in (b"tracked-caf\xe9.py", b"untracked-na\xefve.py"):          # Latin-1 names, as an archive made on Windows leaves them
                with open(os.path.join(os.fsencode(d), name), "wb") as f:
                    f.write(b"# caf\xe9\nX = 1\n")
        except OSError:
            self.skipTest("this file system refuses names that are not UTF-8")
        subprocess.run([b"git", b"add", b"--", b"tracked-caf\xe9.py"], cwd=d, check=True)
        open(os.path.join(d, "tools/app_gym.py"), "w").write('"""G."""\n' + self.HELPER)
        got = quality.duplicate_defs(d, [("A", "tools/app_gym.py")], base="HEAD")
        self.assertEqual([(p, n, o) for p, n, o, _ in got], [("tools/app_gym.py", "eu_offset_hours", "tools/app_activity.py")])
        self.assertEqual(quality.unreferenced(d, ["tools/other.py"]), ["tools/other.py"])
        self.assertEqual(len(quality._all_files(d)), 5)

    def test_injected_markers_and_forbidden_added(self):
        diff = ("--- a/web/a.js\n+++ b/web/a.js\n@@ -1 +1,2 @@\n+// Generated with [Claude Code](x)\n+var ok = 1;\n"
                "--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n+Co-Authored-By: Claude\n")
        self.assertEqual(len(quality.injected_markers(diff)), 1)
        self.assertEqual(quality.injected_markers("", "fix\n\nClaude-Session: https://x"), ["commit message: Claude-Session:"])
        rules = [{"re": "#[0-9a-f]{6}", "msg": "hex colour", "paths": r"\.css$"}, {"re": "("}, "junk"]
        css = "--- a/x.css\n+++ b/x.css\n@@ -1 +1 @@\n+a{color:#abcdef}\n+b{color:#012345}\n"
        self.assertEqual(quality.forbidden_added(css + diff, rules), [("x.css", "hex colour", 2)])

    def test_planner_read_field_is_sanitised(self):
        sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
        from foreman.util import norm_tasks
        (_, f, _), = norm_tasks({"tasks": [{"title": "t", "read": ["a/b.py", "../etc/passwd", "/abs", 3] + ["x%d.py" % i for i in range(9)]}]}, 1)
        self.assertEqual(f["read"][0], "a/b.py")
        self.assertEqual(len(f["read"]), 5)
        self.assertNotIn("../etc/passwd", f["read"])


if __name__ == "__main__":
    unittest.main()
