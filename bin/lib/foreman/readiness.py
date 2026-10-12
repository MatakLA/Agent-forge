"""Core runtime and provider readiness check module.

Public interface:
- runtime_items() -> list[str]: every key of bin/lib/runtimes.py RUNTIMES, in order
- provider_items() -> list[str]: every entry of bin/lib/foreman/settings.py PROVIDERS_ALL, in order
- status_of(kind: str, name: str) -> dict: readiness status of a single runtime or provider
- all_status() -> dict: status of all runtimes and providers

Invariants:
- Pure read-only checks without network calls or secrets exposure.
- State priority when both missing settings and missing egress apply: missing_setting wins.
- Absence of files, credentials, or settings is treated as 'missing' without raising exceptions.
"""
import os

import egress
import providers
import runtimes
import sbx

try:
    from . import ctx, settings
except ImportError:
    import ctx, settings


def runtime_items() -> list[str]:
    """Every key of bin/lib/runtimes.py RUNTIMES, in that dict's order."""
    return list(runtimes.RUNTIMES.keys())


def provider_items() -> list[str]:
    """Every entry of bin/lib/foreman/settings.py PROVIDERS_ALL, in order."""
    return list(settings.PROVIDERS_ALL)


def _has_secret(name: str) -> bool:
    """Check whether a secret is present without ever returning or logging its value."""
    if not name:
        return False
    # 1. Check via providers.get_secret (checks os.environ and ~/.config/sandbox/secrets)
    try:
        if bool(providers.get_secret(name)):
            return True
    except Exception:
        pass
    # 2. Check via sbx.SECRETS directory if configured
    try:
        s_dir = getattr(sbx, "SECRETS", "")
        if s_dir and os.path.isfile(os.path.join(s_dir, name)):
            val = sbx.read(os.path.join(s_dir, name))
            if bool(val):
                return True
    except Exception:
        pass
    # 3. Check os.environ directly
    val = os.environ.get(name, "").strip()
    if bool(val):
        return True
    # 4. Check ctx.CONF / ctx.C if configured
    if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
        if bool(ctx.CONF.get(name)):
            return True
    if hasattr(ctx, "C") and isinstance(ctx.C, dict):
        if bool(ctx.C.get(name)):
            return True
    # 5. Check alternate representations
    alt_keys = {
        "anthropic.key": ["ANTHROPIC_API_KEY", "anthropic_api_key"],
        "openai.key": ["OPENAI_API_KEY", "openai_api_key"],
        "gemini.key": ["GEMINI_API_KEY", "gemini_api_key"],
        "compat.key": ["COMPAT_KEY", "compat_key"],
        "qwen.key": ["DASHSCOPE_API_KEY", "QWEN_API_KEY", "dashscope_api_key", "qwen_api_key"],
        "OPENAI_API_KEY": ["openai.key", "openai_api_key"],
        "GEMINI_API_KEY": ["gemini.key", "gemini_api_key"],
        "ANTHROPIC_API_KEY": ["anthropic.key", "anthropic_api_key"],
    }
    for alt in alt_keys.get(name, []):
        try:
            if bool(providers.get_secret(alt)):
                return True
        except Exception:
            pass
        try:
            s_dir = getattr(sbx, "SECRETS", "")
            if s_dir and os.path.isfile(os.path.join(s_dir, alt)):
                if bool(sbx.read(os.path.join(s_dir, alt))):
                    return True
        except Exception:
            pass
        if bool(os.environ.get(alt, "").strip()):
            return True
        if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
            if bool(ctx.CONF.get(alt)):
                return True
        if hasattr(ctx, "C") and isinstance(ctx.C, dict):
            if bool(ctx.C.get(alt)):
                return True
    return False


def _get_conf_value(key: str) -> str:
    """Retrieve config value from ctx.CONF, ctx.C, or os.environ."""
    if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
        val = ctx.CONF.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    if hasattr(ctx, "C") and isinstance(ctx.C, dict):
        val = ctx.C.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    val = os.environ.get(key, "").strip()
    if val:
        return val
    return ""


def _get_compat_base_url() -> str:
    """Resolve COMPAT_BASE_URL from ctx.CONF, ctx.C, limits, or environment."""
    val = _get_conf_value("COMPAT_BASE_URL")
    if val:
        return val
    try:
        lim = sbx.load_limits()
        if lim and lim.get("COMPAT_BASE_URL"):
            return str(lim["COMPAT_BASE_URL"]).strip()
    except (SystemExit, Exception):
        pass
    return ""


def _allowed_egress_hosts() -> set[str]:
    """Combine allowlist() and local_allowlist() hosts (case-insensitive)."""
    allowed = set()
    try:
        for h in egress.allowlist():
            if h and str(h).strip():
                allowed.add(str(h).strip().lower())
    except Exception:
        pass
    try:
        for h in egress.local_allowlist():
            if h and str(h).strip():
                allowed.add(str(h).strip().lower())
    except Exception:
        pass
    return allowed


def _local_endpoint_host() -> str:
    """Return local endpoint host if configured, or empty string."""
    try:
        lep = egress.local_endpoint()
        if lep and lep[0]:
            return str(lep[0]).strip().lower()
    except Exception:
        pass
    return ""


def _build_result(name: str, entity_type: str, missing_settings: list[str], missing_egress: list[str]) -> dict:
    """Build standardized status dict adhering to state priority and shape requirements."""
    # Deduplicate while preserving order
    missing_settings = list(dict.fromkeys(missing_settings))
    missing_egress = list(dict.fromkeys(missing_egress))

    if not missing_settings and not missing_egress:
        state = "ok"
        detail = f"{entity_type} '{name}' is ready."
    elif missing_settings and missing_egress:
        state = "missing_setting"
        detail = (f"{entity_type} '{name}' is missing setting(s): {', '.join(missing_settings)}, "
                  f"and egress allowlist for: {', '.join(missing_egress)}.")
    elif missing_settings:
        state = "missing_setting"
        detail = f"{entity_type} '{name}' is missing required setting(s): {', '.join(missing_settings)}."
    else:
        state = "missing_egress"
        detail = f"{entity_type} '{name}' is missing egress allowlist for: {', '.join(missing_egress)}."

    return {
        "state": state,
        "detail": detail,
        "missing_settings": missing_settings,
        "missing_egress": missing_egress,
    }


def _status_of_runtime(name: str) -> dict:
    """Evaluate readiness of a worker runtime."""
    r = runtimes.RUNTIMES[name]
    missing_settings = []
    missing_egress = []

    # 1. Endpoint / Base URL check
    endpoint_url = ""
    if runtimes.uses_endpoint(name):
        url = _get_compat_base_url()
        target = runtimes.endpoint_target(url) if url else None
        if not target:
            missing_settings.append("COMPAT_BASE_URL")
        else:
            endpoint_url = url

    # 2. Secret check
    key = r.get("key")
    if key and not r.get("optional_key"):
        if not _has_secret(key):
            missing_settings.append(key)

    # 3. Egress hosts check
    req_hosts = runtimes.hosts(name, endpoint_url)
    if req_hosts:
        allowed = _allowed_egress_hosts()
        lep_host = _local_endpoint_host()
        for h in req_hosts:
            hl = str(h).strip().lower()
            if hl not in allowed and hl != lep_host:
                if h not in missing_egress:
                    missing_egress.append(h)

    return _build_result(name, "Runtime", missing_settings, missing_egress)


def _status_of_provider(name: str) -> dict:
    """Evaluate readiness of an orchestrator provider."""
    missing_settings = []
    missing_egress = []

    if name in ("claude-cli", "agy-claude", "agy-gemini"):
        # Default / subscription providers: no required API keys or external egress
        pass

    elif name == "openai-api":
        # Required model
        model = _get_conf_value("OPENAI_MODEL")
        if not model:
            missing_settings.append("OPENAI_MODEL")

        # Required secret
        if not _has_secret("OPENAI_API_KEY"):
            missing_settings.append("OPENAI_API_KEY")

        # Base URL and Egress
        base_url = _get_conf_value("OPENAI_API_BASE") or "https://api.openai.com/v1"
        target = runtimes.endpoint_target(base_url)
        if not target or not target[1]:
            missing_settings.append("OPENAI_API_BASE")
        else:
            host = target[1]
            allowed = _allowed_egress_hosts()
            lep_host = _local_endpoint_host()
            hl = host.lower()
            if hl not in allowed and hl != lep_host:
                missing_egress.append(host)

    elif name == "gemini-api":
        # Required model
        model = _get_conf_value("GEMINI_MODEL")
        if not model:
            missing_settings.append("GEMINI_MODEL")

        # Required secret
        if not _has_secret("GEMINI_API_KEY"):
            missing_settings.append("GEMINI_API_KEY")

    elif name == "anthropic-api":
        # Required model
        model = _get_conf_value("ANTHROPIC_MODEL")
        if not model:
            missing_settings.append("ANTHROPIC_MODEL")

        # Required secret
        if not _has_secret("ANTHROPIC_API_KEY"):
            missing_settings.append("ANTHROPIC_API_KEY")

    return _build_result(name, "Provider", missing_settings, missing_egress)


def status_of(kind: str, name: str) -> dict:
    """Check readiness status for a runtime or orchestrator provider.

    kind is 'runtime' or 'provider'. Returns:
    {"state": "ok"|"missing_setting"|"missing_egress", "detail": "<one sentence>", "missing_settings": [...], "missing_egress": [...]}
    state priority when both apply: missing_setting wins (list both in detail/lists regardless).
    """
    if kind not in ("runtime", "provider"):
        raise ValueError(f"unknown kind {kind!r}: must be 'runtime' or 'provider'")

    if kind == "runtime":
        if name not in runtimes.RUNTIMES:
            raise ValueError(f"unknown runtime {name!r}")
        return _status_of_runtime(name)

    if kind == "provider":
        if name not in settings.PROVIDERS_ALL:
            raise ValueError(f"unknown provider {name!r}")
        return _status_of_provider(name)


def all_status() -> dict:
    """Return status of all registered runtimes and providers."""
    return {
        "runtimes": {rt: status_of("runtime", rt) for rt in runtime_items()},
        "providers": {pr: status_of("provider", pr) for pr in provider_items()},
    }
