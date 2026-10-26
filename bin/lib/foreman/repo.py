"""The foreman's read-only clone of main, the code map (bin/lib/codemap.py) and the per-task context pack and variant."""
import hashlib, os, re, shutil, tempfile, threading

import sbx, codemap, quality

from . import ctx
from .util import git_auth_env, sh, slugify
from .store import jload, jsave, tasks
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


def branch_tree(branch):
    """A fresh checkout of origin/<branch> as it is now, in a temporary directory of its own (the caller removes it); None when it cannot be made.
    The shared rebase clone is no place to look at a branch: its working tree belongs to whichever task rebased last."""
    if not sbx.SAFE_ARG.fullmatch(branch or ""):
        return None
    os.makedirs(ctx.FD, exist_ok=True)
    dest = tempfile.mkdtemp(prefix="visual-", dir=ctx.FD)
    os.chmod(dest, 0o755)                    # the gate container runs as another user and has to read it
    rc = sh(["git", "clone", "-q", "--depth", "1", "--single-branch", "--branch", branch, f"{sbx.FORGE_URL}/{sbx.FORGE_REPO}.git", dest],
            timeout=180, env=git_auth_env("monitor.token"))[0]
    if rc != 0:
        shutil.rmtree(dest, ignore_errors=True)
        return None
    return dest


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
    if d.get("sha") != sha or d.get("v") != codemap.VERSION:
        with REPO_LOCK:                      # never read the working tree while refresh_repo() checks out
            rc, head, _ = sh(["git", "rev-parse", "HEAD"], cwd=ctx.REPO)
            m = codemap.build(ctx.REPO, ctx.C["PROJECT_DIR"].strip("/"))
        if head.strip() != sha:              # checkout not at origin/main yet: use it, do not cache it
            return m
        d = dict(sha=sha, v=codemap.VERSION, map=m)
        jsave(p, d)
    CODEMAP.update(d)
    return d["map"]


def shared_files():
    return {x.strip() for x in str(ctx.C["SHARED_FILES"]).split(",") if x.strip()}


def own_files(t):
    """The files a task owns, without shared registries (they do not make a task bigger to understand)."""
    sf = shared_files()
    return [f for f in t.get("files", []) if f not in sf and os.path.basename(f) not in sf]


def wiring_report(g):
    """Prompt text listing the source files this goal's finished tasks own that no other non-test file on main refers to
    (empty when WIRING_CHECK is off or everything is wired). The planner then has to add wiring tasks or delete the file."""
    if str(ctx.C.get("QUALITY_CHECK", "warn")) == "off":
        return ""
    files = sorted({f for t in tasks() if (t.get("goal") or t.get("goal_id")) == g["id"] and t.get("status") == "done" for f in t.get("files", [])})
    try:
        loose = quality.unreferenced(ctx.REPO, files)
    except Exception:
        return ""
    if not loose:
        return ""
    return ("\n\n## Deterministic wiring check on main (not an opinion)\nThese files, delivered by this goal's tasks, are not referenced by any "
            "other non-test file, so nothing uses them: " + ", ".join(f"`{f}`" for f in loose[:20]) + ".\nA goal is NOT done while its deliverable "
            "is unreachable. Reply with follow-up tasks that wire each file into its entry point (router, index.html, registry, caller) "
            "and prove it with a test through that entry point - or a task that deletes the file when the goal does not need it.")


def extras_on():
    return str(ctx.C.get("PACK_EXTRAS", "on")) != "off"


def usage_block(t, cm, max_chars):
    """Who uses the public names of the task's own files, from main (one `git grep`, no LLM): a worker that changes a function
    would otherwise grep for its callers one search at a time. Only places outside the task's own files are listed (the files
    themselves are in the pack); a name nothing else uses is said to be free to change."""
    own = [f for f in own_files(t) if f in cm and not cm[f]["test"]]
    names = {}
    for f in own:
        for n in codemap.public_names(cm[f]):
            if len(n) >= 4:
                names.setdefault(n, f)
    names = dict(list(names.items())[:40])
    if not names:
        return ""
    pd = ctx.C["PROJECT_DIR"].strip("/")
    cmd = ["git", "grep", "-n", "-w", "-I", "--no-color"] + [x for n in names for x in ("-e", n)] + ["origin/main", "--"] + ([pd] if pd else [])
    rc, out, _ = sh(cmd, cwd=ctx.REPO, timeout=30)
    if rc not in (0, 1):
        return ""
    skip = set(t.get("files", []))
    hits = {n: [] for n in names}
    for line in out.splitlines()[:20000]:
        ref, _, rest = line.partition(":")                          # origin/main:path:line:text
        path, _, rest = rest.partition(":")
        lineno, _, text = rest.partition(":")
        if path in skip or not lineno.isdigit():
            continue
        for n in names:
            if n in text and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text):
                hits[n].append((codemap.is_test(path), path, int(lineno)))
    used, free = [], []
    for n, defined_in in names.items():
        hs = sorted(set(hits[n]))
        if not hs:
            free.append(n); continue
        by_file = {}
        for is_t, p, ln in hs:
            by_file.setdefault((is_t, p), []).append(ln)           # non-test files first (False sorts before True)
        top = sorted(by_file.items())[:4]
        shown = ", ".join(f"`{p}:{','.join(map(str, lns[:3]))}`" for (_, p), lns in top)
        rest = len(hs) - sum(len(lns[:3]) for _, lns in top)
        used.append(f"- `{n}` ({defined_in}): {shown}" + (f" (+{rest} more places)" if rest > 0 else ""))
    if not used and not free:
        return ""
    text = "\n".join(used)
    if free:
        text += ("\n" if used else "") + "- not used outside the task's own files (free to change): " + ", ".join(f"`{n}`" for n in free[:20])
    return ("## Who uses what you change (from main; `path:line`)\nCheck these callers (and their tests) before you change a signature or "
            "behaviour; you need no grep for it.\n" + text[:max_chars] + "\n")


def branch_diff(t, max_chars):
    """The task branch's earlier work as a diff against main (from the foreman's clone); empty when there is none or it is too big."""
    if not t.get("branch"):
        return ""
    rc, out, _ = sh(["git", "diff", "-U2", "--no-color", f"origin/main...origin/{t['branch']}"], cwd=ctx.REPO, timeout=30)
    out = out.strip()
    return out if rc == 0 and 0 < len(out) <= max_chars else ""


def context_pack(t, parts=False):
    """What a worker would otherwise spend its first 20-30 tool calls reading: the current content of its own files, the
    interfaces of the modules they import / the spec names / their tests, and a one-line map of the rest. Built from main
    without any LLM; bounded by CONTEXT_PACK_CHARS (it is resent with every agent turn, so more is not better).
    parts=True (PROMPT_ORDER=cache): (stable, task-specific) - the stable text is the same for every task on one main, so it can
    lead the prompt and be cached; the task-specific text follows the task."""
    cm = codemap_main()
    budget = int(ctx.C["CONTEXT_PACK_CHARS"])
    pd = ctx.C["PROJECT_DIR"].strip("/")
    ln = extras_on()
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
                    add(f"### `{f}` - too large for the pack; interface:\n{codemap.render(cm, [f], with_lines=ln)}\n")
            elif codemap.is_test(f):
                head = "\n".join(src.splitlines()[:60])
                add(f"### `{f}` - {src.count(chr(10)) + 1} lines, too large to include whole. Its first 60 lines (imports, fixtures):\n"
                    f"```python\n{head}\n```\nHelpers and existing tests:\n{codemap.render(cm, [f], with_lines=ln)}\nAdd new tests at the end; do not "
                    f"rewrite existing ones.\n")
            else:
                add(f"### `{f}` - {src.count(chr(10)) + 1} lines, too large to include whole; its interface:\n{codemap.render(cm, [f], with_lines=ln)}\n"
                    f"Open only the parts you change.\n")
    else:
        d = branch_diff(t, min(20000, budget // 2)) if extras_on() else ""
        if d:
            add("## Your branch's earlier work (diff against main - your own files as they are on the branch, so do not `cat` or `git diff` them)\n"
                f"```diff\n{d}\n```\n")
        else:
            add("(Your branch already has earlier work, so your own files are not included; the changed files are listed in the rework section.)\n")
    if extras_on():
        u = usage_block(t, cm, budget // 4)
        if u:
            add(u)
    related = []
    for f in mine:
        if f in cm:
            related += codemap.imports_of(f, read_repo(f, 200000), cm)
        tf = codemap.test_for(f, cm)
        if tf:
            related.append(tf)
    related += [r for r in t.get("read", []) if r in cm]
    related += codemap.mentioned_in(t.get("spec", "") + " " + " ".join(t.get("acceptance", [])), cm)
    related = [r for r in dict.fromkeys(related) if r not in shown and r not in mine]
    if related:
        add("## Interfaces you will use (from main - signatures and docstrings, no bodies)\n" + codemap.render(cm, related, max_chars=budget // 3, with_lines=ln) + "\n")
    intro = ("## Context pack (generated from main for this task)\nThis already contains the files and interfaces you need. Do not open "
             "them again with tools; read further files only when something you need is really missing here.\n"
             + ("`[L12-40]` after a definition = its lines: read just those (`sed -n '12,40p' file`) instead of grepping for it.\n" if ln else "")
             + f"Test runner: `{sbx.test_cmd()}` (run targeted tests for your changes; merge gate runs full suite){f' (project code in `{pd}/`)' if pd else ''}.\n\n")
    if parts:
        allfiles = codemap.render(cm, sorted(cm), with_api=False, with_lines=False, sizes=False, max_chars=max(2000, budget // 3))
        return (intro + "## All files in the project (one line each)\n" + allfiles + "\n" if cm else ""), "\n".join(out)
    rest = [p for p in sorted(cm) if p not in shown and p not in related and p not in mine]
    if rest:
        add("## Other files in the project (one line each)\n" + codemap.render(cm, rest, with_api=False, max_chars=max(2000, budget - used - 500)) + "\n")
    if not out:
        return ""
    return intro + "\n".join(out)


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
