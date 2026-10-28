"""The planner chat: the owner talks to the orchestrator about the project; it can queue goals and backlog entries.
Public interface: history, reset, chat, parse_reply, queue_goal, queue_backlog.
Important invariants:
- Memory is a JSON file (chat.json in the project's foreman dir), not a growing model session: every call is a fresh tool-less
  model call that gets the project bundle (vision, decisions, code map, queue) plus the last CHAT_TURNS turns, so the context stays bounded.
- The model can act only through a trailing `ACTIONS: [...]` line with at most 3 entries of type goal / backlog; both go through the
  normal pipeline (planning, finite-goal rule, gate, review, approvals). Nothing else can be triggered from chat text.
- What was queued is appended to the reply, so the owner always sees it."""
import json, os, re

import sbx

from . import ctx
from .llm import llm
from .prompts import context_bundle
from .repo import refresh_repo
from .store import event, jload, jsave, locked, new_goal_id, save_goal

CHAT_TURNS = 12          # turns (owner + reply = 2) sent back to the model
MAX_STORED = 80          # turns kept on disk
ACTION_TYPES = ("goal", "backlog")


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


def _render_history(h):
    out = []
    for turn in h[-CHAT_TURNS * 2:]:
        who = "Owner" if turn.get("role") == "owner" else "You (planner)"
        out.append(f"{who}: {str(turn.get('text', ''))[:1500]}")
    return "\n\n".join(out) or "(this is the first message)"


def chat(message):
    """One chat turn: (reply, who, queued) where queued lists what was put into the queue."""
    message = message.strip()[:4000]
    refresh_repo()
    prompt = (f"{context_bundle()}\n## Conversation so far (your memory of it; the project above is current)\n{_render_history(history())}\n\n"
              f"## New message from the owner\n{message}\n\n"
              f"You are the project's planner: you know the whole project from the bundle above and you talk with the owner in {ctx.C['OWNER_LANG']}. "
              "Answer concretely, cite file paths, say plainly what you do not know, and propose small steps. You cannot run commands or read files "
              "that are not in the bundle. Plain text or markdown.\n"
              "Queue work ONLY when the owner clearly asks for it (or agrees to your proposal): put one final line, nothing after it, "
              "`ACTIONS: [{\"do\": \"goal\", \"text\": \"...\"}]` (at most 3 entries; `do` is `goal` = plan and build it now, or `backlog` = wait for a free "
              "worker). Each text is a self-contained goal with a checkable end state (a closed list, a number, or a test that passes), "
              "never open-ended, and not a duplicate of a goal in progress. Without an explicit request, add no ACTIONS line.")
    text, who = llm("ask", prompt)
    answer, acts = parse_reply(text)
    queued = []
    for a in acts:
        if a["do"] == "goal":
            queued.append(f"goal {queue_goal(a['text'])}")
        else:
            queued.append(f"backlog #{queue_backlog(a['text'])}")
    shown = answer + (("\n\nQueued: " + "; ".join(queued)) if queued else "")
    with locked():
        h = history() + [dict(role="owner", text=message, at=sbx.now()), dict(role="planner", text=shown, at=sbx.now(), who=who, queued=queued)]
        jsave(_path(), h[-MAX_STORED:])
    event(f"chat turn answered by {who}" + (f"; queued {len(queued)}" if queued else ""))
    return shown, who, queued
