"""The orchestrator's LLM calls. Every call is a fresh, tool-less session; providers are tried in the configured order
(Claude Code, Claude/Gemini via Antigravity, paid APIs) and one out of quota is retried after its reset."""
import json, os, threading, time

import sbx

from . import ctx
from .util import extract_json, fmt_ts, parse_limit, sh
from .store import event, gstate, state_tx
from .pool import RESERVED, has_quota, order_by_quota, quota_pct, quota_rows, runtime_of, worker_busy, workers
from .knowledge import load_system_prompt


# ---------------------------------------------------------------- LLM layer: free subscription providers first, paid APIs only if configured
class Unavailable(Exception):
    pass


def providers_for(kind):
    order = ctx.C["REVIEW_PROVIDERS"] if kind == "review" else ctx.C["PLAN_PROVIDERS"]
    return [p.strip() for p in str(order).split(",") if p.strip()]


def llm_available():
    st, now = gstate(), time.time()
    for p in providers_for("plan") + providers_for("review"):
        if p == "claude-cli" and st.get("claude_until", 0) <= now:
            return True
        if p.startswith("agy-") and any(st.get("agy_cooldown", {}).get(w, 0) <= now for w in workers() if runtime_of(w) == "agy"):
            return True
        if p in ("anthropic-api", "openai-api", "gemini-api"):
            return True
    return False


def _agy(w, prompt, model, schema=None):
    """Tool-less Antigravity call (no --dangerously-skip-permissions): the reviewer/planner reads only what is in the prompt,
    so worker-written content in a diff cannot make it run commands. Runs from an empty working directory."""
    t0w = time.time()
    rc, out, err = sh(["docker", "exec", "-w", "/tmp", w, "agy-run", "-p", prompt, "--model", model, "--output-format", "stream-json",
                       "--print-timeout", "15m"] + (["--json-schema", schema] if schema else []), timeout=1100)
    res = {}
    for line in out.splitlines():
        e = extract_json(line) if line.startswith("{") else None
        if e and e.get("event") == "result":
            res = e.get("result") or {}
    text = res.get("response") or ""
    log_llm(KIND.k, f"agy:{model}@{w}", rc, time.time() - t0w, None, res.get("usage"), text[:300] or err[:300])
    ok = res.get("status") == "SUCCESS" and bool(text.strip())
    return ok, text, parse_limit(text + "\n" + out[-2000:] + "\n" + err[-2000:]) if not ok else None, (text or err)[:160]


class _Kind(threading.local):
    k = "?"
    subject = ""             # the task or goal the call is about (for the cost of a task / goal)


KIND = _Kind()           # the decision kind (plan/review/...) of the LLM call running in THIS thread - steps run in parallel


LAST_LLM = {}


def _via_agy(kind, prompt, family):
    model = (ctx.C["AGY_REVIEW_MODEL"] if kind == "review" else ctx.C["AGY_PLAN_MODEL"]) if family == "claude" else ctx.C["AGY_FALLBACK_MODEL"]
    st, now = gstate(), time.time()
    cd = st.get("agy_cooldown", {})
    cand = [w for w in workers() if runtime_of(w) == "agy" and cd.get(w, 0) <= now and has_quota(quota_rows(w), family)]
    cand = order_by_quota(cand, lambda w: quota_pct(quota_rows(w), family), LAST_LLM)
    for w in cand:
        idle = not worker_busy(w)
        if idle:
            RESERVED.add(w)
        LAST_LLM[w] = time.time()
        try:
            ok, text, reset_ts, why = _agy(w, prompt, model, REVIEW_SCHEMA if kind == "review" else None if kind == "ask" else OBJECT_SCHEMA)
        finally:
            RESERVED.discard(w)
        if ok:
            return text
        with state_tx() as st:
            st.setdefault("agy_cooldown", {})[w] = reset_ts if reset_ts else time.time() + 90
        event(f"{w}: {family} via Antigravity " + (f"out of quota until {fmt_ts(reset_ts)}" if reset_ts else f"failed ({why})") +
              " - trying the next worker")
    return None


def _via_claude_cli(prompt, system):
    st, t0 = gstate(), time.time()
    if st.get("claude_until", 0) > t0:
        return None
    args = ["claude", "-p", "--model", ctx.C["CLAUDE_MODEL"], "--setting-sources", "", "--no-session-persistence", "--output-format", "json",
            "--permission-mode", "dontAsk", "--tools", "", "--append-system-prompt", system]
    rc, out, err = sh(args, timeout=1200, cwd=ctx.REPO, inp=prompt)
    res = extract_json(out) or {}
    text = res.get("result") or ""
    log_llm(KIND.k, "claude", rc, time.time() - t0, res.get("total_cost_usd"), res.get("usage"), text[:300] or err[:300])
    if rc == 0 and not res.get("is_error") and text:
        if st.get("claude_until") or st.get("claude_fails"):
            with state_tx() as s2:
                s2.pop("claude_until", None); s2.pop("claude_fails", None)
            event("orchestrator Claude Code is available again")
        return text
    until = parse_limit(text + "\n" + out[-2000:] + "\n" + err[-2000:])
    with state_tx() as s2:
        if until:
            s2["claude_until"] = until
        else:
            # not a usage limit (not logged in, CLI missing, crash): back off 2, 4, 8 ... 60 min instead of retrying every tick
            n = s2.get("claude_fails", 0) + 1
            s2["claude_fails"] = n
            s2["claude_until"] = time.time() + min(60, 2 ** n) * 60
    if until:
        event(f"orchestrator Claude Code is out of usage until {fmt_ts(until)} - next provider substitutes")
    else:
        event(f"orchestrator Claude Code call failed (rc {rc}): {(text or err)[:200]} - next provider substitutes; "
              f"Claude Code paused for {min(60, 2 ** s2['claude_fails'])} min")
    return None


def _via_paid_api(name, prompt, system):
    import providers
    try:
        if name == "anthropic-api" and ctx.C["ANTHROPIC_MODEL"]:
            r = providers.call_anthropic_api(prompt, model=ctx.C["ANTHROPIC_MODEL"], system=system)
        elif name == "openai-api" and ctx.C["OPENAI_MODEL"]:
            r = providers.call_openai_api(prompt, model=ctx.C["OPENAI_MODEL"], api_base=ctx.C["OPENAI_API_BASE"], system=system)
        elif name == "gemini-api" and ctx.C["GEMINI_MODEL"]:
            r = providers.call_gemini_api(prompt, model=ctx.C["GEMINI_MODEL"], system=system)
        else:
            return None
    except ValueError:          # no key configured
        return None
    log_llm(KIND.k, f"{name}:{r.get('model')}", 1 if r["is_error"] else 0, r["duration_s"], None, r.get("usage"), r["text"][:300])
    return r["text"] if not r["is_error"] and r["text"].strip() else None


def llm(kind, prompt, subject=""):
    """Returns (text, who). Raises Unavailable when no configured provider can answer right now. Every call is a fresh,
    tool-less session; the stable system prompt goes in as a system prompt (cached by Claude Code). `subject`: the task or
    goal id the call is about (logged, for the cost of a task or goal)."""
    KIND.k, KIND.subject = kind, subject
    system = load_system_prompt()
    for p in providers_for(kind):
        if p == "claude-cli":
            text = _via_claude_cli(prompt, system)
        elif p == "agy-claude":
            text = _via_agy(kind, system + "\n\n" + prompt, "claude")
        elif p == "agy-gemini":
            text = _via_agy(kind, system + "\n\n" + prompt, "gemini")
        else:
            text = _via_paid_api(p, prompt, system)
        if text:
            return text, p
    raise Unavailable(f"no orchestrator provider could answer ({', '.join(providers_for(kind))})")


def log_llm(kind, who, rc, secs, cost, usage, head):
    with open(os.path.join(ctx.FD, "llm.jsonl"), "a") as f:
        f.write(json.dumps(dict(at=sbx.now(), kind=kind, who=who, rc=rc, secs=round(secs, 1), cost_usd=cost, usage=usage, head=head,
                                subject=KIND.subject or None), ensure_ascii=False) + "\n")


REVIEW_SCHEMA = json.dumps({"type": "object", "required": ["verdict", "feedback"], "properties": {
    "verdict": {"type": "string", "enum": ["approve", "rework", "reject"]}, "feedback": {"type": "string"}, "lesson": {"type": "string"},
    "mutations": {"type": "array", "items": {"type": "object", "required": ["name", "file", "old", "new"], "properties": {
        "name": {"type": "string"}, "file": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}}}}})


OBJECT_SCHEMA = json.dumps({"type": "object"})
