"""Deterministic quality measurements of code, no LLM: how much of a change is tests, and which new source files nothing refers to.

Public interface: is_test_path(path), is_source_path(path), reference_pattern(path), unreferenced(repo_dir, paths),
test_ratio(numstat_lines), ratio_message(tests, code, max), oversized(repo_dir, paths, limit), outside_scope(changed, allowed, shared),
weakened_tests(diff_text), injected_markers(diff_text, messages), forbidden_added(diff_text, rules), duplicate_defs(repo_dir, changed, base), unused_imports(text, before), test_targets(repo_dir, changed).
Used by bin/review-branch (per branch), by
the supervisor's self-check inside the worker (this file is sent along with it: standard library only, Python 3.8+, no imports of
other project modules) and by the foreman (per goal, on main).
Invariants: pure functions over a checked-out tree; a file counts as wired when ANOTHER non-test file mentions its module name
(Python: the module name, JS/CSS/HTML: the file name with its extension); entry points and generated names are never reported;
every finding is a fact about the tree (never a guess), and a file that cannot be read or parsed is skipped, not reported.
"""
import ast, difflib, os, re

SOURCE_EXT = (".py", ".js", ".mjs", ".ts", ".css")
TEST_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py|[^/]*\.test\.[mc]?[jt]s|[^/]*\.spec\.[mc]?[jt]s)$|(^|/)(tests?|__tests__)/")
ENTRY_STEMS = {"__init__", "__main__", "conftest", "setup", "main", "index", "app", "manifest", "sw", "service-worker"}
READ_MAX = 400_000


def is_test_path(path):
    return bool(TEST_RE.search(path))


def is_source_path(path):
    return path.endswith(SOURCE_EXT) and not is_test_path(path)


def reference_pattern(path):
    """Regex that finds a mention of this file in another file: a Python module by its name, anything else by its file name."""
    base = os.path.basename(path)
    name = base[:-3] if path.endswith(".py") else base
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + (r"(?![A-Za-z0-9_])" if path.endswith(".py") else r"(?![A-Za-z0-9_-])"))


def _tracked(repo_dir):
    import subprocess
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo_dir, capture_output=True, encoding="utf-8", errors="replace").stdout
    return [p for p in out.split("\0") if p]


def unreferenced(repo_dir, paths):
    """Of `paths` (relative to repo_dir), the source files that no other non-test file mentions."""
    want = [p for p in paths if is_source_path(p) and os.path.splitext(os.path.basename(p))[0] not in ENTRY_STEMS
            and os.path.exists(os.path.join(repo_dir, p))]
    if not want:
        return []
    pats = {p: reference_pattern(p) for p in want}
    left = set(want)
    for q in _tracked(repo_dir):
        if not left:
            break
        if is_test_path(q) or q.endswith((".lock", ".png", ".svg", ".ico", ".db", ".woff2")):
            continue
        try:
            with open(os.path.join(repo_dir, q), errors="ignore") as f:
                text = f.read(READ_MAX)
        except OSError:
            continue
        for p in list(left):
            if p != q and pats[p].search(text):
                left.discard(p)
    return sorted(left)


def test_ratio(numstat_lines):
    """(test lines added, code lines added) from `git diff --numstat` output; documentation and data files count as neither."""
    tests = code = 0
    for l in numstat_lines:
        parts = l.split("\t")
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        n, path = int(parts[0]), parts[2]
        if is_test_path(path) and path.endswith(SOURCE_EXT):
            tests += n
        elif is_source_path(path):
            code += n
    return tests, code


def oversized(repo_dir, paths, limit=400):
    """[(path, lines)] of the source files in `paths` that are longer than `limit` lines."""
    out = []
    for p in paths:
        if not is_source_path(p):
            continue
        try:
            with open(os.path.join(repo_dir, p), errors="ignore") as f:
                n = sum(1 for _ in f)
        except OSError:
            continue
        if n > limit:
            out.append((p, n))
    return out


def ratio_message(t_add, c_add, ratio_max=1.5):
    """The gate's verdict on a change's test/code line ratio: '' when fine, else the instruction for the worker."""
    if not c_add or t_add <= max(150, ratio_max * c_add):
        return ""
    return (f"too many tests: {t_add} test lines for {c_add} code lines (limit {ratio_max} per code line, at least 150 allowed). "
            f"Keep tests for behaviour and invariants (what the function decides, edge cases that change the result); delete tests of "
            f"input validation boundaries that a schema already enforces, repeated cases that one parametrised test covers, and tests "
            f"of implementation details. Aim for about as many test lines as code lines.")


def _tested_stem(path):
    """The module a test file is for: tests/test_foo.py, foo_test.py, foo.test.mjs, foo.spec.ts -> foo."""
    return re.sub(r"^test_|_test$|\.(test|spec)$", "", os.path.splitext(os.path.basename(path))[0])


def outside_scope(changed, allowed, shared=()):
    """Of `changed` ([(status, path)], status A/M/D), the paths a task was not given: not in `allowed` (a file name alone also
    matches), not a shared registry, and not a NEW test file of an allowed module. Empty `allowed` = unrestricted task."""
    if not allowed:
        return []
    ok = set(allowed) | set(shared)
    stems = {os.path.splitext(os.path.basename(a))[0] for a in allowed if is_source_path(a)}
    out = []
    for st, p in changed:
        if p in ok or os.path.basename(p) in ok:
            continue
        if st.startswith("A") and is_test_path(p) and _tested_stem(p) in stems:
            continue
        out.append(p)
    return out


_ASSERT_RE = re.compile(r"\bassert\w*\b|\bexpect\(|\.to[A-Z]\w*\(")
_TESTDEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+test_|^[ \t]*(?:test|it)\(")


def weakened_tests(diff_text):
    """[(path, assertions removed, added, test functions removed, added)] for EXISTING test files whose `git diff --unified=0` loses
    assertions or test functions on balance (a rewrite that keeps the count is not reported)."""
    stats, path, old = {}, None, None
    for l in diff_text.splitlines():
        if l.startswith("--- "):
            old = l[4:].strip()
        elif l.startswith("+++ "):
            new = l[4:].strip()
            path = new[2:] if new.startswith("b/") and old != "/dev/null" and new != "/dev/null" and is_test_path(new[2:]) else None
            if path:
                stats[path] = [0, 0, 0, 0]
        elif path and l[:1] in "+-":
            i = 0 if l[0] == "-" else 1
            if _ASSERT_RE.search(l[1:]):
                stats[path][i] += 1
            if _TESTDEF_RE.search(l[1:]):
                stats[path][2 + i] += 1
    return [(p, *v) for p, v in stats.items() if (v[0] > v[1] and v[0] >= 2) or v[2] > v[3]]


# Attribution / session lines that text planted in a tool result or a fetched page asks the agent to put into commits and PRs. They are data,
# never instructions; a worker that obeyed one shows up here.
INJECTION_RE = re.compile(r"Co-Authored-By:\s*Claude|Claude-Session:|Generated with \[?Claude|claude\.ai/code/session", re.I)


def _added_lines(diff_text):
    """[(path, line)] of the added lines of a `git diff --unified=0` text."""
    path, out = None, []
    for l in diff_text.splitlines():
        if l.startswith("+++ "):
            path = l[6:] if l.startswith("+++ b/") else None
        elif l.startswith("+") and path:
            out.append((path, l[1:]))
    return out


def injected_markers(diff_text, messages=""):
    """Descriptions of injection-style attribution lines: in the commit messages (one text) or in added lines of code files
    (tests and markdown may quote them)."""
    hits = ["commit message: " + m.group(0) for m in INJECTION_RE.finditer(messages or "")][:3]
    for p, l in _added_lines(diff_text):
        if INJECTION_RE.search(l) and not is_test_path(p) and not p.endswith(".md"):
            hits.append(f"{p}: {l.strip()[:80]}")
    return hits[:6]


def forbidden_added(diff_text, rules):
    """[(path, message, count)] for added lines that match a project rule. `rules`: [{"re": regex, "msg": text, "paths": path regex}]
    (project setting LINT_FORBID, JSON): a fast lint the worker's self-check runs before the long gate. Bad rules are skipped."""
    found = {}
    for r in rules or []:
        try:
            rx, pr = re.compile(r["re"]), re.compile(r.get("paths") or ".")
            msg = str(r["msg"])
        except (KeyError, TypeError, re.error):
            continue
        for p, l in _added_lines(diff_text):
            if pr.search(p) and rx.search(l):
                found[(p, msg)] = found.get((p, msg), 0) + 1
    return [(p, m, n) for (p, m), n in found.items()][:8]


DUP_EXT = (".py", ".js", ".mjs", ".ts")
DUP_MIN_LINES = 4
DUP_SIMILAR = 0.9
DUP_SKIP = {"validate", "main", "run", "setup", "teardown", "init", "handler", "render", "default", "build", "create", "update", "load", "save",
            "parse", "start", "stop", "fixture", "app", "index", "wrapper", "decorator", "inner", "helper", "format", "check"}
_JS_DEF_RE = re.compile(r"(?:export[ \t]+)?(?:async[ \t]+)?(?:function\*?[ \t]+([A-Za-z_$][\w$]*)|(?:const|let)[ \t]+([A-Za-z_$][\w$]*)[ \t]*="
                        r"[ \t]*(?:async[ \t]*)?(?:function\b|\(|[A-Za-z_$][\w$]*[ \t]*=>))")


def _norm(lines):
    return "\n".join(s for s in (l.strip() for l in lines) if s and not s.startswith(("#", "//")))


def top_level_defs(text, path):
    """{name: normalized source} of the top-level functions (no methods, no nested ones) of a Python or JS file that are at least
    DUP_MIN_LINES lines long; a file that does not parse gives {}."""
    out, lines = {}, text.splitlines()
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return out
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.end_lineno:
                body = _norm(lines[n.lineno - 1:n.end_lineno])
                if body.count("\n") + 1 >= DUP_MIN_LINES:
                    out[n.name] = body
        return out
    for i, l in enumerate(lines):
        m = _JS_DEF_RE.match(l)
        if not m or not l.rstrip().endswith(("{", "(", "=>")):
            continue
        for j in range(i + 1, min(len(lines), i + 300)):
            if lines[j].startswith(("}", ")")):
                body = _norm(lines[i:j + 1])
                if body.count("\n") + 1 >= DUP_MIN_LINES:
                    out[m.group(1) or m.group(2)] = body
                break
    return out


def _git(repo_dir, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=repo_dir, capture_output=True, encoding="utf-8", errors="replace").stdout


def duplicate_defs(repo_dir, changed, base=None, limit=8):
    """[(path, name, other_path, lines)]: top-level functions that a change ADDS (name not defined in the file at `base` before) and
    that another Python/JS file of the tree already defines under the same name with a (nearly) identical body - a copied helper.
    `changed` = [(status, path)]. Short functions, generic names and test_* names are ignored; copies inside one branch count."""
    cand = {}
    for st, p in changed:
        if not (st[:1] in "AM" and p.endswith(DUP_EXT) and os.path.exists(os.path.join(repo_dir, p))):
            continue
        try:
            with open(os.path.join(repo_dir, p), errors="ignore") as f:
                now = top_level_defs(f.read(READ_MAX), p)
        except OSError:
            continue
        before = top_level_defs(_git(repo_dir, "show", f"{base}:{p}"), p) if base and st[:1] == "M" else {}
        for n, body in now.items():
            if n not in before and n not in DUP_SKIP and len(n) >= 4 and not n.startswith("test") and not (n.startswith("__") and n.endswith("__")):
                cand.setdefault(n, []).append((p, body))
    if not cand:
        return []
    hit = re.compile(r"^(?:export[ \t]+)?(?:async[ \t]+)?(?:def|function\*?|const|let)[ \t]+(?:" + "|".join(re.escape(n) for n in cand) + r")\b", re.M)
    files = set(_tracked(repo_dir)) | {p for p in _git(repo_dir, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p}
    mine_paths = {p for lst in cand.values() for p, _ in lst}
    matches = {}
    for q in sorted(files):
        if not q.endswith(DUP_EXT) or re.search(r"(^|/)(node_modules|vendor|third_party|dist|build)/", q):
            continue
        try:
            with open(os.path.join(repo_dir, q), errors="ignore") as f:
                text = f.read(READ_MAX)
        except OSError:
            continue
        if not hit.search(text):
            continue
        for n, body in top_level_defs(text, q).items():
            for p, mine in cand.get(n, []):
                if p != q and (mine == body or difflib.SequenceMatcher(None, mine, body).ratio() >= DUP_SIMILAR):
                    matches.setdefault((p, n), []).append((q, mine.count("\n") + 1))
    found, pairs = [], set()
    for (p, n), others in sorted(matches.items()):
        old = [o for o in others if o[0] not in mine_paths]            # a copy of code that was already there beats a copy among new files
        q, lines = (old or others)[0]
        if not old and (n, q, p) in pairs:
            continue                                                    # two new files copying each other: report the pair once
        pairs.add((n, p, q))
        found.append((p, n, q, lines))
    return found[:limit]


def unused_imports(text, before=None):
    """Names a Python module imports at its top level and never uses ([] for a file that does not parse, `# noqa` lines, __future__,
    star imports and names listed in __all__). Imports inside try/if blocks are left alone: they are mostly optional-dependency probes.
    With `before` (the file's earlier text) only names that were not already unused there are returned: the change's own leftovers."""
    now = _unused_imports(text)
    old = set(_unused_imports(before)) if before else set()
    return [n for n in now if n not in old]


def _unused_imports(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines = text.splitlines()
    names = {}
    for n in tree.body:
        if isinstance(n, ast.Import):
            for a in n.names:
                names.setdefault(a.asname or a.name.split(".")[0], n.lineno)
        elif isinstance(n, ast.ImportFrom) and n.module != "__future__":
            for a in n.names:
                if a.name != "*":
                    names.setdefault(a.asname or a.name, n.lineno)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 120:
            used.update(re.findall(r"[A-Za-z_]\w*", n.value))            # __all__ entries and string annotations
    return sorted(k for k, ln in names.items() if k not in used and "noqa" not in lines[ln - 1])


def _all_files(repo_dir):
    return sorted(set(_tracked(repo_dir)) | {p for p in _git(repo_dir, "ls-files", "-z", "--others", "--exclude-standard").split("\0") if p})


def test_targets(repo_dir, changed, limit=30):
    """Python test files worth running first for a change (`changed` = [(status, path)]): the changed test files themselves, then
    tests named after a changed module (test_<module>.py anywhere), then tests that import one. Only paths that are safe as
    command-line arguments; at most `limit`."""
    files = _all_files(repo_dir)
    tests = [f for f in files if f.endswith(".py") and is_test_path(f) and not f.endswith("conftest.py")]
    out = []

    def add(p):
        if p not in out and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._/-]*", p) and os.path.exists(os.path.join(repo_dir, p)):
            out.append(p)
    mods = []
    for st, p in changed:
        if st[:1] == "D" or not p.endswith(".py"):
            continue
        if is_test_path(p):
            add(p)
        elif os.path.splitext(os.path.basename(p))[0] not in ENTRY_STEMS:
            mods.append(os.path.splitext(os.path.basename(p))[0])
    for m in mods:
        for t in tests:
            if os.path.basename(t) in (f"test_{m}.py", f"{m}_test.py"):
                add(t)
    if mods:
        alt = "|".join(re.escape(m) for m in mods)
        imp = re.compile(r"^[ \t]*(?:from|import)[ \t]+(?:" + alt + r")\b", re.M)
        for t in tests:
            if len(out) >= limit:
                break
            try:
                with open(os.path.join(repo_dir, t), errors="ignore") as f:
                    if imp.search(f.read(READ_MAX)):
                        add(t)
            except OSError:
                pass
    return out[:limit]


def fast_pytest_cmd(cmd):
    """True when `cmd` is a plain pytest invocation (flags only, no paths or selectors of its own), so that test files can simply be
    appended to run a subset first."""
    import shlex
    try:
        t = shlex.split(cmd)
    except ValueError:
        return False
    if t[:3] == ["python3", "-m", "pytest"] or t[:3] == ["python", "-m", "pytest"]:
        t = t[3:]
    elif t[:1] == ["pytest"]:
        t = t[1:]
    else:
        return False
    return all(x.startswith("-") or (i and t[i - 1] in ("-p", "-W")) for i, x in enumerate(t))
