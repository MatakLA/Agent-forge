"""Worker runtimes: which agent CLI a worker container runs, how it is started, and how its event stream is read.

  agy     Google Antigravity CLI, logged in with the worker's own Google account (subscription quota, `agy /usage`)
  claude  Claude Code with an Anthropic API key        (pay per token; hard per-run cap: --max-budget-usd)
  codex   OpenAI Codex CLI with an OpenAI API key       (pay per token)
  gemini  Gemini CLI with a Gemini API key              (pay per token)
  compat  Codex CLI on your own model server - any OpenAI Responses API: Ollama, vLLM, LM Studio, OpenRouter (COMPAT_BASE_URL)
  aider   Aider on your own model server - any OpenAI chat-completions API (the same COMPAT_BASE_URL)
  opencode OpenCode CLI on your configured provider (COMPAT_BASE_URL)
  goose   Block's Goose CLI agent on your model server (COMPAT_BASE_URL)
  qwen-code Qwen Code CLI on Alibaba DashScope
  anthropic-compat Anthropic Messages API on your model server (COMPAT_BASE_URL)
  mistral-vibe Mistral Vibe CLI on Mistral AI API (api.mistral.ai)
  copilot GitHub Copilot CLI on GitHub API (api.githubcopilot.com, api.github.com)

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
    # Goose: provider-configurable on model server (endpoint runtime, resolves host via uses_endpoint/hosts)
    "goose": dict(label="Goose (Block's Goose CLI agent on your model server)", image=AGY_IMAGE, key=None, hosts=[],
                  api=False, endpoint=True),
    "qwen-code": dict(label="Qwen Code (Alibaba's Qwen Code CLI)", image=AGY_IMAGE, key="qwen.key",
                      hosts=["dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"], api=False),
    "anthropic-compat": dict(label="Anthropic Messages API on your model server", image=API_IMAGE,
                             key="compat.key", hosts=[], api=True, endpoint=True, optional_key=True, prices="PRICE_COMPAT"),
    # Mistral Vibe: Mistral AI's coding CLI agent (api.mistral.ai)
    "mistral-vibe": dict(label="Mistral Vibe (Mistral's Vibe CLI)", image=AGY_IMAGE, key="mistral.key",
                         hosts=["api.mistral.ai"], api=False),
    # GitHub Copilot: GitHub Copilot CLI (api.githubcopilot.com, api.github.com)
    "copilot": dict(label="GitHub Copilot (GitHub Copilot CLI)", image=AGY_IMAGE, key=None,
                    hosts=["api.githubcopilot.com", "api.github.com"], api=False),
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
# Goose: non-interactive execution via goose run --instructions - (empty: goose run is already headless; -q suppresses scan() tool calls)
GOOSE_QUIET = []
# Qwen Code: non-interactive stream-json, auto-approve tool execution
QWEN_QUIET = ["--output-format", "stream-json", "--yolo"]
# Mistral Vibe: non-interactive stream-json output for scan()
MISTRAL_VIBE_QUIET = ["--output-format", "stream-json"]
# GitHub Copilot: non-interactive stream-json output for scan()
COPILOT_QUIET = ["--output-format", "stream-json"]
GEMINI_TRAILER = "Do the task described above."
QWEN_TRAILER = "Do the task described above."
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


def cost_model(rt: str) -> dict:
    """Return the cost/quota model classification for a runtime.

    Returns a dict with exactly two keys:
    - 'kind': 'metered' (API with token cost), 'quota' (CLI with subscription quota), or 'unknown'
    - 'detail': short human-readable explanation
    """
    if rt not in RUNTIMES:
        raise ValueError(f"unknown runtime {rt!r}")
    unknown_details = {
        "opencode": "OpenCode's backend model and its price are set in the user's own OpenCode config, not in Agent-Forge",
        "qwen-code": "Qwen Code (qwen-code) talks to Alibaba DashScope whose model pricing is not tracked in Agent-Forge",
        "goose": "Goose's provider and model pricing are configured in the user's own Goose environment, not in Agent-Forge",
        "anthropic-compat": "Anthropic-compat points to an external or custom model server whose pricing is not set in Agent-Forge",
        "mistral-vibe": "Mistral Vibe (mistral-vibe) subscription or API pricing is managed in Mistral AI, not tracked in Agent-Forge",
        "copilot": "GitHub Copilot (copilot) is covered by a GitHub Copilot subscription; token pricing is not tracked in Agent-Forge",
    }
    if rt in unknown_details:
        return {"kind": "unknown", "detail": unknown_details[rt]}
    if is_api(rt):
        return {"kind": "metered", "detail": "billed per token, see Money page"}
    return {"kind": "quota", "detail": "tracked via agy /usage, not USD"}


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
    if rt == "goose":
        # Block's Goose CLI agent (https://github.com/block/goose).
        # `goose run --instructions -` reads instructions non-interactively from stdin per Goose's CLI.
        # Goose configures its model and provider via ~/.config/goose/config.yaml or environment
        # variables (GOOSE_PROVIDER, GOOSE_MODEL), so model is not passed on the command line.
        argv = ["goose", "run"] + GOOSE_QUIET + ["--instructions", "-"]
        return argv, prompt
    if rt == "qwen-code":
        argv = ["qwen"] + QWEN_QUIET
        argv += (["-m", model] if model else [])
        argv += (["--resume", conversation] if conversation else [])
        argv += ["-p", QWEN_TRAILER]
        return argv, prompt
    if rt == "anthropic-compat":
        argv = ["af-agent", "anthropic-compat", "exec"]
        system_text = ""
        messages = []
        conv_data = None
        if conversation:
            if isinstance(conversation, (list, dict)):
                conv_data = conversation
            elif isinstance(conversation, str):
                conv_str = conversation.strip()
                if conv_str.startswith("[") or conv_str.startswith("{"):
                    try:
                        conv_data = json.loads(conv_str)
                    except Exception:
                        conv_data = None
        if isinstance(conv_data, list):
            for item in conv_data:
                if isinstance(item, dict):
                    role = item.get("role")
                    content = item.get("content", "")
                    if role == "system":
                        system_text = content
                    elif role in ("user", "assistant"):
                        messages.append({"role": role, "content": content})
        elif isinstance(conv_data, dict):
            if "system" in conv_data:
                system_text = conv_data["system"]
            raw_msgs = conv_data.get("messages") or []
            if isinstance(raw_msgs, list):
                for item in raw_msgs:
                    if isinstance(item, dict):
                        role = item.get("role")
                        content = item.get("content", "")
                        if role == "system":
                            system_text = content
                        elif role in ("user", "assistant"):
                            messages.append({"role": role, "content": content})
        if prompt:
            if not messages or messages[-1].get("content") != prompt or messages[-1].get("role") != "user":
                messages.append({"role": "user", "content": prompt})
        elif not messages:
            messages.append({"role": "user", "content": ""})
        body = {
            "model": model or "claude-3-5-sonnet-20241022",
            "system": system_text,
            "messages": messages,
        }
        if conversation and isinstance(conversation, str) and not conv_data:
            body["conversation_id"] = conversation
            body["metadata"] = {"conversation_id": conversation}
        return argv, json.dumps(body)
    if rt == "mistral-vibe":
        argv = ["mistral-vibe"] + MISTRAL_VIBE_QUIET
        argv += (["--model", model] if model else [])
        argv += (["--resume", conversation] if conversation else [])
        return argv, prompt
    if rt == "copilot":
        argv = ["copilot"] + COPILOT_QUIET
        argv += (["--model", model] if model else [])
        argv += (["--resume", conversation] if conversation else [])
        return argv, prompt
    raise ValueError(f"unknown runtime {rt!r}")




def _prices(spec):
    """'in,cached,out' USD per million tokens -> tuple of floats (0 when unparsable)."""
    try:
        a = [float(x) for x in str(spec).split(",")]
        return (a + [0, 0, 0])[:3]
    except ValueError:
        return (0.0, 0.0, 0.0)


class ResultStr(str):
    """Result string representation for Goose CLI output. Supports both string operations
    and dict-like .get() / item access for supervisor inspection and test assertions."""

    def get(self, key, default=None):
        if self.startswith("{"):
            try:
                e = json.loads(self)
                if isinstance(e, dict):
                    if key in e:
                        return e[key]
                    if key == "status":
                        s = e.get("status") or e.get("state")
                        if s in ("success", "SUCCESS"):
                            return "SUCCESS"
                        if s in ("error", "ERROR", "failed", "FAILED") or e.get("error") or e.get("is_error"):
                            return "ERROR"
                        return "SUCCESS"
                    if key == "response":
                        return str(e.get("response") or e.get("text") or e.get("content") or self)
            except Exception:
                pass
        if key == "status":
            low = self.lower()
            if low.startswith("error") or " error:" in low or " failed:" in low or low.startswith("failed"):
                return "ERROR"
            return "SUCCESS"
        if key == "response":
            return str(self)
        if key == "error":
            low = self.lower()
            return str(self) if (low.startswith("error") or " error:" in low or " failed:" in low or low.startswith("failed")) else ""
        return default

    def __getitem__(self, item):
        if isinstance(item, str):
            val = self.get(item)
            if val is not None or item in ("usage", "cost_usd"):
                return val
            raise KeyError(item)
        return super().__getitem__(item)


def scan(rt, lines, prices=None):
    """Read a run's event stream. Returns dict(tokens, tools, conversation_id, result) where result is None (no final event
    yet / never) or dict(status SUCCESS|ERROR, response, error, usage, cost_usd). `tokens` counts what the model processed
    excluding cache reads (the same measure for every runtime; it drives the per-run token cap)."""
    rt = rt or "agy"
    if rt == "compat":
        rt = "codex"                           # the same CLI and event stream
    if rt == "goose":
        # Block's Goose CLI output and SSE event stream parser.
        # Goose runs against model endpoints and can stream SSE events (event: / data:)
        # as emitted by fake-goose-server.js, or standard plain-text / JSON-lines CLI output.
        # Tokens are accumulated if reported in usage/stats (else None for unknown).
        # Tools are counted from tool calls/invocations across all stream formats.
        # Result becomes a ResultStr only on a genuine terminal event (or last non-empty line for non-SSE).
        # Malformed/partial SSE frames are skipped safely without raising.
        if lines is None:
            lines = []
        st = dict(tokens=None, tools=0, conversation_id=None, result=None)
        seen_sse = False
        text = []
        current_event = None
        current_data = []
        terminal_reached = False
        terminal_error = None
        last_non_empty = None

        def dispatch_sse(ev, d_str):
            nonlocal terminal_reached, terminal_error
            if not d_str:
                if ev in ("stop", "message_stop", "done"):
                    terminal_reached = True
                elif ev in ("error", "turn.failed", "step_failed"):
                    terminal_reached = True
                    if not terminal_error:
                        terminal_error = "error"
                return
            if d_str.strip() == "[DONE]":
                terminal_reached = True
                return
            try:
                e = json.loads(d_str)
            except Exception:
                return
            if not isinstance(e, dict):
                return

            sid = (e.get("conversation_id") or e.get("session_id")
                   or e.get("sessionId") or e.get("thread_id"))
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)

            # Tool usage counting
            cb = e.get("content_block") or {}
            it = e.get("item") or {}
            t = e.get("type") or e.get("event") or ev
            part = e.get("part") if isinstance(e.get("part"), dict) else {}

            if isinstance(cb, dict) and cb.get("type") in ("tool_use", "tool_call"):
                st["tools"] += 1
            elif ((ev == "response.output_item.added" or t == "response.output_item.added")
                  and isinstance(it, dict) and it.get("type") in ("function_call", "tool_call", "tool_use", "custom_tool_call")):
                st["tools"] += 1
            else:
                counted_choices = False
                for choice in (e.get("choices") or []):
                    if isinstance(choice, dict):
                        delta = choice.get("delta") or choice.get("message") or {}
                        if isinstance(delta, dict):
                            tcs = delta.get("tool_calls") or []
                            if tcs:
                                st["tools"] += len(tcs)
                                counted_choices = True
                            elif delta.get("function_call"):
                                st["tools"] += 1
                                counted_choices = True
                if not counted_choices:
                    if (t in ("tool_use", "tool_call", "tool", "tool_invocation", "tool_request")
                            or part.get("type") in ("tool", "tool_use", "tool_call", "tool_invocation")
                            or e.get("tool")):
                        st["tools"] += 1

            # Token accumulation
            u = (e.get("usage") or e.get("stats") or e.get("tokens")
                 or (e.get("message") if isinstance(e.get("message"), dict) else {}).get("usage")
                 or (e.get("response") if isinstance(e.get("response"), dict) else {}).get("usage")
                 or (e.get("part") if isinstance(e.get("part"), dict) else {}).get("usage"))
            if isinstance(u, dict):
                inp = u.get("input_tokens") or u.get("prompt_tokens") or u.get("input") or 0
                out = u.get("output_tokens") or u.get("completion_tokens") or u.get("output") or 0
                tot = u.get("total_tokens") or u.get("total")
                if tot is not None:
                    st["tokens"] = (st["tokens"] or 0) + max(0, int(tot))
                elif inp or out:
                    st["tokens"] = (st["tokens"] or 0) + max(0, int(inp) + int(out))
            elif isinstance(u, (int, float)) and not isinstance(u, bool) and u > 0:
                st["tokens"] = (st["tokens"] or 0) + int(u)

            # Text accumulation
            delta = e.get("delta") if isinstance(e.get("delta"), dict) else {}
            txt = delta.get("text_delta") or delta.get("text") or ""
            if txt:
                text.append(str(txt))
            if isinstance(cb, dict) and cb.get("text"):
                text.append(str(cb["text"]))
            if isinstance(it, dict) and it.get("type") == "message" and (ev in ("response.output_item.added", "response.output_item.done") or t in ("response.output_item.added", "response.output_item.done")):
                for c in (it.get("content") or []):
                    if isinstance(c, dict) and c.get("text"):
                        text.append(str(c["text"]))
            for choice in (e.get("choices") or []):
                if isinstance(choice, dict):
                    cdelta = choice.get("delta") or choice.get("message") or {}
                    if isinstance(cdelta, dict) and cdelta.get("content"):
                        text.append(str(cdelta["content"]))
            ptxt = (part.get("text") if isinstance(part, dict) else "") or e.get("text") or ""
            if ptxt and t not in ("tool_use", "tool_call"):
                text.append(str(ptxt))

            # Terminal event checking
            err_raw = None
            if ev in ("error", "turn.failed", "step_failed", "failed"):
                err_raw = e.get("error") or e.get("message") or ev
            elif t in ("error", "turn.failed", "step_failed"):
                err_raw = e.get("error") or e.get("message") or t
            elif e.get("error") or e.get("is_error"):
                err_raw = e.get("error") or "error"
            elif isinstance(part, dict) and (part.get("error") or part.get("reason") in ("error", "failed", "failure")):
                err_raw = part.get("error") or part.get("reason")
            elif isinstance(e.get("response"), dict) and e["response"].get("status") in ("failed", "error", "cancelled"):
                err_raw = e["response"].get("error") or e["response"].get("status")
            elif e.get("status") in ("failed", "error", "FAILED", "ERROR"):
                err_raw = e.get("error") or e.get("message") or e.get("status")
            else:
                for choice in (e.get("choices") or []):
                    if isinstance(choice, dict) and choice.get("finish_reason") in ("error", "failed"):
                        err_raw = choice.get("error") or choice.get("finish_reason")

            if err_raw:
                terminal_reached = True
                err_msg = (err_raw.get("message") or err_raw.get("error") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                terminal_error = err_msg or "error"
            else:
                if ev in ("stop", "message_stop", "response.completed", "step_finish", "result", "done"):
                    terminal_reached = True
                elif t in ("stop", "message_stop", "response.completed", "step_finish", "result"):
                    terminal_reached = True
                elif isinstance(e.get("response"), dict) and e["response"].get("status") == "completed":
                    terminal_reached = True
                elif isinstance(delta, dict) and delta.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens", "stop"):
                    terminal_reached = True
                elif e.get("finish_reason") in ("stop", "end_turn", "completed", "length"):
                    terminal_reached = True
                elif e.get("stop_reason") in ("stop", "end_turn", "completed", "length"):
                    terminal_reached = True
                else:
                    for choice in (e.get("choices") or []):
                        if isinstance(choice, dict) and choice.get("finish_reason") in ("stop", "end_turn", "completed", "length"):
                            terminal_reached = True

        def flush_data_if_valid():
            nonlocal current_event, current_data
            if current_data:
                joined = "\n".join(current_data)
                try:
                    json.loads(joined)
                    dispatch_sse(current_event, joined)
                    current_event = None
                    current_data = []
                except Exception:
                    pass

        for item in lines:
            if isinstance(item, bytes):
                try:
                    item = item.decode("utf-8", errors="replace")
                except Exception:
                    continue
            elif not isinstance(item, str):
                continue
            sublines = item.splitlines() if ("\n" in item or "\r" in item) else [item]
            for line in sublines:
                line_str = line.strip()
                if not line_str:
                    if current_data or current_event:
                        dispatch_sse(current_event, "\n".join(current_data))
                        current_event = None
                        current_data = []
                    continue

                last_non_empty = line_str

                if line_str.startswith("event:"):
                    seen_sse = True
                    if current_data or current_event:
                        dispatch_sse(current_event, "\n".join(current_data))
                        current_data = []
                    current_event = line_str[6:].strip()
                    continue

                if line_str.startswith("data:"):
                    seen_sse = True
                    d_content = line_str[5:].strip()
                    flush_data_if_valid()
                    current_data.append(d_content)
                    continue

                if line_str.startswith(":"):
                    seen_sse = True
                    continue

                if current_data or current_event:
                    dispatch_sse(current_event, "\n".join(current_data))
                    current_event = None
                    current_data = []

                parsed = None
                if line_str.startswith("{"):
                    try:
                        parsed = json.loads(line_str)
                    except ValueError:
                        pass
                if isinstance(parsed, dict):
                    t = parsed.get("type") or parsed.get("event")
                    part = parsed.get("part") if isinstance(parsed.get("part"), dict) else {}
                    low_raw = line_str.lower()
                    if (t in ("tool_use", "tool_call", "tool", "tool_invocation", "tool_request")
                            or part.get("type") in ("tool", "tool_use", "tool_call", "tool_invocation")
                            or parsed.get("tool")
                            or "tool_call" in low_raw
                            or "tool call" in low_raw
                            or "tool_use" in low_raw
                            or "tool use" in low_raw):
                        st["tools"] += 1
                else:
                    low = line_str.lower()
                    if ("tool_call" in low or "tool call" in low
                            or "tool_use" in low or "tool use" in low
                            or "tool invocation" in low or "tool_invocation" in low
                            or "tool request" in low or "tool_request" in low
                            or low.startswith("tool:") or " tool:" in low
                            or "running tool" in low or "invoking tool" in low or "calling tool" in low
                            or "[tool]" in low):
                        st["tools"] += 1

        if current_data or current_event:
            dispatch_sse(current_event, "\n".join(current_data))
            current_data = []

        if seen_sse:
            if terminal_reached:
                if terminal_error:
                    err_prefix = terminal_error if terminal_error.lower().startswith("error") else f"Error: {terminal_error}"
                    resp = "".join(text).strip()
                    err_str = err_prefix if not resp else f"{err_prefix}\n{resp}"
                    st["result"] = ResultStr(err_str)
                else:
                    resp = "".join(text).strip() if text else "Task completed successfully."
                    st["result"] = ResultStr(resp)
            else:
                st["result"] = None
        else:
            st["result"] = ResultStr(last_non_empty) if last_non_empty is not None else None
        return st
    if lines is None:
        lines = []
    st = dict(tokens=None if rt in ("opencode", "mistral-vibe", "copilot") else 0, tools=0, conversation_id=None, result=None)
    text = []
    inp_tokens, out_tokens = 0, 0
    for line in lines:
        if isinstance(line, bytes):
            try:
                line = line.decode("utf-8", errors="replace")
            except Exception:
                continue
        elif not isinstance(line, str):
            continue
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except Exception:
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
            sid = e.get("sessionID") or e.get("session_id") or e.get("sessionId") or e.get("session") or e.get("conversation_id")
            if not sid and isinstance(e.get("part"), dict):
                sid = e["part"].get("sessionID") or e["part"].get("session_id") or e["part"].get("sessionId") or e["part"].get("session")
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)
            part = e.get("part") if isinstance(e.get("part"), dict) else {}
            if t in ("tool_use", "tool_call", "tool", "tool_invocation") or part.get("type") in ("tool", "tool_use", "tool_call", "tool_invocation") or part.get("tool"):
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
            u = part.get("tokens") or e.get("tokens") or part.get("usage") or e.get("usage")
            if isinstance(u, dict):
                if any(k in u for k in ("input", "output", "input_tokens", "output_tokens", "total_tokens", "total", "prompt_tokens", "completion_tokens")):
                    inp = u.get("input") or u.get("input_tokens") or u.get("prompt_tokens") or 0
                    out = u.get("output") or u.get("output_tokens") or u.get("completion_tokens") or 0
                    tot = u.get("total_tokens") or u.get("total") or (inp + out)
                    st["tokens"] = (st["tokens"] or 0) + max(0, tot)
            elif isinstance(u, (int, float)) and not isinstance(u, bool) and u > 0:
                st["tokens"] = (st["tokens"] or 0) + int(u)

            if t == "step_finish":
                finish_txt = part.get("text") or e.get("text") or ""
                if finish_txt:
                    text.append(str(finish_txt))
                reason = part.get("reason") or e.get("reason") or ""
                err_raw = part.get("error") or e.get("error") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                has_err = bool(err_raw or e.get("is_error") or reason in ("error", "failed", "failure"))
                if reason in ("error", "failed", "failure"):
                    st["result"] = dict(status="ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err_msg or reason,
                                        usage=u,
                                        cost_usd=None)
                elif reason in ("stop", "end", "completed", "complete", "success") or (not reason and not part.get("type") == "tool"):
                    ok = not has_err
                    err = "" if ok else (err_msg or "error")
                    st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err,
                                        usage=u,
                                        cost_usd=None)
            elif t == "result":
                ok = e.get("status") in ("success", "SUCCESS") or e.get("subtype") == "success" or not (e.get("error") or e.get("is_error"))
                err_raw = e.get("error") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                err = "" if ok else (err_msg or "error")
                res = str(e.get("response") or e.get("result") or "\n".join(text))[-4000:]
                st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                    response=res,
                                    error=err,
                                    usage=e.get("usage") or e.get("tokens") or u,
                                    cost_usd=None)
            elif t in ("error", "turn.failed", "step_failed"):
                err_raw = part.get("error") or e.get("error") or part.get("message") or e.get("message") or "error"
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                st["result"] = dict(status="ERROR", response="\n".join(text)[-4000:], error=err_msg, usage=None, cost_usd=None)
        elif rt == "qwen-code":
            t = e.get("type") or e.get("event")
            sid = e.get("session_id") or e.get("sessionId") or e.get("thread_id") or e.get("conversation_id")
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)
            if t in ("tool_use", "tool_call", "tool"):
                st["tools"] += 1
            elif t == "message" and (e.get("role") == "assistant" or not e.get("role")):
                text.append(str(e.get("content") or e.get("text") or ""))
            elif t == "text":
                text.append(str(e.get("text") or e.get("content") or ""))
            elif t == "error":
                err = (e.get("error") or {}).get("message", "") if isinstance(e.get("error"), dict) else str(e.get("message") or e.get("error") or e)[:500]
                st["last_error"] = err
                st["result"] = dict(status="ERROR", response="".join(text)[-4000:], error=err, usage=None, cost_usd=None)
            elif t == "result":
                s = e.get("stats") or e.get("usage") or {}
                tot = s.get("total_tokens") or ((s.get("input_tokens") or 0) + (s.get("output_tokens") or 0))
                cached = s.get("cached") or s.get("cached_tokens") or s.get("cached_input_tokens") or 0
                st["tokens"] = max(0, (tot or 0) - cached)
                ok = (e.get("status") in ("success", "SUCCESS") or not e.get("status")) and not (e.get("error") or e.get("is_error"))
                p = _prices(prices or "")
                cost = ((s.get("input_tokens") or 0) - cached) * p[0] + cached * p[1] + (s.get("output_tokens") or 0) * p[2]
                res = "".join(text)[-4000:] if text else str(e.get("response") or e.get("result") or "")[-4000:]
                st["result"] = dict(status="SUCCESS" if ok else "ERROR", response=res,
                                     error="" if ok else str(e.get("error") or st.get("last_error") or e.get("status") or "error"),
                                     usage=s if s else None, cost_usd=round(cost / 1e6, 6) if prices else None)
            elif t == "turn.failed":
                err = (e.get("error") or {}).get("message", "") if isinstance(e.get("error"), dict) else str(e.get("error"))
                st["result"] = dict(status="ERROR", response="".join(text)[-4000:], error=err, usage=None, cost_usd=None)
        elif rt == "anthropic-compat":
            t = e.get("type") or ""
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            cid = e.get("id") or msg.get("id") or e.get("conversation_id") or e.get("session_id") or e.get("sessionId")
            if not cid and isinstance(e.get("metadata"), dict):
                cid = e["metadata"].get("conversation_id") or e["metadata"].get("session_id")
            if cid and not st["conversation_id"]:
                st["conversation_id"] = str(cid)

            u = e.get("usage") if isinstance(e.get("usage"), dict) else (msg.get("usage") if isinstance(msg.get("usage"), dict) else {})
            if u:
                if "input_tokens" in u:
                    inp_tokens = int(u.get("input_tokens") or 0)
                if "output_tokens" in u:
                    out_tokens = int(u.get("output_tokens") or 0)
                tot = u.get("total_tokens")
                if tot is not None:
                    st["tokens"] = int(tot)
                else:
                    st["tokens"] = inp_tokens + out_tokens

            if t in ("tool_use", "tool_call"):
                st["tools"] += 1
            if t == "content_block_start":
                cb = e.get("content_block") if isinstance(e.get("content_block"), dict) else {}
                if cb.get("type") in ("tool_use", "tool_call"):
                    st["tools"] += 1
                elif cb.get("type") == "text" and cb.get("text"):
                    text.append(str(cb.get("text")))

            raw_content = e.get("content") or msg.get("content")
            if isinstance(raw_content, list):
                for block in raw_content:
                    if isinstance(block, dict):
                        if block.get("type") in ("tool_use", "tool_call"):
                            st["tools"] += 1
                        elif block.get("type") == "text" and block.get("text"):
                            text.append(str(block.get("text")))
            elif isinstance(raw_content, str):
                text.append(raw_content)

            if t == "content_block_delta":
                delta = e.get("delta") if isinstance(e.get("delta"), dict) else {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text.append(str(delta.get("text")))
                elif "text" in delta and delta.get("text"):
                    text.append(str(delta.get("text")))
            elif t == "text":
                txt = e.get("text") or (e.get("part") if isinstance(e.get("part"), dict) else {}).get("text")
                if txt:
                    text.append(str(txt))

            if t == "error" or e.get("error") or e.get("is_error"):
                err_obj = e.get("error")
                err_msg = err_obj.get("message") if isinstance(err_obj, dict) else str(err_obj or "error")
                st["result"] = dict(status="ERROR", response="".join(text)[-4000:], error=str(err_msg), usage=u if u else None, cost_usd=None)

            is_final = False
            if t == "message_delta":
                delta = e.get("delta") if isinstance(e.get("delta"), dict) else {}
                if delta.get("stop_reason") or "stop_reason" in e:
                    is_final = True
            elif t == "message_stop":
                is_final = True
            elif (t == "message" or e.get("role") == "assistant") and (e.get("stop_reason") is not None or "content" in e):
                is_final = True

            if is_final and not (st["result"] and st["result"]["status"] == "ERROR"):
                assistant_text = "".join(text)[-4000:]
                p = _prices(prices or "")
                cached = (u.get("cache_read_input_tokens") or u.get("cached_tokens") or u.get("cached_input_tokens") or 0) if isinstance(u, dict) else 0
                cost = max(0, inp_tokens - cached) * p[0] + cached * p[1] + out_tokens * p[2]
                prev = (st["result"] or {}).get("cost_usd") or 0
                st["result"] = dict(status="SUCCESS", response=assistant_text, error="",
                                    usage=dict(input_tokens=inp_tokens, output_tokens=out_tokens) if (inp_tokens or out_tokens) else (u if u else None),
                                    cost_usd=round(prev + cost / 1e6, 6) if prices else None)
        elif rt == "mistral-vibe":
            t = e.get("type") or e.get("event")
            sid = e.get("conversation_id") or e.get("session_id") or e.get("sessionId") or e.get("thread_id") or e.get("session")
            if not sid and isinstance(e.get("part"), dict):
                sid = e["part"].get("conversation_id") or e["part"].get("session_id") or e["part"].get("sessionId") or e["part"].get("session")
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)
            part = e.get("part") if isinstance(e.get("part"), dict) else {}
            if (t in ("tool_use", "tool_call", "tool", "tool_invocation", "tool_execution")
                    or part.get("type") in ("tool", "tool_use", "tool_call", "tool_invocation")
                    or part.get("tool") or e.get("tool")):
                st["tools"] += 1
            elif t in ("message", "text", "assistant_message", "assistant.message"):
                txt = part.get("text") or e.get("text") or e.get("content") or (e.get("message") or {}).get("content") or ""
                if isinstance(txt, list):
                    txt = "".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
                if txt:
                    text.append(str(txt))
            u = e.get("usage") or e.get("stats") or e.get("tokens") or part.get("tokens") or part.get("usage")
            inp, out, cached = 0, 0, 0
            if isinstance(u, dict):
                inp = u.get("input_tokens") or u.get("input") or u.get("prompt_tokens") or 0
                out = u.get("output_tokens") or u.get("output") or u.get("completion_tokens") or 0
                tot = u.get("total_tokens") or u.get("total")
                cached = u.get("cached_tokens") or u.get("cached") or u.get("cached_input_tokens") or 0
                if tot is not None:
                    st["tokens"] = max(0, int(tot) - int(cached))
                elif inp or out:
                    st["tokens"] = max(0, int(inp) + int(out) - int(cached))
            elif isinstance(u, (int, float)) and not isinstance(u, bool) and u > 0:
                st["tokens"] = int(u)

            if t in ("step_finish", "step_completed", "turn.completed"):
                finish_txt = part.get("text") or e.get("text") or ""
                if finish_txt:
                    text.append(str(finish_txt))
                reason = part.get("reason") or e.get("reason") or ""
                err_raw = part.get("error") or e.get("error") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                has_err = bool(err_raw or e.get("is_error") or reason in ("error", "failed", "failure"))
                if reason in ("error", "failed", "failure"):
                    st["result"] = dict(status="ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err_msg or reason,
                                        usage=u if u else None,
                                        cost_usd=None)
                elif reason in ("stop", "end", "completed", "complete", "success") or (not reason and not part.get("type") == "tool"):
                    ok = not has_err
                    err = "" if ok else (err_msg or "error")
                    st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err,
                                        usage=u if u else None,
                                        cost_usd=None)
            elif t == "result":
                ok = e.get("status") in ("success", "SUCCESS") or e.get("subtype") == "success" or not (e.get("error") or e.get("is_error"))
                err_raw = e.get("error") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                err = "" if ok else (err_msg or "error")
                res = str(e.get("response") or e.get("result") or "\n".join(text))[-4000:]
                p = _prices(prices or "") if prices else None
                cost = ((inp - cached) * p[0] + cached * p[1] + out * p[2]) if (p and any(p)) else None
                st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                    response=res,
                                    error=err,
                                    usage=u if u else None,
                                    cost_usd=round(cost / 1e6, 6) if cost is not None else None)
            elif t in ("error", "turn.failed", "step_failed", "failed"):
                err_raw = part.get("error") or e.get("error") or part.get("message") or e.get("message") or "error"
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                st["result"] = dict(status="ERROR", response="\n".join(text)[-4000:], error=err_msg, usage=None, cost_usd=None)
        elif rt == "copilot":
            t = e.get("type") or e.get("event")
            sid = (e.get("sessionID") or e.get("sessionId") or e.get("session_id")
                   or e.get("conversation_id") or e.get("session") or e.get("thread_id"))
            if not sid and isinstance(e.get("part"), dict):
                sid = (e["part"].get("sessionID") or e["part"].get("sessionId")
                       or e["part"].get("session_id") or e["part"].get("conversation_id")
                       or e["part"].get("session") or e["part"].get("thread_id"))
            if sid and not st["conversation_id"]:
                st["conversation_id"] = str(sid)
            part = e.get("part") if isinstance(e.get("part"), dict) else {}
            if (t in ("tool", "tool_use", "tool_call", "tool_invocation", "tool_execution")
                    or part.get("type") in ("tool", "tool_use", "tool_call", "tool_invocation")
                    or part.get("tool") or e.get("tool")):
                st["tools"] += 1
            elif t in ("message", "text", "assistant_message", "assistant.message", "message.part.delta", "message.part.updated"):
                txt = part.get("text") or e.get("text") or e.get("content") or (e.get("message") or {}).get("content") or ""
                if isinstance(txt, list):
                    txt = "".join(str(c.get("text", "")) for c in txt if isinstance(c, dict))
                if txt:
                    text.append(str(txt))
            u = e.get("usage") or e.get("stats") or e.get("tokens") or part.get("tokens") or part.get("usage")
            if isinstance(u, dict):
                inp = u.get("input_tokens") or u.get("input") or u.get("prompt_tokens") or 0
                out = u.get("output_tokens") or u.get("output") or u.get("completion_tokens") or 0
                tot = u.get("total_tokens") or u.get("total")
                cached = u.get("cached_tokens") or u.get("cached") or u.get("cached_input_tokens") or 0
                if tot is not None:
                    st["tokens"] = max(0, int(tot) - int(cached))
                elif inp or out:
                    st["tokens"] = max(0, int(inp) + int(out) - int(cached))
            elif isinstance(u, (int, float)) and not isinstance(u, bool) and u > 0:
                st["tokens"] = int(u)

            if t in ("step_finish", "step_completed", "turn.completed"):
                finish_txt = part.get("text") or e.get("text") or ""
                if finish_txt:
                    text.append(str(finish_txt))
                reason = part.get("reason") or e.get("reason") or ""
                err_raw = part.get("error") or e.get("error") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                has_err = bool(err_raw or e.get("is_error") or reason in ("error", "failed", "failure"))
                if reason in ("error", "failed", "failure"):
                    st["result"] = dict(status="ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err_msg or reason,
                                        usage=u if u else None,
                                        cost_usd=None)
                elif reason in ("stop", "end", "completed", "complete", "success") or (not reason and not part.get("type") == "tool"):
                    ok = not has_err
                    err = "" if ok else (err_msg or "error")
                    st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                        response="\n".join(text)[-4000:],
                                        error=err,
                                        usage=u if u else None,
                                        cost_usd=None)
            elif t == "result":
                status_raw = str(e.get("status") or (part.get("status") if isinstance(part, dict) else "")).lower()
                reason = str(part.get("reason") or e.get("reason") or "").lower() if isinstance(part, dict) else str(e.get("reason") or "").lower()
                err_raw = e.get("error") or (part.get("error") if isinstance(part, dict) else "") or ""
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                has_err = bool(err_raw or e.get("is_error") or status_raw in ("error", "failed", "failure") or reason in ("error", "failed", "failure"))
                ok = not has_err and (status_raw in ("success", "ok") or reason in ("stop", "end", "completed", "complete", "success") or not status_raw)
                err = "" if ok else (err_msg or reason or status_raw or "error")
                res = str(e.get("response") or e.get("result") or (part.get("text") if isinstance(part, dict) else "") or "\n".join(text))[-4000:]
                st["result"] = dict(status="SUCCESS" if ok else "ERROR",
                                    response=res,
                                    error=err,
                                    usage=u if u else (e.get("usage") or e.get("tokens")),
                                    cost_usd=None)
            elif t in ("error", "turn.failed", "step_failed", "failed"):
                err_raw = part.get("error") or e.get("error") or part.get("message") or e.get("message") or "error"
                err_msg = (err_raw.get("message") or str(err_raw)) if isinstance(err_raw, dict) else str(err_raw)
                st["result"] = dict(status="ERROR", response="\n".join(text)[-4000:], error=err_msg, usage=None, cost_usd=None)
    for k in ("aider_sent", "aider_recv", "aider_cost", "last_error"):
        st.pop(k, None)
    return st


def scan_file(rt, path, prices=None):
    try:
        with open(path, errors="replace") as f:
            return scan(rt, f, prices)
    except FileNotFoundError:
        return scan(rt, [], prices)
