"""Deterministic quality measurements of code, no LLM: how much of a change is tests, and which new source files nothing refers to.

Public interface: is_test_path(path), is_source_path(path), reference_pattern(path), unreferenced(repo_dir, paths),
test_ratio(numstat_lines). Used by bin/review-branch (per branch) and the foreman (per goal, on main).
Invariants: pure functions over a checked-out tree; a file counts as wired when ANOTHER non-test file mentions its module name
(Python: the module name, JS/CSS/HTML: the file name with its extension); entry points and generated names are never reported.
"""
import os, re

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
    out = subprocess.run(["git", "ls-files", "-z"], cwd=repo_dir, capture_output=True, text=True).stdout
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
