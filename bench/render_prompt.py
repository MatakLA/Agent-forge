#!/usr/bin/env python3
"""Render the worker prompt that ONE Agent-Forge version would send for the benchmark task.
Run it from inside a checkout (worktree) of that version:  python3 render_prompt.py <scenario-clone-with-origin/main> <task.json> [--continuing]
Prints the prompt on stdout. Works for the early monolithic bin/foreman and the later bin/lib/foreman package.
Invariants: offline, no model calls; the scenario clone is the 'main' the context pack reads."""
import json, os, sys, tempfile

clone, task_file = sys.argv[1], sys.argv[2]
root = os.getcwd()
tmp = tempfile.mkdtemp(prefix="af-render-")
os.environ.update(SANDBOX_TEST="1", SANDBOX_RUNS_DIR=tmp, LIMITS_FILE=os.path.join(root, "etc", "limits.example.conf"))
sys.path.insert(0, os.path.join(root, "bin", "lib"))
sys.path.insert(0, os.path.join(root, "bin"))
t = json.load(open(task_file))
t.setdefault("attempts", 0)
if os.path.isdir(os.path.join(root, "bin", "lib", "foreman")):
    from foreman import ctx, prompts
    ctx.REPO = clone
    ctx.C["CONTEXT_PACK"] = os.environ.get("BENCH_PACK", "on")
    ctx.C["PROMPT_ORDER"] = os.environ.get("BENCH_ORDER", "default")
    try:
        from foreman import repo
        repo.ctx.REPO = clone
    except ImportError:
        pass
    print(prompts.worker_spec(t, "--continuing" in sys.argv))
else:
    import importlib.machinery, importlib.util
    loader = importlib.machinery.SourceFileLoader("foreman_mod", os.path.join(root, "bin", "foreman"))
    spec = importlib.util.spec_from_loader("foreman_mod", loader)
    m = importlib.util.module_from_spec(spec); loader.exec_module(m)
    for name in ("REPO", "REPO_DIR"):
        if hasattr(m, name):
            setattr(m, name, clone)
    if hasattr(m, "C"):
        m.C["CONTEXT_PACK"] = os.environ.get("BENCH_PACK", "on")
    print(m.worker_spec(t))
