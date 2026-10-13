"""Optional PAID per-token API providers for the orchestrator (planning / review).

Agent-Forge's default orchestrators cost nothing beyond subscriptions you already pay for: Claude Code (`claude -p`) and
Antigravity (`agy`) inside a worker container. These adapters are only used when you list them in PLAN_PROVIDERS /
REVIEW_PROVIDERS in etc/foreman.conf AND configure a key - see docs/PROVIDERS.md. They bill per token.

Standard library only. Keys come from the environment or ~/.config/sandbox/secrets/<NAME> (mode 0600) and are sent in
request headers only (never in a URL, so they cannot end up in proxy or access logs).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_lib_dir = os.path.dirname(os.path.abspath(__file__))
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)
if "egress" in sys.modules and not hasattr(sys.modules["egress"], "allowlist"):
    del sys.modules["egress"]
import egress

DEFAULT_TIMEOUT = 600

PROVIDER_HOSTS = {
    "anthropic-api": "api.anthropic.com",
    "openai-api": "api.openai.com",
    "gemini-api": "generativelanguage.googleapis.com",
}


def missing_hosts(provider, api_base=''):
    """The egress host `provider` (one of bin/lib/foreman/settings.py PROVIDERS_ALL) needs that
    is not currently allowed, as a list with 0 or 1 entries. `api_base`, when given, overrides the
    default host (e.g. an owner-configured OPENAI_API_BASE) - parsed with urllib.parse.urlparse,
    using .hostname (scheme/path ignored). claude-cli, agy-claude and agy-gemini are routed
    through agy, need no pinned egress host, and always return []."""
    if provider not in PROVIDER_HOSTS:
        return []
    if api_base:
        parsed = urllib.parse.urlparse(api_base)
        host = parsed.hostname or (urllib.parse.urlparse(f"//{api_base}").hostname if "://" not in api_base else None)
    else:
        host = PROVIDER_HOSTS[provider]
    if not host:
        return []
    covered = set()
    for fn in (egress.allowlist, egress.local_allowlist):
        for entry in fn() or ():
            line = str(entry).split("#", 1)[0].strip().lower()
            if line:
                covered.add(line)
    if host.lower() in covered:
        return []
    return [host]


def get_secret(name, default=""):
    val = os.environ.get(name, "").strip()
    if val:
        return val
    p = os.path.expanduser(f"~/.config/sandbox/secrets/{name}")
    try:
        return open(p).read().strip()
    except OSError:
        return default


def _post(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _result(provider, model, t0, text="", usage=None, error=None):
    return dict(text=text, provider=provider, model=model, duration_s=time.time() - t0, usage=usage or {},
                is_error=error is not None, error=error)


def call_anthropic_api(prompt, model, system="", api_base="https://api.anthropic.com"):
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    t0 = time.time()
    payload = dict(model=model, max_tokens=8192, messages=[dict(role="user", content=prompt)])
    if system:
        payload["system"] = system
    try:
        d = _post(f"{api_base.rstrip('/')}/v1/messages",
                  {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, payload)
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        return _result("anthropic-api", model, t0, text, d.get("usage"))
    except urllib.error.HTTPError as e:
        return _result("anthropic-api", model, t0, error=f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
    except OSError as e:
        return _result("anthropic-api", model, t0, error=str(e))


def call_openai_api(prompt, model, system="", api_base="https://api.openai.com/v1"):
    """OpenAI-compatible Chat Completions (OpenAI, OpenRouter, a local vLLM/Ollama endpoint, ...)."""
    key = get_secret("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not configured")
    t0 = time.time()
    msgs = ([dict(role="system", content=system)] if system else []) + [dict(role="user", content=prompt)]
    try:
        d = _post(f"{api_base.rstrip('/')}/chat/completions",
                  {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, dict(model=model, messages=msgs, temperature=0.2))
        text = ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return _result("openai-api", model, t0, text, d.get("usage"))
    except urllib.error.HTTPError as e:
        return _result("openai-api", model, t0, error=f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
    except OSError as e:
        return _result("openai-api", model, t0, error=str(e))


def call_gemini_api(prompt, model, system=""):
    key = get_secret("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not configured")
    t0 = time.time()
    payload = dict(contents=[dict(parts=[dict(text=prompt)])], generationConfig=dict(temperature=0.2, maxOutputTokens=8192))
    if system:
        payload["systemInstruction"] = dict(parts=[dict(text=system)])
    try:
        d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                  {"x-goog-api-key": key, "Content-Type": "application/json"}, payload)
        parts = (((d.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        return _result("gemini-api", model, t0, "".join(p.get("text", "") for p in parts), d.get("usageMetadata"))
    except urllib.error.HTTPError as e:
        return _result("gemini-api", model, t0, error=f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
    except OSError as e:
        return _result("gemini-api", model, t0, error=str(e))
