"""Worker runtimes: which agent CLI a worker container runs, how it is started, and how its event stream is read.

  agy     Google Antigravity CLI, logged in with the worker's own Google account (subscription quota, `agy /usage`)
  claude  Claude Code with an Anthropic API key        (pay per token; hard per-run cap: --max-budget-usd)
  codex   OpenAI Codex CLI with an OpenAI API key       (pay per token)
  gemini  Gemini CLI with a Gemini API key              (pay per token)
  compat  Codex CLI on your own model server - any OpenAI Responses API: Ollama, vLLM, LM Studio, OpenRouter (COMPAT_BASE_URL)
  aider   Aider on your own model server - any OpenAI chat-completions API (the same COMPAT_BASE_URL)
  opencode OpenCode CLI on your configured provider (COMPAT_BASE_URL)

The API runtimes run inside the same locked-down container as agy (worker/Dockerfile.api adds Node, the CLIs and Aider). A key is
a read-only file mount (/run/secrets/api-key) that worker/af-agent puts into the agent's environment only - it never appears in a
command line, a log or the repository (the gate refuses branches that contain key-like strings). Egress: each runtime may reach
exactly its provider's API host (for compat / aider: the configured model server, also on a private network - see SECURITY.md);
the flags below switch off everything else the CLIs would contact (update checks, telemetry, discovery - measured with a logging
proxy, docs/RUNTIMES.md).

Adding a runtime = one entry in RUNTIMES plus a branch in command() and scan() (and af-agent if it needs a key): the rest of
Agent-Forge (dispatch, supervisor caps, budgets, egress, the dashboard) only uses this interface. docs/RUNTIMES.md has the
checklist. Event formats were taken from the real CLIs (claude 2.1, codex 0.161, gemini 0.63, aider 0.86) run against a fake API
server, and are parsed defensively: unknown events are ignored, a missing result means the run failed."""
import json, re
from urllib.parse import urlsplit

AGY_IMAGE = "sandbox-worker:1.2.11"
API_IMAGE = "agent-forge-worker-api:1"

# label: shown in the dashboard; key: the secrets file mounted into the container (None = none); hosts: what egress must allow;
# api: paid per token (budgets apply, no subscription quota); endpoint: talks to COMPAT_BASE_URL; optional_key: works without a
# key (a local model server); prices: the limits.conf key with "in,cached,out" USD per million tokens (None = the CLI reports cost);
# own_git: the agent runs git itself (else the supervisor checks out the task branch before the run)
RUNTIMES = {
    "agy": dict(label="Antigravity (Google account, subscription)", image=AGY_IMAGE, key=None, hosts=[], api=False),
    "claude": dict(label="Claude Code (Anthropic API key)", image=API_IMAGE, key="anthropic.key", hosts=["api.anthropic.com"], api=True),
    "codex": dict(label="Codex CLI (OpenAI API key)", image=API_IMAGE, key="openai.key", hosts=["api.openai.com"], api=True,
                  prices="PRICE_CODEX"),
    "gemini": dict(label="Gemini CLI (Gemini API key)", image=API_IMAGE, key="gemini.key", hosts=["generativelanguage.googleapis.com"],
                   api=True, prices="PRICE_GEMINI"),
    "compat": dict(label="Codex CLI on your model server (Ollama, vLLM, LM Studio, OpenRouter - OpenAI Responses API)", image=API_IMAGE,
                   key="compat.key", hosts=[], api=True, endpoint=True, optional_key=True, prices="PRICE_COMPAT"),
    "aider": dict(label="Aider on your model server (any OpenAI-compatible chat API)", image=API_IMAGE, key="compat.key", hosts=[],
                  api=True, endpoint=True, optional_key=True, prices="PRICE_COMPAT", own_git=False),
    "opencode": dict(label="OpenCode (SST's OpenCode CLI, configured provider)", image=AGY_IMAGE, key=None, hosts=[],
                     api=False, endpoint=True),
}
# Codex contacts chatgpt.com and api.github.com unless these are off (checked with a logging proxy: then only api.openai.com)
CODEX_QUIET = ["-c", "check_for_update_on_startup=false", "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
               "--disable", "plugins", "--disable", "apps", "--disable", "api_key_model_discovery", "--disable", "browser_use",
               "--disable", "computer_use"]
# Aider: no update check, no analytics, no release notes, no edits to .gitignore, plain output for the event filter in af-agent
AIDER_QUIET = ["--yes-always", "--no-pretty", "--no-stream", "--no-fancy-input", "--no-check-update", "--no-show-release-notes",
               "--analytics-disable", "--no-gitignore", "--no-show-model-warnings", "--no-detect-urls"]
# OpenCode: non-interactive JSON stream for scan(), quiet
OPENCODE_QUIET = ["--format", "json"]
GEMINI_TRAILER = "Do the task described above."
ENDPOINT_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?")


def is_api(rt):
    return RUNTIMES.get(rt or "agy", RUNTIMES["agy"])["api"]


def uses_endpoint(rt):
    return bool(RUNTIMES.get(rt or "agy", {}).get("endpoint"))


def endpoint_target(url):
    """(scheme, host, port) of a model server URL, or None when it is not a plain http(s) URL."""
    if not url or not ENDPOINT_RE.fullmatch(url):
        return None
    u = urlsplit(url)
    return u.scheme, (u.hostname or "").lower(), u.port or (443 if u.scheme == "https" else 80)


def hosts(rt, endpoint_url=""):
    """The hosts this runtime's workers must reach (endpoint runtimes: the model server's host)."""
    r = RUNTIMES[rt]
    if r.get("endpoint"):
        t = endpoint_target(endpoint_url)
        return [t[1]] if t else []
    return list(r["hosts"])


def command(rt, prompt, model, conversation=None, left_min=30, budget_usd=0.0):
    """(argv inside the worker container, stdin text). API runtimes get the prompt on stdin (no 128 KiB argv limit). The model
    server URL of compat / aider comes in the environment (AF_ENDPOINT, set by bin/run-supervisor), not in argv."""
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
    if rt in ("codex", "compat"):
        argv = ["af-agent", rt, "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"] + CODEX_QUIET
        argv += (["-m", model] if model else []) + (["resume", conversation] if conversation else []) + ["-"]
        return argv, prompt
    if rt == "gemini":
        argv = ["af-agent", "gemini", "--output-format", "stream-json", "--yolo", "--skip-trust"]
        argv += (["-m", model] if model else []) + (["--resume", conversation] if conversation else []) + ["-p", GEMINI_TRAILER]
        return argv, prompt
    if rt == "aider":
        # af-agent writes the prompt to a file for --message-file, keeps the chat history outside the repository (per branch:
        # the conversation id), runs the project's tests after each edit (--auto-test, AF_TEST_CMD) and turns Aider's text output
        # into JSON events (aider.init / aider.line / aider.usage / aider.result) for scan() and the dashboard
        argv = ["af-agent", "aider"] + AIDER_QUIET + (["--model", "openai/" + model] if model else [])
        argv += ["--restore-chat-history"] if conversation else []
        return argv, prompt
    if rt == "opencode":
        argv = ["opencode", "run"] + OPENCODE_QUIET
        argv += (["--model", model] if model else [])
        argv += (["--session", conversation] if conversation else [])
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
    if rt == "compat":
        rt = "codex"                           # the same CLI and event stream
    st = dict(tokens=None if rt == "opencode" else 0, tools=0, conversation_id=None, result=None)
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
        elif rt == "aider":
            # events written by af-agent's filter around Aider's plain-text output
            t = e.get("type")
            if t == "aider.init" and e.get("conversation_id"):
                st["conversation_id"] = st["conversation_id"] or e["conversation_id"]
            elif t == "aider.line":
                s = str(e.get("text") or "")
                if s.startswith(("Applied edit to ", "Running ")):
                    st["tools"] += 1
                elif s and not s.startswith(("Tokens:", "Aider v", "Model:", "Git repo:", "Repo-map:", "Added ", "Commit ", "Cost:")):
                    text.append(s)
            elif t == "aider.usage":
                st["aider_sent"] = st.get("aider_sent", 0) + int(e.get("sent") or 0)
                st["aider_recv"] = st.get("aider_recv", 0) + int(e.get("received") or 0)
                st["aider_cost"] = st.get("aider_cost", 0.0) + float(e.get("cost") or 0)
                st["tokens"] = st["aider_sent"] + st["aider_recv"]
            elif t == "aider.result":
                ok = e.get("status") == "SUCCESS"
                p = _prices(prices or "")
                if prices and any(p):
                    cost = (st.get("aider_sent", 0) * p[0] + st.get("aider_recv", 0) * p[2]) / 1e6
                else:
                    cost = st.get("aider_cost") if prices is None else 0.0
                st["result"] = dict(status="SUCCESS" if ok else "ERROR", response="\n".join(text)[-4000:],
                                    error="" if ok else str(e.get("error") or f"aider exited with {e.get('exit_code')}"),
                                    usage=dict(sent=st.get("aider_sent", 0), received=st.get("aider_recv", 0)),
                                    cost_usd=round(cost, 6) if cost is not None else None)
        elif rt == "opencode":
            t = e.get("type") or e.get("event")
            sid = e.get("sessionID") or e.get("session_id") or e.get("sessionId") or e.get("conversation_id")
            if not sid and isinstance(e.get("part"), dict):
                sid = e["part"].get("sessionID") or e["part"].get("session_id") or e["part"].get("sessionId")
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)
            part = e.get("part") if isinstance(e.get("part"), dict) else {}
            if t in ("tool_use", "tool_call", "tool") or part.get("type") in ("tool", "tool_use", "tool_call") or part.get("tool"):
                st["tools"] += 1
            elif t == "text":
                txt = part.get("text") or e.get("text") or e.get("content") or ""
                if txt:
                    text.append(str(txt))
            elif t in ("message", "message.part.delta", "message.part.updated", "assistant.message"):
                txt = part.get("text") or e.get("text") or e.get("content") or (e.get("message") or {}).get("content") or ""
                if isinstance(txt, list):
                    txt = "".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
                if txt:
                    text.append(str(txt))
            u = part.get("tokens") or e.get("tokens") or e.get("usage")
            if isinstance(u, dict):
                if any(k in u for k in ("input", "output", "input_tokens", "output_tokens", "total_tokens")):
                    inp = u.get("input") or u.get("input_tokens") or 0
                    out = u.get("output") or u.get("output_tokens") or 0
                    tot = u.get("total_tokens") or (inp + out)
                    st["tokens"] = (st["tokens"] or 0) + max(0, tot)
            elif isinstance(u, (int, float)) and u > 0:
                st["tokens"] = (st["tokens"] or 0) + int(u)

            if t == "step_finish":
                reason = part.get("reason") or e.get("reason") or ""
                if reason in ("stop", "end", "completed", "complete") or (not reason and not part.get("type") == "tool"):
                    ok = not part.get("error") and not e.get("error") and not e.get("is_error")
                    err = str(part.get("error") or e.get("error") or ("error" if not ok else ""))
                    st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err,
                                        usage=part.get("tokens") or e.get("tokens") or e.get("usage"),
                                        cost_usd=None)
                elif reason in ("error", "failed"):
                    st["result"] = dict(status="ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=str(part.get("error") or e.get("error") or reason),
                                        usage=part.get("tokens") or e.get("tokens") or e.get("usage"),
                                        cost_usd=None)
            elif t == "result":
                ok = e.get("status") in ("success", "SUCCESS") or e.get("subtype") == "success" or not (e.get("error") or e.get("is_error"))
                err = str(e.get("error") or ("" if ok else "error"))
                res = str(e.get("response") or e.get("result") or "\n".join(text))[-4000:]
                st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                    response=res,
                                    error=err,
                                    usage=e.get("usage") or e.get("tokens"),
                                    cost_usd=None)
            elif t == "error":
                err = (e.get("error") or {}).get("message", "") if isinstance(e.get("error"), dict) else str(e.get("error") or e.get("message") or "error")
                st["result"] = dict(status="ERROR", response="\n".join(text)[-4000:], error=err, usage=None, cost_usd=None)
    for k in ("aider_sent", "aider_recv", "aider_cost", "last_error"):
        st.pop(k, None)
    return st


def scan_file(rt, path, prices=None):
    try:
        with open(path, errors="replace") as f:
            return scan(rt, f, prices)
    except FileNotFoundError:
        return scan(rt, [], prices)
