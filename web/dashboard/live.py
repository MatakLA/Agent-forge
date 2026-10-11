"""Live transcripts: one tail per running AI (the orchestrator's Claude Code session, each worker's current run), rendered into
short typed lines for the Live tab; a roster of every AI ever seen; the Antigravity quota of each worker."""
import atexit, collections, json, os, re, signal, subprocess, threading, time

from .backend import (ALL_PROCS, CLAUDE_PROJECT, DISCOVER_INTERVAL, FINISHED_GRACE, FORGE_DIR, LINE_BUFFER, SSH_BASE, reap,
                      ssh_run)


# ---- rendering: turn one raw JSON line into zero or more (kind, text) pairs -----------------------------------
# kind is one of: user, assistant, tool, tool_done, result — used for stats and for coloring lines in the UI.
def trunc(s, n=4000):
    s = (s or "").strip().replace("\r", "")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_orchestrator_line(d, acc):
    t = d.get("type")
    if t == "assistant":
        for block in (d.get("message") or {}).get("content") or []:
            bt = block.get("type")
            if bt == "text" and block.get("text", "").strip():
                yield "assistant", trunc(block["text"])
            elif bt == "tool_use":
                yield "tool", trunc(f"{block.get('name')}({json.dumps(block.get('input', {}), ensure_ascii=False)})")
    elif t == "user":
        content = (d.get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            yield "user", trunc(content)
        elif isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    val = block.get("content")
                    text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False) if val else ""
                    yield "tool_done", trunc(text)
    elif t == "last-prompt":
        text = d.get("lastPrompt")
        if text:
            yield "task", trunc(text, 140)


def render_worker_line(d, acc):
    """One event of a worker's stream, whatever its runtime (detected from the event's shape): agy has "event"; Claude Code has
    type system/assistant/user/result; Codex has thread.* / turn.* / item.*; Gemini has init/message/tool_use/tool_result/result
    with a "timestamp"."""
    t = d.get("type")
    if "event" not in d and isinstance(t, str):
        if t.startswith("aider."):
            yield from _render_aider(d)
            return
        if t.startswith(("thread.", "turn.", "item.")) or (t == "error" and "timestamp" not in d):
            yield from _render_codex(d)
        elif "timestamp" in d and t in ("init", "message", "tool_use", "tool_result", "result", "error"):
            yield from _render_gemini(d, acc)
        else:
            yield from _render_claude(d)
        return
    yield from _render_agy(d, acc)


def is_final(d):
    """The event that ends a worker's run, for any runtime."""
    t = d.get("type")
    return d.get("event") == "result" or t == "result" or t in ("turn.completed", "turn.failed", "aider.result")


def _render_aider(d):
    """Aider's output lines (wrapped into events by worker/af-aider-events): edits and commands as tools, the rest as text."""
    t = d.get("type")
    if t == "aider.line":
        s = str(d.get("text") or "")
        if s.startswith(("Applied edit to ", "Running ")):
            yield "tool", trunc(s)
        elif s.startswith(("Commit ", "Tokens:", "Aider v", "Model:", "Git repo:", "Repo-map:", "Analytics ")):
            yield "tool_done", trunc(s)
        else:
            yield "assistant", trunc(s)
    elif t == "aider.result":
        yield "result", ("SUCCESS" if d.get("status") == "SUCCESS" else "ERROR: " + str(d.get("error") or "")) + \
            f" ({d.get('edits', 0)} edit(s))"


def _render_claude(d):
    if d.get("type") == "result":
        ok = d.get("subtype") == "success" and not d.get("is_error")
        cost = d.get("total_cost_usd")
        yield "result", ("SUCCESS: " if ok else f"ERROR {d.get('subtype')}: ") + trunc(str(d.get("result") or "")) + \
            (f"  (${cost:.2f})" if isinstance(cost, (int, float)) else "")
        return
    if d.get("type") == "system":
        return
    yield from render_orchestrator_line(d, {})


def _render_codex(d):
    t, it = d.get("type"), d.get("item") or {}
    if t == "item.started" and it.get("type") == "command_execution":
        yield "tool", trunc(f"shell: {it.get('command', '')}")
    elif t == "item.completed":
        k = it.get("type")
        if k == "command_execution":
            yield "tool_done", f"done (exit {it.get('exit_code')})"
        elif k == "agent_message":
            yield "assistant", trunc(it.get("text", ""))
        elif k == "file_change":
            yield "tool", trunc("files: " + ", ".join(str(c.get("path", "")) for c in it.get("changes") or [] if isinstance(c, dict)))
        elif k == "error":
            yield "tool_done", trunc("note: " + str(it.get("message", "")))
    elif t == "turn.completed":
        u = d.get("usage") or {}
        yield "result", f"SUCCESS: turn completed ({u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens)"
    elif t == "turn.failed":
        err = d.get("error")
        yield "result", "ERROR: " + trunc(str(err.get("message") if isinstance(err, dict) else err))


def _render_gemini(d, acc):
    t = d.get("type")
    if t == "message" and d.get("role") == "user":
        yield "user", trunc(str(d.get("content", "")))
        yield "task", trunc(str(d.get("content", "")), 140)
    elif t == "message":
        acc["g"] = acc.get("g", "") + str(d.get("content", ""))
    elif t == "tool_use":
        if acc.get("g"):
            yield "assistant", trunc(acc.pop("g"))
        p = d.get("parameters") or {}
        yield "tool", trunc(f"{d.get('tool_name')}: {p.get('command') or json.dumps(p, ensure_ascii=False)}")
    elif t == "tool_result":
        yield "tool_done", "done" if d.get("status") == "success" else trunc(f"{d.get('status')}: {d.get('output', '')}")
    elif t == "result":
        if acc.get("g"):
            yield "assistant", trunc(acc.pop("g"))
        yield "result", f"{'SUCCESS' if d.get('status') == 'success' else 'ERROR'}: {(d.get('stats') or {}).get('total_tokens', 0)} tokens"
    elif t == "error":
        yield "tool_done", trunc("error: " + str(d.get("message") or d))


def _render_agy(d, acc):
    ev = d.get("event")
    su = d.get("step_update") or {}
    idx = su.get("step_index")
    stype = su.get("step_type")
    state = su.get("state")
    if ev == "step_update" and stype == "user_input":
        text = su.get("text") or su.get("tool_info", {}).get("text") or ""
        if text:
            yield "user", trunc(text)
            yield "task", trunc(text, 140)
    elif ev == "step_update" and stype == "agent_response":
        key = ("resp", idx)
        acc[key] = acc.get(key, "") + (su.get("text_delta") or "")
        if state == "DONE" and acc[key].strip():
            yield "assistant", trunc(acc[key])
    elif ev == "step_update" and stype == "tool":
        info = su.get("tool_info") or {}
        name = su.get("tool_name") or info.get("name") or "tool"
        params = info.get("parameters") or {}
        cmd = params.get("CommandLine") or params.get("command") or json.dumps(params, ensure_ascii=False)
        if state == "ACTIVE":
            yield "tool", trunc(f"{name}: {cmd}")
        elif state == "DONE":
            dur = su.get("duration_seconds")
            yield "tool_done", "done" + (f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else "")
    elif ev == "result":
        res = d.get("result") or {}
        status = res.get("status", "?")
        yield "result", f"{status}: " + trunc(res.get("response", ""))


ICONS = {"user": "\U0001f9d1", "assistant": "\U0001f916", "tool": "\U0001f527", "tool_done": "  ✓", "result": None}


# ---- roster: persistent summary per known AI (orchestrator + every worker ever seen), survives pane removal ---
class Roster:
    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}   # id -> dict

    def touch(self, pane_id, **fields):
        with self.lock:
            e = self.entries.setdefault(pane_id, {})
            e.update(fields)
            e["last_active_at"] = time.time()

    def snapshot(self):
        with self.lock:
            return {k: dict(v) for k, v in self.entries.items()}


ROSTER = Roster()


# ---- pane state (live tail; only exists while running or briefly after finishing) ------------------------------
class Pane:
    def __init__(self, pane_id, title, kind, remote_path, renderer, tail_argv):
        self.id, self.title, self.kind, self.remote_path, self.renderer = pane_id, title, kind, remote_path, renderer
        self.tail_argv = tail_argv
        self.lines = collections.deque(maxlen=LINE_BUFFER)
        self.seq = 0                                 # running number of the rendered lines (the live stream sends only new ones)
        self.state = "running"
        self.finished_at = None
        self.tool_calls = 0
        self.task_label = None
        self._acc = {}
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        ROSTER.touch(pane_id, title=title, kind=kind, state=self.state, tool_calls=0)

    def start(self):
        self._thread = threading.Thread(target=self._tail, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def _tail(self):
        try:
            self._proc = subprocess.Popen(self.tail_argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                           bufsize=1, start_new_session=True)
        except Exception:
            return
        ALL_PROCS.add(self._proc)
        try:
            self._read_lines()
        finally:
            # close the pipe and reap the process: a finished tail that kept its pipe open leaked one fd per worker run
            # until the process hit its open-files limit (1024) and could not start ssh any more - the "stale data" bug
            reap(self._proc)

    def _read_lines(self):
        for raw in self._proc.stdout:
            if self._stop.is_set():
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue
            try:
                for kind, text in self.renderer(d, self._acc):
                    if kind == "task":
                        self.task_label = text
                        continue
                    self.seq += 1
                    self.lines.append({"kind": kind, "text": text, "n": self.seq})
                    if kind == "tool":
                        self.tool_calls += 1
            except Exception:
                continue
            if self.kind == "worker" and is_final(d):
                self.state = "finished"
                self.finished_at = time.time()
            ROSTER.touch(self.id, title=self.title, kind=self.kind, state=self.state,
                         tool_calls=self.tool_calls, task=self.task_label,
                         last_line=(self.lines[-1] if self.lines else None))


PANES = {}


PANES_LOCK = threading.Lock()


# pane id -> path of the finished run whose pane was dropped. Without it the next scan (5 s later) found the same latest run,
# started a new `tail -F` for it, saw "finished" again, dropped it 90 s later, and so on - a new ssh process per worker every
# ~95 s for as long as the worker was idle.
RETIRED = {}


def discover_orchestrator():
    """The orchestrator's Claude Code session (if one runs next to Agent-Forge): the newest transcript of CLAUDE_PROJECT."""
    out = ssh_run(f"ls -t ~/.claude/projects/{CLAUDE_PROJECT}/*.jsonl 2>/dev/null | head -1").strip()
    if not out:
        return None
    sid = os.path.basename(out).removesuffix(".jsonl")
    running = sid[:8] in ssh_run("claude agents --json 2>/dev/null")
    tail_argv = SSH_BASE + [f"tail -n 200 -F {out} 2>/dev/null"]
    return sid, out, running, tail_argv


RUN_DIR_RE = re.compile(r"^\d{8}T\d{6}Z-(.+)$")   # bin/dispatch's own naming: <UTC timestamp>-<worker-name>


def discover_workers():
    """Latest run directory per worker under ~/sandbox/runs/, and the full set of worker names ever seen.
    Older, differently-named directories (e.g. task-numbered dirs from early V4 runs like 0001-wordcount) are
    not dispatch runs and are skipped — matched strictly against bin/dispatch's own <timestamp>-<worker> format."""
    # newest 200 run directories with the age of their "finished" marker (-1 = still running) in one call
    out = ssh_run(f"cd {FORGE_DIR}/runs 2>/dev/null && now=$(date +%s) && ls -1dt */ 2>/dev/null | head -200 | while read d; do "
                  f"f=-1; [ -e \"$d/finished\" ] && f=$(( now - $(stat -c %Y \"$d/finished\") )); echo \"$d $f\"; done")
    latest_per_worker, all_workers = {}, set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        base = parts[0].rstrip("/")
        m = RUN_DIR_RE.match(base)
        if not m:
            continue
        worker = m.group(1)
        all_workers.add(worker)
        if worker not in latest_per_worker:
            # a live pane only for a run that is running or finished a moment ago - an old finished run (or one that
            # never wrote a transcript, e.g. a failed setup) must not show up as a "running" empty pane
            if parts[1].lstrip("-").isdigit() and (int(parts[1]) < 0 or int(parts[1]) < FINISHED_GRACE):
                latest_per_worker[worker] = (base, f"{FORGE_DIR}/runs/{base}")
            else:
                latest_per_worker.setdefault(worker, None)
    return {w: v for w, v in latest_per_worker.items() if v}, all_workers


QUOTA_POLL_INTERVAL = 60   # seconds; agy /usage is metadata, not a model call, but no need to hammer it


def parse_usage(text):
    """Same as bin/lib/sbx.py's parse_usage: tab-separated model / label / pct% / reset."""
    rows = []
    for line in text.splitlines():
        p = [x.strip() for x in line.split("\t")]
        if len(p) >= 3 and p[2].endswith("%"):
            try:
                rows.append({"model": p[0], "label": p[1], "pct": float(p[2][:-1]), "reset": p[3] if len(p) > 3 else ""})
            except ValueError:
                pass
    return rows


def quota_loop():
    while True:
        try:
            _, all_workers = discover_workers()
            for worker in all_workers:
                out = ssh_run(f"docker exec {worker} agy-run -p /usage --print-timeout 60s 2>/dev/null", timeout=70)
                rows = [r for r in parse_usage(out) if "5h" in r["label"].lower() or "five hour" in r["label"].lower()
                        or "week" in r["label"].lower()]
                if rows:
                    ROSTER.touch("worker:" + worker, quota=rows)
        except Exception:
            pass
        time.sleep(QUOTA_POLL_INTERVAL)


def discover_loop():
    while True:
        try:
            _discover_once()
        except Exception:
            pass
        time.sleep(DISCOVER_INTERVAL)


def _discover_once():
    wanted = {}
    orch = discover_orchestrator()
    if orch:
        sid, path, running, tail_argv = orch
        wanted["orchestrator"] = ("Orchestrator (Claude Code)", "orchestrator", path, render_orchestrator_line, running, tail_argv)

    latest, all_workers = discover_workers()
    for worker, (run_name, run_dir) in latest.items():
        tail_argv = SSH_BASE + [f"tail -n 200 -F {run_dir}/stream.jsonl 2>/dev/null"]
        wanted["worker:" + worker] = (worker, "worker", run_dir + "/stream.jsonl", render_worker_line, True, tail_argv)
    # workers seen historically but with no run right now still get a roster entry (idle), so they never vanish
    for worker in all_workers:
        pane_id = "worker:" + worker
        if pane_id not in wanted:
            ROSTER.touch(pane_id, title=worker, kind="worker", state="idle")

    with PANES_LOCK:
        for pane_id, (title, kind, path, renderer, alive, tail_argv) in wanted.items():
            existing = PANES.get(pane_id)
            if existing is None and RETIRED.get(pane_id) == path:
                continue        # this run already finished and its pane was retired - do not start a new tail for it
            if existing is None or existing.remote_path != path:
                if existing:
                    existing.stop()
                p = Pane(pane_id, title, kind, path, renderer, tail_argv)
                if kind == "orchestrator":
                    p.state = "running" if alive else "idle"
                PANES[pane_id] = p
                p.start()
            elif kind == "orchestrator":
                PANES[pane_id].state = "running" if alive else "idle"
                ROSTER.touch(pane_id, state=PANES[pane_id].state)
        now = time.time()
        for pane_id in list(PANES):
            if pane_id not in wanted and pane_id != "orchestrator":
                PANES[pane_id].stop()
                del PANES[pane_id]
                continue
            p = PANES[pane_id]
            if p.kind == "worker" and p.state == "finished" and p.finished_at and now - p.finished_at > FINISHED_GRACE:
                ROSTER.touch(pane_id, state="idle")   # keep the roster row, drop the live pane
                RETIRED[pane_id] = p.remote_path
                p.stop()
                del PANES[pane_id]


def pane_delta(state):
    """What changed in the live panes since this client last asked: new lines per pane (all of them when the client is new or
    fell behind the buffer: reset), panes that are gone, and the roster when it changed. `state` is the client's memory and is
    updated in place. None = nothing new."""
    out, ids = [], set()
    with PANES_LOCK:
        order = {"orchestrator": 0, "worker": 1}
        for p in sorted(PANES.values(), key=lambda p: (order.get(p.kind, 9), p.title)):
            ids.add(p.id)
            lines = list(p.lines)
            last = state.get(p.id)
            if last is None or (lines and last < lines[0]["n"] - 1):
                new, reset = lines, True
            else:
                new, reset = [l for l in lines if l["n"] > last], False
            meta = (p.state, p.title)
            if new or reset or state.get(p.id + "|meta") != meta:
                out.append(dict(id=p.id, title=p.title, kind=p.kind, state=p.state, reset=reset, lines=new))
            state[p.id] = lines[-1]["n"] if lines else (last or 0)
            state[p.id + "|meta"] = meta
    gone = [k for k in list(state) if "|" not in k and k not in ids]
    for k in gone:
        state.pop(k, None); state.pop(k + "|meta", None)
    roster = ROSTER.snapshot()
    roster_key = json.dumps({k: {f: v.get(f) for f in ("state", "task", "tool_calls", "quota", "title")} for k, v in roster.items()},
                            sort_keys=True, default=str)
    changed = state.get("|roster") != roster_key
    state["|roster"] = roster_key
    if not out and not gone and not changed:
        return None
    return dict(panes=out, gone=gone, roster=roster if changed else None)


RUN_ID = re.compile(r"\d{8}T\d{6}Z-worker-\d+")


def render_run(raw, limit=6000):
    """A finished run's whole transcript (its stream.jsonl text) as typed lines for the replay view: (kind, text) with the
    index of each tool call, so the page can jump between them and copy commands."""
    acc, out = {}, []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        try:
            for kind, text in render_worker_line(d, acc):
                if kind != "task":
                    out.append({"kind": kind, "text": text})
        except Exception:
            continue
        if len(out) >= limit:
            out.append({"kind": "tool_done", "text": f"(transcript cut at {limit} lines)"})
            break
    return out
