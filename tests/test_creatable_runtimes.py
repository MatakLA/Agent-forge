#!/usr/bin/env python3
"""Pins what a user is told about creating workers (bin/worker-add, the wizard's next steps, the docs).

bin/worker-add is the authority: runtimes.CREATABLE is the set it accepts. The other registered runtimes (cost model, egress hosts,
readiness) have no worker image yet, so bin/worker-add refuses them with exit 2. No shipped text may tell the user to run
`bin/worker-add N --runtime <one of those>`.

Run directly: python3 tests/test_creatable_runtimes.py
"""
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "bin", "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import runtimes
from foreman import wizard

WORKER_ADD = os.path.join(ROOT, "bin", "worker-add")
NOT_CREATABLE = frozenset(runtimes.RUNTIMES) - runtimes.CREATABLE
RUNTIME_ARG = re.compile(r"--runtime[ =]+['\"]?([a-z][a-z0-9|-]*)")


def runtime_args(text):
    return [name for group in RUNTIME_ARG.findall(text) for name in group.split("|") if name]


def shipped_text_files():
    """Tracked files that are not tests and not the changelog (the changelog tells history, tests pass runtime names to mocks)."""
    try:
        out = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], capture_output=True, check=True, timeout=30).stdout.decode()
        names = [n for n in out.split("\0") if n]
    except (OSError, subprocess.SubprocessError):
        names = []
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("bench", "node_modules")]
            names += [os.path.relpath(os.path.join(base, f), ROOT) for f in files]
    for name in names:
        path = os.path.join(ROOT, name)
        if name == "CHANGELOG.md" or name.startswith("tests/") or os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                yield name, f.read()
        except (UnicodeDecodeError, OSError):
            continue


class TestWorkerAdd(unittest.TestCase):
    def setUp(self):
        with open(WORKER_ADD, encoding="utf-8") as f:
            self.text = f.read()

    def test_there_are_registered_runtimes_without_an_image(self):
        self.assertTrue(runtimes.CREATABLE < frozenset(runtimes.RUNTIMES))
        self.assertTrue(NOT_CREATABLE)

    def test_accepted_runtimes_are_exactly_creatable(self):
        m = re.search(r'\^\(([a-z|-]+)\)\$ \]\] \|\| \{ echo "runtime must be', self.text)
        self.assertIsNotNone(m, "the runtime check in bin/worker-add moved")
        self.assertEqual(set(m.group(1).split("|")), set(runtimes.CREATABLE))

    def test_its_own_hints_name_creatable_runtimes_only(self):
        for name in runtime_args(self.text):
            self.assertIn(name, runtimes.CREATABLE, f"bin/worker-add itself suggests --runtime {name}")

    def test_refuses_a_registered_runtime_without_an_image(self):
        bash = shutil.which("bash")
        for name in sorted(NOT_CREATABLE):
            # empty PATH: the refusal comes before any external command, so nothing else can run
            r = subprocess.run([bash, WORKER_ADD, "1", "--runtime", name], capture_output=True, text=True, timeout=20, env={"PATH": "/nonexistent"})
            self.assertEqual(r.returncode, 2, f"{name}: {r.stderr}")
            self.assertIn("runtime must be", r.stderr)


class TestWizardNextSteps(unittest.TestCase):
    def test_next_steps_name_creatable_runtimes_only(self):
        for key, text in wizard.NEXT_STEPS.items():
            for name in runtime_args(text):
                self.assertIn(name, runtimes.CREATABLE, f"NEXT_STEPS[{key!r}] says to run bin/worker-add --runtime {name}")

    def test_creatable_runtime_points_to_worker_add(self):
        for name in sorted(runtimes.CREATABLE):
            self.assertIn("bin/worker-add", wizard.next_steps(name), name)

    def test_runtime_without_an_image_says_so(self):
        for name in sorted(NOT_CREATABLE):
            self.assertIn("no worker image yet", wizard.next_steps(name), name)


class TestShippedText(unittest.TestCase):
    def test_no_file_tells_to_create_a_worker_for_a_runtime_without_an_image(self):
        bad = []
        for name, text in shipped_text_files():
            for number, line in enumerate(text.splitlines(), 1):
                bad += [f"{name}:{number}: --runtime {rt}" for rt in runtime_args(line) if rt in NOT_CREATABLE]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
