#!/usr/bin/env python3
"""Deterministic verifier for the benchmark scenario (tasklog `total`).

Usage: python3 scenario.checks.py REPO_DIR   -> prints one JSON line
  {"tests_pass": bool, "output_ok": bool, "wired": bool, "test_lines": int, "code_lines": int}
tests_pass: the whole suite passes and tests/test_total.py exists; output_ok: exact stdout/exit codes of `cli.py total`
on a seeded store (also --since, empty, usage errors); wired: cli.py defines cmd_total; test_lines / code_lines: total lines of
tests/*.py and of the top-level *.py (the untouched scenario has 466 / 896).
Exit code 0 when everything is ok, else 1.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

SEED = [
    ("2024-03-04T09:00", 90, "api", ["backend", "api"]),
    ("2024-03-04T13:00", 45, "review", ["review"]),
    ("2024-03-05T10:00", 120, "api", ["backend"]),
    ("2024-03-07T08:30", 30, "inbox", []),
    ("2024-03-11T09:00", 60, "ui", ["frontend"]),
]
ALL = "5 entries, 5h45m\n"
SINCE = "2 entries, 1h30m\n"


def run(cmd, cwd, store, timeout=300):
    env = dict(os.environ, TASKLOG_STORE=store, PYTHONDONTWRITEBYTECODE="1")
    env.pop("TASKLOG_TODAY", None)
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def lines(paths):
    n = 0
    for p in paths:
        with open(p, errors="ignore") as f:
            n += sum(1 for _ in f)
    return n


def suite(repo, store):
    if not os.path.exists(os.path.join(repo, "tests", "test_total.py")):
        return False
    have_pytest = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True).returncode == 0
    cmd = [sys.executable, "-m", "pytest", "-q"] if have_pytest else [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
    return run(cmd, repo, store).returncode == 0


def output_ok(repo, tmp):
    full = os.path.join(tmp, "full.json")
    empty = os.path.join(tmp, "empty.json")
    entries = [{"id": i + 1, "start": s, "minutes": m, "label": lb, "tags": t, "note": ""} for i, (s, m, lb, t) in enumerate(SEED)]
    with open(full, "w") as f:
        json.dump({"next_id": len(SEED) + 1, "entries": entries}, f)
    cli = [sys.executable, "cli.py", "total"]
    checks = [
        (cli, full, 0, ALL),
        (cli + ["--since", "2024-03-07"], full, 0, SINCE),
        (cli + ["--since", "2030-01-01"], full, 0, "No entries.\n"),
        (cli, empty, 0, "No entries.\n"),
        (cli + ["--since", "yesterday"], full, 2, None),
    ]
    for cmd, store, code, out in checks:
        r = run(cmd, repo, store, 60)
        if r.returncode != code or (out is not None and r.stdout != out):
            return False
    return True


def main():
    repo = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "s.json")
        res = {"tests_pass": suite(repo, store), "output_ok": output_ok(repo, tmp)}
    try:
        res["wired"] = bool(re.search(r"def cmd_total", open(os.path.join(repo, "cli.py")).read(), re.M))
    except OSError:
        res["wired"] = False
    res["test_lines"] = lines(glob.glob(os.path.join(repo, "tests", "*.py")))
    res["code_lines"] = lines(p for p in glob.glob(os.path.join(repo, "*.py")))
    print(json.dumps(res))
    return 0 if res["tests_pass"] and res["output_ok"] and res["wired"] else 1


if __name__ == "__main__":
    sys.exit(main())
