"""
Multi-provider LLM adapters for Orchestrator and Workers:
Supports Claude (Anthropic), ChatGPT (OpenAI), and Gemini (Google).
Pure standard-library Python (no external pip dependencies required).
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 1200


def get_secret(name, default=""):
    """Read secret from environment or ~/.config/sandbox/secrets/<name>."""
    val = os.environ.get(name, "")
    if val:
        return val.strip()
    sec_path = os.path.expanduser(f"~/.config/sandbox/secrets/{name}")
    if os.path.isfile(sec_path):
        try:
            return open(sec_path).read().strip()
        except Exception:
            pass
    return default


# -----------------------------------------------------------------------------
# Anthropic / Claude Provider
# -----------------------------------------------------------------------------
def call_anthropic_api(prompt, model="claude-3-7-sonnet-20250219", api_key=None, api_base="https://api.anthropic.com"):
    """Call Anthropic Messages API."""
    key = api_key or get_secret("ANTHROPIC_API_KEY") or get_secret("anthropic.token")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    
    url = f"{api_base.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 8192,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {})
            return {
                "text": text,
                "provider": "anthropic-api",
                "model": model,
                "duration_s": time.time() - t0,
                "usage": usage,
                "is_error": False
            }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return {
            "text": "",
            "provider": "anthropic-api",
            "model": model,
            "duration_s": time.time() - t0,
            "is_error": True,
            "error": f"HTTP {e.code}: {err_body}"
        }


def call_claude_cli(prompt, model="sonnet", cwd=None):
    """Call local headless Claude Code CLI."""
    args = [
        "claude", "-p",
        "--model", model,
        "--setting-sources", "",
        "--no-session-persistence",
        "--output-format", "json",
        "--permission-mode", "dontAsk",
        "--tools", ""
    ]
    t0 = time.time()
    p = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT, cwd=cwd)
    res = {}
    try:
        res = json.loads(p.stdout)
    except Exception:
        pass
    text = res.get("result") or ""
    return {
        "text": text,
        "raw_stdout": p.stdout,
        "raw_stderr": p.stderr,
        "exit_code": p.returncode,
        "provider": "claude-cli",
        "model": model,
        "duration_s": time.time() - t0,
        "usage": res.get("usage"),
        "cost_usd": res.get("total_cost_usd"),
        "is_error": p.returncode != 0 or bool(res.get("is_error"))
    }


# -----------------------------------------------------------------------------
# OpenAI / ChatGPT Provider
# -----------------------------------------------------------------------------
def call_openai_api(prompt, model="gpt-4o", api_key=None, api_base="https://api.openai.com/v1"):
    """Call OpenAI Chat Completions API (compatible with OpenAI, vLLM, OpenRouter, etc.)."""
    key = api_key or get_secret("OPENAI_API_KEY") or get_secret("openai.token")
    if not key:
        raise ValueError("OPENAI_API_KEY not configured")
    
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            text = (choice.get("message") or {}).get("content", "")
            usage = data.get("usage", {})
            return {
                "text": text,
                "provider": "openai-api",
                "model": model,
                "duration_s": time.time() - t0,
                "usage": usage,
                "is_error": False
            }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return {
            "text": "",
            "provider": "openai-api",
            "model": model,
            "duration_s": time.time() - t0,
            "is_error": True,
            "error": f"HTTP {e.code}: {err_body}"
        }


# -----------------------------------------------------------------------------
# Google Gemini Provider
# -----------------------------------------------------------------------------
def call_gemini_api(prompt, model="gemini-2.5-pro", api_key=None):
    """Call Google Generative Language API directly."""
    key = api_key or get_secret("GEMINI_API_KEY") or get_secret("gemini.token")
    if not key:
        raise ValueError("GEMINI_API_KEY not configured")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    headers = {
        "Content-Type": "application/json",
    }
    payload = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192
        }
    }
    
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cands = data.get("candidates") or []
            text = ""
            if cands:
                parts = (cands[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts)
            usage = data.get("usageMetadata", {})
            return {
                "text": text,
                "provider": "gemini-api",
                "model": model,
                "duration_s": time.time() - t0,
                "usage": usage,
                "is_error": False
            }
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        return {
            "text": "",
            "provider": "gemini-api",
            "model": model,
            "duration_s": time.time() - t0,
            "is_error": True,
            "error": f"HTTP {e.code}: {err_body}"
        }


def call_agy_container(worker, prompt, model="gemini-3.1-pro-high", timeout_min=15):
    """Invoke Antigravity CLI headlessly inside an isolated worker container."""
    cmd = [
        "docker", "exec", worker,
        "agy-run", "-p", prompt,
        "--model", model,
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--print-timeout", f"{timeout_min}m"
    ]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_min * 60 + 60)
    res = {}
    for line in p.stdout.splitlines():
        if line.startswith("{"):
            try:
                e = json.loads(line)
                if e.get("event") == "result":
                    res = e.get("result") or {}
            except Exception:
                pass
    text = res.get("response") or ""
    return {
        "text": text,
        "raw_stdout": p.stdout,
        "raw_stderr": p.stderr,
        "exit_code": p.returncode,
        "provider": f"agy:{model}@{worker}",
        "model": model,
        "duration_s": time.time() - t0,
        "usage": res.get("usage"),
        "status": res.get("status"),
        "is_error": p.returncode != 0 or res.get("status") != "SUCCESS" or not text.strip()
    }
