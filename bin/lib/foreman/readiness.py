"""Core runtime and provider readiness check module.

Public interface:
- runtime_items() -> list[str]: every key of bin/lib/runtimes.py RUNTIMES, in order
- provider_items() -> list[str]: every entry of bin/lib/foreman/settings.py PROVIDERS_ALL, in order
- status_of(kind: str, name: str) -> dict: readiness status of a single runtime or provider
- all_status() -> dict: status of all runtimes and providers

Important invariants:
- Pure read-only checks without network calls or secrets exposure.
- Provider requirements are derived generically from settings.EDITABLE and settings.PROVIDERS_ALL.
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
    # 2. Check via sbx.SECRETS directory
    try:
        s_dir = getattr(sbx, "SECRETS", "")
        if s_dir and os.path.isfile(os.path.join(s_dir, name)):
            if os.path.getsize(os.path.join(s_dir, name)) > 0:
                return True
    except Exception:
        pass
    # 3. Check os.environ directly
    val = os.environ.get(name, "").strip()
    if bool(val):
        return True
    # 4. Check ctx.C and ctx.CONF
    if hasattr(ctx, "C") and isinstance(ctx.C, dict):
        if bool(ctx.C.get(name)):
            return True
    if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
        if bool(ctx.CONF.get(name)):
            return True
    return False


def _get_conf_value(key: str) -> str:
    """Retrieve config value from os.environ, ctx.C, or ctx.CONF."""
    val = os.environ.get(key, "").strip()
    if val:
        return val
    if hasattr(ctx, "C") and isinstance(ctx.C, dict):
        v = ctx.C.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
        v = ctx.CONF.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _get_compat_base_url() -> str:
    """Resolve COMPAT_BASE_URL from environment, ctx.C, ctx.CONF, or limits."""
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


def _provider_spec(name: str) -> dict:
    """Derive required settings, secret names, and base URL keys generically from settings.EDITABLE."""
    if not name.endswith("-api"):
        return {"model_key": None, "secret_key": None, "base_key": None, "default_base_url": ""}

    prefix = name[:-4].replace("-", "_").upper()

    # 1. Model key: key in EDITABLE matching {prefix}_MODEL
    model_key = f"{prefix}_MODEL"
    if model_key not in settings.EDITABLE:
        candidates = [k for k in settings.EDITABLE if k.startswith(f"{prefix}_") and k.endswith("_MODEL")]
        model_key = candidates[0] if candidates else None

    # 2. Secret key: key in EDITABLE with SECRET or KEY, or {prefix}_API_KEY
    secret_candidates = [
        k for k in settings.EDITABLE
        if k.startswith(f"{prefix}_") and ("KEY" in k or "SECRET" in k)
    ]
    secret_key = secret_candidates[0] if secret_candidates else f"{prefix}_API_KEY"

    # 3. Base URL key: key in EDITABLE for URL/BASE
    url_candidates = [
        k for k in settings.EDITABLE
        if k.startswith(f"{prefix}_") and ("BASE" in k or "URL" in k or "ENDPOINT" in k)
    ]
    base_key = url_candidates[0] if url_candidates else None
    default_base_url = ""
    if base_key:
        if hasattr(ctx, "CONF") and isinstance(ctx.CONF, dict):
            default_base_url = str(ctx.CONF.get(base_key, "")).strip()

    return {
        "model_key": model_key,
        "secret_key": secret_key,
        "base_key": base_key,
        "default_base_url": default_base_url,
    }


def _status_of_provider(name: str) -> dict:
    """Evaluate readiness of an orchestrator provider derived generically from settings."""
    spec = _provider_spec(name)
    missing_settings = []
    missing_egress = []

    # 1. Check required model setting
    model_key = spec["model_key"]
    if model_key:
        val = _get_conf_value(model_key)
        if not val:
            missing_settings.append(model_key)

    # 2. Check required secret
    secret_key = spec["secret_key"]
    if secret_key:
        if not _has_secret(secret_key):
            missing_settings.append(secret_key)

    # 3. Check base URL and required egress
    base_key = spec["base_key"]
    if base_key:
        val = _get_conf_value(base_key) or spec["default_base_url"]
        target = runtimes.endpoint_target(val) if val else None
        if not target or not target[1]:
            missing_settings.append(base_key)
        else:
            host = target[1]
            allowed = _allowed_egress_hosts()
            lep_host = _local_endpoint_host()
            hl = host.lower()
            if hl not in allowed and hl != lep_host:
                missing_egress.append(host)

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
