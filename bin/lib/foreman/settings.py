"""Settings the dashboard may change (validated), file-only settings, new projects, the model list.

Public interface: settings_get(), settings_apply(), validate_providers(), validate_url(), models_cached(), init_project(), VALID_RUNTIMES.
Invariants:
- settings_apply validates egress allowlist requirements for submitted providers and runtimes before saving.
- Unrelated editable settings preserve their existing validation rules.
- Sensitive keys and secrets are masked in event logs and responses.
- settings_get surfaces file-only settings and reasons alongside editable settings.
"""
import os, re, sys, time

import sbx, runtimes
try:
    import egress, providers
except ImportError:
    _lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _lib not in sys.path:
        sys.path.insert(0, _lib)
    import egress, providers

from . import ctx
from .util import sh
from .store import event, jload, jsave
from .pool import workers


PROVIDERS_ALL = ("claude-cli", "agy-claude", "agy-gemini", "anthropic-api", "openai-api", "gemini-api")
VALID_RUNTIMES = frozenset({
    "agy", "claude", "codex", "gemini", "compat", "aider", "opencode", "goose", "qwen", "qwen-code", "anthropic-compat",
    "copilot", "mistral-vibe", "ollama", "vllm", "lmstudio", "llamacpp"
} | set(runtimes.RUNTIMES.keys()))


MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9.\-\[\]]{0,63}"
# a model on the owner's own server: "qwen3-coder:30b" (Ollama), "anthropic/claude-sonnet-4.5" (OpenRouter), "Qwen/Qwen3-32B" (vLLM)
SERVER_MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,99}"
ORCHESTRATOR_MODEL_RE = rf"({MODEL_RE})|({SERVER_MODEL_RE})"
SECRET_KEY_RE = r"(?!.*\.\.)[A-Za-z0-9_.*•-]{1,128}"
URL_RE = r"https?://(?!.*\.\.)[A-Za-z0-9.-]+(:\d{1,5})?(/[A-Za-z0-9._~/-]*)?"
USD_RE = r"\d{1,6}(\.\d{1,2})?"
NOTIFY_KINDS = "goal_done|task_blocked|killswitch|questions|quota_out|auth|approval|stalled|project_done"


# key -> (validation regex or (min, max), scope: "project" = this project's config, "limits" = etc/limits.conf for ALL projects)
EDITABLE = {
    "PLAN_PROVIDERS": (r"(%s)(\s*,\s*(%s))*" % ("|".join(PROVIDERS_ALL), "|".join(PROVIDERS_ALL)), "project"),
    "REVIEW_PROVIDERS": (r"(%s)(\s*,\s*(%s))*" % ("|".join(PROVIDERS_ALL), "|".join(PROVIDERS_ALL)), "project"),
    "WORKER_RUNTIME": (r"|" + "|".join(sorted(VALID_RUNTIMES)), "project"),
    "CLAUDE_MODEL": (MODEL_RE, "project"), "AGY_PLAN_MODEL": (MODEL_RE, "project"), "AGY_REVIEW_MODEL": (MODEL_RE, "project"),
    "AGY_FALLBACK_MODEL": (MODEL_RE, "project"), "WORKER_MODEL": ("|" + MODEL_RE, "project"), "WORKER_MODEL_RISKY": ("|" + MODEL_RE, "project"),
    "ANTHROPIC_MODEL": ("|" + MODEL_RE, "project"), "OPENAI_MODEL": ("|" + ORCHESTRATOR_MODEL_RE, "project"), "GEMINI_MODEL": ("|" + ORCHESTRATOR_MODEL_RE, "project"),
    "OPENAI_API_KEY": ("|" + SECRET_KEY_RE, "project"), "GEMINI_API_KEY": ("|" + SECRET_KEY_RE, "project"),
    "WORKERS": (r"|worker-\d+(,worker-\d+)*", "project"), "OWNER_LANG": (r"[^\W\d_][^\W\d_ ]*( [^\W\d_]+)?", "project"),
    "AUTOPILOT": (r"[01]", "project"), "AUTO_MERGE": (r"[01]", "project"), "CONTINUE_CONVERSATIONS": (r"[01]", "project"),
    "MAX_REWORK": ((0, 10), "project"), "MAX_ACTIVE_GOALS": ((1, 50), "project"), "SPLIT_AFTER_CAPS": ((1, 10), "project"),
    "TASK_TOKEN_BUDGET": ((50000, 5000000), "project"),
    "CONTEXT_PACK": (r"on|off|ab", "project"), "CONTEXT_PACK_CHARS": ((5000, 200000), "project"), "SELF_CHECK_ROUNDS": ((0, 3), "project"),
    "MAX_TASK_FILES": ((1, 20), "project"), "WORKER_MODEL_LIGHT": ("|" + MODEL_RE, "project"),
    "LIGHT_ROLES": (r"|[a-z][a-z-]{1,40}(,[a-z][a-z-]{1,40})*", "project"), "AB_MODEL": ("|" + MODEL_RE, "project"),
    "AB_SHARE": ((0, 100), "project"), "PLAN_AHEAD": (r"[01]", "project"), "MAINTENANCE": (r"[01]", "project"),
    "TEST_CMD": ("|" + sbx.TEST_CMD_RE.pattern, "project"), "GATE_IMAGE": ("|" + sbx.IMAGE_RE.pattern, "project"),
    "NOTIFY_URL": (r"|outbox|https?://[A-Za-z0-9._~:/?\[\]@!&()*+,;=%-]{4,400}", "project"),     # no '#': it starts a comment in .conf
    "NOTIFY_FORMAT": (r"ntfy|slack|discord|json", "project"),
    "NOTIFY_EVENTS": (r"|(%s)(,(%s))*" % ((NOTIFY_KINDS,) * 2), "project"),
    "WORKER_MODEL_CLAUDE": ("|" + MODEL_RE, "project"), "WORKER_MODEL_CODEX": ("|" + MODEL_RE, "project"),
    "WORKER_MODEL_GEMINI": ("|" + MODEL_RE, "project"), "API_WORKERS_POLICY": (r"overflow|always", "project"),
    "API_RUN_BUDGET_USD": (r"\d{1,4}(\.\d{1,2})?", "limits"), "API_DAILY_BUDGET_USD": (r"\d{1,5}(\.\d{1,2})?", "limits"),
    "RUN_MAX_TOOL_CALLS": ((10, 5000), "limits"),
    "FORGE_KIND": (r"forgejo|github|gitlab", "project"), "UPSTREAM_REPO": (r"|[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){1,4}", "project"),
    "UPSTREAM_URL": (r"|https?://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?", "project"), "UPSTREAM_BRANCH": (r"[A-Za-z0-9._/-]{1,100}", "project"),
    "UPSTREAM_MERGE": (r"merge|squash|rebase", "project"),
    "PRICE_CODEX": (r"\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?", "limits"),
    "PRICE_GEMINI": (r"\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?", "limits"),
    "RUNS_KEEP_DAYS": ((1, 3650), "project"),
    "COMPAT_BASE_URL": ("|" + runtimes.ENDPOINT_RE.pattern, "limits"), "PRICE_COMPAT": (r"\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?,\d{1,4}(\.\d{1,4})?", "limits"),
    "WORKER_MODEL_COMPAT": ("|" + SERVER_MODEL_RE, "project"), "WORKER_MODEL_AIDER": ("|" + SERVER_MODEL_RE, "project"),
    "OPENCODE_MODEL": ("|" + ORCHESTRATOR_MODEL_RE, "project"),  # Model name for OpenCode CLI (empty for default)
    "QWEN_MODEL": ("|" + ORCHESTRATOR_MODEL_RE, "project"),      # Model name for Qwen Code CLI (empty for default)
    "GOOSE_MODEL": ("|" + ORCHESTRATOR_MODEL_RE, "project"),     # Model name for Goose CLI agent (empty for default)
    "API_MONTHLY_BUDGET_USD": (USD_RE, "limits"), "PROJECT_DAILY_BUDGET_USD": (USD_RE, "project"), "PROJECT_MONTHLY_BUDGET_USD": (USD_RE, "project"),
    "WORKER_DAILY_BUDGET_USD": (r"|worker-\d+=%s(,worker-\d+=%s)*" % (USD_RE, USD_RE), "limits"),
    "APPROVE": (r"|(plan|risky|merge)(,(plan|risky|merge))*", "project"),
    "MODEL_ROUTING": (r"off|auto", "project"), "ROUTING_MIN_TASKS": ((1, 100), "project"), "ROUTING_MIN_FIRST_PASS": ((0, 100), "project"),
    "MAX_PARALLEL": ((1, 64), "limits"), "RUN_TOKEN_CAP": ((100000, 20000000), "limits"), "RUN_TIMEOUT_MIN": ((5, 240), "limits"),
    "QUOTA_MIN_5H_PCT": ((0, 90), "limits"), "QUOTA_MIN_WEEK_PCT": ((0, 90), "limits"), "CONSEC_FAIL_TRIP": ((1, 100), "limits"),
    "DAILY_TOKEN_BUDGET": ((100000, 10000000000000), "limits"), "MAX_RUNS_PER_DAY": ((1, 10000000), "limits"),
    "RUN_TIMEOUT_MAX_MIN": ((5, 1440), "limits"), "QUOTA_MODEL_FILTER": (r"[A-Za-z0-9_-]{1,32}", "limits"),
    "POLL_SEC": ((1, 60), "limits"),
    "OPENAI_API_BASE": ("|" + URL_RE, "project"),
    "DIFF_MAX": ((1000, 1000000), "project"), "MAX_GOAL_ROUNDS": ((1, 20), "project"), "LESSONS_MAX": ((1, 500), "project"),
    "LOOP_SEC": ((1, 300), "project"), "LOCAL_TZ": (r"(?!/)(?!.*\.\.)[A-Za-z0-9_./+-]{1,60}", "project"),
    "SHARED_FILES": (r"|(?!/)(?!.*\.\.)[A-Za-z0-9._~/-]+(,(?!/)(?!.*\.\.)[A-Za-z0-9._~/-]+)*", "project"),
    "VISION_FILE": (r"(?!.*\.\.)[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*", "project"),
    "PROJECT_DIR": (r"|(?!.*\.\.)[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*", "project"),
    "ORPHAN_LIMIT": ((1, 100), "project"),  # consecutive orphan recoveries before the task is blocked
    "ORPHAN_WARN": ((1, 100), "project"),   # consecutive orphan recoveries before a warning
}

HELP = {
    "PLAN_PROVIDERS": "Ordered orchestrator providers for planning: claude-cli, agy-claude, agy-gemini, anthropic-api, openai-api, gemini-api. Egress host check happens at save time. Applies live.",
    "REVIEW_PROVIDERS": "Ordered orchestrator providers for reviews: agy-claude, claude-cli, agy-gemini, anthropic-api, openai-api, gemini-api. Egress host check happens at save time. Applies live.",
    "WORKER_RUNTIME": "Default worker runtime (e.g. agy, claude, codex, gemini, copilot, mistral-vibe, ollama, vllm, lmstudio, llamacpp). Egress host check happens at save time. Applies live.",
    "OPENCODE_MODEL": "Model name for OpenCode CLI (e.g. gpt-4o, anthropic/claude-sonnet-4.5, Ollama/OpenRouter model). Empty for default. Applies live.",
    "QWEN_MODEL": "Model name for Qwen Code CLI (e.g. qwen-max, qwen-plus, qwen2.5-coder-32b-instruct). Empty for default. Applies live.",
    "GOOSE_MODEL": "Model name for Goose CLI agent (e.g. gpt-4o, qwen2.5-coder:32b). Empty for default. Applies live.",
    "copilot": "GitHub Copilot CLI runtime (subscription, needs gh/Copilot CLI auth). Egress host check happens at save time. Applies live.",
    "mistral-vibe": "Mistral Vibe CLI runtime (subscription or API key). Egress host check happens at save time. Applies live.",
    "ollama": "Local Ollama server runtime (no API key, point at its OpenAI-compatible endpoint URL). Applies live.",
    "vllm": "Local vLLM server runtime (point at its OpenAI-compatible endpoint URL). Applies live.",
    "lmstudio": "Local LM Studio server runtime (point at its OpenAI-compatible endpoint URL). Applies live.",
    "llamacpp": "Local llama.cpp server runtime (point at its OpenAI-compatible endpoint URL). Applies live.",
    "opencode": "OpenCode CLI runtime (configured provider, point at its model server endpoint URL). Egress host check happens at save time. Applies live.",
    "qwen": "Qwen Code CLI runtime (Alibaba DashScope API key in qwen.key). Egress host check happens at save time. Applies live.",
    "qwen-code": "Qwen Code CLI runtime (Alibaba DashScope API key in qwen.key). Egress host check happens at save time. Applies live.",
    "goose": "Goose CLI agent runtime (Block's CLI agent, point at its model server endpoint URL). Egress host check happens at save time. Applies live.",
}

FILE_ONLY = {
    "FORGE_URL": "Install-time internal git forge URL; set in etc/forge.conf and requires restart.",
    "FORGE_REPO": "Install-time default repository; set in etc/forge.conf and requires restart.",
}

CONF_DEFAULTS = {"ORPHAN_LIMIT": 5, "ORPHAN_WARN": 3, "WORKER_RUNTIME": "agy"}


def conf_path(scope):
    if scope == "limits":
        return os.path.join(ctx.ROOT, "etc", "limits.conf")
    return os.path.join(ctx.PDIR, "project.conf") if ctx.PDIR else os.path.join(ctx.ROOT, "etc", "foreman.conf")


def write_conf(path, values):
    """Update KEY=VALUE lines in place (keeping comments and order), append keys that are not there yet."""
    for k, v in values.items():
        if any(c in "\r\n" for c in f"{k}{v}") or "=" in str(k):
            raise ValueError(f"newline or '=' injection in config: {k!r}={v!r}")
    lines = []
    if os.path.exists(path):
        with open(path) as f:
            lines = f.read().splitlines()
    done = set()
    for i, l in enumerate(lines):
        k = l.split("=", 1)[0].strip()
        if "=" in l and not l.lstrip().startswith("#") and k in values:
            lines[i] = f"{k}={values[k]}"; done.add(k)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def validate_url(url):
    """Validate OPENAI_API_BASE URL: must be http(s) with valid hostname and optional port/path,
    rejecting shell characters, credentials, query strings, and path traversal."""
    if not url:
        return None
    if any(c in "\r\n\t `$;|&<>'\"\\" for c in url):
        return "malformed OPENAI_API_BASE: shell characters and whitespace not allowed"
    if "@" in url:
        return "malformed OPENAI_API_BASE: credentials not allowed in URL"
    if "?" in url:
        return "malformed OPENAI_API_BASE: query strings not allowed"
    if "#" in url:
        return "malformed OPENAI_API_BASE: fragments not allowed"
    if ".." in url:
        return "malformed OPENAI_API_BASE: path traversal ('..') not allowed"
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
    except Exception:
        return "malformed OPENAI_API_BASE: invalid URL format"
    if u.scheme not in ("http", "https"):
        return "malformed OPENAI_API_BASE: scheme must be http:// or https://"
    if not u.netloc or not u.hostname:
        return "malformed OPENAI_API_BASE: missing valid hostname"
    host = u.hostname
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or host.startswith(".") or host.endswith("."):
        return "malformed OPENAI_API_BASE: invalid hostname"
    try:
        port = u.port
    except ValueError:
        return "malformed OPENAI_API_BASE: invalid port"
    if port is not None and not (1 <= port <= 65535):
        return "malformed OPENAI_API_BASE: port must be between 1 and 65535"
    if u.path and not re.fullmatch(r"(/[A-Za-z0-9._~/-]*)?", u.path):
        return "malformed OPENAI_API_BASE: invalid path"
    return None


def validate_providers(v, k):
    """Validate comma-separated providers against PROVIDERS_ALL."""
    if not v:
        return f"{k} must specify at least one provider from: {', '.join(PROVIDERS_ALL)}"
    parts = [p.strip() for p in v.split(",")]
    if any(not p for p in parts):
        return f"malformed {k}: empty provider entry"
    for p in parts:
        if p not in PROVIDERS_ALL:
            return f"unknown provider '{p}' in {k} (allowed: {', '.join(PROVIDERS_ALL)})"
    return None


def mask_secret(v):
    """Mask secret value so raw keys are never returned or echoed."""
    if not v:
        return ""
    if any(c in v for c in "*•"):
        return v
    if re.fullmatch(r"[a-z0-9_-]+\.(key|token)", v):
        return v
    return "***"


def mask_log_value(k, v):
    """Mask sensitive values in event logs."""
    if not v:
        return ""
    if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET")):
        return "***"
    return v


def settings_get():
    lim = sbx.load_limits()
    vals = {k: (int(lim[k]) if isinstance(rule, tuple) and isinstance(lim.get(k), (int, float)) else lim[k]) if sc == "limits" else (ctx.C[k] if k in ctx.C and ctx.C[k] != "" else CONF_DEFAULTS.get(k, "")) for k, (rule, sc) in EDITABLE.items()}
    vals["FORGE_URL"] = sbx.FORGE_URL
    vals["FORGE_REPO"] = sbx.FORGE_REPO
    rc, out, _ = sh(["docker", "ps", "-a", "--format", '{{.Names}} {{.State}} {{.Label "agent-forge.runtime"}}'], timeout=30)
    allw = sorted((x.split() + ["agy"])[:3] for x in out.splitlines() if re.match(r"worker-\d+ \w+", x))
    allw.sort(key=lambda x: int(x[0].split("-")[1]))
    keys = {n: os.path.exists(os.path.join(sbx.SECRETS, n)) and os.path.getsize(os.path.join(sbx.SECRETS, n)) > 0
            for n in ("anthropic.key", "openai.key", "gemini.key", "compat.key", "github.token", "gitlab.token", "notify.token")}
    for sk in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        if sk in vals and vals[sk]:
            vals[sk] = mask_secret(vals[sk])
    worker_tuples = [(n, rt if (rt in runtimes.RUNTIMES or rt in VALID_RUNTIMES) else "agy") for n, st, rt in allw]
    accounts = sbx.get_all_worker_accounts(worker_tuples)
    return dict(project=ctx.PROJECT or "default", values=vals, scopes={k: sc for k, (_, sc) in EDITABLE.items()},
                ranges={k: list(rule) for k, (rule, _) in EDITABLE.items() if isinstance(rule, tuple)},
                file_only=dict(FILE_ONLY),
                limits_worker_model=lim["WORKER_MODEL"], providers=list(PROVIDERS_ALL), models=models_cached(),
                workers=[dict(name=n, state=st, runtime=rt if (rt in runtimes.RUNTIMES or rt in VALID_RUNTIMES) else "agy",
                              account=accounts.get(n) or "") for n, st, rt in allw],
                runtimes={k: dict(label=v["label"], key=v["key"], api=v["api"], endpoint=bool(v.get("endpoint")),
                                  optional_key=bool(v.get("optional_key")), creatable=k in runtimes.CREATABLE)
                          for k, v in runtimes.RUNTIMES.items()},
                secrets=keys, api_spent_today=sbx.api_spent_today(), api_spent_month=sbx.api_spent(period="month"))


def settings_apply(changes):
    """Validate every value; write project keys to this project's config and limit keys to etc/limits.conf. Returns errors."""
    errors, by_scope = {}, {}
    cleaned = {}
    for k, v in (changes or {}).items():
        if k not in EDITABLE:
            errors[k] = "not editable"; continue
        rule, scope = EDITABLE[k]
        v = str(v).strip()
        if isinstance(rule, tuple):
            if not re.fullmatch(r"\d+", v) or not rule[0] <= int(v) <= rule[1]:
                errors[k] = f"number {rule[0]}-{rule[1]}"; continue
        elif k == "OPENAI_API_BASE":
            err = validate_url(v)
            if err:
                errors[k] = err; continue
        elif k in ("PLAN_PROVIDERS", "REVIEW_PROVIDERS"):
            err = validate_providers(v, k)
            if err:
                errors[k] = err; continue
            v = ",".join(p.strip() for p in v.split(","))
            parts = [p.strip() for p in v.split(",") if p.strip()]
            for p in parts:
                base_key = f"{p.split('-', 1)[0].upper()}_API_BASE"
                api_base = str(cleaned.get(base_key, (changes or {}).get(base_key, ctx.C.get(base_key, ""))) or "").strip()
                missing = providers.missing_hosts(p, api_base)
                if missing:
                    missing_str = ", ".join(missing)
                    errors[k] = f"missing egress host '{missing_str}' for provider '{p}' (add to egress/allowlist.txt or the dashboard's Egress page)"
                    break
            if k in errors:
                continue
        elif k in ("WORKER_RUNTIME", "RUNTIME", "DEFAULT_RUNTIME"):
            if not re.fullmatch(rule, v):
                errors[k] = "invalid value"; continue
            if v:
                endpoint_url = str(cleaned.get("COMPAT_BASE_URL", (changes or {}).get("COMPAT_BASE_URL", ctx.C.get("COMPAT_BASE_URL", ""))) or "").strip()
                if not endpoint_url:
                    try:
                        lim = sbx.load_limits()
                        endpoint_url = str((lim or {}).get("COMPAT_BASE_URL", "")).strip()
                    except Exception:
                        endpoint_url = ""
                try:
                    missing = egress.runtime_missing_hosts(v, endpoint_url)
                except KeyError:
                    missing = []
                if missing:
                    missing_str = ", ".join(missing)
                    errors[k] = f"missing egress host '{missing_str}' for runtime '{v}' (add to egress/allowlist.txt or the dashboard's Egress page)"
                    continue
        elif k in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
            if not re.fullmatch(rule, v):
                errors[k] = "invalid secret name or masked identifier"; continue
        elif not re.fullmatch(rule, v):
            errors[k] = "invalid value"; continue
        by_scope.setdefault(scope, {})[k] = v
        cleaned[k] = v

    eff_plan = [p.strip() for p in str(cleaned.get("PLAN_PROVIDERS", ctx.C.get("PLAN_PROVIDERS", ""))).split(",") if p.strip()]
    eff_review = [p.strip() for p in str(cleaned.get("REVIEW_PROVIDERS", ctx.C.get("REVIEW_PROVIDERS", ""))).split(",") if p.strip()]
    eff_providers = set(eff_plan + eff_review)

    eff_openai_model = cleaned.get("OPENAI_MODEL", ctx.C.get("OPENAI_MODEL", "")).strip()
    eff_gemini_model = cleaned.get("GEMINI_MODEL", ctx.C.get("GEMINI_MODEL", "")).strip()

    if "openai-api" in eff_providers and not eff_openai_model:
        errors["OPENAI_MODEL"] = "OPENAI_MODEL cannot be blank when openai-api is configured in PLAN_PROVIDERS or REVIEW_PROVIDERS"
    if "gemini-api" in eff_providers and not eff_gemini_model:
        errors["GEMINI_MODEL"] = "GEMINI_MODEL cannot be blank when gemini-api is configured in PLAN_PROVIDERS or REVIEW_PROVIDERS"

    if errors:
        return errors
    for scope, vals in by_scope.items():
        write_conf(conf_path(scope), vals)
    event("settings changed: " + ", ".join(f"{k}={mask_log_value(k, v)}" for sc in by_scope.values() for k, v in sc.items())[:300])
    if "COMPAT_BASE_URL" in by_scope.get("limits", {}):
        # the workers may reach exactly the new model server (and no longer the old one, if it was a local exception)
        try:
            for m in egress.set_endpoint(by_scope["limits"]["COMPAT_BASE_URL"]):
                event(m)
        except Exception as e:
            event(f"egress: could not apply the model server setting: {e}")
    return {}


def models_cached(refresh=False):
    """Models Antigravity offers (agy models, no quota used), cached for a day in runs/agy-models.json."""
    p = os.path.join(sbx.RUNS, "agy-models.json")
    d = jload(p, {})
    if refresh or time.time() - d.get("at", 0) > 86400:
        for w in workers():
            rc, out, _ = sh(["docker", "exec", w, "agy-run", "models"], timeout=90)
            ms = [dict(id=l.split("\t")[0].strip(), name=l.split("\t")[1].strip()) for l in out.splitlines()
                  if "\t" in l and re.fullmatch(MODEL_RE, l.split("\t")[0].strip())]
            if ms:
                d = dict(at=time.time(), models=ms); jsave(p, d); break
    return d.get("models", [])


def init_project(name, repo, workers_, lang, vision=None):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name or "") or name == "default":
        sys.exit("project name: lowercase letters, digits, dashes (not 'default')")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        sys.exit("--repo must be org/repository")
    d = os.path.join(ctx.ROOT, "projects", name)
    if os.path.exists(os.path.join(d, "project.conf")):
        sys.exit(f"projects/{name} already exists")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "project.conf"), "w").write(f"""# Agent-Forge project "{name}" - overrides etc/foreman.conf for this project only (same keys).
FORGE_REPO={repo}
# Worker containers this project's foreman may use (comma-separated; empty = all). Give each project its own pool.
WORKERS={workers_}
OWNER_LANG={lang}
# Subdirectory of the repository the team works in ("" = whole repo), and the vision file in THIS directory.
PROJECT_DIR=
VISION_FILE=vision.md
SHARED_FILES=
""")
    if vision and vision.strip():
        open(os.path.join(d, "vision.md"), "w").write(vision.strip() + "\n")
    for src, dst in (("etc/team-rules.example.md", "team-rules.md"), ("docs/vision.example.md", "vision.md")):
        if os.path.exists(os.path.join(ctx.ROOT, src)) and not os.path.exists(os.path.join(d, dst)):
            open(os.path.join(d, dst), "w").write(open(os.path.join(ctx.ROOT, src)).read())
    print(f"""created projects/{name}/ (project.conf, team-rules.md, vision.md)
next:
  1. edit projects/{name}/vision.md and team-rules.md
  2. FORGE_REPO={repo} scripts/forge-bootstrap          # creates the repository, access and branch protection
  3. sudo scripts/install-services --project {name}      # its own foreman service
  4. bin/foreman --project {name} goal "..."            # or pick the project in the dashboard""")
