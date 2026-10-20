"""Pure helpers (selftested): JSON out of an LLM reply, usage-limit messages, task readiness, the planner's output made
safe, and small shell/git helpers."""
import datetime as dt, json, os, re, subprocess, time

import sbx

from . import ctx


FILES_LINE = re.compile(r"^[\w./-]+$")


# ---------------------------------------------------------------- pure helpers (selftested)
def extract_json(text):
    """First top-level JSON object in an LLM reply (tolerates ``` fences and prose around it)."""
    s = text.find("{")
    while s != -1:
        depth, instr, esc = 0, False, False
        for i in range(s, len(text)):
            ch = text[i]
            if instr:
                esc = (ch == "\\") and not esc
                if ch == '"' and not esc:
                    instr = False
                continue
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[s:i + 1])
                    except Exception:
                        break
        s = text.find("{", s + 1)
    return None


LIMIT_RE = re.compile(r"(hit your [\w ]*limit|usage limit|limit reached|quota reached|quota exhausted|out of (extra )?usage|RESOURCE_EXHAUSTED)", re.I)


RESET_RE = re.compile(r"resets?\s+(?:at\s+)?(?:([A-Z][a-z]{2,8}\s+\d{1,2}),?\s+(?:at\s+)?)?(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*(?:\(([^)]+)\))?", re.I)


REL_RESET_RE = re.compile(r"resets?\s+in\s+(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?", re.I)


ISO_RESET_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)", re.I)


def parse_limit(text, now=None):
    """None if `text` is not a usage-limit message, else the epoch second to retry at (reset + 2 min; 60 min if unparseable)."""
    if not LIMIT_RE.search(text or ""):
        return None
    now = now or time.time()
    m_rel = REL_RESET_RE.search(text)
    if m_rel and any(m_rel.groups()):
        h = int(m_rel.group(1) or 0)
        m = int(m_rel.group(2) or 0)
        s = int(m_rel.group(3) or 0)
        secs = h * 3600 + m * 60 + s
        if secs > 0:
            return now + secs + 120
    m_iso = ISO_RESET_RE.search(text)
    if m_iso:
        try:
            d = dt.datetime.fromisoformat(m_iso.group(1).replace("Z", "+00:00"))
            return d.timestamp() + 120
        except Exception:
            pass
    import zoneinfo
    m = RESET_RE.search(text)
    if not m:
        return now + 3600
    day, hh, mm, ampm, tz = m.groups()
    try:
        z = zoneinfo.ZoneInfo(tz.strip()) if tz else zoneinfo.ZoneInfo(ctx.C["LOCAL_TZ"])
    except Exception:
        z = zoneinfo.ZoneInfo(ctx.C["LOCAL_TZ"])
    h = int(hh) % 12 + (12 if (ampm or "").lower() == "pm" else 0) if ampm else int(hh)
    local = dt.datetime.fromtimestamp(now, z)
    cand = local.replace(hour=h, minute=int(mm or 0), second=0, microsecond=0)
    if day:
        try:
            md = dt.datetime.strptime(day.strip() + f" {local.year}", "%b %d %Y")
        except ValueError:
            md = dt.datetime.strptime(day.strip() + f" {local.year}", "%B %d %Y")
        cand = cand.replace(month=md.month, day=md.day)
        if cand.timestamp() <= now:
            cand = cand.replace(year=cand.year + 1)
    while cand.timestamp() <= now:
        cand += dt.timedelta(days=1)
    return cand.timestamp() + 120


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:40] or "task"


def parse_numstat(text):
    """(added, removed) totals from `git diff --numstat` output; binary files ('-\t-\tpath') contribute 0."""
    added = removed = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            added += int(parts[0]); removed += int(parts[1])
    return added, removed


def ready_tasks(ts, running_files, goals_by_id=None):
    """Pending tasks whose dependencies are done, whose files do not overlap a running task's files and whose goal is not
    paused - the owner's prioritized goals first, then oldest first. SHARED_FILES (registries every task appends one entry to)
    never block: a conflict there is a small rebase, which the merge step sends back without counting a failed attempt."""
    running_files = set(running_files) - {f.strip() for f in str(ctx.C["SHARED_FILES"]).split(",") if f.strip()}
    done = {t["id"] for t in ts if t["status"] == "done"}
    gm = goals_by_id or {}
    out = []
    for t in ts:
        if t["status"] != "pending" or t.get("not_before", 0) > time.time():
            continue
        if (gm.get(t.get("goal")) or {}).get("paused"):
            continue
        if not all(d in done for d in t.get("depends_on", [])):
            continue
        if set(t.get("files", [])) & running_files:
            continue
        out.append(t)
    return sorted(out, key=lambda t: (-int((gm.get(t.get("goal")) or {}).get("priority") or 0), t["id"]))


def approvals():
    """What the owner wants to approve before the team goes on (APPROVE): a subset of {"plan", "risky", "merge"}."""
    return {x.strip() for x in str(ctx.C.get("APPROVE", "")).split(",") if x.strip()}


# ---------------------------------------------------------------- shell helpers
def sh(args, timeout=600, cwd=None, inp=None, env=None):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd, input=inp,
                           env=dict(os.environ, **env) if env else None)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    except OSError as e:
        # missing program, missing cwd, or an argument over the kernel's 128 KiB limit (E2BIG): a failed call, not a crashed step
        return 127, "", f"{type(e).__name__}: {e}"


def git_auth_env(token_file):
    """Forgejo auth for git through the environment (GIT_CONFIG_*), not argv: argv is world-readable in /proc/*/cmdline."""
    tok = open(os.path.join(sbx.SECRETS, token_file)).read().strip()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: token {tok}"}


def fmt_ts(ts):
    import zoneinfo
    return dt.datetime.fromtimestamp(ts, zoneinfo.ZoneInfo(ctx.C["LOCAL_TZ"])).strftime("%d.%m. %H:%M") if ts else "-"


# ---------------------------------------------------------------- steps (run in worker threads, return a function applied in the main loop)
def _strs(v):
    return [str(x) for x in v if isinstance(x, (str, int, float))] if isinstance(v, list) else ([str(v)] if isinstance(v, str) and v else [])


FULL_SUITE_RE = re.compile(r"\b(full|entire|whole)\s+(test\s+)?suite\b|\ball\s+(existing\s+)?tests?\s+(in\s+the\s+repo(sitory)?\s+)?pass", re.I)


def sanitize_acceptance(items):
    """Ensures tasks never demand running the entire repository test suite, which causes worker timeouts."""
    out = []
    for a in _strs(items):
        s = str(a).strip()
        if not s:
            continue
        if FULL_SUITE_RE.search(s):
            out.append("relevant tests for touched files/domain pass (merge gate runs full suite)")
        else:
            out.append(s[:500])
    return out[:12]


def norm_tasks(d, start, limit=8):
    """The planner's task list made safe to use, whatever the model returned: only objects, every field coerced to the
    expected type, unique ids. Returns [(task_id, fields, [depends_on task ids])]. Malformed output used to raise in the
    main loop - the goal stayed "new" and was re-planned (and re-paid) every tick."""
    raw = d.get("tasks") if isinstance(d, dict) and isinstance(d.get("tasks"), list) else []
    items = [x for x in raw if isinstance(x, dict)][:limit]
    ids, tids, out = {}, [], []
    for i, x in enumerate(items):
        orig = str(x.get("key") or "").strip()
        slug = slugify(orig or str(x.get("title") or "task"))
        tid = f"{start + i:04d}-{slug}"          # the number makes it unique
        tids.append(tid)
        ids.setdefault(orig or str(i), tid)       # a duplicate key points at its first task
        ids.setdefault(slug, tid)
    for i, x in enumerate(items):
        tid = tids[i]
        deps = [ids[str(k)] for k in _strs(x.get("depends_on")) if str(k) in ids and ids[str(k)] != tid]
        fields = dict(title=str(x.get("title") or tid)[:150], role=str(x.get("role") or "developer")[:60],
                      risk="risky" if str(x.get("risk", "")).lower() == "risky" else "routine",
                      files=[f for f in (x.get("files") if isinstance(x.get("files"), list) else [])
                             if isinstance(f, str) and FILES_LINE.match(f) and ".." not in f.split("/") and not f.startswith("/")][:20],
                      read=[f for f in (x.get("read") if isinstance(x.get("read"), list) else []) if isinstance(f, str) and FILES_LINE.match(f) and ".." not in f.split("/") and not f.startswith("/")][:5],
                      spec=str(x.get("spec") or "")[:20000], acceptance=sanitize_acceptance(x.get("acceptance")))
        out.append((tid, fields, deps))
    return out


def _iso_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
