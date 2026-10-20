"""The foreman's context: which project it acts on, where that project's files live, and its settings (CONF defaults,
overlaid by etc/foreman.conf and then projects/<name>/project.conf). Other modules always read these as `ctx.X` - never
`from .ctx import X` - so a test (or a command acting on another directory) can point them somewhere else.

The project is chosen before anything is imported: bin/foreman reads `--project NAME` (or FORGE_PROJECT) and puts it into the
environment, which bin/lib/sbx.py reads at import."""
import os, sys

import sbx

ROOT = sbx.ROOT
BIN = os.path.join(ROOT, "bin")
PROJECT = sbx.PROJECT
PDIR = os.path.join(ROOT, "projects", PROJECT) if PROJECT else ""
FD = os.path.join(sbx.RUNS, "projects", PROJECT, "foreman") if PROJECT else os.path.join(sbx.RUNS, "foreman")
REPO = os.path.join(FD, "repo")
CFG_DIR = PDIR or os.path.join(ROOT, "etc")         # where this project's team-rules.md, lessons.md, decisions.md live
CONF = dict(CLAUDE_MODEL="sonnet", AGY_REVIEW_MODEL="claude-sonnet-4-6", AGY_PLAN_MODEL="claude-sonnet-4-6",
            AGY_FALLBACK_MODEL="gemini-3.8-flash-high", ANTHROPIC_MODEL="", OPENAI_MODEL="", GEMINI_MODEL="", OPENAI_API_BASE="https://api.openai.com/v1",
            # Which orchestrator answers each decision, tried in order. Free (subscription) providers: claude-cli (Claude Code),
            # agy-claude / agy-gemini (Antigravity in a worker container). Paid per-token APIs: anthropic-api, openai-api - only
            # used when listed here AND a key is configured (see docs/PROVIDERS.md).
            PLAN_PROVIDERS="claude-cli,agy-claude,agy-gemini", REVIEW_PROVIDERS="agy-claude,claude-cli,agy-gemini",
            MAX_REWORK=3, MAX_GOAL_ROUNDS=3, LOOP_SEC=20, DIFF_MAX=60000, AUTO_MERGE=1, LOCAL_TZ="UTC", AUTOPILOT=1, MAX_ACTIVE_GOALS=8,
            ORPHAN_LIMIT=5, ORPHAN_WARN=3,
            SHARED_FILES="", PROJECT_DIR="", QUALITY_CHECK="warn", MAX_FILE_LINES=400, TEST_RATIO_MAX=1.5, OWNER_LANG="English", TASK_LANG="English", PRODUCT_LANG="", DOCS_LANG="English", VISION_FILE="docs/vision.md", TASK_TOKEN_BUDGET=600000,
            # this project's worker pool ("" = every worker container); risky tasks may use a stronger worker model
            WORKERS="", WORKER_MODEL="", WORKER_MODEL_RISKY="", CONTINUE_CONVERSATIONS=1, SPLIT_AFTER_CAPS=2, LESSONS_MAX=40,
            # efficiency (docs/EFFICIENCY.md): a context pack in every task (on|off|ab = half the tasks, to measure it), test the
            # worker's clone before the run ends and let it fix failures in the same conversation, split tasks that own more files
            # than MAX_TASK_FILES before anyone works on them (tasks with 4-5 files hit the token cap twice as often as 1-2)
            CONTEXT_PACK="on", CONTEXT_PACK_CHARS=60000, SELF_CHECK_ROUNDS=1, MAX_TASK_FILES=3,
            # cheaper model for light roles; A/B: AB_SHARE % of routine tasks use AB_MODEL (compare in Metrics)
            WORKER_MODEL_LIGHT="", LIGHT_ROLES="tester,technical-writer,translator", AB_MODEL="", AB_SHARE=0,
            # while every worker waits for quota: plan backlog goals ahead (orchestrator only) and check main without any LLM
            PLAN_AHEAD=1, MAINTENANCE=1,
            # how the project is tested (gate, self-check, maintenance) and in which image; any language with a CLI test runner
            TEST_CMD="", GATE_IMAGE="",
            # owner notifications: an ntfy topic, a Slack / Discord webhook or any JSON endpoint ("" = off)
            NOTIFY_URL="", NOTIFY_FORMAT="ntfy", NOTIFY_EVENTS="goal_done,task_blocked,killswitch,questions,quota_out,auth,approval,stalled,project_done",
            # finished runs older than this are compressed by maintenance (their transcripts are the bulk of runs/)
            RUNS_KEEP_DAYS=14,
            # API-key worker runtimes (bin/worker-add N --runtime claude|codex|gemini): their models ("" = the CLI's default),
            # and when they get work: "overflow" = only when no subscription (agy) worker can take it, "always" = first
            WORKER_MODEL_CLAUDE="sonnet", WORKER_MODEL_CODEX="", WORKER_MODEL_GEMINI="", API_WORKERS_POLICY="overflow",
            # where the code lives (bin/lib/upstream.py): "" = not chosen yet (the dashboard asks), forgejo = internal only,
            # github / gitlab = merged there through pull / merge requests, main synced back
            FORGE_KIND="", UPSTREAM_REPO="", UPSTREAM_URL="", UPSTREAM_BRANCH="main", UPSTREAM_MERGE="merge",
            # workers on the owner's own model server (COMPAT_BASE_URL in etc/limits.conf): the model name there
            WORKER_MODEL_COMPAT="", WORKER_MODEL_AIDER="",
            # this project's API money caps in USD (0 = none; the install-wide ones are in etc/limits.conf)
            PROJECT_DAILY_BUDGET_USD=0.0, PROJECT_MONTHLY_BUDGET_USD=0.0,
            # the owner approves before the team goes on: "plan" (each plan of a goal), "risky" (each risky task before it
            # starts), "merge" (each approved branch before it is merged) - comma-separated, "" = fully automatic
            APPROVE="",
            # model routing: "auto" = for each role, the cheapest worker model that has proven itself here (at least
            # ROUTING_MIN_TASKS merged tasks of that role, ROUTING_MIN_FIRST_PASS % approved at once); "off" = the rules above
            MODEL_ROUTING="off", ROUTING_MIN_TASKS=5, ROUTING_MIN_FIRST_PASS=70)


def load_conf():
    """etc/foreman.conf (shared defaults), then projects/<name>/project.conf (per project) on top. A value that does not fit the
    key's type (e.g. an empty number) keeps the default and is reported, instead of stopping the foreman at start."""
    c = dict(CONF)
    for path in [os.path.join(ROOT, "etc", "foreman.conf")] + ([os.path.join(PDIR, "project.conf")] if PDIR else []):
        try:
            for line in open(path):
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = (x.strip() for x in line.split("=", 1))
                    if k in c:
                        try:
                            c[k] = type(CONF[k])(v.strip('"'))
                        except ValueError:
                            print(f"{path}: {k}={v!r} is not a valid {type(CONF[k]).__name__} - using {CONF[k]!r}", file=sys.stderr)
        except FileNotFoundError:
            pass
    return c


C = load_conf()
