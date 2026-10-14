"""Offline recommendation wizard module for runtime and provider selection.

Public interface:
- NEXT_STEPS: dict[str, str] mapping runtime/provider keys to setup instructions
- next_steps(name: str) -> str: return setup instruction with generic fallback
- generate_report(status: dict | None = None) -> str: ranked text recommendation report
- main(argv=None) -> int: CLI entrypoint printing the report

Important invariants:
- Pure offline module with zero network calls, zero docker calls, and no secret leaks.
- Plain functions and stdlib only, no classes, matching readiness.py style.
- Never raises KeyError or unhandled exceptions for unknown keys or status shapes.
- Reuses readiness.recommend() ranking logic directly without private attribute access.
- Directly consumes actual status schema keys (state, detail) without speculative fallbacks.
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


NEXT_STEPS: dict[str, str] = {
    # 16 worker runtimes from readiness.runtime_items()
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

    # 6 orchestrator providers from readiness.provider_items()
    "claude-cli": "Install Claude Code CLI on host (claude /login) and set PLAN_PROVIDERS=claude-cli in etc/foreman.conf",
    "agy-claude": "Log in to Antigravity worker container and set REVIEW_PROVIDERS=agy-claude in etc/foreman.conf",
    "agy-gemini": "Log in to Antigravity worker container and set PLAN_PROVIDERS=agy-gemini in etc/foreman.conf",
    "anthropic-api": "Set ANTHROPIC_API_KEY in ~/.config/sandbox/secrets/ANTHROPIC_API_KEY and configure ANTHROPIC_MODEL in etc/foreman.conf",
    "openai-api": "Set OPENAI_API_KEY in ~/.config/sandbox/secrets/OPENAI_API_KEY and configure OPENAI_MODEL in etc/foreman.conf",
    "gemini-api": "Set GEMINI_API_KEY in ~/.config/sandbox/secrets/GEMINI_API_KEY and configure GEMINI_MODEL in etc/foreman.conf",
}


def next_steps(name: str) -> str:
    """Return next steps instruction for a runtime or provider key with generic fallback."""
    if name in NEXT_STEPS:
        return NEXT_STEPS[name]
    return f"see docs/RUNTIMES.md and docs/PROVIDERS.md for {name}"


def _cost_tier(name: str) -> str:
    """Safely obtain cost tier for any runtime or provider name without raising."""
    try:
        cm = readiness.cost_model(name)
        if isinstance(cm, dict) and "kind" in cm:
            return str(cm["kind"])
    except (ValueError, KeyError, TypeError):
        pass
    if name in ("claude-cli", "agy-claude", "agy-gemini"):
        return "quota"
    elif name in ("anthropic-api", "openai-api", "gemini-api"):
        return "metered"
    return "unknown"


def generate_report(status: dict | None = None) -> str:
    """Produce a deterministic multi-line text report ranking runtimes and providers.

    If status is None, calls readiness.all_status(). Reuses readiness.recommend()'s
    ranking order directly. Each line shows readiness state and cost tier.
    Highlights a 'Recommended' block with next_steps() for the top candidate.
    When nothing is ready, explicitly states so and suggests the cheapest/easiest candidate.
    """
    if status is None:
        status = readiness.all_status()

    # Reuse readiness.recommend()'s ranked runtimes and cost tier metadata directly
    rec = readiness.recommend()

    runtimes_st = status.get("runtimes", {}) if isinstance(status, dict) else {}
    providers_st = status.get("providers", {}) if isinstance(status, dict) else {}

    # Project status onto the ranked runtimes from readiness.recommend()
    ready_entries = []
    not_ready_entries = []
    for r in rec.get("ranked", []):
        name = r["name"]
        st = runtimes_st.get(name, {})
        is_ready = bool(isinstance(st, dict) and st.get("state") == "ok")
        note = str(st.get("detail", r.get("note", ""))) if isinstance(st, dict) else r.get("note", "")
        entry = {
            "name": name,
            "ready": is_ready,
            "cost_tier": r["cost_tier"],
            "note": note,
        }
        if is_ready:
            ready_entries.append(entry)
        else:
            not_ready_entries.append(entry)

    ranked_runtimes = ready_entries + not_ready_entries
    top_runtime = ready_entries[0] if ready_entries else None

    # Determine top candidate:
    # 1. Top ready runtime conforming to readiness.recommend() ranking.
    # 2. If no runtime is ready, check ready providers in provider_items order.
    # 3. If nothing is ready, explicitly report not ready and suggest initial candidate.
    nothing_ready = False
    if top_runtime is not None:
        top_name = top_runtime["name"]
        top_tier = top_runtime["cost_tier"]
        top_reason = top_runtime["note"]
    else:
        ready_providers = [
            pr for pr in readiness.provider_items()
            if isinstance(providers_st.get(pr), dict) and providers_st[pr].get("state") == "ok"
        ]
        if ready_providers:
            top_name = ready_providers[0]
            top_tier = _cost_tier(top_name)
            top_reason = str(providers_st[top_name].get("detail", f"Provider '{top_name}' is ready."))
        else:
            nothing_ready = True
            top_name = ranked_runtimes[0]["name"] if ranked_runtimes else "agy"
            top_tier = _cost_tier(top_name)
            top_reason = f"No runtime or provider is currently ready. Recommended initial setup: {top_name}."

    lines = [
        "=== AI Recommendation Report ===",
        "",
        "Ranked Runtimes:",
    ]
    for idx, r in enumerate(ranked_runtimes, 1):
        st_val = runtimes_st.get(r["name"], {}) if isinstance(runtimes_st, dict) else {}
        state = str(st_val.get("state", "missing_setting")) if isinstance(st_val, dict) else "missing_setting"
        tier = r["cost_tier"]
        note = r["note"]
        lines.append(f"  {idx}. {r['name']}: state={state}, cost_tier={tier} - {note}")

    lines.append("")
    lines.append("Providers:")
    for pr in readiness.provider_items():
        st_p = providers_st.get(pr, {}) if isinstance(providers_st, dict) else {}
        state = str(st_p.get("state", "missing_setting")) if isinstance(st_p, dict) else "missing_setting"
        tier = _cost_tier(pr)
        note = str(st_p.get("detail", "")) if isinstance(st_p, dict) else ""
        lines.append(f"  - {pr}: state={state}, cost_tier={tier} - {note}")

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
