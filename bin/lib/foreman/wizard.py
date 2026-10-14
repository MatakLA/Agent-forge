"""Offline recommendation wizard module for runtime and provider selection.

Public interface:
- NEXT_STEPS: dict[str, str] mapping runtime/provider keys to setup instructions
- next_steps(name: str) -> str: return setup instruction with generic fallback
- generate_report(status: dict | None = None) -> str: ranked text recommendation report
- main(argv=None) -> int: CLI entrypoint printing the report

Important invariants:
- Pure offline module with zero network calls, zero docker calls, and no secret leaks.
- Never raises KeyError or unhandled exceptions for unknown keys or status shapes.
- Reuses readiness.recommend() ranking logic without re-implementing ranking rules.
- When nothing is ready, explicitly reports not-ready status and recommends easiest next step.
"""
import os
import sys

try:
    from . import readiness
except ImportError:
    try:
        import readiness
    except ImportError:
        _foreman_dir = os.path.dirname(os.path.abspath(__file__))
        _lib_dir = os.path.dirname(_foreman_dir)
        for _p in (_lib_dir, _foreman_dir):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from foreman import readiness


class _StepsDict(dict):
    """Dictionary mapping keys to next steps instructions with generic fallback."""

    def __missing__(self, key: str) -> str:
        return f"see docs/RUNTIMES.md and docs/PROVIDERS.md for {key}"

    def get(self, key: str, default=None):
        if key in self:
            return super().get(key, default)
        if default is not None:
            return default
        return self[key]


NEXT_STEPS: dict[str, str] = _StepsDict({
    # Worker runtimes (readiness.runtime_items())
    "agy": "Log in to your Google Antigravity account and add worker with: bin/worker-add 1",
    "claude": "Set Anthropic API key with bin/secret-set anthropic.key and add worker: bin/worker-add 1 --runtime claude",
    "codex": "Set OpenAI API key with bin/secret-set openai.key and add worker: bin/worker-add 1 --runtime codex",
    "gemini": "Set Gemini API key with bin/secret-set gemini.key and add worker: bin/worker-add 1 --runtime gemini",
    "compat": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime compat",
    "aider": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime aider",
    "opencode": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime opencode",
    "goose": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime goose",
    "qwen-code": "Set Alibaba DashScope key with bin/secret-set qwen.key and add worker: bin/worker-add 1 --runtime qwen-code",
    "anthropic-compat": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime anthropic-compat",
    "mistral-vibe": "Set Mistral API key with bin/secret-set mistral.key and add worker: bin/worker-add 1 --runtime mistral-vibe",
    "copilot": "Log in via GitHub Copilot CLI and add worker: bin/worker-add 1 --runtime copilot",
    "ollama": "Start Ollama locally, set COMPAT_BASE_URL=http://127.0.0.1:11434/v1, and add worker: bin/worker-add 1 --runtime ollama",
    "vllm": "Start vLLM locally, set COMPAT_BASE_URL=http://127.0.0.1:8000/v1, and add worker: bin/worker-add 1 --runtime vllm",
    "lmstudio": "Start LM Studio locally, set COMPAT_BASE_URL=http://127.0.0.1:1234/v1, and add worker: bin/worker-add 1 --runtime lmstudio",
    "llamacpp": "Start llama-server locally, set COMPAT_BASE_URL=http://127.0.0.1:8080/v1, and add worker: bin/worker-add 1 --runtime llamacpp",

    # Orchestrator providers (readiness.provider_items())
    "claude-cli": "Install Claude Code CLI on host (claude /login) and set PLAN_PROVIDERS=claude-cli in etc/foreman.conf",
    "agy-claude": "Log in to Antigravity worker container and set REVIEW_PROVIDERS=agy-claude in etc/foreman.conf",
    "agy-gemini": "Log in to Antigravity worker container and set PLAN_PROVIDERS=agy-gemini in etc/foreman.conf",
    "anthropic-api": "Set ANTHROPIC_API_KEY in ~/.config/sandbox/secrets/ANTHROPIC_API_KEY and configure ANTHROPIC_MODEL in etc/foreman.conf",
    "openai-api": "Set OPENAI_API_KEY in ~/.config/sandbox/secrets/OPENAI_API_KEY and configure OPENAI_MODEL in etc/foreman.conf",
    "gemini-api": "Set GEMINI_API_KEY in ~/.config/sandbox/secrets/GEMINI_API_KEY and configure GEMINI_MODEL in etc/foreman.conf",

    # Common aliases
    "qwen": "Set Alibaba DashScope key with bin/secret-set qwen.key and add worker: bin/worker-add 1 --runtime qwen-code",
    "openai-compat": "Configure COMPAT_BASE_URL in etc/limits.conf and add worker: bin/worker-add 1 --runtime compat",
})


def next_steps(name: str) -> str:
    """Return next steps instruction for a runtime or provider name with generic fallback."""
    if not isinstance(name, str) or not name:
        return "see docs/RUNTIMES.md and docs/PROVIDERS.md"
    return NEXT_STEPS[name]


def _cost_tier(name: str) -> str:
    """Safely obtain cost tier for any runtime or provider name without raising."""
    try:
        cm = readiness.cost_model(name)
        if isinstance(cm, dict) and "kind" in cm:
            return str(cm["kind"])
    except Exception:
        pass
    if name in ("claude-cli", "agy-claude", "agy-gemini"):
        return "quota"
    elif name in ("anthropic-api", "openai-api", "gemini-api"):
        return "metered"
    return "unknown"


def generate_report(status: dict | None = None) -> str:
    """Produce a deterministic multi-line text report ranking runtimes and providers.

    If status is None, calls readiness.all_status(). Reuses readiness.recommend()
    for ranking order. Each line shows readiness state and cost tier.
    Highlights a 'Recommended' block with next_steps() for the top candidate.
    When nothing is ready, explicitly states so and suggests the cheapest/easiest candidate.
    """
    is_live = (status is None)
    if is_live:
        raw_status = readiness.all_status()
    else:
        raw_status = status

    runtimes_st: dict[str, dict] = {}
    providers_st: dict[str, dict] = {}

    if isinstance(raw_status, dict):
        if "runtimes" in raw_status and isinstance(raw_status["runtimes"], dict):
            runtimes_st.update(raw_status["runtimes"])
            # If claude-cli was passed under runtimes, track it for providers
            if "claude-cli" in raw_status["runtimes"] and "claude-cli" not in providers_st:
                providers_st["claude-cli"] = raw_status["runtimes"]["claude-cli"]
        if "providers" in raw_status and isinstance(raw_status["providers"], dict):
            providers_st.update(raw_status["providers"])

        # Also support flat dictionaries
        for k, v in raw_status.items():
            if k in ("runtimes", "providers"):
                continue
            if isinstance(v, dict):
                if k in readiness.runtime_items() and k not in runtimes_st:
                    runtimes_st[k] = v
                elif k in readiness.provider_items() and k not in providers_st:
                    providers_st[k] = v
                elif k == "claude-cli" and k not in providers_st:
                    providers_st[k] = v

    # Fallback defaults for missing items
    for rt in readiness.runtime_items():
        if rt not in runtimes_st:
            if is_live:
                try:
                    runtimes_st[rt] = readiness.status_of("runtime", rt)
                except Exception:
                    runtimes_st[rt] = {"ready": False, "state": "missing_setting", "detail": f"Runtime '{rt}' is not configured."}
            else:
                runtimes_st[rt] = {"ready": False, "state": "missing_setting", "detail": f"Runtime '{rt}' is not configured."}

    for pr in readiness.provider_items():
        if pr not in providers_st:
            if is_live:
                try:
                    providers_st[pr] = readiness.status_of("provider", pr)
                except Exception:
                    providers_st[pr] = {"ready": False, "state": "missing_setting", "detail": f"Provider '{pr}' is not configured."}
            else:
                providers_st[pr] = {"ready": False, "state": "missing_setting", "detail": f"Provider '{pr}' is not configured."}

    # Re-use readiness.recommend() ranking
    if is_live:
        rec = readiness.recommend()
    else:
        orig_status_of = readiness.status_of
        try:
            def _fake_status_of(kind, name):
                if kind == "runtime":
                    return runtimes_st.get(name, {"ready": False, "state": "missing_setting", "detail": ""})
                return providers_st.get(name, {"ready": False, "state": "missing_setting", "detail": ""})
            readiness.status_of = _fake_status_of
            rec = readiness.recommend()
        finally:
            readiness.status_of = orig_status_of

    ranked_runtimes = rec.get("ranked", [])
    top_runtime = rec.get("top")

    # Evaluate providers ranking matching readiness tier priority
    _tier_prio = {"quota": 0, "metered": 1, "unknown": 2}
    ready_providers = []
    not_ready_providers = []
    for pr in readiness.provider_items():
        st = providers_st.get(pr, {})
        is_ready = bool(isinstance(st, dict) and (st.get("state") == "ok" or st.get("ready") is True))
        tier = _cost_tier(pr)
        note = str(st.get("detail", "")) if isinstance(st, dict) else ""
        item = {
            "name": pr,
            "ready": is_ready,
            "cost_tier": tier,
            "note": note,
            "state": st.get("state", "ok" if is_ready else "missing_setting") if isinstance(st, dict) else "missing_setting",
        }
        if is_ready:
            ready_providers.append(item)
        else:
            not_ready_providers.append(item)

    ready_providers.sort(key=lambda x: _tier_prio.get(x["cost_tier"], 99))
    ranked_providers = ready_providers + not_ready_providers

    # Select top-ranked ready-or-best candidate:
    # 1. If any runtime is ready, top_runtime from readiness.recommend() is top pick.
    # 2. If no runtime is ready, check if any provider is ready (e.g. claude-cli).
    # 3. When nothing is ready, say so explicitly and recommend cheapest/easiest candidate.
    nothing_ready = False
    if top_runtime is not None:
        top_name = top_runtime["name"]
        top_tier = top_runtime.get("cost_tier", _cost_tier(top_name))
        top_reason = top_runtime.get("reason", "")
    elif ready_providers:
        top_name = ready_providers[0]["name"]
        top_tier = ready_providers[0]["cost_tier"]
        top_reason = ready_providers[0]["note"]
    else:
        nothing_ready = True
        top_name = ranked_runtimes[0]["name"] if ranked_runtimes else "agy"
        top_tier = _cost_tier(top_name)
        top_reason = f"No runtime or provider is currently ready. Recommended initial setup: {top_name}."

    # Build multi-line report
    lines = [
        "=== AI Recommendation Report ===",
        "",
        "Ranked Runtimes:",
    ]
    for idx, r in enumerate(ranked_runtimes, 1):
        st_val = runtimes_st.get(r["name"], {})
        state = st_val.get("state", "ok" if r.get("ready") else "missing_setting") if isinstance(st_val, dict) else "missing_setting"
        tier = r.get("cost_tier", _cost_tier(r["name"]))
        note = r.get("note", "")
        lines.append(f"  {idx}. {r['name']}: state={state}, cost_tier={tier} - {note}")

    lines.append("")
    lines.append("Ranked Providers:")
    for idx, p in enumerate(ranked_providers, 1):
        lines.append(f"  {idx}. {p['name']}: state={p['state']}, cost_tier={p['cost_tier']} - {p['note']}")

    lines.append("")
    lines.append("=" * 50)
    if nothing_ready:
        lines.append("Notice: No runtime or provider is currently ready.")
    lines.append(f"Recommended: {top_name}")
    lines.append(f"Cost Tier: {top_tier}")
    if top_reason:
        lines.append(f"Reason: {top_reason}")
    lines.append("Next steps:")
    lines.append(f"  {next_steps(top_name)}")
    lines.append("=" * 50)

    return "\n".join(lines)


def main(argv=None) -> int:
    """Print the recommendation report to stdout."""
    print(generate_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
