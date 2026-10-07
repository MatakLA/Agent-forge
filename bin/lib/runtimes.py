"""Worker runtimes: which agent CLI a worker container runs, how it is started, and how its event stream is read.

  agy     Google Antigravity CLI, logged in with the worker's own Google account (subscription quota, `agy /usage`)
  claude  Claude Code with an Anthropic API key        (pay per token; hard per-run cap: --max-budget-usd)
  codex   OpenAI Codex CLI with an OpenAI API key       (pay per token)
  gemini  Gemini CLI with a Gemini API key              (pay per token)

The API runtimes run inside the same locked-down container as agy (worker/Dockerfile.api adds Node and the three CLIs). The
key is a read-only file mount (/run/secrets/api-key) that worker/af-agent puts into the agent's environment only - it never
appears in a command line, a log or the repository (the gate refuses branches that contain key-like strings). Egress: each
runtime adds exactly one host to the proxy allowlist; the flags below switch off everything else the CLIs would contact
(update checks, telemetry, plugin/app/model discovery - measured with a logging proxy, see docs/RUNTIMES.md).

Event formats were taken from the real CLIs (claude 2.1, codex 0.161, gemini 0.63) run against a fake API server, and are
parsed defensively: unknown events are ignored, a missing result means the run failed."""
import json

AGY_IMAGE = "sandbox-worker:1.2.11"
API_IMAGE = "agent-forge-worker-api:1"

RUNTIMES = {
    "agy": dict(label="Antigravity (Google account, subscription)", image=AGY_IMAGE, key=None, hosts=[], api=False),
    "claude": dict(label="Claude Code (Anthropic API key)", image=API_IMAGE, key="anthropic.key", hosts=["api.anthropic.com"], api=True),
    "codex": dict(label="Codex CLI (OpenAI API key)", image=API_IMAGE, key="openai.key", hosts=["api.openai.com"], api=True),
    "gemini": dict(label="Gemini CLI (Gemini API key)", image=API_IMAGE, key="gemini.key", hosts=["generativelanguage.googleapis.com"], api=True),
}
# Codex contacts chatgpt.com and api.github.com unless these are off (checked with a logging proxy: then only api.openai.com)
CODEX_QUIET = ["-c", "check_for_update_on_startup=false", "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
               "--disable", "plugins", "--disable", "apps", "--disable", "api_key_model_discovery", "--disable", "browser_use",
               "--disable", "computer_use"]
GEMINI_TRAILER = "Do the task described above."


def is_api(rt):
    return RUNTIMES.get(rt or "agy", RUNTIMES["agy"])["api"]


def command(rt, prompt, model, conversation=None, left_min=30, budget_usd=0.0):
    """(argv inside the worker container, stdin text). API runtimes get the prompt on stdin (no 128 KiB argv limit)."""
    rt = rt or "agy"
    if rt == "agy":
        argv = ["agy-run", "-p", prompt, "--model", model, "--dangerously-skip-permissions", "--output-format", "stream-json",
                "--print-timeout", f"{left_min}m"] + (["--conversation", conversation] if conversation else [])
        return argv, None
    if rt == "claude":
        argv = ["af-agent", "claude", "-p", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
        argv += (["--model", model] if model else []) + (["--max-budget-usd", f"{budget_usd:g}"] if budget_usd else [])
        argv += ["--resume", conversation] if conversation else []
        return argv, prompt
    if rt == "codex":
        argv = ["af-agent", "codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"] + CODEX_QUIET
        argv += (["-m", model] if model else []) + (["resume", conversation] if conversation else []) + ["-"]
        return argv, prompt
    if rt == "gemini":
        argv = ["af-agent", "gemini", "--output-format", "stream-json", "--yolo", "--skip-trust"]
        argv += (["-m", model] if model else []) + (["--resume", conversation] if conversation else []) + ["-p", GEMINI_TRAILER]
        return argv, prompt
    raise ValueError(f"unknown runtime {rt!r}")


def _prices(spec):
    """'in,cached,out' USD per million tokens -> tuple of floats (0 when unparsable)."""
    try:
        a = [float(x) for x in str(spec).split(",")]
        return (a + [0, 0, 0])[:3]
    except ValueError:
        return (0.0, 0.0, 0.0)


def scan(rt, lines, prices=None):
    """Read a run's event stream. Returns dict(tokens, tools, conversation_id, result) where result is None (no final event
    yet / never) or dict(status SUCCESS|ERROR, response, error, usage, cost_usd). `tokens` counts what the model processed
    excluding cache reads (the same measure for every runtime; it drives the per-run token cap)."""
    rt = rt or "agy"
    st = dict(tokens=0, tools=0, conversation_id=None, result=None)
    text = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if rt == "agy":
            if e.get("event") == "step_update":
                s = e.get("step_update") or {}
                if s.get("state") == "DONE":
                    st["tokens"] += (s.get("usage") or {}).get("total_tokens", 0) or 0
                    st["tools"] += s.get("step_type") == "tool"
            elif e.get("event") == "result":
                r = e.get("result") or {}
                st["result"] = dict(status=r.get("status"), response=r.get("response") or "", error=str(r.get("error") or ""),
                                    usage=r.get("usage"), cost_usd=None)
            for part in (e.get("init"), e.get("result"), e.get("step_update")):
                if isinstance(part, dict) and part.get("conversation_id") and not st["conversation_id"]:
                    st["conversation_id"] = part["conversation_id"]
        elif rt == "claude":
            t = e.get("type")
            if e.get("session_id") and not st["conversation_id"]:
                st["conversation_id"] = e["session_id"]
            if t == "assistant":
                msg = e.get("message") or {}
                u = msg.get("usage") or {}
                st["tokens"] += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("output_tokens") or 0)
                st["tools"] += sum(1 for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use")
            elif t == "result":
                u = e.get("usage") or {}
                ok = e.get("subtype") == "success" and not e.get("is_error")
                st["tokens"] = max(st["tokens"], (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("output_tokens") or 0))
                st["result"] = dict(status="SUCCESS" if ok else "ERROR", response=str(e.get("result") or ""),
                                    error="" if ok else f"{e.get('subtype')} {e.get('api_error_status') or ''} {e.get('result') or ''}".strip(),
                                    usage=u, cost_usd=e.get("total_cost_usd"), subtype=e.get("subtype"))
        elif rt == "codex":
            t = e.get("type", "")
            if t == "thread.started" and e.get("thread_id"):
                st["conversation_id"] = st["conversation_id"] or e["thread_id"]
            elif t in ("item.completed",):
                it = e.get("item") or {}
                if it.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                    st["tools"] += 1
                elif it.get("type") == "agent_message":
                    text.append(str(it.get("text") or ""))
            elif t == "turn.completed":
                u = e.get("usage") or {}
                st["tokens"] += max(0, (u.get("input_tokens") or 0) - (u.get("cached_input_tokens") or 0)) + (u.get("output_tokens") or 0)
                p = _prices(prices or "")
                cost = ((u.get("input_tokens") or 0) - (u.get("cached_input_tokens") or 0)) * p[0] + (u.get("cached_input_tokens") or 0) * p[1] \
                    + (u.get("output_tokens") or 0) * p[2]
                prev = (st["result"] or {}).get("cost_usd") or 0
                st["result"] = dict(status="SUCCESS", response="\n".join(text)[-4000:], error="", usage=u,
                                    cost_usd=round(prev + cost / 1e6, 6) if prices else None)
            elif t == "turn.failed":
                err = (e.get("error") or {}).get("message", "") if isinstance(e.get("error"), dict) else str(e.get("error"))
                st["result"] = dict(status="ERROR", response="\n".join(text)[-4000:], error=err, usage=None, cost_usd=None)
        elif rt == "gemini":
            t = e.get("type")
            if t == "init" and e.get("session_id"):
                st["conversation_id"] = st["conversation_id"] or e["session_id"]
            elif t == "tool_use":
                st["tools"] += 1
            elif t == "message" and e.get("role") == "assistant":
                text.append(str(e.get("content") or ""))
            elif t == "error":
                st["last_error"] = str(e.get("message") or e.get("error") or e)[:500]
            elif t == "result":
                s = e.get("stats") or {}
                st["tokens"] = max(0, (s.get("total_tokens") or 0) - (s.get("cached") or 0))
                ok = e.get("status") == "success"
                p = _prices(prices or "")
                cost = ((s.get("input_tokens") or 0) - (s.get("cached") or 0)) * p[0] + (s.get("cached") or 0) * p[1] + (s.get("output_tokens") or 0) * p[2]
                st["result"] = dict(status="SUCCESS" if ok else "ERROR", response="".join(text)[-4000:],
                                    error="" if ok else str(e.get("error") or st.get("last_error") or e.get("status")),
                                    usage=s, cost_usd=round(cost / 1e6, 6) if prices else None)
    return st


def scan_file(rt, path, prices=None):
    try:
        with open(path, errors="replace") as f:
            return scan(rt, f, prices)
    except FileNotFoundError:
        return scan(rt, [], prices)
