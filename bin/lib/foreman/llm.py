"""The orchestrator's LLM calls. Every call is a fresh, tool-less session; providers are tried in the configured order
(Claude Code, Claude/Gemini via Antigravity, paid APIs: OpenAI-compatible, Gemini API, Anthropic API).
Public interface: llm(kind, prompt, subject=""), providers_for(kind), llm_available(), log_llm().
Invariants:
- providers_for resolves configured providers by decision kind with fallback to ORCHESTRATOR_PROVIDER.
- llm validates JSON responses against schemas (REVIEW_SCHEMA, OBJECT_SCHEMA), extracting JSON from code fences and prose.
- Provider errors or credential failures cascade to the next provider, raising Unavailable only on total exhaustion.
- Raw API keys, tokens, and Authorization headers are stripped before logging, event recording, or exception re-raising.
"""
import json, os, re, threading, time, urllib.parse

import sbx

from . import ctx, util
from .util import extract_json, fmt_ts, parse_limit, sh
from .store import event as _raw_event, gstate, state_tx
from .pool import RESERVED, has_quota, order_by_quota, quota_pct, quota_rows, runtime_of, worker_busy, workers
from .knowledge import load_system_prompt


def _sanitize_secrets(text):
    """Strips API keys, bearer tokens, and Authorization/x-api-key headers from text."""
    if not isinstance(text, str) or not text:
        return text
    secrets = set()
    try:
        import providers
        for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
            try:
                s = providers.get_secret(k)
                if s and isinstance(s, str) and s.strip():
                    secrets.add(s.strip())
            except Exception:
                pass
    except Exception:
        pass
    for k in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        val = os.environ.get(k, "").strip()
        if val:
            secrets.add(val)

    for s in secrets:
        if len(s) >= 3:
            text = text.replace(s, "[REDACTED]")
            try:
                enc = urllib.parse.quote(s)
                if enc != s:
                    text = text.replace(enc, "[REDACTED]")
            except Exception:
                pass

    text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9_\-\.\:\+]+', 'Bearer [REDACTED]', text)
    text = re.sub(r'''(?i)(['"](?:authorization|x-api-key|x-goog-api-key)['"]\s*:\s*['"])(?:Bearer\s+)?[^'"]+(['"])''', r'\1[REDACTED]\2', text)
    text = re.sub(r'''(?i)(['"](?:authorization|x-api-key|x-goog-api-key)['"]\s*,\s*['"])(?:Bearer\s+)?[^'"]+(['"])''', r'\1[REDACTED]\2', text)
    text = re.sub(r'(?i)\b(authorization)\s*:\s*(?:token\s+|basic\s+|bearer\s+)?([^\s,;\'"\}]+)', r'\1: [REDACTED]', text)
    text = re.sub(r'(?i)\b(x-api-key|x-goog-api-key)\s*[:=]\s*[\'"]?[^\s,;\'"\}]+[\'"]?', r'\1: [REDACTED]', text)
    text = re.sub(r'(?i)([?&](?:api_?key|key|token|secret)=)[^\s&\'"\}]+', r'\1[REDACTED]', text)
    return text


def event(msg, **kw):
    _raw_event(_sanitize_secrets(str(msg)), **kw)


# ---------------------------------------------------------------- LLM layer: free subscription providers first, paid APIs only if configured
class Unavailable(Exception):
    pass


def providers_for(kind):
    order = ctx.C.get("REVIEW_PROVIDERS") if kind == "review" else ctx.C.get("PLAN_PROVIDERS")
    if isinstance(order, (list, tuple)):
        provs = [str(p).strip() for p in order if str(p).strip()]
    else:
        provs = [p.strip() for p in str(order or "").split(",") if p.strip()]
    if not provs:
        fallback = ctx.C.get("ORCHESTRATOR_PROVIDER")
        if isinstance(fallback, (list, tuple)):
            provs = [str(p).strip() for p in fallback if str(p).strip()]
        else:
            provs = [p.strip() for p in str(fallback or "").split(",") if p.strip()]
        if not provs:
            env_fallback = os.environ.get("ORCHESTRATOR_PROVIDER", "")
            if isinstance(env_fallback, (list, tuple)):
                provs = [str(p).strip() for p in env_fallback if str(p).strip()]
            else:
                provs = [p.strip() for p in str(env_fallback or "").split(",") if p.strip()]
    return provs


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
            ok, text, reset_ts, why = _agy(w, prompt, model, schema_for(kind))
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
    model = (
        ctx.C.get("ANTHROPIC_MODEL") if name == "anthropic-api" else
        ctx.C.get("OPENAI_MODEL") if name == "openai-api" else
        ctx.C.get("GEMINI_MODEL") if name == "gemini-api" else ""
    )
    if not str(model or "").strip():
        model = (
            os.environ.get("ANTHROPIC_MODEL") if name == "anthropic-api" else
            os.environ.get("OPENAI_MODEL") if name == "openai-api" else
            os.environ.get("GEMINI_MODEL") if name == "gemini-api" else ""
        )
    model = str(model or "").strip()
    if not model:
        return None, None
    who = f"{name}:{model}"
    t0 = time.time()
    try:
        if name == "anthropic-api":
            r = providers.call_anthropic_api(prompt, model, system=system)
        elif name == "openai-api":
            api_base = str(ctx.C.get("OPENAI_API_BASE") or "").strip() or os.environ.get("OPENAI_API_BASE", "").strip() or "https://api.openai.com/v1"
            r = providers.call_openai_api(prompt, model, system=system, api_base=api_base)
        elif name == "gemini-api":
            r = providers.call_gemini_api(prompt, model, system=system)
        else:
            return None, None
    except ValueError:          # no key configured
        return None, None
    except Exception as e:
        safe_err = _sanitize_secrets(str(e))
        log_llm(KIND.k, who, 1, time.time() - t0, None, None, safe_err[:300])
        return None, None

    if not isinstance(r, dict):
        return None, None
    is_err = r.get("is_error") or not (r.get("text") or "").strip()
    dur = r.get("duration_s")
    dur = (time.time() - t0) if dur is None else dur
    raw_head = r.get("text") or r.get("error") or ""
    safe_head = _sanitize_secrets(raw_head)[:300]
    log_llm(KIND.k, who, 1 if is_err else 0, dur, r.get("cost_usd"), r.get("usage"), safe_head)
    if is_err:
        return None, None
    return r["text"], who


def schema_for(kind):
    if kind == "review":
        return REVIEW_SCHEMA
    if kind == "ask":
        return None
    return OBJECT_SCHEMA


def validate_schema(data, schema):
    """Validates that parsed dict `data` satisfies JSON schema dict/string `schema`."""
    if not isinstance(data, dict):
        return False
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except Exception:
            return False
    if not isinstance(schema, dict):
        return False
    if schema.get("type") == "object":
        if not isinstance(data, dict):
            return False
    for req in schema.get("required", []):
        if req not in data:
            return False
    props = schema.get("properties", {})
    if "verdict" in props and "verdict" in data:
        allowed = props["verdict"].get("enum")
        if allowed and data["verdict"] not in allowed:
            return False
    return True


def parse_and_validate(text, schema_str):
    """Extracts JSON and validates against schema. Returns parsed dict, or text for schemaless, or None if invalid."""
    if not text:
        return None
    if not schema_str:
        return text if bool(text.strip()) else None
    data = util.extract_json(text)
    if data is None or not validate_schema(data, schema_str):
        return None
    return data


def _llm_inner(kind, prompt, subject=""):
    KIND.k, KIND.subject = kind, subject
    system = load_system_prompt()
    schema = schema_for(kind)
    for p in providers_for(kind):
        text, who = None, p
        try:
            if p == "claude-cli":
                text = _via_claude_cli(prompt, system)
                who = "claude-cli"
            elif p == "agy-claude":
                text = _via_agy(kind, system + "\n\n" + prompt, "claude")
                who = "agy-claude"
            elif p == "agy-gemini":
                text = _via_agy(kind, system + "\n\n" + prompt, "gemini")
                who = "agy-gemini"
            else:
                text, who = _via_paid_api(p, prompt, system)
        except Unavailable:
            continue
        except Exception:
            continue
        if text and parse_and_validate(text, schema) is not None:
            return text, who
    raise Unavailable(_sanitize_secrets(f"no orchestrator provider could answer ({', '.join(providers_for(kind))})"))


def llm(kind, prompt, subject=""):
    """Returns (text, who). Raises Unavailable when no configured provider can answer right now. Every call is a fresh,
    tool-less session; the stable system prompt goes in as a system prompt (cached by Claude Code). `subject`: the task or
    goal id the call is about (logged, for the cost of a task or goal)."""
    try:
        return _llm_inner(kind, prompt, subject)
    except Unavailable as e:
        raise Unavailable(_sanitize_secrets(str(e))) from None
    except Exception as e:
        safe_msg = _sanitize_secrets(str(e))
        raise type(e)(safe_msg) from None


def log_llm(kind, who, rc, secs, cost, usage, head):
    if ctx.FD:
        os.makedirs(ctx.FD, exist_ok=True)
    safe_head = _sanitize_secrets(head) if isinstance(head, str) else head
    with open(os.path.join(ctx.FD or ".", "llm.jsonl"), "a") as f:
        f.write(json.dumps(dict(at=sbx.now(), kind=kind, who=who, rc=rc,
                                secs=round(secs, 1) if secs is not None else 0.0,
                                cost_usd=cost, usage=usage, head=safe_head,
                                subject=KIND.subject or None), ensure_ascii=False) + "\n")


REVIEW_SCHEMA = json.dumps({"type": "object", "required": ["verdict", "feedback"], "properties": {
    "verdict": {"type": "string", "enum": ["approve", "rework", "reject"]}, "feedback": {"type": "string"}, "lesson": {"type": "string"},
    "mutations": {"type": "array", "items": {"type": "object", "required": ["name", "file", "old", "new"], "properties": {
        "name": {"type": "string"}, "file": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}}}}})


OBJECT_SCHEMA = json.dumps({"type": "object"})
