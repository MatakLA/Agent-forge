"""The foreman's read-only clone of main, the code map (bin/lib/codemap.py) and the per-task context pack and variant."""
import hashlib, os, threading

import sbx, codemap

from . import ctx
from .util import git_auth_env, sh, slugify
from .store import jload, jsave
from .stats import pick_model, routing_table, run_stats


REPO_LOCK = threading.Lock()


def refresh_repo():
    """Bring the foreman's read-only clone of main up to date. Serialized: several steps run in parallel threads and two
    concurrent fetch/checkout runs in one repository fail on git's index.lock."""
    env = git_auth_env("monitor.token")
    with REPO_LOCK:
        if not os.path.isdir(os.path.join(ctx.REPO, ".git")):
            os.makedirs(os.path.dirname(ctx.REPO), exist_ok=True)
            sh(["git", "clone", "-q", f"{sbx.FORGE_URL}/{sbx.FORGE_REPO}.git", ctx.REPO], timeout=120, env=env)
        sh(["git", "fetch", "-q", "origin"], cwd=ctx.REPO, timeout=120, env=env)
        sh(["git", "checkout", "-q", "--detach", "origin/main"], cwd=ctx.REPO)


def repo_tree():
    rc, out, _ = sh(["git", "ls-tree", "-r", "--name-only", "origin/main"], cwd=ctx.REPO)
    return "\n".join(l for l in out.splitlines() if "__pycache__" not in l)


def read_repo(path, limit=20000):
    rc, out, _ = sh(["git", "show", f"origin/main:{path}"], cwd=ctx.REPO)
    return out[:limit] if rc == 0 else ""


# ---------------------------------------------------------------- code map + context pack (deterministic, no tokens)
CODEMAP = {}


def codemap_main():
    """The code map (bin/lib/codemap.py) of PROJECT_DIR on main, cached per main commit."""
    rc, sha, _ = sh(["git", "rev-parse", "origin/main"], cwd=ctx.REPO)
    sha = sha.strip()
    if not sha:
        return {}
    if CODEMAP.get("sha") == sha:
        return CODEMAP["map"]
    p = os.path.join(ctx.FD, "codemap.json")
    d = jload(p, {})
    if d.get("sha") != sha:
        with REPO_LOCK:                      # never read the working tree while refresh_repo() checks out
            rc, head, _ = sh(["git", "rev-parse", "HEAD"], cwd=ctx.REPO)
            m = codemap.build(ctx.REPO, ctx.C["PROJECT_DIR"].strip("/"))
        if head.strip() != sha:              # checkout not at origin/main yet: use it, do not cache it
            return m
        d = dict(sha=sha, map=m)
        jsave(p, d)
    CODEMAP.update(d)
    return d["map"]


def shared_files():
    return {x.strip() for x in str(ctx.C["SHARED_FILES"]).split(",") if x.strip()}


def own_files(t):
    """The files a task owns, without shared registries (they do not make a task bigger to understand)."""
    sf = shared_files()
    return [f for f in t.get("files", []) if f not in sf and os.path.basename(f) not in sf]


def context_pack(t):
    """What a worker would otherwise spend its first 20-30 tool calls reading: the current content of its own files, the
    interfaces of the modules they import / the spec names / their tests, and a one-line map of the rest. Built from main
    without any LLM; bounded by CONTEXT_PACK_CHARS (it is resent with every agent turn, so more is not better)."""
    cm = codemap_main()
    budget = int(ctx.C["CONTEXT_PACK_CHARS"])
    pd = ctx.C["PROJECT_DIR"].strip("/")
    out, used, shown = [], 0, set()

    def add(block):
        nonlocal used
        if used + len(block) > budget:
            return False
        out.append(block); used += len(block); return True

    fresh = not t.get("attempts") and not t.get("last_run")
    mine = t.get("files", [])
    if fresh:
        for f in mine:
            src = read_repo(f, 200000)
            if not src:
                add(f"### `{f}` - does not exist yet (you create it)\n"); continue
            shown.add(f)
            if len(src) <= min(25000, budget // 2):
                lang = os.path.splitext(f)[1].lstrip(".") or ""
                if not add(f"### `{f}` (current content on main, {src.count(chr(10)) + 1} lines)\n```{lang}\n{src}\n```\n"):
                    add(f"### `{f}` - too large for the pack; interface:\n{codemap.render(cm, [f])}\n")
            elif codemap.is_test(f):
                head = "\n".join(src.splitlines()[:60])
                add(f"### `{f}` - {src.count(chr(10)) + 1} lines, too large to include whole. Its first 60 lines (imports, fixtures):\n"
                    f"```python\n{head}\n```\nHelpers and existing tests:\n{codemap.render(cm, [f])}\nAdd new tests at the end; do not "
                    f"rewrite existing ones.\n")
            else:
                add(f"### `{f}` - {src.count(chr(10)) + 1} lines, too large to include whole; its interface:\n{codemap.render(cm, [f])}\n"
                    f"Open only the parts you change.\n")
    else:
        add("(Your branch already has earlier work, so your own files are not included - start with `git diff origin/main --stat`.)\n")
    related = []
    for f in mine:
        if f in cm:
            related += codemap.imports_of(f, read_repo(f, 200000), cm)
        tf = codemap.test_for(f, cm)
        if tf:
            related.append(tf)
    related += codemap.mentioned_in(t.get("spec", "") + " " + " ".join(t.get("acceptance", [])), cm)
    related = [r for r in dict.fromkeys(related) if r not in shown and r not in mine]
    if related:
        add("## Interfaces you will use (from main - signatures and docstrings, no bodies)\n" + codemap.render(cm, related, max_chars=budget // 3) + "\n")
    rest = [p for p in sorted(cm) if p not in shown and p not in related and p not in mine]
    if rest:
        add("## Other files in the project (one line each)\n" + codemap.render(cm, rest, with_api=False, max_chars=max(2000, budget - used - 500)) + "\n")
    if not out:
        return ""
    return ("## Context pack (generated from main for this task)\nThis already contains the files and interfaces you need. Do not open "
            "them again with tools; read further files only when something you need is really missing here.\n"
            f"Test runner: `{sbx.test_cmd()}` (run targeted tests for your changes; merge gate runs full suite){f' (project code in `{pd}/`)' if pd else ''}.\n\n" + "\n".join(out))


def _bucket(key, n):
    return int(hashlib.sha1(key.encode()).hexdigest(), 16) % n


def variant(t):
    """Per-task experiment settings, fixed at the first dispatch (reworks keep them): context pack on/off, worker model."""
    v = t.get("variant")
    if v is None:
        cp = str(ctx.C["CONTEXT_PACK"]).lower()
        pack = cp == "on" or (cp == "ab" and _bucket(t["id"] + ":pack", 2) == 0)
        risky = t.get("risk") == "risky"
        light = slugify(t.get("role", "")) in {slugify(r) for r in str(ctx.C["LIGHT_ROLES"]).split(",") if r.strip()}
        routed = routed_model(t) if not risky and str(ctx.C.get("MODEL_ROUTING", "off")) == "auto" else None
        if risky and ctx.C["WORKER_MODEL_RISKY"]:
            model, why = ctx.C["WORKER_MODEL_RISKY"], "risky"
        elif not risky and ctx.C["AB_MODEL"] and _bucket(t["id"] + ":model", 100) < int(ctx.C["AB_SHARE"]):
            model, why = ctx.C["AB_MODEL"], "ab"            # the A/B experiment keeps exploring, routing exploits what it found
        elif routed:
            model, why = routed, "routed"
        elif light and ctx.C["WORKER_MODEL_LIGHT"]:
            model, why = ctx.C["WORKER_MODEL_LIGHT"], "light"
        else:
            model, why = ctx.C["WORKER_MODEL"], "default"
        v = dict(pack=pack, model=model, why=why)
        t["variant"] = v
    return v


ROUTING = {}


def routed_model(t):
    """MODEL_ROUTING=auto: the cheapest worker model that has proven itself for this task's role in this project (see
    stats.pick_model), among the models configured here; None when none has enough history yet. The table is rebuilt at most
    once a minute."""
    import time
    if time.time() - ROUTING.get("at", 0) > 60:
        from .store import tasks
        try:
            ROUTING.update(at=time.time(), table=routing_table(tasks(), run_stats()))
        except Exception:
            ROUTING.update(at=time.time(), table={})
    try:
        default = str(sbx.load_limits().get("WORKER_MODEL") or "")
    except SystemExit:
        default = ""
    cands = [m for m in dict.fromkeys([ctx.C["WORKER_MODEL"] or default, ctx.C["WORKER_MODEL_LIGHT"], ctx.C["AB_MODEL"], default]) if m]
    return pick_model(t.get("role", ""), ROUTING.get("table") or {}, cands, int(ctx.C.get("ROUTING_MIN_TASKS", 5)),
                      int(ctx.C.get("ROUTING_MIN_FIRST_PASS", 70)))
