#!/usr/bin/env python3
"""End-to-end runner executing the quickstart walkthrough pipeline within wall-clock budgets.

Public interface: run_step, run_all, main.
Invariants:
- Imports STEP_BUDGETS from tests.fixtures.quickstart_budget and derives step order from its keys.
- Never hardcodes a second copy of the five pipeline step names or their ordering.
- run_step executes argv using subprocess.run without shell=True and returns wall-clock elapsed seconds.
- run_all stops execution immediately before starting any later step once an earlier step fails budget check.
- main operates strictly against an isolated scratch directory without mutating real runs/ or ~/sandbox.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.fixtures.quickstart_budget import (
    STEP_BUDGETS,
    check_budget,
    check_total,
    format_report,
)


def default_step_commands() -> dict[str, list[str]]:
    """Map documented pipeline steps from docs/QUICKSTART.md to their CLI commands.

    Step commands as documented in docs/QUICKSTART.md:
    1. install:
       Documented in docs/QUICKSTART.md Section 1 as:
         cd examples/sample-project/seed-repo
         python3 -m unittest discover -s tests
       Deviation: executed from the repository root as
         ["python3", "-m", "unittest", "discover", "-s", "examples/sample-project/seed-repo/tests"]
       to preserve the python3 entry point while avoiding changing working directory for subsequent steps.
    2. plan:
       Documented in docs/QUICKSTART.md Section 2 as:
         bin/foreman goal 'Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it.'
    3. dispatch:
       Documented in docs/QUICKSTART.md Section 3 under Dispatch as:
         bin/foreman status
    4. gate:
       Documented in docs/QUICKSTART.md Section 3 under Gate as:
         bin/foreman status
    5. merge:
       Documented in docs/QUICKSTART.md Section 3 under Merge as:
         bin/foreman status
    """
    return {
        "install": ["python3", "-m", "unittest", "discover", "-s", "examples/sample-project/seed-repo/tests"],
        "plan": ["bin/foreman", "goal", "Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it."],
        "dispatch": ["bin/foreman", "status"],
        "gate": ["bin/foreman", "status"],
        "merge": ["bin/foreman", "status"],
    }


def get_pipeline_steps(commands: dict[str, list[str]] | None = None) -> list[tuple[str, list[str]]]:
    """Derive ordered (name, argv) pairs from STEP_BUDGETS keys without a duplicate list."""
    cmds = commands if commands is not None else default_step_commands()
    return [(step, cmds.get(step, ["bin/foreman", "status"])) for step in STEP_BUDGETS]


def run_step(name: str, argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None) -> float:
    """Run argv via subprocess.run (never shell=True) and return elapsed wall-clock seconds."""
    t0 = time.monotonic()
    subprocess.run(argv, cwd=cwd, env=env, check=True)
    t1 = time.monotonic()
    return t1 - t0


def run_all(
    steps: list[tuple[str, list[str]]],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[bool, str, dict[str, float]]:
    """Run each (name, argv) pair in order, halting immediately on first over-budget step."""
    timings: dict[str, float] = {}
    for name, argv in steps:
        try:
            elapsed = run_step(name, argv, cwd=cwd, env=env)
        except subprocess.CalledProcessError as exc:
            return False, f"{name} failed with exit code {exc.returncode}", timings
        except Exception as exc:
            return False, f"{name} failed: {exc}", timings

        timings[name] = elapsed
        ok, msg = check_budget(name, elapsed)
        if not ok:
            return False, msg, timings

    return True, "", timings


def main(argv: list[str] | None = None) -> int:
    """Encode per-step commands from docs/QUICKSTART.md to drive examples/sample-project through bin/foreman."""
    if argv is None:
        argv = sys.argv[1:]

    scratch_dir = os.environ.get("QUICKSTART_RUNS_DIR")
    own_scratch = False
    if not scratch_dir or not os.path.isdir(scratch_dir):
        scratch_dir = tempfile.mkdtemp(prefix="af-quickstart-e2e-")
        own_scratch = True

    try:
        env = os.environ.copy()
        env["SANDBOX_TEST"] = "1"
        env["SANDBOX_RUNS_DIR"] = scratch_dir
        env["SANDBOX_SECRETS"] = os.path.join(scratch_dir, "secrets")
        os.makedirs(env["SANDBOX_SECRETS"], exist_ok=True)

        fake_docker_dir = os.environ.get("QUICKSTART_FAKE_DOCKER_DIR")
        if fake_docker_dir and os.path.isdir(fake_docker_dir):
            if fake_docker_dir not in env.get("PATH", ""):
                env["PATH"] = f"{fake_docker_dir}:{env.get('PATH', '')}"
        else:
            docker_shim = os.path.join(scratch_dir, "docker-shim")
            os.makedirs(docker_shim, exist_ok=True)
            docker_bin = os.path.join(docker_shim, "docker")
            fake_docker_src = os.path.join(ROOT, "tools", "fake-docker")
            if os.path.exists(fake_docker_src) and not os.path.exists(docker_bin):
                shutil.copy(fake_docker_src, docker_bin)
                os.chmod(docker_bin, 0o755)
            env["PATH"] = f"{docker_shim}:{env.get('PATH', '')}"

        steps = get_pipeline_steps()
        ok, msg, timings = run_all(steps, cwd=ROOT, env=env)
        if not ok:
            sys.stderr.write(f"{msg}\n")
            return 1

        total_elapsed = sum(timings.values())
        total_ok, total_msg = check_total(total_elapsed)
        if not total_ok:
            sys.stderr.write(f"{total_msg}\n")
            return 1

        print(format_report(timings))
        return 0
    finally:
        if own_scratch and os.path.isdir(scratch_dir):
            shutil.rmtree(scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
