"""Settings the dashboard may change (validated), new projects, the model list."""
import os, re, sys, time

import sbx, runtimes

from . import ctx
from .util import sh
from .store import event, jload, jsave
from .pool import workers


PROVIDERS_ALL = ("claude-cli", "agy-claude", "agy-gemini", "anthropic-api", "openai-api", "gemini-api")


MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9.\-\[\]]{0,63}"
# a model on the owner's own server: "qwen3-coder:30b" (Ollama), "anthropic/claude-sonnet-4.5" (OpenRouter), "Qwen/Qwen3-32B" (vLLM)
SERVER_MODEL_RE = r"[A-Za-z0-9][A-Za-z0-9._:/@\-]{0,99}"
USD_RE = r"\d{1,6}(\.\d{1,2})?"
NOTIFY_KINDS = "goal_done|task_blocked|killswitch|questions|quota_out|auth|approval|project_done"


# key -> (validation regex or (min, max), scope: "project" = this project's config, "limits" = etc/limits.conf for ALL projects)
EDITABLE = {
    "PLAN_PROVIDERS": (r"(%s)(,(%s))*" % ("|".join(PROVIDERS_ALL), "|".join(PROVIDERS_ALL)), "project"),
    "REVIEW_PROVIDERS": (r"(%s)(,(%s))*" % ("|".join(PROVIDERS_ALL), "|".join(PROVIDERS_ALL)), "project"),
    "CLAUDE_MODEL": (MODEL_RE, "project"), "AGY_PLAN_MODEL": (MODEL_RE, "project"), "AGY_REVIEW_MODEL": (MODEL_RE, "project"),
    "AGY_FALLBACK_MODEL": (MODEL_RE, "project"), "WORKER_MODEL": ("|" + MODEL_RE, "project"), "WORKER_MODEL_RISKY": ("|" + MODEL_RE, "project"),
    "ANTHROPIC_MODEL": ("|" + MODEL_RE, "project"), "OPENAI_MODEL": ("|" + MODEL_RE, "project"), "GEMINI_MODEL": ("|" + MODEL_RE, "project"),
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
    "API_MONTHLY_BUDGET_USD": (USD_RE, "limits"), "PROJECT_DAILY_BUDGET_USD": (USD_RE, "project"), "PROJECT_MONTHLY_BUDGET_USD": (USD_RE, "project"),
    "WORKER_DAILY_BUDGET_USD": (r"|worker-\d+=%s(,worker-\d+=%s)*" % (USD_RE, USD_RE), "limits"),
    "APPROVE": (r"|(plan|risky|merge)(,(plan|risky|merge))*", "project"),
    "MODEL_ROUTING": (r"off|auto", "project"), "ROUTING_MIN_TASKS": ((1, 100), "project"), "ROUTING_MIN_FIRST_PASS": ((0, 100), "project"),
    "MAX_PARALLEL": ((1, 64), "limits"), "RUN_TOKEN_CAP": ((100000, 20000000), "limits"), "RUN_TIMEOUT_MIN": ((5, 240), "limits"),
    "QUOTA_MIN_5H_PCT": ((0, 90), "limits"), "QUOTA_MIN_WEEK_PCT": ((0, 90), "limits"), "CONSEC_FAIL_TRIP": ((1, 100), "limits"),
}


def conf_path(scope):
    if scope == "limits":
        return os.path.join(ctx.ROOT, "etc", "limits.conf")
    return os.path.join(ctx.PDIR, "project.conf") if ctx.PDIR else os.path.join(ctx.ROOT, "etc", "foreman.conf")


def write_conf(path, values):
    """Update KEY=VALUE lines in place (keeping comments and order), append keys that are not there yet."""
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    done = set()
    for i, l in enumerate(lines):
        k = l.split("=", 1)[0].strip()
        if "=" in l and not l.lstrip().startswith("#") and k in values:
            lines[i] = f"{k}={values[k]}"; done.add(k)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    tmp = path + ".tmp"
    open(tmp, "w").write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def settings_get():
    lim = sbx.load_limits()
    vals = {k: (lim[k] if sc == "limits" else ctx.C[k]) for k, (_, sc) in EDITABLE.items()}
    rc, out, _ = sh(["docker", "ps", "-a", "--format", '{{.Names}} {{.State}} {{.Label "agent-forge.runtime"}}'], timeout=30)
    allw = sorted((x.split() + ["agy"])[:3] for x in out.splitlines() if re.match(r"worker-\d+ \w+", x))
    allw.sort(key=lambda x: int(x[0].split("-")[1]))
    keys = {n: os.path.exists(os.path.join(sbx.SECRETS, n)) and os.path.getsize(os.path.join(sbx.SECRETS, n)) > 0
            for n in ("anthropic.key", "openai.key", "gemini.key", "compat.key", "github.token", "gitlab.token")}
    return dict(project=ctx.PROJECT or "default", values=vals, scopes={k: sc for k, (_, sc) in EDITABLE.items()},
                limits_worker_model=lim["WORKER_MODEL"], providers=list(PROVIDERS_ALL), models=models_cached(),
                workers=[dict(name=n, state=st, runtime=rt if rt in runtimes.RUNTIMES else "agy") for n, st, rt in allw],
                runtimes={k: dict(label=v["label"], key=v["key"], api=v["api"], endpoint=bool(v.get("endpoint")),
                                  optional_key=bool(v.get("optional_key"))) for k, v in runtimes.RUNTIMES.items()},
                secrets=keys, api_spent_today=sbx.api_spent_today(), api_spent_month=sbx.api_spent(period="month"))


def settings_apply(changes):
    """Validate every value; write project keys to this project's config and limit keys to etc/limits.conf. Returns errors."""
    errors, by_scope = {}, {}
    for k, v in (changes or {}).items():
        if k not in EDITABLE:
            errors[k] = "not editable"; continue
        rule, scope = EDITABLE[k]
        v = str(v).strip()
        if isinstance(rule, tuple):
            if not re.fullmatch(r"\d+", v) or not rule[0] <= int(v) <= rule[1]:
                errors[k] = f"number {rule[0]}-{rule[1]}"; continue
        elif not re.fullmatch(rule, v):
            errors[k] = "invalid value"; continue
        by_scope.setdefault(scope, {})[k] = v
    if errors:
        return errors
    for scope, vals in by_scope.items():
        write_conf(conf_path(scope), vals)
    event("settings changed: " + ", ".join(f"{k}={v}" for sc in by_scope.values() for k, v in sc.items())[:300])
    if "COMPAT_BASE_URL" in by_scope.get("limits", {}):
        # the workers may reach exactly the new model server (and no longer the old one, if it was a local exception)
        import egress
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
