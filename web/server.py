#!/usr/bin/env python3
"""Agent-Forge web dashboard: a live view of the team - orchestrator, every worker, goals, the task board with details, events
and daily stats - plus one action button ("Continue" after a soft kill-switch trip).

Python standard library only, one file. Runs next to bin/foreman (LOCAL mode, default) or on another machine reading it over ssh
(REMOTE mode: set VM_IP, VM_USER, VM_KEY, VM_KNOWN_HOSTS, and FORGE_DIR if Agent-Forge is not in ~/sandbox there). Binds 127.0.0.1
only - publish it with a reverse proxy / Tailscale serve if you want it on your phone.

Everything it does is read-only (tail of run logs, `bin/foreman status --dashboard`, `agy /usage`) except POST /api/resume, which
runs the same recovery a human would after a SOFT kill-switch trip: `bin/egress-report --ack`, `bin/killswitch off --start`, and a
foreman restart if it is not running. It refuses on a HARD kill switch (containers stopped - that needs a real look).

Config (environment): BIND_PORT (8171), FORGE_DIR, VM_IP / VM_USER / VM_KEY / VM_KNOWN_HOSTS for remote mode.
"""
import atexit
import collections
import json
import os
import re
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BIND_PORT = int(os.environ.get("BIND_PORT", "8171"))
DISCOVER_INTERVAL = 5           # seconds between "what's running" scans
FINISHED_GRACE = 90             # seconds a finished worker's live pane stays visible after its run ends
LINE_BUFFER = 400               # rendered lines kept per pane
SSH_TIMEOUT = 6

# Two modes: LOCAL (default) - the dashboard runs on the same machine as bin/foreman and reads it directly; REMOTE - set VM_IP (+
# VM_USER, VM_KEY, VM_KNOWN_HOSTS) and it reads the Agent-Forge machine over ssh, e.g. from a host that runs Agent-Forge in a VM.
VM_IP = os.environ.get("VM_IP", "")
FORGE_DIR = os.environ.get("FORGE_DIR", "~/sandbox" if VM_IP else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if VM_IP:
    VM_USER, VM_KEY, VM_KNOWN_HOSTS = os.environ["VM_USER"], os.environ["VM_KEY"], os.environ["VM_KNOWN_HOSTS"]
    SSH_BASE = [
        "ssh", "-i", VM_KEY,
        "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=15",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + VM_KNOWN_HOSTS,
        "-o", "LogLevel=ERROR", f"{VM_USER}@{VM_IP}",
    ]
else:
    SSH_BASE = ["bash", "-c"]
# Claude Code keeps a project's transcripts under ~/.claude/projects/<the project path with / replaced by ->
CLAUDE_PROJECT = "-" + os.path.expanduser(FORGE_DIR).strip("/").replace("/", "-") if not VM_IP else "-home-dev-sandbox"


def ssh_run(remote_cmd, timeout=SSH_TIMEOUT):
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def ssh_run_full(remote_cmd, timeout=30):
    """Like ssh_run but returns (returncode, stdout, stderr) - used by the one write action this dashboard has
    (/api/resume), so a failure can be reported instead of silently swallowed."""
    try:
        r = subprocess.run(SSH_BASE + [remote_cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return 1, "", str(e)


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
        ALL_PROCS.append(self._proc)
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
                    self.lines.append({"kind": kind, "text": text})
                    if kind == "tool":
                        self.tool_calls += 1
            except Exception:
                continue
            if self.kind == "worker" and d.get("event") == "result":
                self.state = "finished"
                self.finished_at = time.time()
            ROSTER.touch(self.id, title=self.title, kind=self.kind, state=self.state,
                         tool_calls=self.tool_calls, task=self.task_label,
                         last_line=(self.lines[-1] if self.lines else None))


ALL_PROCS = []   # every ssh subprocess ever started, for a clean shutdown
PANES = {}
PANES_LOCK = threading.Lock()


def discover_devbox_orchestrator():
    """The most recently modified transcript directly under -home-dev-sandbox (not memory/, not tool-results/)."""
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
    out = ssh_run(f"ls -1dt {FORGE_DIR}/runs/*/ 2>/dev/null | grep -vE '/(audit|unattended)/$'")
    latest_per_worker, all_workers = {}, set()
    for line in out.splitlines():
        base = os.path.basename(line.strip().rstrip("/"))
        m = RUN_DIR_RE.match(base)
        if not m:
            continue
        worker = m.group(1)
        all_workers.add(worker)
        if worker not in latest_per_worker:
            latest_per_worker[worker] = (base, line.strip().rstrip("/"))
    return latest_per_worker, all_workers


QUOTA_POLL_INTERVAL = 60   # seconds; agy /usage is metadata, not a model call, but no need to hammer it


def parse_usage(text):
    """Exact copy of bin/lib/sbx.py's parse_usage on devbox: tab-separated model / label / pct% / reset."""
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


FOREMAN_INTERVAL = 4
FOREMAN = {"data": None, "at": 0, "error": "not loaded yet"}


def foreman_from_old_json(d):
    """Older bin/foreman without `status --dashboard`: map `status --json` onto the same shape (fewer details)."""
    tasks = []
    for g in d.get("goals", []):
        for t in g.get("tasks", []):
            tasks.append(dict(t, goal=g["id"], max_attempts=4, depends_on=[], files=[], spec="", acceptance=[], history=[]))
    return dict(now=time.time(), alive=True, paused=d.get("paused"), killswitch=d.get("killswitch"), autopilot=False,
                claude_until=None, agy_cooldown={}, live={}, backlog=[], goals=[dict(g, tasks=None) for g in d.get("goals", [])],
                tasks=tasks, events=d.get("events", []), llm_today={}, today={}, old=True)


def foreman_loop():
    while True:
        try:
            out = ssh_run(f"cd {FORGE_DIR} && bin/foreman status --dashboard 2>/dev/null", timeout=12)
            data = None
            try:
                data = json.loads(out)
            except Exception:
                try:
                    data = foreman_from_old_json(json.loads(ssh_run(f"cd {FORGE_DIR} && bin/foreman status --json 2>/dev/null", timeout=12)))
                except Exception:
                    pass
            if data is not None:
                FOREMAN.update(data=data, at=time.time(), error=None)
            else:
                FOREMAN["error"] = "cannot read foreman status (devbox unavailable?)"
        except Exception as e:
            # whatever the cause, never let this thread die silently and leave the dashboard frozen on stale data forever
            FOREMAN["error"] = f"foreman_loop crashed: {e}"
        time.sleep(FOREMAN_INTERVAL)


def discover_loop():
    while True:
        try:
            _discover_once()
        except Exception:
            pass
        time.sleep(DISCOVER_INTERVAL)


def _discover_once():
    wanted = {}
    orch = discover_devbox_orchestrator()
    if orch:
        sid, path, running, tail_argv = orch
        wanted["orchestrator"] = ("Orchestrator (devbox)", "orchestrator", path, render_orchestrator_line, running, tail_argv)

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
                p.stop()
                del PANES[pane_id]


def shutdown(*_a):
    with PANES_LOCK:
        for p in list(PANES.values()):
            p.stop()
    for proc in ALL_PROCS:
        try:
            proc.terminate()
        except Exception:
            pass
    os._exit(0)


# ---- HTTP -----------------------------------------------------------------------------------------------------
PAGE = """<!DOCTYPE html>
<html lang="sk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent-Forge dev tím</title>
<style>
  :root { --bg:#0f1115; --card:#1a1d24; --card2:#22262f; --text:#e8e9ec; --muted:#8b8f99; --accent:#5b8def; --ok:#3ecf8e; --idle:#5a5f6b;
          --bad:#e5566d; --tool:#c9a84c; --warn:#e59a3c; --line:#2c313b; }
  @media (prefers-color-scheme: light) { :root { --bg:#f4f5f7; --card:#ffffff; --card2:#f0f2f5; --text:#1a1d24; --muted:#6b7280; --accent:#3b6fd6;
          --ok:#1a9a63; --idle:#9aa0ab; --bad:#c23a52; --tool:#96751c; --warn:#b86e12; --line:#e2e5ea; } }
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         display:flex; flex-direction:column; height:100dvh; overflow:hidden; }
  header { padding:10px 14px 0; flex:0 0 auto; }
  .top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  h1 { font-size:17px; margin:0 6px 0 0; }
  .pill { font-size:12px; padding:3px 9px; border-radius:999px; background:var(--card2); color:var(--muted); white-space:nowrap; display:inline-flex; align-items:center; gap:6px; }
  .pill.ok { color:var(--ok); } .pill.bad { color:var(--bad); background:color-mix(in srgb, var(--bad) 14%, transparent); } .pill.warn { color:var(--warn); }
  .resume-btn { font-size:12.5px; font-weight:700; padding:4px 12px; border-radius:999px; border:0; background:var(--ok); color:#06130d; cursor:pointer; }
  .resume-btn:disabled { opacity:.6; cursor:default; }
  .stats { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .stat { background:var(--card); border-radius:10px; padding:6px 10px; min-width:88px; }
  .stat b { display:block; font-size:16px; }
  .stat span { font-size:11px; color:var(--muted); }
  nav { display:flex; gap:4px; margin-top:10px; border-bottom:1px solid var(--line); }
  nav button { background:none; border:0; color:var(--muted); font-size:14px; padding:8px 12px; cursor:pointer; border-bottom:2px solid transparent; font-weight:600; }
  nav button.on { color:var(--text); border-bottom-color:var(--accent); }
  nav .cnt { font-size:11px; background:var(--card2); border-radius:999px; padding:1px 6px; margin-left:4px; color:var(--muted); }
  main { flex:1 1 auto; min-height:0; overflow-y:auto; padding:12px 14px 20px; }
  section { display:none; } section.on { display:block; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin:16px 0 8px; }
  h3:first-child { margin-top:0; }
  .dot { width:8px; height:8px; border-radius:50%; background:var(--idle); flex-shrink:0; display:inline-block; }
  .dot.running { background:var(--ok); animation:pulse 1.6s infinite; } .dot.think { background:var(--accent); animation:pulse 1.2s infinite; }
  .dot.bad { background:var(--bad); }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }

  /* team tree */
  .team { display:flex; flex-direction:column; align-items:center; }
  .node { background:var(--card); border-radius:12px; padding:10px 13px; box-shadow:0 1px 3px rgba(0,0,0,.15); width:100%; max-width:360px; }
  .node.orch { border:1px solid var(--accent); }
  .node-title { font-size:14px; font-weight:600; display:flex; align-items:center; gap:7px; }
  .node-sub { font-size:12.5px; color:var(--muted); margin-top:4px; }
  .node-task { font-size:13px; margin-top:5px; }
  .node-quota { font-size:11px; color:var(--accent); margin-top:4px; }
  .branch { width:1px; height:14px; background:var(--idle); }
  .workers { display:flex; flex-wrap:wrap; gap:10px; justify-content:center; padding-top:12px; border-top:1px dashed var(--idle); width:100%; }
  .workers .node { flex:1 1 240px; }
  .step { font-size:12.5px; margin-top:5px; display:flex; gap:6px; align-items:center; }

  /* goals */
  .goal { background:var(--card); border-radius:12px; padding:10px 12px; margin-bottom:8px; }
  .goal-head { display:flex; gap:8px; align-items:baseline; justify-content:space-between; }
  .goal-text { font-size:14px; font-weight:600; }
  .goal-meta { font-size:11.5px; color:var(--muted); white-space:nowrap; }
  .bar { height:6px; border-radius:3px; background:var(--card2); margin-top:8px; overflow:hidden; display:flex; }
  .bar i { display:block; height:100%; }
  .goal-sum { font-size:12.5px; color:var(--muted); margin-top:6px; }
  .chips { display:flex; flex-wrap:wrap; gap:5px; margin-top:7px; }
  .chip { font-size:11.5px; padding:2px 7px; border-radius:6px; background:var(--card2); cursor:pointer; }

  /* kanban */
  .board { display:grid; grid-template-columns:repeat(5, minmax(220px, 1fr)); gap:10px; overflow-x:auto; padding-bottom:6px; }
  @media (max-width:760px) { .board { grid-template-columns:1fr; } }
  .col { background:var(--card2); border-radius:12px; padding:8px; min-height:60px; }
  .col h4 { margin:2px 4px 8px; font-size:13px; display:flex; justify-content:space-between; }
  .card { background:var(--card); border-radius:10px; padding:8px 10px; margin-bottom:7px; cursor:pointer; border-left:3px solid var(--idle); }
  .card:hover { outline:1px solid var(--line); }
  .card .id { font-size:11px; color:var(--muted); font-family:ui-monospace,Menlo,monospace; }
  .card .t { font-size:13.5px; font-weight:600; margin:2px 0 4px; }
  .card .m { font-size:11.5px; color:var(--muted); display:flex; flex-wrap:wrap; gap:6px; }
  .role { font-size:11px; padding:1px 6px; border-radius:5px; background:color-mix(in srgb, var(--accent) 16%, transparent); color:var(--accent); }
  .role.test { background:color-mix(in srgb, var(--warn) 16%, transparent); color:var(--warn); }
  .role.sec { background:color-mix(in srgb, var(--bad) 16%, transparent); color:var(--bad); }
  .s-running { border-left-color:var(--ok); } .s-gate { border-left-color:var(--accent); } .s-done { border-left-color:var(--idle); opacity:.85; }
  .s-blocked { border-left-color:var(--bad); } .s-pending { border-left-color:var(--tool); }

  /* detail drawer */
  .detail { position:fixed; inset:0; background:rgba(0,0,0,.45); display:none; align-items:flex-end; justify-content:center; z-index:10; }
  .detail.on { display:flex; }
  .sheet { background:var(--card); width:100%; max-width:820px; max-height:88dvh; overflow-y:auto; border-radius:16px 16px 0 0; padding:16px; }
  @media (min-width:760px) { .detail { align-items:center; } .sheet { border-radius:16px; } }
  .sheet h2 { font-size:16px; margin:0 0 4px; }
  .sheet pre { white-space:pre-wrap; word-break:break-word; background:var(--card2); border-radius:8px; padding:10px; font-size:12.5px; margin:6px 0; }
  .hist { border-left:2px solid var(--line); padding-left:10px; margin:6px 0; font-size:12.5px; }
  .close { float:right; background:var(--card2); color:var(--text); border:0; border-radius:8px; padding:5px 10px; cursor:pointer; }

  /* events */
  .ev { display:flex; gap:10px; padding:6px 2px; border-bottom:1px solid var(--line); font-size:13px; }
  .ev time { color:var(--muted); font-family:ui-monospace,Menlo,monospace; font-size:12px; flex:0 0 auto; }
  .ev.bad { color:var(--bad); } .ev.good { color:var(--ok); }

  /* live panes */
  .grid { display:flex; flex-direction:column; gap:10px; height:calc(100dvh - 210px); min-height:300px; }
  @media (min-width:760px) { .grid { flex-direction:row; } }
  .pane { flex:1 1 0; min-height:0; min-width:0; background:var(--card); border-radius:14px; padding:10px; display:flex; flex-direction:column; }
  .pane h2 { font-size:15px; margin:0 0 6px; display:flex; align-items:center; gap:8px; }
  .log { flex:1; min-height:0; font-family:ui-monospace,Menlo,Consolas,monospace; font-size:14px; line-height:1.45; overflow-y:auto; }
  .line { padding:3px 8px; margin-bottom:3px; border-left:3px solid transparent; border-radius:4px; word-break:break-word; white-space:pre-wrap; }
  .l-user { border-left-color:var(--accent); color:var(--accent); font-weight:700; background:color-mix(in srgb, var(--accent) 10%, transparent); }
  .l-assistant { border-left-color:var(--ok); }
  .l-tool { border-left-color:var(--tool); color:var(--tool); background:color-mix(in srgb, var(--tool) 8%, transparent); }
  .l-tool_done { border-left-color:var(--idle); color:var(--muted); font-size:12.5px; }
  .l-result { border-left-color:var(--bad); font-weight:800; background:color-mix(in srgb, var(--ok) 10%, transparent); }
  .empty { color:var(--muted); text-align:center; padding:20px 0; font-size:13px; }
  .muted { color:var(--muted); }
</style></head>
<body>
<header>
  <div class="top"><h1>Agent-Forge — dev tím</h1><span id="pills"></span></div>
  <div id="jserr" style="display:none; margin-top:8px; font-size:12.5px; color:var(--bad);"></div>
  <div id="resume-result" style="display:none; margin-top:8px; font-size:12.5px;"></div>
  <div class="stats" id="stats"></div>
  <nav id="nav">
    <button data-t="overview">Prehľad</button>
    <button data-t="queue">Fronta <span class="cnt" id="c-queue">0</span></button>
    <button data-t="live">Live <span class="cnt" id="c-live">0</span></button>
    <button data-t="events">Udalosti</button>
  </nav>
</header>
<main>
  <section id="t-overview">
    <h3>Tím</h3><div id="team" class="team"><div class="empty">načítavam…</div></div>
    <h3>Ciele</h3><div id="goals"></div>
    <h3>Backlog</h3><div id="backlog"></div>
  </section>
  <section id="t-queue"><div id="board" class="board"></div></section>
  <section id="t-live"><div id="grid" class="grid"></div></section>
  <section id="t-events"><div id="events"></div></section>
</main>
<div class="detail" id="detail"><div class="sheet" id="sheet"></div></div>
<script>
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tfmt = ts => { const d = typeof ts === 'number' ? new Date(ts*1000) : new Date(ts); return isNaN(d) ? '' : d.toLocaleTimeString('sk-SK', {hour:'2-digit', minute:'2-digit'}); };
const dfmt = ts => { const d = new Date(ts); if (isNaN(d)) return ''; const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString('sk-SK', {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : d.toLocaleString('sk-SK', {day:'numeric', month:'numeric', hour:'2-digit', minute:'2-digit'}); };
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Math.round(Date.now()/1000 - (typeof ts === 'number' ? ts : Date.parse(ts)/1000)));
  return s < 60 ? s + ' s' : s < 3600 ? Math.round(s/60) + ' min' : Math.round(s/3600) + ' h'; };
const STATUS = {pending:'Čaká', running:'Beží', gate:'Kontrola', review:'Kontrola', done:'Hotovo', blocked:'Zaseknuté', cancelled:'Zrušené'};
const roleCls = r => /secur/i.test(r) ? 'sec' : (/test/i.test(r) && !/develop/i.test(r)) ? 'test' : '';
let tab = 'overview', F = null, P = {roster:{}, panes:[]}, openTask = null;
try { tab = localStorage.getItem('app-tab') || 'overview'; } catch (e) {}

function setTab(t) {
  tab = t;
  try { localStorage.setItem('app-tab', t); } catch (e) {}
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('main section').forEach(s => s.classList.toggle('on', s.id === 't-' + t));
  render();
}
document.getElementById('nav').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.t); });
document.getElementById('detail').addEventListener('click', e => { if (e.target.id === 'detail' || e.target.classList.contains('close')) { openTask = null; e.currentTarget.classList.remove('on'); } });
document.addEventListener('click', e => { const c = e.target.closest('[data-task]'); if (c) { openTask = c.dataset.task; renderDetail(); } });

let resuming = false;
async function resumeDev() {
  if (resuming) return;
  resuming = true;
  const btn = document.getElementById('resume-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Pokračujem…'; }
  let res;
  try {
    res = await (await fetch('/api/resume', {method: 'POST'})).json();
  } catch (e) {
    res = {ok: false, steps: [{label: 'spojenie', ok: false, output: String(e)}]};
  }
  resuming = false;
  const box = document.getElementById('resume-result');
  const lines = (res.steps || []).map(s => '<div class="ev ' + (s.ok ? 'good' : 'bad') + '">' + (s.ok ? '✓ ' : '✗ ') +
    esc(s.label) + (s.output ? ': ' + esc(s.output) : '') + '</div>').join('');
  box.innerHTML = lines;
  box.style.display = 'block';
  poll();
}

function pills() {
  if (!F) return '<span class="pill bad">foreman: ' + esc(window._ferr || 'načítavam') + '</span>';
  const p = [];
  const staleFor = foremanFetchedAt ? (Date.now()/1000 - foremanFetchedAt) : 0;
  if (staleFor > 30) {
    p.push('<span class="pill bad">dashboard nemá čerstvé dáta (' + Math.round(staleFor/60) + ' min staré) - nedôveruj stavu nižšie, skontroluj priamo</span>');
    return p.join(' ');
  }
  if (!F.alive) p.push('<span class="pill bad"><span class="dot bad"></span>foreman nebeží</span>');
  else if (F.killswitch) {
    p.push('<span class="pill bad">kill switch ZAP — ' + esc(F.killswitch.reason || '') + '</span>');
    p.push(F.killswitch.hard
      ? '<span class="pill bad">tvrdý režim — kontajnery zastavené, toto vyžaduje ručnú kontrolu</span>'
      : '<button class="resume-btn" onclick="resumeDev()" id="resume-btn">Pokračovať</button>');
  }
  else if (F.paused) p.push('<span class="pill warn">foreman pozastavený</span>');
  else p.push('<span class="pill ok"><span class="dot running"></span>foreman beží</span>');
  if (F.claude_until) p.push('<span class="pill warn">orchestrátor: Claude cez Antigravity (Claude Code späť ' + tfmt(F.claude_until) + ')</span>');
  else p.push('<span class="pill ok">orchestrátor: Claude Code</span>');
  const agyDown = Object.keys(F.agy_cooldown || {}).length;
  if (agyDown) p.push('<span class="pill warn">' + agyDown + ' worker(y) dočasne bez náhradného orchestrátora</span>');
  p.push('<span class="pill">autopilot ' + (F.autopilot ? 'zap' : 'vyp') + '</span>');
  if (F.old) p.push('<span class="pill warn">stará verzia foremana — menej detailov</span>');
  return p.join(' ');
}

function stats() {
  if (!F) return '';
  const t = F.today || {}, l = F.llm_today || {}, ts = F.tasks || [];
  const n = s => ts.filter(x => x.status === s).length;
  const box = (v, lab) => '<div class="stat"><b>' + v + '</b><span>' + lab + '</span></div>';
  const blocked = n('blocked');
  return box(n('running'), 'beží') + box(n('pending'), 'čaká') + box(n('gate') + n('review'), 'kontrola') +
         box(t.merged != null ? t.merged : n('done'), 'hotovo dnes') +
         (blocked ? box('<span style="color:var(--bad)">' + blocked + '</span>', 'zaseknuté') : '') +
         (t.tokens ? box((t.tokens/1e6).toFixed(1) + 'M', 'tokeny dnes') : '') +
         (l.cost_usd ? box('$' + l.cost_usd.toFixed(2), 'orchestrátor dnes') : '');
}

function team() {
  const R = P.roster || {}, ts = (F && F.tasks) || [], live = (F && F.live) || {};
  const steps = (live.steps || []).map(s => {
    const [kind, id] = s.key.split(':');
    const t = ts.find(x => x.id === id), g = F && (F.goals || []).find(x => x.id === id);
    const what = s.phase || (kind === 'goal' ? 'plánovanie' : kind === 'task' ? 'kontrola' : kind);
    const obj = t ? esc(t.id) : g ? '„' + esc(g.text.slice(0, 60)) + '“' : '';
    return '<div class="step"><span class="dot think"></span>' + esc(what) + (obj ? ' — ' + obj : '') + ' <span class="muted">(' + ago(s.since) + ')</span></div>';
  }).join('');
  let orch = '<div class="node orch"><div class="node-title"><span class="dot ' + (steps ? 'think' : '') + '"></span>Orchestrátor (šéf)</div>' +
    '<div class="node-sub">' + (F && F.claude_until ? 'Claude cez Antigravity (náhradník)' : 'Claude Code Sonnet') + ' · plánuje, kontroluje, merguje</div>' +
    (steps || '<div class="node-sub">nič nerieši — čaká na workerov</div>') +
    (live.unavailable ? '<div class="step" style="color:var(--warn)">čaká: ' + esc(live.unavailable) + '</div>' : '') + '</div>';
  const names = new Set(Object.keys(R).filter(k => k.startsWith('worker:')).map(k => k.slice(7)));
  ts.forEach(t => t.worker && names.add(t.worker));
  const ws = [...names].sort().map(w => {
    const e = R['worker:' + w] || {}, t = ts.find(x => x.worker === w && x.status === 'running');
    const cool = live.cooldown && live.cooldown[w];
    const state = t ? 'running' : '';
    const q5h = (e.quota || []).filter(q => /five hour/i.test(q.label));
    const shortModel = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
    const quota = q5h.length ? '<div class="node-quota">' + q5h.map(q => shortModel(q.model) + ' ' + q.pct.toFixed(0) + '%').join(' · ') + '</div>' : '';
    const task = t ? '<div class="node-task" data-task="' + esc(t.id) + '" style="cursor:pointer"><span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> ' +
      esc(t.title) + '</div><div class="node-sub">' + esc(t.id) + ' · pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + ' · beží ' + ago(t.dispatched) +
      (e.tool_calls ? ' · ' + e.tool_calls + ' nástrojov' : '') + '</div>'
      : '<div class="node-sub">' + (cool ? 'pauza do ' + tfmt(cool.until) + ': ' + esc(cool.reason || 'neznámy dôvod') : (live.reserved || []).includes(w) ? 'požičaný orchestrátorovi (Claude cez Antigravity)' : 'voľný') + '</div>';
    return '<div class="node"><div class="node-title"><span class="dot ' + state + '"></span>' + esc(w) + '</div>' + task + quota + '</div>';
  }).join('');
  return orch + (ws ? '<div class="branch"></div><div class="workers">' + ws + '</div>' : '');
}

function goalsHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const gs = (F.goals || []).filter(g => g.status !== 'cancelled').slice().reverse();
  if (!gs.length) return '<div class="empty">Žiadne ciele. Zadaj: bin/foreman goal "…" (alebo cez Remote Control „devbox“).</div>';
  const col = {done:'var(--ok)', gate:'var(--accent)', review:'var(--accent)', running:'var(--tool)', blocked:'var(--bad)'};
  return gs.map(g => {
    const mine = ts.filter(t => t.goal === g.id), n = mine.length || 1;
    const seg = ['done','gate','review','running','blocked'].map(s => { const k = mine.filter(t => t.status === s).length;
      return k ? '<i style="width:' + (100*k/n) + '%;background:' + col[s] + '"></i>' : ''; }).join('');
    const done = mine.filter(t => t.status === 'done').length;
    const st = {new:'plánuje sa', active:'beží', done:'hotový', blocked:'zaseknutý'}[g.status] || g.status;
    return '<div class="goal"><div class="goal-head"><div class="goal-text">' + esc(g.text) + '</div><div class="goal-meta">' + esc(st) +
      (mine.length ? ' · ' + done + '/' + mine.length : '') + (g.source && g.source !== 'owner' ? ' · ' + esc(g.source) : '') + '</div></div>' +
      '<div class="bar">' + seg + '</div>' + ((g.summary || g.notes) ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') +
      '<div class="chips">' + mine.map(t => '<span class="chip" data-task="' + esc(t.id) + '">' + esc(STATUS[t.status] || t.status) + ' · ' + esc(t.id) +
        (t.diff ? ' · +' + t.diff.added + '/-' + t.diff.removed : '') + '</span>').join('') + '</div></div>';
  }).join('');
}

function boardHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const cols = [['pending', 'Čaká'], ['running', 'Beží'], ['gate', 'Kontrola (testy + review)'], ['done', 'Hotovo'], ['blocked', 'Zaseknuté / zrušené']];
  return cols.map(([s, lab]) => {
    let items = ts.filter(t => t.status === s || (s === 'gate' && t.status === 'review') || (s === 'blocked' && t.status === 'cancelled'));
    if (s === 'done') items = items.slice().sort((a, b) => (b.finished || '').localeCompare(a.finished || '')).slice(0, 15);
    const phase = id => { const st = ((F.live || {}).steps || []).find(x => x.key === 'task:' + id); return st ? st.phase : ''; };
    return '<div class="col"><h4>' + lab + '<span class="muted">' + items.length + '</span></h4>' + (items.map(t => {
      const deps = (t.depends_on || []).filter(d => !ts.find(x => x.id === d && x.status === 'done'));
      return '<div class="card s-' + t.status + '" data-task="' + esc(t.id) + '"><div class="id">' + esc(t.id) + '</div><div class="t">' + esc(t.title) + '</div><div class="m">' +
        '<span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span>' + (t.worker && s !== 'pending' ? '<span>' + esc(t.worker) + '</span>' : '') +
        (t.attempts ? '<span style="color:var(--warn)">pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + '</span>' : '') +
        (t.pr ? '<span>PR #' + t.pr + '</span>' : '') + (deps.length && s === 'pending' ? '<span>čaká na ' + deps.map(esc).join(', ') + '</span>' : '') +
        (t.diff ? '<span style="color:var(--ok)">+' + t.diff.added + '</span><span style="color:var(--bad)">-' + t.diff.removed + '</span>' : '') +
        (phase(t.id) ? '<span style="color:var(--accent)">' + esc(phase(t.id)) + '</span>' : '') + '</div></div>';
    }).join('') || '<div class="empty">—</div>') + '</div>';
  }).join('');
}

function renderDetail() {
  const d = document.getElementById('detail');
  const t = F && (F.tasks || []).find(x => x.id === openTask);
  if (!t) { d.classList.remove('on'); return; }
  const g = (F.goals || []).find(x => x.id === t.goal);
  const sheet = document.getElementById('sheet'), keep = sheet.scrollTop;
  const hist = (t.history || []).map(h => '<div class="hist"><b>' + esc(dfmt(h.at)) + ' · ' + esc(h.outcome) + '</b>' +
    (h.reviewer ? ' (' + esc(h.reviewer) + ')' : '') + '<br>' + esc(h.feedback || h.why || h.what || '') + '</div>').join('');
  document.getElementById('sheet').innerHTML = '<button class="close">Zavrieť</button><h2>' + esc(t.title) + '</h2>' +
    '<div class="muted" style="font-size:12.5px">' + esc(t.id) + ' · <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> · ' +
    esc(STATUS[t.status] || t.status) + ' · pokus ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + (t.worker ? ' · ' + esc(t.worker) : '') +
    (t.pr ? ' · PR #' + t.pr : '') + (t.risk ? ' · riziko: ' + esc(t.risk) : '') + (t.run_tokens ? ' · ' + (t.run_tokens/1000).toFixed(0) + 'k tokenov' : '') +
    (t.diff ? ' · <span style="color:var(--ok)">+' + t.diff.added + '</span> <span style="color:var(--bad)">-' + t.diff.removed + '</span> riadkov' : '') +
    (t.total_lines != null ? ' · súbor(y) spolu ' + t.total_lines + ' riadkov' : '') + '</div>' +
    (g ? '<h3>Cieľ</h3><div style="font-size:13px">' + esc(g.text) + '</div>' : '') +
    (t.blocked_why ? '<h3 style="color:var(--bad)">Prečo zaseknuté</h3><pre>' + esc(t.blocked_why) + '</pre>' : '') +
    (t.feedback && t.status !== 'done' ? '<h3>Posledná spätná väzba workerovi</h3><pre>' + esc(t.feedback) + '</pre>' : '') +
    (hist ? '<h3>História</h3>' + hist : '') +
    ((t.acceptance || []).length ? '<h3>Akceptačné kritériá</h3><ul style="font-size:13px;margin:0;padding-left:18px">' + t.acceptance.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '') +
    ((t.files || []).length ? '<h3>Súbory</h3><div class="muted" style="font-size:12.5px;font-family:ui-monospace,monospace">' + t.files.map(esc).join('<br>') + '</div>' : '') +
    (t.spec ? '<h3>Zadanie</h3><pre>' + esc(t.spec) + '</pre>' : '');
  d.classList.add('on');
  sheet.scrollTop = keep;
}

function eventsHtml() {
  if (!F) return '';
  const ev = (F.events || []).slice().reverse();
  return ev.map(e => { const m = e.msg || '';
    const cls = /BLOCKED|refused|error|failed|unavailable|kill switch/i.test(m) ? 'bad' : /done|DONE|merged|planned/.test(m) ? 'good' : '';
    return '<div class="ev ' + cls + '"><time>' + esc(dfmt(e.at)) + '</time><div>' + esc(m) + '</div></div>'; }).join('') || '<div class="empty">Zatiaľ nič.</div>';
}

function liveHtml() {
  const grid = document.getElementById('grid');
  const panes = P.panes || [];
  document.getElementById('c-live').textContent = panes.length;
  if (tab !== 'live') return;
  if (!panes.length) { grid.innerHTML = '<div class="empty">Žiadne AI momentálne nebežia — prepis sa zjaví, keď niečo pobeží.</div>'; return; }
  const ts = (F && F.tasks) || [];
  grid.innerHTML = '';
  for (const p of panes) {
    const w = p.id.startsWith('worker:') ? p.id.slice(7) : null, t = w && ts.find(x => x.worker === w && x.status === 'running');
    const el = document.createElement('div'); el.className = 'pane';
    el.innerHTML = '<h2><span class="dot ' + (p.state === 'running' ? 'running' : '') + '"></span>' + esc(p.title) +
      (t ? ' <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> <span class="muted" style="font-weight:400;font-size:13px">' + esc(t.id) + '</span>' :
           ' <span class="muted" style="font-weight:400;font-size:13px">(' + esc(p.state) + ')</span>') + '</h2>';
    const log = document.createElement('div'); log.className = 'log';
    log.innerHTML = p.lines.map(l => '<div class="line l-' + l.kind + '">' + esc(l.text) + '</div>').join('');
    el.appendChild(log); grid.appendChild(el);
    log.scrollTop = log.scrollHeight;
  }
}

function render() {
  document.getElementById('pills').innerHTML = pills();
  document.getElementById('stats').innerHTML = stats();
  const ts = (F && F.tasks) || [];
  document.getElementById('c-queue').textContent = ts.filter(t => ['pending', 'running', 'gate', 'review'].includes(t.status)).length;
  if (tab === 'overview') {
    document.getElementById('team').innerHTML = team();
    document.getElementById('goals').innerHTML = goalsHtml();
    const bl = (F && F.backlog) || [];
    document.getElementById('backlog').innerHTML = bl.length ? bl.map((b, i) => '<div class="goal"><span class="muted">' + (i + 1) + '.</span> ' + esc(b) + '</div>').join('')
      : '<div class="empty">Prázdny' + (F && F.autopilot ? ' — keď bude worker voľný, autopilot vyberie ďalší cieľ z vízie appky.' : '.') + '</div>';
  } else if (tab === 'queue') document.getElementById('board').innerHTML = boardHtml();
  else if (tab === 'events') document.getElementById('events').innerHTML = eventsHtml();
  liveHtml();
  if (openTask) renderDetail();
}

let foremanFetchedAt = 0;
async function poll() {
  try { const r = await (await fetch('/api/foreman')).json(); F = r.foreman; window._ferr = r.error; foremanFetchedAt = r.fetched_at || 0; } catch (e) {}
  try { P = await (await fetch('/api/panes')).json(); } catch (e) {}
  render();
}
function showJsError(msg) {
  const el = document.getElementById('jserr');
  if (el) { el.style.display = 'block'; el.textContent = 'Chyba stránky: ' + msg; }
}
window.addEventListener('error', e => showJsError((e.message || 'neznáma') + ' (' + (e.lineno || '?') + ')'));
window.addEventListener('unhandledrejection', e => showJsError(String(e.reason && e.reason.message || e.reason)));
setTab(tab);
poll(); setInterval(poll, 2000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        if self.path != "/api/resume":
            self.send_response(404); self.end_headers(); return
        steps = []

        def run(label, cmd, timeout=30):
            rc, out, err = ssh_run_full(cmd, timeout=timeout)
            steps.append({"label": label, "ok": rc == 0, "output": (out or err or "").strip()[-600:]})
            return rc == 0

        rc, status_out, _ = ssh_run_full(f"cd {FORGE_DIR} && bin/killswitch status", timeout=15)
        if "kill switch: off" in status_out:
            self._json({"ok": True, "steps": [{"label": "status", "ok": True, "output": "kill switch is already off"}]})
            return
        if "(hard)" in status_out:
            self._json({"ok": False, "steps": [{"label": "status", "ok": False,
                        "output": "kill switch is in hard mode (containers stopped) — manual inspection required"}]})
            return
        run("acknowledge egress alerts", f"cd {FORGE_DIR} && bin/egress-report --ack", timeout=20)
        ok_off = run("turn off kill switch", f"cd {FORGE_DIR} && bin/killswitch off --start", timeout=40)
        rc, active_out, _ = ssh_run_full("systemctl is-active sandbox-foreman", timeout=10)
        if active_out.strip() != "active":
            run("restart foreman service", f"cd {FORGE_DIR} && sudo systemctl restart sandbox-foreman", timeout=30)
        self._json({"ok": ok_off, "steps": steps})

    def do_GET(self):
        if self.path == "/api/panes":
            with PANES_LOCK:
                order = {"orchestrator": 0, "worker": 1}
                items = sorted(PANES.values(), key=lambda p: (order.get(p.kind, 9), p.title))
                panes = [{"id": p.id, "title": p.title, "kind": p.kind, "state": p.state,
                          "lines": list(p.lines)} for p in items]
            self._json({"roster": ROSTER.snapshot(), "panes": panes})
        elif self.path == "/api/foreman":
            self._json({"foreman": FOREMAN["data"], "fetched_at": FOREMAN["at"], "error": FOREMAN["error"]})
        elif self.path in ("/", "/index.html"):
            raw = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path == "/healthz":
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    atexit.register(shutdown)
    threading.Thread(target=discover_loop, daemon=True).start()
    threading.Thread(target=quota_loop, daemon=True).start()
    threading.Thread(target=foreman_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", BIND_PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
