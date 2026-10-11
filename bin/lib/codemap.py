"""A deterministic map of a repository's code: for every source file its purpose (module docstring or first comment) and its
public interface (top-level functions/classes with their signatures and first docstring line). No LLM, no tokens.

Used by the foreman for
  - the planner's context (one compact line block per module instead of the first 60 lines of every file), and
  - each worker's context pack (the interfaces of the modules its files import, so it does not open them to find out).
It is always generated from the current main, so - unlike hand-written docs - it cannot go stale. Its quality rises with the
module docstrings the team writes (team rule: every module starts with what it is for, its interface and its invariants)."""
import ast, os, re

SRC_EXT = (".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".sh")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".pytest_cache", ".mypy_cache"}


def is_test(path):
    n = os.path.basename(path)
    return n.startswith("test_") or n.endswith(("_test.py", ".test.js", ".test.ts", ".spec.ts", ".spec.js", "_test.go")) or n == "conftest.py"


def _first_line(doc):
    """The first meaningful line of a docstring (a bare file name/path line such as "pkg/tools/x.py" is skipped)."""
    for l in (doc or "").strip().splitlines():
        l = l.strip()
        if l and not re.fullmatch(r"[\w./-]+\.\w{1,4}", l):
            return l[:160]
    return ""


def _sig(node, src_lines=None):
    """`def name(args) -> ret` / `class Name(bases)` rebuilt from the syntax tree (no body, no decorators)."""
    try:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            return f"class {node.name}" + (f"({bases})" if bases else "")
        pre = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
        return f"{pre} {node.name}({ast.unparse(node.args)}){ret}"[:220]
    except Exception:
        return (src_lines or [""])[node.lineno - 1].strip()[:220] if src_lines else node.name


def python_entry(src):
    """(purpose, [interface lines]) of a Python module."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return "(does not parse)", []
    lines = src.splitlines()
    purpose = _first_line(ast.get_docstring(tree))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            d = _first_line(ast.get_docstring(node))
            out.append(_sig(node, lines) + (f"  # {d}" if d else ""))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            d = _first_line(ast.get_docstring(node))
            out.append(_sig(node, lines) + (f"  # {d}" if d else ""))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (not sub.name.startswith("_") or sub.name == "__init__"):
                    sd = _first_line(ast.get_docstring(sub))
                    out.append("    " + _sig(sub, lines) + (f"  # {sd}" if sd else ""))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper() and not node.targets[0].id.startswith("_"):
            out.append(lines[node.lineno - 1].strip()[:160])
    return purpose, out


GENERIC_DEF = re.compile(r"^\s*(export\s+(default\s+)?)?(async\s+)?(function\*?\s+\w+\s*\([^)]*\)|class\s+\w+[^{]*|"
                         r"(const|let)\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*=>|func\s+(\([^)]*\)\s*)?\w+\s*\([^)]*\)[^{]*|"
                         r"(pub\s+)?fn\s+\w+[^{]*|def\s+\w+[^\n]*|(public|private|protected)?\s*(static\s+)?[\w<>\[\]]+\s+\w+\s*\([^)]*\)\s*\{)")


def generic_entry(src):
    lines = src.splitlines()
    purpose = ""
    for l in lines[:15]:
        s = l.strip().lstrip("/#*!").strip()
        if s and not s.startswith(("usr/bin", "use strict", "-*-")):
            purpose = s[:160]; break
    out = [re.sub(r"\s*\{\s*$", "", l.strip())[:200] for l in lines if GENERIC_DEF.match(l) and not l.startswith((" ", "\t"))]
    return purpose, out[:40]


def entry(path, src):
    if path.endswith(".py"):
        return python_entry(src)
    return generic_entry(src)


def build(root, subdir="", max_files=400):
    """{path: {"lines": n, "purpose": str, "api": [str], "test": bool}} for every source file under root/subdir."""
    res = {}
    base = os.path.join(root, subdir) if subdir else root
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            if not f.endswith(SRC_EXT):
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            try:
                src = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            purpose, api = entry(rel, src)
            res[rel] = dict(lines=src.count("\n") + 1, purpose=purpose, api=api, test=is_test(rel))
            if len(res) >= max_files:
                return res
    return res


def render(cm, paths=None, with_api=True, max_chars=40000):
    """Markdown block: one heading line per module (path, size, purpose) and, if with_api, its interface."""
    out, n = [], 0
    for p in (paths if paths is not None else sorted(cm)):
        e = cm.get(p)
        if not e:
            continue
        head = f"- `{p}` ({e['lines']} lines){': ' + e['purpose'] if e['purpose'] else ' - NO MODULE DOCSTRING'}"
        if with_api and e["api"] and e["test"]:
            # a test file: its helpers/fixtures in full, the test names compactly (new tests should reuse the helpers)
            helpers = [a for a in e["api"] if not re.match(r"(async )?def test_", a) and not a.startswith("    ")]
            names = [re.sub(r"^\s*(async )?def (test_\w+).*", r"\2", a) for a in e["api"] if re.match(r"\s*(async )?def test_", a)]
            s = head + "".join("\n    " + a for a in helpers[:25])
            if names:
                tl = ", ".join(names)
                s += f"\n    tests ({len(names)}): " + (tl[:1200] + " …" if len(tl) > 1200 else tl)
        else:
            s = head + ("\n" + "\n".join("    " + a for a in e["api"][:40]) if with_api and e["api"] and not e["test"] else "")
        if n + len(s) > max_chars:
            s = head                                  # no room for the interface: keep at least the one-line entry
        if n + len(s) > max_chars:
            out.append(f"- … more files (map truncated)")
            break
        out.append(s); n += len(s) + 1
    return "\n".join(out)


PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+))", re.M)
JS_IMPORT = re.compile(r"""(?:import\s[^'"]*from\s*|require\()\s*['"](\.{1,2}/[^'"]+)['"]""")


def imports_of(path, src, cm):
    """Repo files that `path` imports (Python module names resolved against the map; relative JS/TS imports)."""
    found = set()
    if path.endswith(".py"):
        here = os.path.dirname(path)
        for a, b in PY_IMPORT.findall(src):
            for mod in ([a] if a else [x.strip().split(" ")[0] for x in b.split(",")]):
                rel = mod.replace(".", "/") + ".py"
                for cand in (rel, os.path.join(here, rel), os.path.join(here, mod.split(".")[-1] + ".py")):
                    cand = os.path.normpath(cand)
                    if cand in cm and cand != path:
                        found.add(cand)
    else:
        here = os.path.dirname(path)
        for rel in JS_IMPORT.findall(src):
            base = os.path.normpath(os.path.join(here, rel))
            for cand in (base, base + ".js", base + ".ts", base + ".tsx", base + "/index.js", base + "/index.ts"):
                if cand in cm:
                    found.add(cand); break
    return sorted(found)


def mentioned_in(text, cm):
    """Repo files whose path or file name appears in a spec text."""
    out = []
    for p in cm:
        name = os.path.basename(p)
        if p in text or re.search(r"(?<![\w/.-])" + re.escape(name) + r"(?![\w-])", text) or \
                (name.endswith(".py") and re.search(r"(?<![\w.])" + re.escape(name[:-3]) + r"\.\w+\(", text)):
            out.append(p)
    return sorted(out)


def test_for(path, cm):
    """The test file that belongs to a module (test_<name>.py next to it or under tests/), if the map has one."""
    d, n = os.path.split(path)
    stem, ext = os.path.splitext(n)
    for cand in (os.path.join(d, f"test_{stem}{ext}"), os.path.join(d, "tests", f"test_{stem}{ext}"), os.path.join("tests", f"test_{stem}{ext}"),
                 os.path.join(d, f"{stem}.test{ext}"), os.path.join(d, f"{stem}_test{ext}")):
        if os.path.normpath(cand) in cm:
            return os.path.normpath(cand)
    return None
