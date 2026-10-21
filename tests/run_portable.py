#!/usr/bin/env python3
"""Run the pure-Python tests that are known to pass outside Linux (macOS, Windows).

  python tests/run_portable.py            run the tests listed in tests/portable-python.txt; exit 1 when one fails
  python tests/run_portable.py --all      run every tests/test_*.py and the foreman self-test and report each one, always exit 0
                                          (use it to see what a new platform can do, then update the list)

Agent-Forge itself needs Linux (systemd, Docker). This only guards the part that does not.
"""
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "tests", "portable-python.txt")
SELFTEST = "bin/foreman --selftest"


def listed():
    """Entries for this platform: `all` runs everywhere, `posix` only where os.name is posix (macOS, Linux)."""
    out = []
    with open(LIST, encoding="utf-8") as f:
        for ln in f:
            if not ln.strip() or ln.startswith("#"):
                continue
            entry, _, where = ln.rstrip().rpartition(" ")
            entry = entry.strip()
            if where == "all" or (where == "posix" and os.name == "posix"):
                out.append(entry)
    return out


def run(entry):
    args = entry.split()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable] + args, cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=240)
        ok, out = p.returncode == 0, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        ok, out = False, "timed out after 240 s"
    return ok, out, time.time() - t0


def main():
    every = "--all" in sys.argv
    entries = [SELFTEST] + sorted(os.path.relpath(p, ROOT).replace(os.sep, "/") for p in glob.glob(os.path.join(ROOT, "tests", "test_*.py"))) \
        if every else listed()
    failed = []
    for e in entries:
        ok, out, secs = run(e)
        print("%-4s %s (%.1f s)" % ("ok" if ok else "FAIL", e, secs), flush=True)
        if not ok:
            failed.append(e)
            print("\n".join("       " + ln for ln in out.splitlines()[-6:]), flush=True)
    print("%d of %d passed" % (len(entries) - len(failed), len(entries)))
    return 0 if every else (1 if failed else 0)


if __name__ == "__main__":
    sys.exit(main())
