#!/usr/bin/env python3
"""Runs INSIDE devbox: collect.py <branch> - clone the bench repo's branch from the internal Forgejo (read-only monitor token),
run scenario.checks.py on it and print its one JSON line. Env FORGE_PROJECT=bench."""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.expanduser("~/sandbox/bin/lib"))
import sbx
branch = sys.argv[1]
tok = open(os.path.join(sbx.SECRETS, "monitor.token")).read().strip()
env = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.extraHeader", GIT_CONFIG_VALUE_0="Authorization: token " + tok)
d = tempfile.mkdtemp(prefix="bench-collect-")
r = subprocess.run(["git", "clone", "-q", "--branch", branch, f"{sbx.FORGE_URL}/{sbx.FORGE_REPO}.git", d], env=env, capture_output=True, text=True)
if r.returncode:
    print('{"branch_found": false}'); sys.exit(0)
out = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenario.checks.py"), d], capture_output=True, text=True).stdout
print(out.strip().splitlines()[-1] if out.strip() else '{"branch_found": true, "tests_pass": false}')
