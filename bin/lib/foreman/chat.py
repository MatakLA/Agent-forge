"""The planner chat: the owner talks to the orchestrator about the project; it can queue goals and backlog entries.
Public interface: history, status, reset, chat, parse_reply, parse_tools, queue_goal, queue_backlog.
Important invariants:
- Memory is a JSON file (chat.json in the project's foreman dir), not a growing model session: every call is a fresh tool-less
  model call that gets the project bundle (vision, decisions, code map, queue) plus the last CHAT_TURNS turns, so the context stays bounded.
- The model can act only through a trailing `ACTIONS: [...]` line with at most 3 entries of type goal / backlog; both go through the
  normal pipeline (planning, finite-goal rule, gate, review, approvals). Nothing else can be triggered from chat text.
- What was queued is appended to the reply, so the owner always sees it.
- Read-only code access: the model may answer a round with `READ: path` / `GREP: pattern` lines (max CHAT_ROUNDS rounds, 3 per round); the
  foreman answers from origin/main (git show / git grep) and calls again. Secret-looking paths are refused. No shell, no writes.
- Progress for the dashboard is in chat_status.json (busy, started, steps) so the UI can show what the planner is doing."""
import json, os, re

import sbx

from . import ctx
from .llm import llm
from .prompts import context_bundle
from .repo import read_repo, refresh_repo
from .util import sh
from .store import event, jload, jsave, locked, new_goal_id, save_goal

CHAT_TURNS = 12          # turns (owner + reply = 2) sent back to the model
MAX_STORED = 80          # turns kept on disk
ACTION_TYPES = ("goal", "backlog")
CHAT_ROUNDS = 4          # model calls per owner message (the last one must answer)
TOOLS_PER_ROUND = 3
SECRET_PATH = re.compile(r"(^|/)(\.env|\.vmkeys|id_[a-z0-9]+|[^/]*\.(key|pem|p12|pfx)|[^/]*(token|secret|passw)[^/]*)$", re.I)


def _path():
    return os.path.join(ctx.FD, "chat.json")


def history():
    h = jload(_path(), [])
    return h if isinstance(h, list) else []


def reset():
    with locked():
        jsave(_path(), [])


def queue_goal(text):
    with locked():
        gid = new_goal_id()
        save_goal(dict(id=gid, text=text.strip()[:4000], status="new", created=sbx.now()))
    event(f"goal {gid} queued: {text.strip()[:200]}", goal=gid)
    return gid


def queue_backlog(text):
    p = os.path.join(ctx.FD, "backlog.json")
    with locked():
        bl = jload(p, [])
        bl.append(text.strip()[:4000]); jsave(p, bl)
    event(f"backlog +{len(bl)}: {text.strip()[:200]}")
    return len(bl)


def parse_reply(text):
    """(answer, actions): the trailing `ACTIONS: [json]` line is cut off; malformed or unknown actions are dropped."""
    m = re.search(r"^[ \t]*ACTIONS:[ \t]*(\[.*\])[ \t]*\Z", text.rstrip(), re.M | re.S)
    if not m:
        return text.strip(), []
    answer, acts = text[:m.start()].strip(), []
    try:
        raw = json.loads(m.group(1))
    except Exception:
        return answer, []
    for a in raw if isinstance(raw, list) else []:
        if isinstance(a, dict) and a.get("do") in ACTION_TYPES and isinstance(a.get("text"), str) and 20 <= len(a["text"].strip()) <= 4000:
            acts.append(dict(do=a["do"], text=a["text"].strip()))
    return answer, acts[:3]


def _status_path():
    return os.path.join(ctx.FD, "chat_status.json")


def status():
    st = jload(_status_path(), {})
    return st if isinstance(st, dict) else {}


def _set_status(**kw):
    st = status(); st.update(kw)
    jsave(_status_path(), st)


def parse_tools(text):
    """[(tool, arg)] from lines `READ: path` / `GREP: pattern` (max TOOLS_PER_ROUND); empty when the reply has an ACTIONS line."""
    if re.search(r"^[ \t]*ACTIONS:", text, re.M):
        return []
    out = []
    for m in re.finditer(r"^[ \t]*(READ|GREP):[ \t]*(.+?)[ \t]*$", text, re.M):
        out.append((m.group(1), m.group(2).strip().strip("`")))
    return out[:TOOLS_PER_ROUND]


def run_tool(tool, arg):
    """Result text of one read-only tool call on origin/main; refusals are returned as text for the model to see."""
    if tool == "READ":
        path = arg.lstrip("./")
        if ".." in path.split("/") or SECRET_PATH.search(path):
            return f"(refused: {arg})"
        txt = read_repo(path, 12000)
        return txt if txt else f"(no such file on main: {arg})"
    pat = arg[:200]
    rc, out, _ = sh(["git", "grep", "-n", "-I", "-E", "--max-count=5", "-e", pat, "origin/main", "--"], cwd=ctx.REPO)
    lines = [l.replace("origin/main:", "", 1) for l in out.splitlines() if not SECRET_PATH.search(l.split(":", 2)[1] if l.count(":") >= 2 else "")]
    return "\n".join(lines[:40])[:6000] or "(no matches)"


def _render_history(h):
    out = []
    for turn in h[-CHAT_TURNS * 2:]:
        who = "Owner" if turn.get("role") == "owner" else "You (planner)"
        out.append(f"{who}: {str(turn.get('text', ''))[:1500]}")
    return "\n\n".join(out) or "(this is the first message)"


def _rules():
    return (f"You are the project's planner: you know the project from the bundle above and you talk with the owner in {ctx.C['OWNER_LANG']}, "
            "like a colleague in a chat: direct, concrete, cite file paths, say plainly what you do not know, propose small steps. Markdown is fine.\n"
            "You may look at the code before answering. To do so reply ONLY with lines (at most 3 per reply): `READ: path/to/file` "
            "(a file on main) or `GREP: regex` (matching lines on main). You get the results and can look again; after at most "
            f"{CHAT_ROUNDS - 1} rounds you must answer. Look only when the bundle does not answer the question.\n"
            "Queue work ONLY when the owner clearly asks for it (or agrees to your proposal): put one final line, nothing after it, "
            "`ACTIONS: [{\"do\": \"goal\", \"text\": \"...\"}]` (at most 3 entries; `do` is `goal` = plan and build it now, or `backlog` = wait for a free "
            "worker). Each text is a self-contained goal with a checkable end state (a closed list, a number, or a test that passes), "
            "never open-ended, and not a duplicate of a goal in progress. Without an explicit request, add no ACTIONS line.")


def chat(message):
    """One chat turn: (reply, who, queued) where queued lists what was put into the queue."""
    message = message.strip()[:4000]
    with locked():
        h = history() + [dict(role="owner", text=message, at=sbx.now())]
        jsave(_path(), h[-MAX_STORED:])
    _set_status(busy=True, started=sbx.now(), steps=["reading the project"], error="")
    try:
        refresh_repo()
        base = (f"{context_bundle()}\n## Conversation so far (your memory of it; the project above is current)\n{_render_history(history()[:-1])}\n\n"
                f"## New message from the owner\n{message}\n\n{_rules()}")
        extra, steps = "", []
        model = ctx.C.get("CHAT_MODEL", "")
        for rnd in range(CHAT_ROUNDS):
            _set_status(steps=steps + ["thinking"])
            prompt = base + extra + ("\n\n(Last round: answer now, no more READ/GREP.)" if rnd == CHAT_ROUNDS - 1 else "")
            text, who = llm("ask", prompt, model=model)
            tools = parse_tools(text) if rnd < CHAT_ROUNDS - 1 else []
            if not tools:
                break
            res = []
            for tool, arg in tools:
                steps.append(f"{tool.lower()} {arg}"[:120])
                _set_status(steps=steps)
                res.append(f"### {tool}: {arg}\n{run_tool(tool, arg)}")
            extra += f"\n\n## You asked (round {rnd + 1}):\n" + "\n".join(f"{t}: {a}" for t, a in tools) + "\n\n## Results\n" + "\n\n".join(res)
        answer, acts = parse_reply(text)
        queued = []
        for a in acts:
            if a["do"] == "goal":
                queued.append(f"goal {queue_goal(a['text'])}")
            else:
                queued.append(f"backlog #{queue_backlog(a['text'])}")
        shown = answer + (("\n\nQueued: " + "; ".join(queued)) if queued else "")
        with locked():
            jsave(_path(), (history() + [dict(role="planner", text=shown, at=sbx.now(), who=who, queued=queued, steps=steps)])[-MAX_STORED:])
        event(f"chat turn answered by {who}" + (f"; looked at {len(steps)} places" if steps else "") + (f"; queued {len(queued)}" if queued else ""))
        return shown, who, queued
    except Exception as e:
        _set_status(error=str(e)[:300])
        raise
    finally:
        _set_status(busy=False)
