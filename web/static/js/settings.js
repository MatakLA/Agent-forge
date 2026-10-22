// Settings: eight short pages - Workers (kill switch, the pool, adding / removing workers), Runtimes (what can run, API keys,
// your model server), Models (who plans and reviews, worker models, routing, experiments), Project (where the code lives,
// approvals, behaviour, tests, notifications), Money (budgets and prices), Knowledge (the team's documents and roles),
// Limits (all projects) and Advanced (tuning). Every edit is collected in PEND and saved together from the bar at the bottom,
// so nothing typed is lost when you change page or the screen refreshes. Also: where the code lives (dialog),
// a worker's Antigravity sign-in, a new project.

// ---- kill switch (per project; "all" = every project)
let ksBusy = false;
async function killswitch(action, opts) {
  opts = opts || {};
  if (ksBusy) return;
  const hard = !!opts.hard, all = !!opts.all;
  if (action === 'on' && !confirm((all ? 'Stop ALL projects' : 'Stop project “' + project + '”') + (hard ? ' HARD (also stops the worker containers)' : '') + '?')) return;
  if (action === 'off' && F && F.killswitch && F.killswitch.hard && !confirm('The kill switch is in hard mode. Start the workers again and continue?')) return;
  ksBusy = true;
  const bx = $('resume-result');
  bx.style.display = 'block'; bx.innerHTML = '<div class="ev">' + (action === 'on' ? 'stopping…' : 'continuing…') + '</div>';
  let res;
  try { res = await (await fetch('/api/killswitch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, action, hard, all})})).json(); }
  catch (e) { res = {ok: false, steps: [{label: 'connection', ok: false, output: String(e)}]}; }
  ksBusy = false;
  const rows = (res.steps || []).map(s => '<div class="ev ' + (s.ok ? 'good' : 'bad') + '">' + (s.ok ? '✓ ' : '✗ ') + esc(s.label) + (s.output ? ': ' + esc(s.output) : '') + '</div>').join('');
  bx.innerHTML = (rows || '<div class="ev bad">✗ ' + esc(res.error || 'no answer') + '</div>') + '<button class="linkbtn" data-hide-result>hide</button>';
  if (action === 'off') {
    if (typeof SYS !== 'undefined' && SYS && SYS.egress) SYS.egress.killswitch = null;
    if (F && F.killswitch) F.killswitch = null;
  }
  if (typeof sysLoad === 'function') sysLoad('egress', 0, true);
  refreshSoon();
}
function resumeDev() { killswitch('off', {all: !!(F && F.killswitch && F.killswitch.scope === 'all')}); }
document.addEventListener('click', e => {
  if (e.target.closest('[data-hide-result]')) { $('resume-result').style.display = 'none'; return; }
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = {resume: resumeDev, reconnect: () => { if (typeof connect === 'function') connect(); if (typeof tickPoll === 'function') tickPoll(); },
    'ks-on': () => killswitch('on'), 'ks-hard': () => killswitch('on', {hard: true}), 'ks-all': () => killswitch('on', {all: true})}[a.dataset.act];
  if (act) act();
});

let S = null, sLoadedFor = null;
const PEND = {}, BASE = {}, ERRS = {};          // unsaved edits (key -> value), the values as rendered, the server's complaints per key
const SX = {addOpen: false, addRt: '', focus: null, msg: '', wmsg: '', keyOpen: '', busy: false, open: new Set()};
const SPAGES = ['workers', 'runtimes', 'models', 'project', 'money', 'knowledge', 'limits', 'advanced'];
const PAGETITLE = {workers: 'Workers', runtimes: 'Runtimes', models: 'Models', project: 'Project', money: 'Money', knowledge: 'Knowledge', limits: 'Limits', advanced: 'Advanced'};
// which page shows which setting (a setting is on exactly one page)
const PAGEKEYS = {
  workers: 'WORKERS API_WORKERS_POLICY',
  runtimes: 'COMPAT_BASE_URL',
  models: 'PLAN_PROVIDERS REVIEW_PROVIDERS CLAUDE_MODEL AGY_PLAN_MODEL AGY_REVIEW_MODEL AGY_FALLBACK_MODEL ANTHROPIC_MODEL OPENAI_API_BASE OPENAI_MODEL GEMINI_API_BASE GEMINI_MODEL ' +
    'WORKER_MODEL WORKER_MODEL_RISKY WORKER_MODEL_LIGHT LIGHT_ROLES WORKER_MODEL_CLAUDE WORKER_MODEL_CODEX WORKER_MODEL_GEMINI WORKER_MODEL_COMPAT WORKER_MODEL_AIDER ' +
    'MODEL_ROUTING ROUTING_MIN_TASKS ROUTING_MIN_FIRST_PASS AB_MODEL AB_SHARE CONTEXT_PACK CONTEXT_PACK_CHARS',
  project: 'UPSTREAM_BRANCH UPSTREAM_MERGE APPROVE AUTOPILOT AUTO_MERGE CONTINUE_CONVERSATIONS PLAN_AHEAD MAINTENANCE OWNER_LANG TASK_LANG PRODUCT_LANG DOCS_LANG LOCAL_TZ PROJECT_DIR VISION_FILE SHARED_FILES ' +
    'TEST_CMD GATE_IMAGE NOTIFY_URL NOTIFY_FORMAT NOTIFY_EVENTS',
  money: 'API_RUN_BUDGET_USD API_DAILY_BUDGET_USD API_MONTHLY_BUDGET_USD PROJECT_DAILY_BUDGET_USD PROJECT_MONTHLY_BUDGET_USD WORKER_DAILY_BUDGET_USD RUN_MAX_TOOL_CALLS PRICE_CODEX PRICE_GEMINI PRICE_COMPAT',
  limits: 'MAX_PARALLEL RUN_TOKEN_CAP RUN_TIMEOUT_MIN RUN_TIMEOUT_MAX_MIN DAILY_TOKEN_BUDGET MAX_RUNS_PER_DAY QUOTA_MODEL_FILTER QUOTA_MIN_5H_PCT QUOTA_MIN_WEEK_PCT CONSEC_FAIL_TRIP POLL_SEC',
  advanced: 'LOOP_SEC ORPHAN_LIMIT ORPHAN_WARN TASK_TOKEN_BUDGET MAX_TASK_FILES SELF_CHECK_ROUNDS MAX_REWORK SPLIT_AFTER_CAPS MAX_ACTIVE_GOALS MAX_GOAL_ROUNDS DIFF_MAX LESSONS_MAX RUNS_KEEP_DAYS ' +
    'QUALITY_CHECK TEST_RATIO_MAX MAX_FILE_LINES ORIENT PACK_EXTRAS PROMPT_ORDER ' +
    'OPENCODE_MODEL QWEN_MODEL GOOSE_MODEL'};
const KEYPAGE = {};
for (const [pg, ks] of Object.entries(PAGEKEYS)) ks.split(' ').forEach(k => { KEYPAGE[k] = pg; });
// the secret files -> the page where they are set
const SECRETPAGE = {'anthropic.key': 'runtimes', 'openai.key': 'runtimes', 'gemini.key': 'runtimes', 'compat.key': 'runtimes', 'notify.token': 'project'};
if (!SPAGES.includes(SUB.settings)) SUB.settings = {onboarding: 'runtimes'}[SUB.settings] || 'workers';

const LABEL = {PLAN_PROVIDERS: 'Who plans (in order)', REVIEW_PROVIDERS: 'Who reviews (in order)', CLAUDE_MODEL: 'Claude Code model',
  AGY_PLAN_MODEL: 'Antigravity model for planning', AGY_REVIEW_MODEL: 'Antigravity model for reviews', AGY_FALLBACK_MODEL: 'Antigravity fallback model',
  WORKER_MODEL: 'Worker model', WORKER_MODEL_RISKY: 'Model for risky tasks', WORKER_MODEL_LIGHT: 'Model for light roles', LIGHT_ROLES: 'Light roles',
  ANTHROPIC_MODEL: 'Anthropic API model', OPENAI_MODEL: 'OpenAI API model', OPENAI_API_BASE: 'OpenAI API address', GEMINI_MODEL: 'Gemini API model', GEMINI_API_BASE: 'Gemini API address',
  OWNER_LANG: 'Language for you (goals, questions, notes)', TASK_LANG: 'Language of tasks and reviews', PRODUCT_LANG: 'Language of the product\'s own texts', DOCS_LANG: 'Language of code comments and commits', LOCAL_TZ: 'Time zone', PROJECT_DIR: 'Work only in this folder', VISION_FILE: 'Vision file', SHARED_FILES: 'Files tasks may share',
  LOOP_SEC: 'Foreman interval (seconds)', ORPHAN_LIMIT: 'Block a task after N crashed runs', ORPHAN_WARN: 'Warn after N crashed runs', WORKERS: 'Workers of this project',
  AUTOPILOT: 'Autopilot', AUTO_MERGE: 'Merge automatically', CONTINUE_CONVERSATIONS: 'Reworks continue the worker\'s conversation', PLAN_AHEAD: 'Plan ahead while workers wait',
  MAINTENANCE: 'Check main while idle', MAX_REWORK: 'Reworks per task, at most', MAX_ACTIVE_GOALS: 'Active goals, at most', MAX_GOAL_ROUNDS: 'Rounds per goal, at most',
  DIFF_MAX: 'Largest diff shown to the reviewer (bytes)', LESSONS_MAX: 'Lessons in each worker prompt, at most', SPLIT_AFTER_CAPS: 'Split a task after N token caps',
  TASK_TOKEN_BUDGET: 'Target task size (tokens)', MAX_TASK_FILES: 'Files per task, at most', SELF_CHECK_ROUNDS: 'Self-check rounds',
  CONTEXT_PACK: 'Code summary in each task', CONTEXT_PACK_CHARS: 'Summary size, at most (characters)',
  QUALITY_CHECK: 'Quality checks', TEST_RATIO_MAX: 'Test code per code line, at most', MAX_FILE_LINES: 'Source file length, at most (lines)',
  ORIENT: 'Tell the worker where it stands', PACK_EXTRAS: 'Line spans and usages in the summary', PROMPT_ORDER: 'Order of the worker prompt', AB_MODEL: 'Model to compare', AB_SHARE: 'Share of tasks on it (%)',
  TEST_CMD: 'Test command', GATE_IMAGE: 'Test image', NOTIFY_URL: 'Send notifications to', NOTIFY_FORMAT: 'Message format', NOTIFY_EVENTS: 'Tell me when',
  RUNS_KEEP_DAYS: 'Compress run transcripts after (days)', API_WORKERS_POLICY: 'When API workers get work',
  WORKER_MODEL_CLAUDE: 'Claude Code worker model', WORKER_MODEL_CODEX: 'Codex worker model', WORKER_MODEL_GEMINI: 'Gemini CLI worker model',
  WORKER_MODEL_COMPAT: 'Model on your server (Codex)', WORKER_MODEL_AIDER: 'Model on your server (Aider)', COMPAT_BASE_URL: 'Address of your model server',
  PRICE_COMPAT: 'Your model server (USD / 1M tokens)', PRICE_CODEX: 'Codex (USD / 1M tokens)', PRICE_GEMINI: 'Gemini CLI (USD / 1M tokens)',
  API_RUN_BUDGET_USD: 'One run, at most', API_DAILY_BUDGET_USD: 'Per day, all projects', API_MONTHLY_BUDGET_USD: 'Per month, all projects',
  PROJECT_DAILY_BUDGET_USD: 'Per day, this project', PROJECT_MONTHLY_BUDGET_USD: 'Per month, this project', WORKER_DAILY_BUDGET_USD: 'Per day, per worker',
  RUN_MAX_TOOL_CALLS: 'Tool calls per run, at most', APPROVE: 'Wait for my OK before', MODEL_ROUTING: 'Model routing',
  ROUTING_MIN_TASKS: 'Merged tasks a model needs per role', ROUTING_MIN_FIRST_PASS: 'Approved first time, at least (%)',
  FORGE_KIND: 'Where the code lives', UPSTREAM_REPO: 'Repository', UPSTREAM_URL: 'Forge address', UPSTREAM_BRANCH: 'Branch on GitHub / GitLab', UPSTREAM_MERGE: 'How changes are merged there',
  FORGE_URL: 'Internal Git forge address', FORGE_REPO: 'Internal repository',
  MAX_PARALLEL: 'Runs at the same time, at most', RUN_TOKEN_CAP: 'Token cap per run', RUN_TIMEOUT_MIN: 'Normal time limit per run (minutes)',
  RUN_TIMEOUT_MAX_MIN: 'Longest time limit per run (minutes)', DAILY_TOKEN_BUDGET: 'Tokens per day', MAX_RUNS_PER_DAY: 'Runs per day',
  QUOTA_MODEL_FILTER: 'Model family whose quota is checked', QUOTA_MIN_5H_PCT: 'Minimum 5-hour quota left (%)', QUOTA_MIN_WEEK_PCT: 'Minimum weekly quota left (%)',
  CONSEC_FAIL_TRIP: 'Kill switch after N failed runs in a row', POLL_SEC: 'Run check interval (seconds)',
  OPENCODE_MODEL: 'OpenCode model', QWEN_MODEL: 'Qwen Code model', GOOSE_MODEL: 'Goose model', WORKER_RUNTIME: 'Default worker runtime'};
const HELP = {
  PLAN_PROVIDERS: 'Who writes the plan for a goal. Tick the ones to use and put them in order with the arrows; the first one that still has quota does the work.',
  REVIEW_PROVIDERS: 'Who reviews finished work. Same rule: the ticked ones, from the top.',
  WORKER_RUNTIME: 'The runtime a new worker starts with.',
  CLAUDE_MODEL: 'Which Claude model Claude Code uses when it plans or reviews.',
  AGY_PLAN_MODEL: 'The Antigravity model that writes plans.',
  AGY_REVIEW_MODEL: 'The Antigravity model that reviews work.',
  AGY_FALLBACK_MODEL: 'Used instead when the main Antigravity model is out of quota.',
  WORKER_MODEL: 'The Antigravity model workers use for ordinary tasks.',
  WORKER_MODEL_RISKY: 'A stronger model for risky tasks (security, money, migrations). Empty = the same as for ordinary tasks.',
  WORKER_MODEL_LIGHT: 'A cheaper model for the light roles below. Empty = the same as for ordinary tasks.',
  LIGHT_ROLES: 'Roles whose tasks are simple enough for the cheaper model.',
  ANTHROPIC_MODEL: 'The model used when the Anthropic API plans or reviews (paid per token), e.g. claude-sonnet-4-6.',
  OPENAI_API_BASE: 'Where the OpenAI API lives. Empty = OpenAI itself (https://api.openai.com/v1).',
  OPENAI_MODEL: 'The model used when the OpenAI API plans or reviews (paid per token), e.g. gpt-4o.',
  GEMINI_API_BASE: 'Where the Gemini API lives. Empty = Google (https://generativelanguage.googleapis.com).',
  GEMINI_MODEL: 'The model used when the Gemini API plans or reviews (paid per token), e.g. gemini-2.5-pro.',
  OPENAI_API_KEY: 'Set on the Runtimes page.',
  GEMINI_API_KEY: 'Set on the Runtimes page.',
  OWNER_LANG: 'Language the AI team writes in when it formulates goals, questions, summaries and notes for you. Independent of the dashboard language below.',
  TASK_LANG: 'Language of task titles, specs and acceptance criteria, review feedback and everything the orchestrator tells the workers. English is usually the most accurate and the cheapest in tokens.',
  PRODUCT_LANG: 'Language of the texts the product\'s own users read (labels, messages, help). Empty = same as your language above.',
  DOCS_LANG: 'Language of code comments, docstrings, commit messages and developer documentation.',
  LOCAL_TZ: 'Time zone for server logs, CLI reports, and notifications (click Detect to use your browser time zone).',
  PROJECT_DIR: 'Work only inside this folder of the repository. Empty = the whole repository.',
  VISION_FILE: 'The file that says what the product should become. Autopilot reads it to choose the next goal.',
  SHARED_FILES: 'Files that several tasks may change at the same time (a registry, for example). Separate with commas.',
  LOOP_SEC: 'How often the foreman looks for work. Shorter = quicker reactions, more load.',
  ORPHAN_LIMIT: 'A task is blocked after this many crashed runs in a row without any progress.',
  ORPHAN_WARN: 'You get a warning after this many crashed runs in a row (before the task is blocked).',
  WORKERS: 'Ticked workers take this project\'s tasks. None ticked = every worker.',
  AUTOPILOT: 'When nothing is left to do, choose the next goal from the vision file.',
  AUTO_MERGE: 'Merge approved work that passes the tests without waiting for you.',
  CONTINUE_CONVERSATIONS: 'On a rework the worker continues its earlier conversation (cheaper) instead of starting over - as long as that conversation is still small.',
  MAX_REWORK: 'How many times a task goes back to its worker after a rejected review before it is paused.',
  MAX_ACTIVE_GOALS: 'How many goals are worked on at the same time.',
  MAX_GOAL_ROUNDS: 'How many plan-and-review rounds one goal gets before it is paused.',
  DIFF_MAX: 'The largest change the reviewer sees, in bytes; the rest is cut off.',
  LESSONS_MAX: 'How many recent lessons from rejected work are put into each worker prompt.',
  SPLIT_AFTER_CAPS: 'A task that hit the token cap this many times is split into smaller tasks.',
  TASK_TOKEN_BUDGET: 'How big the planner aims to make one task, in tokens.',
  CONTEXT_PACK: 'A short summary of the code that every task starts with. “ab” gives it to half of the tasks, so you can measure what it brings.',
  CONTEXT_PACK_CHARS: 'The longest that summary may be, in characters.',
  QUALITY_CHECK: 'Scripted checks on finished work, with no AI involved: far more test code than code, new source files nothing uses, over-long files. “warn” tells the reviewer, “block” sends the work back, “off” skips the checks.',
  TEST_RATIO_MAX: 'Tests may add at most this many times the code lines of the change (but never less than 150 lines).',
  MAX_FILE_LINES: 'A source file longer than this that a task touches is flagged, because workers re-read big files every time.',
  ORIENT: 'The supervisor puts the worker on its task branch and says what is already done, so the worker spends no steps on git commands.',
  PACK_EXTRAS: 'Adds line ranges and “who uses what you change” to the code summary, and the branch diff on a rework. Off = the earlier, shorter prompts.',
  PROMPT_ORDER: 'Experimental. “cache” puts the text that is the same for every task first, so providers with prompt caches can reuse it.',
  SELF_CHECK_ROUNDS: 'How many times a worker may fix failing tests itself before handing in the work. 0 = never.',
  MAX_TASK_FILES: 'A task that would change more files than this is split first.',
  AB_MODEL: 'A second worker model to try on ordinary tasks. Empty = no experiment.',
  AB_SHARE: 'The share of ordinary tasks that go to that model, in percent.',
  PLAN_AHEAD: 'While every worker waits for quota, keep planning the next goals.',
  MAINTENANCE: 'While idle, run the tests on main (no AI involved) and report problems.',
  TEST_CMD: 'Runs the project\'s tests - in the gate, in the worker\'s self-check and on main. Empty = pytest.',
  GATE_IMAGE: 'The Docker image the tests run in. Empty = the worker image.',
  RUNS_KEEP_DAYS: 'Run transcripts older than this are compressed to save disk space.',
  NOTIFY_URL: 'Where notifications go: an ntfy topic address, a Slack or Discord webhook, or “outbox” (the foreman appends to a file that something else of yours forwards). Empty = no notifications.',
  NOTIFY_FORMAT: 'How the message is written for that service. Chosen for you when the address is a Slack or Discord webhook.',
  NOTIFY_EVENTS: 'What you are told about.',
  API_WORKERS_POLICY: 'Workers that pay per token take tasks only when no subscription worker is free (overflow), or are preferred (always).',
  WORKER_MODEL_CLAUDE: 'The model Claude Code workers use, e.g. sonnet. Empty = the CLI\'s default.',
  WORKER_MODEL_CODEX: 'The model Codex workers use, e.g. gpt-5.1-codex. Empty = the CLI\'s default.',
  WORKER_MODEL_GEMINI: 'The model Gemini CLI workers use. Empty = the CLI\'s default.',
  WORKER_MODEL_COMPAT: 'The model on your own server for Codex workers, e.g. qwen3-coder:30b.',
  WORKER_MODEL_AIDER: 'The model on your own server for Aider workers, e.g. qwen3-coder:30b.',
  COMPAT_BASE_URL: 'The address of your own model server (Ollama, vLLM, LM Studio, OpenRouter…): an OpenAI-compatible URL, e.g. http://192.168.1.20:11434/v1.',
  PRICE_COMPAT: 'For the cost estimate only: input, cached input and output price. 0 / 0 / 0 for your own hardware.',
  PRICE_CODEX: 'For the cost estimate only: input, cached input and output price (e.g. 1.25 / 0.125 / 10).',
  PRICE_GEMINI: 'For the cost estimate only: input, cached input and output price (e.g. 1.25 / 0.31 / 10).',
  API_RUN_BUDGET_USD: 'The most one API worker run may cost.',
  API_DAILY_BUDGET_USD: 'The most all API workers of all projects may cost per day.',
  API_MONTHLY_BUDGET_USD: 'The most all API workers of all projects may cost per calendar month. 0 = no limit.',
  PROJECT_DAILY_BUDGET_USD: 'The most this project\'s API workers may cost per day. 0 = no limit.',
  PROJECT_MONTHLY_BUDGET_USD: 'The most this project\'s API workers may cost per calendar month. 0 = no limit.',
  WORKER_DAILY_BUDGET_USD: 'A daily cap for each API worker on its own. Empty = no cap.',
  RUN_MAX_TOOL_CALLS: 'A run is stopped after this many tool calls.',
  APPROVE: 'What waits for your OK under Overview → Needs you. Nothing ticked = fully automatic.',
  MODEL_ROUTING: 'With auto, every task gets the cheapest worker model that has proven itself in its role.',
  ROUTING_MIN_TASKS: 'A model needs this many merged tasks in a role before auto-routing trusts it.',
  ROUTING_MIN_FIRST_PASS: 'And it must have been approved on the first review at least this often.',
  UPSTREAM_BRANCH: 'The branch on GitHub / GitLab that main is kept in sync with.',
  UPSTREAM_MERGE: 'How approved changes are merged on GitHub / GitLab.',
  FORGE_URL: 'The address of the internal Git forge. It is set in etc/forge.conf; to change it, edit the file and restart the foreman.',
  FORGE_REPO: 'The repository on the internal forge. It is set when the project is created; changing it needs a restart of the foreman.',
  MAX_PARALLEL: 'How many runs may go at the same time, over all projects.',
  RUN_TOKEN_CAP: 'A run is stopped when it has used this many tokens.',
  RUN_TIMEOUT_MIN: 'The normal time limit of one run. A task that needs longer can get more, up to the longest limit below.',
  RUN_TIMEOUT_MAX_MIN: 'The hard limit: no run may take longer than this.',
  DAILY_TOKEN_BUDGET: 'No new run starts once all projects together have used this many tokens today (UTC).',
  MAX_RUNS_PER_DAY: 'No new run starts after this many runs today (UTC), all projects together.',
  QUOTA_MODEL_FILTER: 'The model family whose quota is checked before a run starts, e.g. Gemini.',
  QUOTA_MIN_5H_PCT: 'A worker is skipped while its 5-hour quota is below this many percent.',
  QUOTA_MIN_WEEK_PCT: 'A worker is skipped while its weekly quota is below this many percent.',
  CONSEC_FAIL_TRIP: 'The kill switch trips by itself after this many failed runs in a row.',
  POLL_SEC: 'How often a running run is checked against its limits.',
  OPENCODE_MODEL: 'The model OpenCode uses, e.g. gpt-4o. Empty = its default. (OpenCode workers cannot be added from here yet.)',
  QWEN_MODEL: 'The model Qwen Code uses, e.g. qwen-max. Empty = its default. (Qwen Code workers cannot be added from here yet.)',
  GOOSE_MODEL: 'The model Goose uses, e.g. gpt-4o. Empty = its default. (Goose workers cannot be added from here yet.)'};
const PROVLABEL = {'claude-cli': 'Claude Code (subscription)', 'agy-claude': 'Claude via Antigravity', 'agy-gemini': 'Gemini via Antigravity',
  'anthropic-api': 'Anthropic API (paid)', 'openai-api': 'OpenAI API (paid)', 'gemini-api': 'Gemini API (paid)'};
const EVENTS = [['goal_done', 'a goal is done'], ['approval', 'something waits for my OK'], ['task_blocked', 'a task is blocked'], ['questions', 'the planner has a question'],
  ['killswitch', 'the kill switch tripped'], ['quota_out', 'every worker is out of quota'], ['stalled', 'tasks are ready but nothing starts'], ['auth', 'a key was rejected'], ['project_done', 'the project is finished']];
const SECRETS = {'anthropic.key': ['Anthropic API', 'pays per token; used by Claude Code workers and the Anthropic API planner'],
  'openai.key': ['OpenAI API', 'pays per token; used by Codex workers and the OpenAI API planner'],
  'gemini.key': ['Gemini API', 'pays per token; used by Gemini CLI workers and the Gemini API planner'],
  'compat.key': ['Your model server', 'only if your server wants one (OpenRouter does, Ollama does not)']};

// ---- loading
async function loadSettings(force) {
  if (!force && sLoadedFor === project && S) return;
  sLoadedFor = project;
  try { S = await getJSON('/api/settings', force ? {fresh: 1} : {}); } catch (e) { S = {error: String(e)}; }
  schedule();
}
const V = () => (S && S.values) || {};
const cur = k => { const v = V()[k]; return v == null ? '' : v; };

// ---- reading a setting back from the page (and the inverse, to put an unsaved edit back after a redraw)
function sval(k) {
  const el = $('set-' + k); if (!el) return null;
  const kind = el.dataset.kind;
  if (kind === 'prov') return [...el.querySelectorAll('.prov')].filter(r => r.querySelector('input').checked).map(r => r.dataset.p).join(',');
  if (kind === 'pool' || kind === 'multi') return [...el.querySelectorAll('input:checked')].map(i => i.value).join(',');
  if (kind === 'wbudget') {
    const keep = Object.fromEntries(String(cur(k)).split(',').filter(x => x.includes('=')).map(x => x.split('=')));
    el.querySelectorAll('input').forEach(i => { if (i.value.trim() && Number(i.value) > 0) keep[i.dataset.w] = i.value.trim(); else delete keep[i.dataset.w]; });
    return Object.entries(keep).map(([w, v]) => w + '=' + v).join(',');
  }
  if (kind === 'triple') { const p = [...el.querySelectorAll('input')].map(i => i.value.trim()); return p.some(Boolean) ? p.join(',') : ''; }
  if (el.type === 'checkbox') return el.checked ? '1' : '0';
  return el.value.trim();
}
function setVal(k, v) {
  const el = $('set-' + k); if (!el) return;
  const kind = el.dataset.kind, on = String(v).split(',').filter(Boolean);
  if (kind === 'prov') {
    const rows = [...el.querySelectorAll('.prov')], by = Object.fromEntries(rows.map(r => [r.dataset.p, r]));
    on.filter(p => by[p]).concat(rows.map(r => r.dataset.p).filter(p => !on.includes(p))).forEach(p => el.appendChild(by[p]));
    rows.forEach(r => { r.querySelector('input').checked = on.includes(r.dataset.p); });
  } else if (kind === 'pool' || kind === 'multi') el.querySelectorAll('input[type=checkbox]').forEach(i => { i.checked = on.includes(i.value); });
  else if (kind === 'wbudget') { const m = Object.fromEntries(String(v).split(',').filter(x => x.includes('=')).map(x => x.split('='))); el.querySelectorAll('input').forEach(i => { i.value = m[i.dataset.w] || ''; }); }
  else if (kind === 'triple') { const p = String(v).split(','); el.querySelectorAll('input').forEach((i, n) => { i.value = (p[n] || '').trim(); }); }
  else if (el.type === 'checkbox') el.checked = String(v) === '1';
  else {
    if (el.tagName === 'SELECT' && v !== '' && ![...el.options].some(o => o.value === v)) el.add(new Option(v, v));
    el.value = v;
  }
}

// ---- controls
const unitTag = u => u ? ' <span class="muted small">' + esc(u) + '</span>' : '';
const num = (k, ph, unit) => { const r = ((S && S.ranges) || {})[k];
  return '<span class="unit"><input type="number" inputmode="numeric" step="1" id="set-' + k + '" value="' + esc(cur(k)) + '"' + (r ? ' min="' + r[0] + '" max="' + r[1] + '"' : '') +
    (ph != null ? ' placeholder="' + esc(ph) + '"' : '') + '>' + unitTag(unit) + '</span>'; };
const money = (k, ph) => '<span class="unit"><input type="number" inputmode="decimal" min="0" step="0.01" id="set-' + k + '" value="' + esc(cur(k)) + '"' + (ph != null ? ' placeholder="' + esc(ph) + '"' : '') + '>' + unitTag('USD') + '</span>';
const txt = (k, ph, list) => '<input type="text" id="set-' + k + '" value="' + esc(cur(k)) + '"' + (ph ? ' placeholder="' + esc(ph) + '"' : '') + (list ? ' list="' + list + '"' : '') + ' autocomplete="off" spellcheck="false">';
const tog = k => '<label class="switch"><input type="checkbox" id="set-' + k + '"' + (String(cur(k)) === '1' ? ' checked' : '') + '><i></i></label>';
const sel = (k, opts) => { const v = String(cur(k)), list = opts.map(o => Array.isArray(o) ? o : [o, o]);
  if (v && !list.some(([x]) => String(x) === v)) list.unshift([v, v]);
  return '<select id="set-' + k + '">' + list.map(([x, l]) => '<option value="' + esc(x) + '"' + (String(x) === v ? ' selected' : '') + '>' + esc(l) + '</option>').join('') + '</select>'; };
const multi = (k, opts) => { const on = String(cur(k)).split(',').filter(Boolean), known = opts.map(o => o[0]), all = opts.concat(on.filter(x => !known.includes(x)).map(x => [x, x]));
  return '<div id="set-' + k + '" data-kind="multi" class="multi">' + all.map(([v, l]) => '<label class="chk"><input type="checkbox" value="' + esc(v) + '"' + (on.includes(v) ? ' checked' : '') + '> ' + esc(l) + '</label>').join('') + '</div>'; };
const triple = (k, names) => { const p = String(cur(k)).split(',');
  return '<div id="set-' + k + '" data-kind="triple" class="triple">' + names.map((n, i) => '<label>' + esc(n) + ' <input type="number" inputmode="decimal" min="0" step="0.001" value="' + esc((p[i] || '').trim()) + '"></label>').join('') + ' <span class="muted small">USD / 1M tokens</span></div>'; };
function provEditor(k) {
  const on = String(cur(k)).split(',').filter(Boolean), all = on.concat(((S && S.providers) || []).filter(p => !on.includes(p)));
  if (!all.length) return '<div class="provlist" id="set-' + k + '" data-kind="prov"><div class="empty">No providers available</div></div>';
  return '<div class="provlist" id="set-' + k + '" data-kind="prov">' + all.map(p => '<div class="prov" data-p="' + esc(p) + '"><label><input type="checkbox"' +
    (on.includes(p) ? ' checked' : '') + '> ' + esc(PROVLABEL[p] || p) + '</label><span><button type="button" class="iconbtn sm" data-mv="-1" aria-label="Move ' + esc(PROVLABEL[p] || p) + ' up">↑</button><button type="button" class="iconbtn sm" data-mv="1" aria-label="Move ' + esc(PROVLABEL[p] || p) + ' down">↓</button></span></div>').join('') + '</div>';
}
function modelSel(k, allowEmpty, emptyLabel) {
  const v = cur(k), ms = ((S && S.models) || []).map(m => m.id), opts = (allowEmpty ? [''] : []).concat(ms.includes(v) || !v ? ms : [v].concat(ms));
  return '<select id="set-' + k + '">' + opts.map(m => '<option value="' + esc(m) + '"' + (m === v ? ' selected' : '') + '>' +
    esc(m ? (((S && S.models) || []).find(x => x.id === m) || {name: m}).name + (((S && S.models) || []).find(x => x.id === m) ? ' (' + m + ')' : '') : emptyLabel) + '</option>').join('') + '</select>';
}
const field = (k, inner, help) => { const h = help === undefined ? HELP[k] : help;
  return '<div class="setrow" id="row-' + k + '"><label for="set-' + k + '">' + esc(LABEL[k] || k) + (h ? '<span class="help">' + esc(h) + '</span>' : '') + '</label>' +
    '<div class="setctl">' + inner + '<div class="seterr" id="err-' + k + '">' + esc(ERRS[k] || '') + '</div></div></div>'; };
const card = (title, body, intro) => '<div class="setcard"><h3>' + title + '</h3>' + (intro ? '<div class="muted small intro">' + intro + '</div>' : '') + body + '</div>';
const det = (id, title, body, hint) => '<details class="setdet" id="' + id + '"' + (SX.open.has(id) ? ' open' : '') + '><summary>' + esc(title) + (hint ? ' <span class="muted small">' + esc(hint) + '</span>' : '') + '</summary>' + body + '</details>';
const rowAct = (id, label, body) => '<div class="setrow" id="' + id + '"><label id="' + id + '-l">' + esc(label) + '</label><div class="setctl">' + body + '</div></div>';
const rtName = k => RTNAME[k] || ((S && S.runtimes && S.runtimes[k] && S.runtimes[k].label) || k);
const failed = what => '<div class="empty bad">Could not load ' + what + ': ' + esc((S && S.error) || window._ferr || 'no answer') + ' <button type="button" class="mini" data-retry>Try again</button></div>';

// ---- unsaved edits: collected here, saved together from the bar at the bottom
function pendPages() { const pg = []; for (const k of Object.keys(PEND)) { const p = KEYPAGE[k] || 'workers'; if (!pg.includes(p)) pg.push(p); } return pg; }
function notePend(k) {
  const v = sval(k); if (v === null) return;
  if (v === BASE[k]) delete PEND[k]; else PEND[k] = v;
  delete ERRS[k]; const e = $('err-' + k); if (e) e.textContent = '';
  if (SX.msg) SX.msg = '';
  updateBar();
}
function updateBar() {
  const bar = $('savebar'); if (!bar) return;
  const n = Object.keys(PEND).length, pg = pendPages();
  bar.hidden = !n && !SX.msg;
  $('save-count').textContent = n ? n + (n === 1 ? ' unsaved change' : ' unsaved changes') + (pg.length ? ' · ' + pg.map(p => PAGETITLE[p]).join(', ') : '') : '';
  $('save-msg').textContent = SX.msg;
  $('save-settings').disabled = !n || SX.busy; $('discard-settings').disabled = !n || SX.busy;
}
function afterRender() {
  document.querySelectorAll('#settings [id^="set-"]').forEach(el => { const k = el.id.slice(4), v = sval(k); if (v !== null) BASE[k] = v; });
  for (const [k, v] of Object.entries(PEND)) if ($('set-' + k)) setVal(k, v);
  const first = Object.keys(ERRS).find(k => $('set-' + k));
  if (first) { const d = $('set-' + first).closest('details'); if (d) { d.open = true; SX.open.add(d.id); } }
  updateBar();
  if (SX.focus) focusRow();
}
function discardPend() { for (const k of Object.keys(PEND)) delete PEND[k]; for (const k of Object.keys(ERRS)) delete ERRS[k]; SX.msg = ''; schedule(true); }
document.addEventListener('input', e => { const el = e.target.closest && e.target.closest('#settings [id^="set-"]'); if (el) noteEdit(el); });
document.addEventListener('change', e => { const el = e.target.closest && e.target.closest('#settings [id^="set-"]'); if (el) noteEdit(el); });
function noteEdit(el) {
  const k = el.id.slice(4); if (!(k in V())) return;
  notePend(k);
  if (k === 'NOTIFY_URL') {                                 // a Slack / Discord webhook is recognised by its address
    const u = el.value, f = /hooks\.slack\.com/.test(u) ? 'slack' : /discord(app)?\.com\/api\/webhooks/.test(u) ? 'discord' : '';
    if (f && sval('NOTIFY_FORMAT') !== f) { setVal('NOTIFY_FORMAT', f); notePend('NOTIFY_FORMAT'); }
  }
}
// the <details> the owner opened stay open when the page is drawn again
document.addEventListener('toggle', e => { const d = e.target; if (d && d.classList && d.classList.contains('setdet') && d.id) { if (d.open) SX.open.add(d.id); else SX.open.delete(d.id); } }, true);

// ---- go to one setting (from "Fix it" buttons in System, from error messages, from other pages)
function settingsGoto(id) {
  id = String(id || '').replace(/^settings:/, '');
  let page, row;
  if (id.startsWith('key:')) {
    let n = id.slice(4).toLowerCase().replace(/_api_key$/, '.key');
    page = SECRETPAGE[n] || 'runtimes'; row = 'secret-' + n;
  } else { page = KEYPAGE[id] || 'workers'; row = 'row-' + id; }
  SUB.settings = page; store('af-sub', JSON.stringify(SUB));
  SX.focus = {row, until: Date.now() + 8000};
  setTab('settings');
}
function focusRow() {
  const f = SX.focus; if (!f) return;
  const row = $(f.row);
  if (!row) { if (Date.now() > f.until) SX.focus = null; else setTimeout(() => schedule(), 400); return; }
  SX.focus = null;
  for (let d = row.closest('details'); d; d = d.parentElement && d.parentElement.closest('details')) { d.open = true; if (d.id) SX.open.add(d.id); }
  row.classList.add('flash'); setTimeout(() => row.classList.remove('flash'), 2600);
  try { row.scrollIntoView({block: 'center', behavior: 'smooth'}); } catch (e) {}
  const inp = row.querySelector('input:not([type=checkbox]):not([type=radio]), select, textarea, button'); if (inp) { try { inp.focus({preventScroll: true}); } catch (e) {} }
}

// ---- Workers
const jobWait = async (j, tick) => {
  while (j && j.state === 'running') {
    if (tick) tick(j);
    await new Promise(r => setTimeout(r, 3000));
    try { j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); } catch (e) { return {state: 'failed', error: String(e)}; }
  }
  return j || {state: 'failed', error: 'no answer'};
};
const wstatus = t => { SX.wmsg = t; const e = $('workers-status'); if (e) e.textContent = t; };
const addable = () => Object.entries((S && S.runtimes) || {}).filter(([, r]) => r.creatable).sort((a, b) => (a[0] === 'agy' ? -1 : 0) - (b[0] === 'agy' ? -1 : 0));
function killCard() {
  const ks = F && F.killswitch;
  return card('Kill switch', '<div class="btnrow">' + (ks ? '<button class="primary sm" data-act="resume">Continue</button>' :
      '<button class="warnbtn" data-act="ks-on">Stop this project</button><button class="badbtn" data-act="ks-hard">Stop hard</button>') +
      '<button class="badbtn ghost" data-act="ks-all">Stop all projects</button></div>',
    ks ? '<b class="bad">ON</b>' + (ks.scope === 'all' ? ' for all projects' : ' for this project') + (ks.hard ? ' (hard)' : '') + ' — ' + esc(ks.reason || '') :
      'Off - the project is running. “Stop hard” also stops the worker containers.');
}
function workerRow(w, pool) {
  const rt = w.runtime || 'agy', up = w.state === 'running';
  const acc = w.account || (P && P.roster && (P.roster['worker:' + w.name] || {}).account) || '';
  const rnode = P && P.roster && P.roster['worker:' + w.name];
  const q = (rnode && rnode.quota) || [];
  const fam = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
  const low = (rows, re) => { const v = rows.filter(x => re.test(x.label)); return v.length ? Math.min(...v.map(x => x.pct)) : null; };
  const qparts = [...new Set(q.map(x => fam(x.model)))].map(f => {
    const rows = q.filter(x => fam(x.model) === f), h5 = low(rows, /five hour|5h/i), wk = low(rows, /week/i);
    const bits = [h5 == null ? '' : h5.toFixed(0) + '%', wk == null ? '' : 'week ' + wk.toFixed(0) + '%'].filter(Boolean);
    return bits.length ? f + ' ' + bits.join(' · ') : '';
  }).filter(Boolean);
  const qstr = qparts.length ? ' · ' + qparts.join(' · ') : '';
  const accInfo = acc ? '<span class="worker-account muted small" title="Account / email" style="margin-right:8px">' + esc(acc) + (qstr ? ' <span class="badge" style="font-size:11px">' + esc(qstr.slice(3)) + '</span>' : '') + '</span>' : (qstr ? '<span class="badge" style="font-size:11px;margin-right:8px">' + esc(qstr.slice(3)) + '</span>' : '');

  return '<div class="poolrow" data-worker="' + esc(w.name) + '"><label class="chk"><input type="checkbox" value="' + esc(w.name) + '"' + (pool.includes(w.name) ? ' checked' : '') + '> <b>' + esc(w.name) + '</b></label>' +
    '<span class="poolinfo">' + accInfo + '<span class="rt rt-' + esc(rt) + '">' + esc(rtName(rt)) + '</span> <span class="badge ' + (up ? 'ok' : 'warn') + '">' + (up ? 'running' : esc(w.state || 'stopped')) + '</span></span>' +
    '<span class="rowact">' + (rt === 'agy' ? '<button type="button" class="mini" data-login="' + esc(w.name) + '">Sign in…</button>' : '') +
    '<button type="button" class="mini ghost" data-wremove="' + esc(w.name) + '"' + (SX.busy ? ' disabled' : '') + ' aria-label="Remove ' + esc(w.name) + '">Remove</button></span></div>';
}
function addWorkerForm() {
  const list = addable(), sec = S.secrets || {};
  if (!list.length) return '<div class="empty">No runtime can be added from here.</div>';
  if (!list.some(([k]) => k === SX.addRt)) SX.addRt = list[0][0];
  const r = S.runtimes[SX.addRt] || {}, needKey = r.key && !sec[r.key], mk = {compat: 'WORKER_MODEL_COMPAT', aider: 'WORKER_MODEL_AIDER'}[SX.addRt];
  return '<div id="addw-form" class="addw"><div class="muted small">What should the new worker run on?</div>' +
    list.map(([k, x]) => '<label class="chk block"><input type="radio" name="addw-rt" value="' + esc(k) + '"' + (k === SX.addRt ? ' checked' : '') + '> <b>' + esc(rtName(k)) + '</b> <span class="muted">— ' + esc(RTDESC[k] || x.label) + '</span></label>').join('') +
    (r.endpoint ? '<div id="addw-url"><input type="text" id="addw-urlin" placeholder="your model server, e.g. http://192.168.1.20:11434/v1" value="' + esc(SX.addUrl != null ? SX.addUrl : cur('COMPAT_BASE_URL')) + '" autocomplete="off">' +
      '<input type="text" id="addw-model" placeholder="model on it, e.g. qwen3-coder:30b" value="' + esc(SX.addModel != null ? SX.addModel : mk ? cur(mk) : '') + '" autocomplete="off"></div>' : '') +
    (needKey ? '<div id="addw-key"><input type="password" id="addw-keyin" autocomplete="off" placeholder="' + (r.optional_key ? 'API key, only if your server wants one (OpenRouter does, Ollama does not)' : 'API key (stored on the server only)') + '"></div>' : '') +
    '<div class="btnrow"><button type="button" class="primary sm" id="addw-go"' + (SX.busy ? ' disabled' : '') + '>Add the worker</button><button type="button" class="mini" id="addw-cancel">Cancel</button></div></div>';
}
function pageWorkers() {
  const pool = String(cur('WORKERS')).split(',').filter(Boolean), ws = S.workers || [], hasApi = ws.some(w => ((S.runtimes || {})[w.runtime] || {}).api);
  const rows = ws.length ? ws.map(w => workerRow(w, pool)).join('') : '<div class="empty">No workers yet - add the first one.</div>';
  return killCard() +
    card('Workers', '<div id="set-WORKERS" data-kind="pool" class="pool">' + rows + '</div><div class="seterr" id="err-WORKERS">' + esc(ERRS.WORKERS || '') + '</div>' +
      '<div class="btnrow"><button type="button" class="mini" id="add-worker">' + (SX.addOpen ? '− Close' : '+ Add a worker') + '</button><span id="workers-status" class="muted small">' + esc(SX.wmsg) + '</span></div>' +
      (SX.addOpen ? addWorkerForm() : ''),
      'Ticked workers take the tasks of project “' + esc(project) + '” (none ticked = all of them). Each worker is its own Antigravity account, API key or connection to your model server.') +
    (hasApi ? card('Paid API workers', field('API_WORKERS_POLICY', sel('API_WORKERS_POLICY', [['overflow', 'only when no subscription worker is free'], ['always', 'first']])) + '<div class="muted small">Budgets for them: <a href="#" data-goto="money">Money</a>.</div>') : '');
}

// ---- Runtimes: what can run, and the keys it needs
const RT = {data: null, status: null, rec: null, err: null, at: 0, busy: false};
async function loadRuntimes(force) {
  if (RT.busy) return;
  if (!force && RT.at && Date.now() - RT.at < 60000) return;
  RT.busy = true; RT.at = Date.now();
  const q = force ? {fresh: 1} : {};
  const [a, b, c] = await Promise.allSettled([getJSON('/api/readiness', q), getJSON('/api/readiness/status', q), getJSON('/api/readiness/recommend', q)]);
  const ok = r => r.status === 'fulfilled' && r.value && !(r.value.error && !r.value.runtimes && !r.value.providers && !r.value.top && !r.value.ranked) ? r.value : null;
  RT.data = ok(a); RT.status = ok(b); RT.rec = ok(c);
  RT.err = !RT.data && !RT.status ? String((a.reason && a.reason.message) || (a.value && a.value.error) || 'not available') : null;
  RT.busy = false; RT.at = Date.now();
  schedule();
}
const READY = {ok: ['ok', 'Ready'], missing_setting: ['warn', 'Needs setup'], missing_egress: ['warn', 'Blocked by the egress list']};
function rtBadge(k) {
  const rd = ((RT.data && RT.data.runtimes) || {})[k], st = ((RT.status && RT.status.runtimes) || {})[k];
  if (rd && READY[String(rd.state)]) return '<span class="badge ' + READY[rd.state][0] + '" data-state="' + esc(rd.state) + '">' + READY[rd.state][1] + '</span>';
  if (st && st.ready === true) return '<span class="badge ok" data-state="ok">Ready</span>';
  if (st && st.ready === false) return '<span class="badge warn" data-state="not_ready">Not ready</span>';
  return '<span class="badge" data-state="unknown">' + (RT.busy ? 'checking…' : 'unknown') + '</span>';
}
function rtCost(k) {
  const st = ((RT.status && RT.status.runtimes) || {})[k], cm = st && st.cost_model;
  if (!cm || typeof cm !== 'object') return '';
  const kind = String(cm.kind || '').toLowerCase();
  return kind === 'self-hosted' || /self-hosted/i.test(cm.detail || '') ? 'your own hardware, no API cost' : kind === 'quota' ? 'uses your subscription quota' : kind === 'metered' ? 'pays per token' : '';
}
function rtRow(k, r) {
  const rd = ((RT.data && RT.data.runtimes) || {})[k] || {}, st = ((RT.status && RT.status.runtimes) || {})[k] || {};
  const why = rd.detail || st.reason || '', cost = rtCost(k), sec = S.secrets || {};
  const acts = (r.creatable ? '<button type="button" class="mini" data-addw-for="' + esc(k) + '">Add a worker</button>' : '') +
    (r.key && !sec[r.key] && SECRETS[r.key] ? '<button type="button" class="mini ghost" data-keyfor="' + esc(r.key) + '">Set the key…</button>' : '');
  return '<div class="rtrow" data-runtime="' + esc(k) + '"><div class="rtmain"><b>' + esc(rtName(k)) + '</b> ' + rtBadge(k) +
    (cost ? ' <span class="muted small">' + esc(cost) + '</span>' : '') + (why ? '<div class="muted small">' + esc(why) + '</div>' : '') + '</div><span class="rowact">' + acts + '</span></div>';
}
function keyRow(name, title, note) {
  const has = (S.secrets || {})[name], open = SX.keyOpen === name;
  return '<div class="setrow" id="secret-' + esc(name) + '" data-secret="' + esc(name) + '"><label>' + esc(title) + (note ? '<span class="help">' + esc(note) + '</span>' : '') + '</label>' +
    '<div class="setctl"><div class="keyline">' + (has ? '<b class="plus">✓ set</b>' : '<span class="muted">not set</span>') +
    ' <button type="button" class="mini" data-keyopen="' + esc(name) + '">' + (open ? 'Cancel' : has ? 'Replace…' : 'Set…') + '</button></div>' +
    (open ? '<div class="keyform"><input type="password" id="key-val" autocomplete="off" placeholder="paste it here (stored on the server only, never shown again)"><button type="button" class="primary sm" data-keysave="' + esc(name) + '">Save</button></div>' : '') + '</div></div>';
}
function pageRuntimes() {
  loadRuntimes();
  const R = S.runtimes || {}, names = Object.keys(R), ok = names.filter(k => R[k].creatable), rest = names.filter(k => !R[k].creatable);
  const rd = RT.data || {}, prov = Object.entries(rd.providers || {}), rec = RT.rec || {};
  let h = '';
  if (RT.err && !RT.busy) h += '<div class="empty bad">The readiness check is not available: ' + esc(RT.err) + ' <button type="button" class="mini" data-rtcheck>Try again</button></div>';
  h += card('What can run', (ok.length ? ok.map(k => rtRow(k, R[k])).join('') : '<div class="empty">Nothing to show.</div>') +
      (rest.length ? det('d-rt-more', 'Not available as workers yet (' + rest.length + ')', rest.map(k => rtRow(k, R[k])).join(''), 'their model setting is under Advanced') : '') +
      '<div class="btnrow"><button type="button" class="mini" data-rtcheck' + (RT.busy ? ' disabled' : '') + '>' + (RT.busy ? 'Checking…' : 'Check again') + '</button>' +
      (RT.at && !RT.busy ? '<span class="muted small">checked ' + esc(ago(RT.at / 1000)) + '</span>' : '') + '</div>',
    'Workers can run on any of these. “Ready” means the CLI, the key and the network access it needs are all in place.');
  h += card('API keys', Object.entries(SECRETS).map(([n, [t, note]]) => keyRow(n, t, note)).join(''),
    'A key is saved on the server (readable only by the dashboard) and goes only into the environment of the agent that needs it.');
  h += card('Your model server', field('COMPAT_BASE_URL', txt('COMPAT_BASE_URL', 'http://192.168.1.20:11434/v1')) +
    '<div class="muted small">Models for it: <a href="#" data-goto="models">Models</a> · key (if any): above.</div>', 'Ollama, vLLM, LM Studio, OpenRouter - anything that speaks the OpenAI API. Used by the “own server” runtimes. Applies to all projects.');
  if (prov.length) h += card('Who can plan and review', prov.map(([n, info]) => '<div class="rtrow"><div class="rtmain"><b>' + esc(PROVLABEL[n] || n) + '</b> ' +
      ((info && READY[info.state]) ? '<span class="badge ' + READY[info.state][0] + '">' + READY[info.state][1] + '</span>' : '<span class="badge">' + esc((info && info.state) || 'unknown') + '</span>') +
      (info && info.detail ? '<div class="muted small">' + esc(info.detail) + '</div>' : '') + '</div></div>').join(''),
    'Choose the order on the <a href="#" data-goto="models">Models</a> page.');
  const ranked = Array.isArray(rec.ranked) ? rec.ranked : [];
  if (rec.top || ranked.length || rec.message) {
    const top = rec.top && typeof rec.top === 'object' ? rec.top : null;
    h += card('Which one to pick', (top ? '<div class="recommend-top">Start with <b>' + esc(rtName(top.name) || top.name) + '</b>' + (top.cost_tier ? ' — ' + esc(top.cost_tier) : '') + (top.reason ? ' — ' + esc(top.reason) : '') + '</div>' : (rec.message ? '<div class="muted">' + esc(rec.message) + '</div>' : '')) +
      (ranked.length ? '<ol class="recommend-ranked">' + ranked.map(r => '<li data-runtime="' + esc(r.name) + '" data-ready="' + (r.ready === true || r.ready === 'true') + '">' + [rtName(r.name), r.cost_tier, r.note].filter(Boolean).map(esc).join(' — ') + '</li>').join('') + '</ol>' : '') +
      '<div class="muted small">More on prices and trade-offs: <code>docs/CHOOSING-AN-AI.md</code>.</div>');
  }
  return h;
}

// ---- Models
function pageModels() {
  loadDocs();
  const sec = S.secrets || {}, roles = Array.isArray(DOCS.list) ? DOCS.list.filter(d => d.name.startsWith('role:')).map(d => [d.name.slice(5), d.title]) : [];
  const keyNote = (n, t) => '<div class="setrow"><label>' + esc(t) + ' key</label><div class="setctl">' + (sec[n] ? '<b class="plus">✓ set</b>' : '<span class="muted">not set</span>') +
    ' <a href="#" data-goto-key="' + n + '">' + (sec[n] ? 'change it' : 'set it') + ' on the Runtimes page</a></div></div>';
  return card('Who plans and reviews', field('PLAN_PROVIDERS', provEditor('PLAN_PROVIDERS')) + field('REVIEW_PROVIDERS', provEditor('REVIEW_PROVIDERS')) +
      field('CLAUDE_MODEL', sel('CLAUDE_MODEL', ['sonnet', 'opus', 'haiku', 'fable'])) +
      field('AGY_PLAN_MODEL', modelSel('AGY_PLAN_MODEL')) + field('AGY_REVIEW_MODEL', modelSel('AGY_REVIEW_MODEL')) + field('AGY_FALLBACK_MODEL', modelSel('AGY_FALLBACK_MODEL')) +
      det('d-paid', 'Paid APIs', field('ANTHROPIC_MODEL', txt('ANTHROPIC_MODEL')) + keyNote('anthropic.key', 'Anthropic') +
        field('OPENAI_API_BASE', txt('OPENAI_API_BASE', 'https://api.openai.com/v1')) + field('OPENAI_MODEL', txt('OPENAI_MODEL', 'e.g. gpt-4o')) + keyNote('openai.key', 'OpenAI') +
        field('GEMINI_API_BASE', txt('GEMINI_API_BASE', 'https://generativelanguage.googleapis.com')) + field('GEMINI_MODEL', txt('GEMINI_MODEL', 'e.g. gemini-2.5-pro')) + keyNote('gemini.key', 'Gemini'),
        'only used when ticked above'),
      'The ticked ones are tried from the top; one that is out of quota is skipped until its quota comes back.') +
    card('Worker models', field('WORKER_MODEL', modelSel('WORKER_MODEL', true, 'default (' + (S.limits_worker_model || '') + ')')) +
      field('WORKER_MODEL_RISKY', modelSel('WORKER_MODEL_RISKY', true, 'same as ordinary tasks')) +
      field('WORKER_MODEL_LIGHT', modelSel('WORKER_MODEL_LIGHT', true, 'same as ordinary tasks')) +
      field('LIGHT_ROLES', roles.length || cur('LIGHT_ROLES') ? multi('LIGHT_ROLES', roles) : txt('LIGHT_ROLES', 'tester,technical-writer')) +
      '<div class="btnrow"><button type="button" class="mini" id="refresh-models">Refresh the list of Antigravity models</button></div>',
      'For workers running on Antigravity.') +
    card('Models of the other runtimes',
      field('WORKER_MODEL_CLAUDE', sel('WORKER_MODEL_CLAUDE', [['sonnet', 'sonnet (recommended)'], ['opus', 'opus'], ['haiku', 'haiku']])) +
      field('WORKER_MODEL_CODEX', sel('WORKER_MODEL_CODEX', [['', 'CLI default (gpt-5.1-codex)'], ['gpt-5.1-codex', 'gpt-5.1-codex'], ['gpt-4o', 'gpt-4o'], ['gpt-4o-mini', 'gpt-4o-mini'], ['o3-mini', 'o3-mini'], ['o1', 'o1']])) +
      field('WORKER_MODEL_GEMINI', sel('WORKER_MODEL_GEMINI', [['', 'CLI default'], ['gemini-2.5-pro', 'gemini-2.5-pro'], ['gemini-2.5-flash', 'gemini-2.5-flash'], ['gemini-3.8-flash-high', 'gemini-3.8-flash-high']])) +
      field('WORKER_MODEL_COMPAT', txt('WORKER_MODEL_COMPAT', 'e.g. qwen3-coder:30b')) +
      field('WORKER_MODEL_AIDER', txt('WORKER_MODEL_AIDER', 'e.g. qwen3-coder:30b')), 'Empty = the CLI\'s own default.') +
    card('Model routing', field('MODEL_ROUTING', sel('MODEL_ROUTING', [['off', 'off - use the models above'], ['auto', 'auto - the cheapest model that has proven itself']])) +
      field('ROUTING_MIN_TASKS', num('ROUTING_MIN_TASKS', null, 'tasks')) + field('ROUTING_MIN_FIRST_PASS', num('ROUTING_MIN_FIRST_PASS', null, '%')),
      'With auto, each new task uses the model with the least quota per merged task among those that have merged enough tasks of its role here. Risky tasks keep their model.') +
    card('Experiments', field('AB_MODEL', modelSel('AB_MODEL', true, 'no experiment')) + field('AB_SHARE', num('AB_SHARE', null, '%')) +
      field('CONTEXT_PACK', sel('CONTEXT_PACK', [['on', 'on - every task'], ['ab', 'ab - half of the tasks, to measure it'], ['off', 'off']])) + field('CONTEXT_PACK_CHARS', num('CONTEXT_PACK_CHARS', null, 'characters')),
      'Results with 95 % intervals and a button to adopt the winner: Insights → Experiments.');
}

// ---- Project
const LANG_OPTIONS = [
  ['Slovak', 'Slovenčina (Slovak)'],
  ['English', 'English'],
  ['Czech', 'Čeština (Czech)'],
  ['German', 'Deutsch'],
  ['Polish', 'Polski'],
  ['French', 'Français'],
  ['Spanish', 'Español'],
  ['Italian', 'Italiano'],
  ['Hungarian', 'Magyar'],
  ['Ukrainian', 'Українська']
];
const TZ_OPTIONS = [
  ['Europe/Bratislava', 'Europe/Bratislava (CET/CEST)'],
  ['Europe/Prague', 'Europe/Prague (CET/CEST)'],
  ['Europe/Vienna', 'Europe/Vienna (CET/CEST)'],
  ['Europe/Berlin', 'Europe/Berlin (CET/CEST)'],
  ['Europe/Budapest', 'Europe/Budapest (CET/CEST)'],
  ['Europe/Warsaw', 'Europe/Warsaw (CET/CEST)'],
  ['Europe/London', 'Europe/London (GMT/BST)'],
  ['Europe/Paris', 'Europe/Paris (CET/CEST)'],
  ['Europe/Rome', 'Europe/Rome (CET/CEST)'],
  ['Europe/Kyiv', 'Europe/Kyiv (EET/EEST)'],
  ['UTC', 'UTC (Coordinated Universal Time)'],
  ['America/New_York', 'America/New_York (Eastern)'],
  ['America/Chicago', 'America/Chicago (Central)'],
  ['America/Denver', 'America/Denver (Mountain)'],
  ['America/Los_Angeles', 'America/Los_Angeles (Pacific)'],
  ['Asia/Tokyo', 'Asia/Tokyo (JST)'],
  ['Asia/Shanghai', 'Asia/Shanghai (CST)'],
  ['Asia/Dubai', 'Asia/Dubai (GST)'],
  ['Australia/Sydney', 'Australia/Sydney (AEST/AEDT)']
];

function pageProject() {
  const fk = V().FORGE_KIND || '', gh = fk === 'github' || fk === 'gitlab';
  return card('Where the code lives',
      rowAct('row-repo', 'Repository', (gh ? esc(fk === 'github' ? 'GitHub' : 'GitLab') + ': <b>' + esc(V().UPSTREAM_REPO) + '</b>' : fk === 'forgejo' ? 'only the internal Forgejo' : 'not chosen yet') +
        ' <button type="button" class="mini" id="open-setup">' + (fk ? 'Change…' : 'Choose…') + '</button>') +
      (gh ? field('UPSTREAM_BRANCH', sel('UPSTREAM_BRANCH', [['main', 'main'], ['master', 'master'], ['develop', 'develop']])) + field('UPSTREAM_MERGE', sel('UPSTREAM_MERGE', [['merge', 'a merge commit'], ['squash', 'squash and merge'], ['rebase', 'rebase and merge']])) : '') +
      det('d-forge', 'Internal Forgejo', rowAct('row-forge-url', LABEL.FORGE_URL, '<code>' + esc(V().FORGE_URL || '') + '</code>') + rowAct('row-forge-repo', LABEL.FORGE_REPO, '<code>' + esc(V().FORGE_REPO || '') + '</code>'),
        'set at install time, read-only here')) +
    card('What waits for your OK', field('APPROVE', multi('APPROVE', [['plan', 'a goal\'s plan is carried out'], ['risky', 'a risky task starts'], ['merge', 'a reviewed branch is merged']])),
      'What you tick waits for you under Overview → Needs you (and a notification, if set up below). Nothing ticked = fully automatic.') +
    card('How the team behaves', field('AUTOPILOT', tog('AUTOPILOT')) + field('AUTO_MERGE', tog('AUTO_MERGE')) + field('CONTINUE_CONVERSATIONS', tog('CONTINUE_CONVERSATIONS')) +
      field('PLAN_AHEAD', tog('PLAN_AHEAD')) + field('MAINTENANCE', tog('MAINTENANCE')) +
      field('OWNER_LANG', sel('OWNER_LANG', LANG_OPTIONS)) + field('TASK_LANG', sel('TASK_LANG', LANG_OPTIONS)) +
      field('PRODUCT_LANG', sel('PRODUCT_LANG', [['', 'same as my language']].concat(LANG_OPTIONS))) + field('DOCS_LANG', sel('DOCS_LANG', LANG_OPTIONS)) +
      field('LOCAL_TZ', '<div style="display:flex;gap:6px;align-items:center">' + sel('LOCAL_TZ', TZ_OPTIONS) + '<button type="button" class="mini" id="detect-tz" title="Set to browser timezone">Detect</button></div>')) +
    card(th('settings.appearance_title', 'Dashboard appearance & language'),
      rowAct('row-ui-lang', t('settings.ui_lang', 'Dashboard UI language'),
        '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">' +
        '<select id="ui-lang-sel" aria-labelledby="row-ui-lang-l" style="min-width:180px">' +
        (LOCALES_LIST.length ? LOCALES_LIST : [
          {code: 'en', name: 'English'},
          {code: 'sk', name: 'Slovenčina'},
          {code: 'sk_qwerty', name: 'Slovenčina (QWERTY)'},
          {code: 'eng_us', name: 'English (US)'}
        ]).map(l => '<option value="' + esc(l.code) + '"' + (currentLang === l.code ? ' selected' : '') + '>' + esc(l.name) + ' (' + esc(l.code) + ')</option>').join('') +
        (!['en', 'sk', 'sk_qwerty', 'eng_us'].concat(LOCALES_LIST.map(l => l.code)).includes(currentLang) ? '<option value="' + esc(currentLang) + '" selected>' + esc(currentLang) + ' (custom)</option>' : '') +
        '<option value="__upload">+ Upload locale (.json)…</option>' +
        '<option value="__custom">+ Enter code manually…</option>' +
        '</select>' +
        '<input type="file" id="locale-file-picker" accept=".json" style="display:none">' +
        '<button type="button" class="mini" id="upload-locale-btn" title="Upload a custom JSON dictionary file">Upload .json…</button>' +
        '</div>') +
      rowAct('row-ui-theme', t('settings.ui_theme', 'Color theme'),
        '<select id="ui-theme-sel" aria-labelledby="row-ui-theme-l" style="min-width:180px">' +
        [['auto', 'Auto (system default)'], ['light', 'Light mode'], ['dark', 'Dark mode']].map(([m, n]) => '<option value="' + esc(m) + '"' + (currentTheme === m ? ' selected' : '') + '>' + esc(n) + '</option>').join('') +
        '</select>'),
      th('settings.appearance_desc', 'Changes are applied immediately and saved in your browser. Custom dictionaries live in web/static/locales/<name>.json.')) +
    card('Files', field('PROJECT_DIR', txt('PROJECT_DIR')) + field('VISION_FILE', txt('VISION_FILE', 'docs/vision.md')) + field('SHARED_FILES', txt('SHARED_FILES')),
      'Paths inside the repository.') +
    card('Tests', field('TEST_CMD', txt('TEST_CMD', 'python3 -m pytest -q')) + field('GATE_IMAGE', txt('GATE_IMAGE')),
      'The same command runs in the merge gate, in the worker\'s self-check and in the maintenance of main.') +
    card('Notifications', field('NOTIFY_URL', txt('NOTIFY_URL', 'https://ntfy.sh/your-topic')) + field('NOTIFY_FORMAT', sel('NOTIFY_FORMAT', ['ntfy', 'slack', 'discord', 'json'])) +
      keyRow('notify.token', 'Access token', 'only for a ntfy server that asks for a login') + field('NOTIFY_EVENTS', multi('NOTIFY_EVENTS', EVENTS)),
      'The same address goes into the ntfy app on your phone (subscribe to the topic at the end of it).');
}

// ---- Money
function pageMoney() {
  const api = (S.workers || []).filter(w => ((S.runtimes || {})[w.runtime] || {}).api);
  const wb = Object.fromEntries(String(cur('WORKER_DAILY_BUDGET_USD')).split(',').filter(x => x.includes('=')).map(x => x.split('=')));
  const wfield = '<div id="set-WORKER_DAILY_BUDGET_USD" data-kind="wbudget" class="wbudget">' + (api.length ? api.map(w => '<label>' + esc(w.name) + ' <span class="unit"><input type="number" min="0" step="0.5" data-w="' +
    esc(w.name) + '" value="' + esc(wb[w.name] || '') + '" placeholder="no cap"><span class="muted small">USD</span></span></label>').join('') : '<span class="muted small">No worker uses a paid API.</span>') + '</div>';
  return card('Budgets', field('API_RUN_BUDGET_USD', money('API_RUN_BUDGET_USD')) + field('API_DAILY_BUDGET_USD', money('API_DAILY_BUDGET_USD')) + field('API_MONTHLY_BUDGET_USD', money('API_MONTHLY_BUDGET_USD')) +
      field('PROJECT_DAILY_BUDGET_USD', money('PROJECT_DAILY_BUDGET_USD')) + field('PROJECT_MONTHLY_BUDGET_USD', money('PROJECT_MONTHLY_BUDGET_USD')) + field('WORKER_DAILY_BUDGET_USD', wfield),
      'Spent today: <b>' + usd(S.api_spent_today) + '</b>, this month: <b>' + usd(S.api_spent_month) + '</b> (all projects). Empty = no limit. Details: Insights → Money.') +
    card('Per run', field('RUN_MAX_TOOL_CALLS', num('RUN_MAX_TOOL_CALLS', null, 'calls'))) +
    card('Prices for the cost estimate', field('PRICE_CODEX', triple('PRICE_CODEX', ['input', 'cached', 'output'])) + field('PRICE_GEMINI', triple('PRICE_GEMINI', ['input', 'cached', 'output'])) +
      field('PRICE_COMPAT', triple('PRICE_COMPAT', ['input', 'cached', 'output'])), 'Codex, Gemini CLI and Aider report tokens, not money. Your own hardware: 0 / 0 / 0.');
}

// ---- Knowledge: the team's documents, edited here (a role is saved as this project's own version)
const DOCS = {list: null, at: 0, busy: false};
async function loadDocs(force) {
  if (DOCS.busy || (!force && DOCS.list && Date.now() - DOCS.at < 30000)) return;
  DOCS.busy = true;
  try { DOCS.list = await getJSON('/api/docs'); } catch (e) { DOCS.list = {error: String(e)}; }
  DOCS.at = Date.now(); DOCS.busy = false; schedule();
}
const roleInfo = name => (Array.isArray(DOCS.list) ? DOCS.list : []).find(d => d.name === name) || {};
function pageKnowledge() {
  loadDocs();
  const L = DOCS.list;
  if (!L) return '<div class="empty">loading…</div>';
  if (!Array.isArray(L)) return '<div class="empty bad">Could not load the documents: ' + esc(L.error || 'not available') + ' <button type="button" class="mini" data-docsreload>Try again</button></div>';
  const main = L.filter(d => !d.name.startsWith('role:')), roles = L.filter(d => d.name.startsWith('role:'));
  return card('What the team knows', main.length ? main.map(d => '<div class="docrow" data-doc="' + esc(d.name) + '" role="button" tabindex="0"><b>' + esc(d.title) + '</b> ' + small(esc(d.about)) +
      (d.exists ? '' : ' <span class="badge">empty</span>') + '</div>').join('') : '<div class="empty">No documents yet.</div>', 'Every plan, review and task reads these. Write them like a briefing for a new colleague.') +
    card('Roles (' + roles.length + ')', '<div class="btnrow"><button type="button" class="primary sm" id="new-role">+ New role</button></div>' +
      (roles.length ? '<div class="roles">' + roles.map(d => '<button type="button" class="rolebtn" data-doc="' + esc(d.name) + '" title="' + esc(d.about || '') + '">' + esc(d.title) +
        (d.customized ? ' <span class="badge">' + (d.standard ? 'edited' : 'yours') + '</span>' : '') + '</button>').join('') + '</div>' : '<div class="empty">No roles yet - add the first one.</div>'),
      'Every worker gets the playbook of its role together with the task. Saving a standard role keeps your version for this project only.');
}
async function openDoc(name) {
  openSheet('doc', name, '<div class="empty">loading…</div>', true);
  let d; try { d = await getJSON('/api/doc', {name}); } catch (e) { d = {error: String(e)}; }
  if (d.error) return setSheet('doc', name, '<div class="empty bad">Could not load the document: ' + esc(d.error) + '</div>');
  const role = name.startsWith('role:'), std = roleInfo(name).standard !== false;
  setSheet('doc', name, '<h2>' + esc(role ? 'Role: ' + name.slice(5) : name.replace('-', ' ')) + '</h2>' +
    (d.customized ? '<div class="note">' + (std ? 'This project uses its own version of this role.' : 'This role exists only in this project.') + '</div>' : '') +
    '<textarea class="doced" id="doc-text" spellcheck="false">' + esc(d.text) + '</textarea>' +
    '<div class="btnrow"><button type="button" class="primary sm" data-docsave="' + esc(name) + '">Save</button>' +
    (role && d.customized ? '<button type="button" class="mini" data-docreset="' + esc(name) + '">' + (std ? 'Back to the standard role' : 'Delete this role') + '</button>' : '') + small('Markdown. Applies from the next plan / review / task.') + '</div>');
}
function openNewRole() {
  openSheet('doc', 'new-role', '<h2>New role</h2><div class="muted small">A role is a playbook: what the worker owns, how it works, when it is done. You can edit all of it afterwards.</div>' +
    '<div class="setrow"><label for="nr-name">Name<span class="help">letters, digits and dashes, e.g. database-admin</span></label><div class="setctl"><input type="text" id="nr-name" autocomplete="off" spellcheck="false" placeholder="database-admin"></div></div>' +
    '<div class="setrow"><label for="nr-mission">Mission<span class="help">one sentence</span></label><div class="setctl"><input type="text" id="nr-mission" autocomplete="off" placeholder="keep the schema and its migrations safe"></div></div>' +
    '<div class="seterr" id="nr-err"></div><div class="btnrow"><button type="button" class="primary sm" id="nr-create">Create the role</button></div>');
}
async function createRole() {
  const slug = $('nr-name').value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''), mission = $('nr-mission').value.trim().replace(/\s+/g, ' '), err = t => { $('nr-err').textContent = t; };
  if (!/^[a-z][a-z0-9-]{1,40}$/.test(slug)) return err('The name needs 2-41 characters, starting with a letter (letters, digits, dashes).');
  if (roleInfo('role:' + slug).name) return err('A role called “' + slug + '” already exists - open it from the list.');
  if (!mission) return err('Write the mission in one sentence.');
  const title = slug.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  const text = '# ' + title + '\n**Mission:** ' + mission + '\n\n## You own\n\n\n## How you work\n1. \n\n## Done when\n- \n';
  if (!done(await post('/api/doc', {name: 'role:' + slug, text}), 'Role created')) return;
  await loadDocs(true); openDoc('role:' + slug);
}

// ---- Limits (all projects)
function pageLimits() {
  const n = (S.workers || []).length, par = Number(cur('MAX_PARALLEL'));
  const R = (P && P.roster) || {};
  const agyWorkers = (S.workers || []).filter(w => (w.runtime || 'agy') === 'agy');
  const quotaRows = agyWorkers.map(w => {
    const e = R['worker:' + w.name] || {};
    const acc = w.account || e.account || '–';
    const q = e.quota || [];
    const fam = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
    const low = (rows, re) => { const v = rows.filter(x => re.test(x.label)); return v.length ? Math.min(...v.map(x => x.pct)) : null; };
    const gem = q.filter(x => fam(x.model) === 'Gemini'), cl = q.filter(x => fam(x.model) === 'Claude');
    const h5 = low(gem, /five hour|5h/i), wkp = low(gem, /week/i), clw = low(cl, /week/i);
    const pc = v => v != null ? v.toFixed(0) + '%' : '–';
    return '<tr><td><b>' + esc(w.name) + '</b></td><td class="muted small">' + esc(acc) + '</td><td>' + pc(h5) + '</td><td>' + pc(wkp) + '</td><td>' + pc(clw) + '</td></tr>';
  });
  const quotaTable = quotaRows.length ? '<div class="tablewrap pad" style="margin-top:12px"><table class="m"><tr><th>Worker</th><th>Account / Email</th><th>Gemini 5-hour</th><th>Gemini weekly</th><th>Claude/GPT weekly</th></tr>' + quotaRows.join('') + '</table></div>' : '';

  return card('Limits <span class="pill warn">all projects</span>', (n > par ? '<div class="note">There are more workers (' + n + ') than runs allowed at the same time (' + par + ') - some will always wait.</div>' : '') +
    field('MAX_PARALLEL', num('MAX_PARALLEL', null, 'runs')) + field('RUN_TOKEN_CAP', num('RUN_TOKEN_CAP', null, 'tokens')) + field('RUN_TIMEOUT_MIN', num('RUN_TIMEOUT_MIN', null, 'min')) +
    field('RUN_TIMEOUT_MAX_MIN', num('RUN_TIMEOUT_MAX_MIN', null, 'min')) + field('DAILY_TOKEN_BUDGET', num('DAILY_TOKEN_BUDGET', null, 'tokens')) + field('MAX_RUNS_PER_DAY', num('MAX_RUNS_PER_DAY', null, 'runs')) +
    field('CONSEC_FAIL_TRIP', num('CONSEC_FAIL_TRIP', null, 'runs')) + field('POLL_SEC', num('POLL_SEC', null, 'seconds'))) +
    card('Quota of the Antigravity accounts', field('QUOTA_MODEL_FILTER', sel('QUOTA_MODEL_FILTER', [['Gemini', 'Gemini (default)'], ['Claude', 'Claude'], ['all', 'All / any']])) + field('QUOTA_MIN_5H_PCT', num('QUOTA_MIN_5H_PCT', null, '%')) + field('QUOTA_MIN_WEEK_PCT', num('QUOTA_MIN_WEEK_PCT', null, '%')) + quotaTable);
}

// ---- Advanced: rarely needed
function fileOnlyCard() {
  const fo = S.file_only, es = fo && typeof fo === 'object' && !Array.isArray(fo) ? Object.entries(fo).filter(([k]) => k !== 'FORGE_URL' && k !== 'FORGE_REPO') : [];   // those two are shown on the Project page
  if (!es.length) return '';
  return det('d-fileonly', 'Set in files on the host (' + es.length + ')', es.map(([k, why]) => '<div class="setrow setting-file-only" data-key="' + esc(k) + '"><label>' + esc(LABEL[k] || k) + '<span class="help"><code>' + esc(k) + '</code></span></label>' +
    '<div class="setctl muted small">' + esc(why) + '</div></div>').join(''), 'not editable from here');
}
function pageAdvanced() {
  return card('Rhythm and task size', field('LOOP_SEC', num('LOOP_SEC', null, 'seconds')) + field('ORPHAN_LIMIT', num('ORPHAN_LIMIT', 5, 'times')) + field('ORPHAN_WARN', num('ORPHAN_WARN', 3, 'times')) +
      field('TASK_TOKEN_BUDGET', num('TASK_TOKEN_BUDGET', null, 'tokens')) + field('MAX_TASK_FILES', num('MAX_TASK_FILES', null, 'files')) + field('SELF_CHECK_ROUNDS', num('SELF_CHECK_ROUNDS', null, 'rounds')) +
      field('MAX_REWORK', num('MAX_REWORK', null, 'rounds')) + field('SPLIT_AFTER_CAPS', num('SPLIT_AFTER_CAPS', null, 'times')) + field('MAX_ACTIVE_GOALS', num('MAX_ACTIVE_GOALS', null, 'goals')) +
      field('MAX_GOAL_ROUNDS', num('MAX_GOAL_ROUNDS', null, 'rounds')) + field('DIFF_MAX', num('DIFF_MAX', null, 'characters')) + field('LESSONS_MAX', num('LESSONS_MAX', null, 'lessons'))) +
    card('Quality checks', field('QUALITY_CHECK', sel('QUALITY_CHECK', [['warn', 'warn - tell the reviewer'], ['block', 'block - send the work back'], ['off', 'off']])) +
      field('TEST_RATIO_MAX', sel('TEST_RATIO_MAX', [['1', '1'], ['1.5', '1.5'], ['2', '2'], ['2.5', '2.5'], ['3', '3'], ['4', '4'], ['5', '5'], ['8', '8'], ['10', '10']])) +
      field('MAX_FILE_LINES', num('MAX_FILE_LINES', 400, 'lines')), 'Scripted, no AI involved; the details are in docs/EFFICIENCY.md.') +
    card('Worker prompt', field('ORIENT', sel('ORIENT', [['on', 'on'], ['off', 'off']])) + field('PACK_EXTRAS', sel('PACK_EXTRAS', [['on', 'on'], ['off', 'off']])) +
      field('PROMPT_ORDER', sel('PROMPT_ORDER', [['default', 'default'], ['cache', 'cache - shared text first (experimental)']])), 'Turn these off to get the earlier prompts back, for example to compare in the benchmark.') +
    card('Housekeeping', field('RUNS_KEEP_DAYS', num('RUNS_KEEP_DAYS', null, 'days'))) +
    det('d-unsupported-runtimes', 'Experimental runtimes (not yet available as workers)',
      field('OPENCODE_MODEL', txt('OPENCODE_MODEL', 'e.g. gpt-4o')) +
      field('QWEN_MODEL', txt('QWEN_MODEL', 'e.g. qwen-max')) +
      field('GOOSE_MODEL', txt('GOOSE_MODEL', 'e.g. gpt-4o')),
      'placeholder settings for future CLI runtimes') +
    fileOnlyCard();
}

// ---- the page
function settingsHtml() {
  if (!S) {
    if (window._ferr) return '<div class="empty bad">Could not load settings: ' + esc(window._ferr) + ' <button type="button" class="mini" data-retry>Try again</button></div>';
    loadSettings();
    return '<div class="empty">loading…</div>';
  }
  if (S.error) return failed('the settings');
  const pages = {workers: pageWorkers, runtimes: pageRuntimes, models: pageModels, project: pageProject, money: pageMoney, knowledge: pageKnowledge, limits: pageLimits, advanced: pageAdvanced};
  return (pages[SUB.settings] || pageWorkers)();
}
function renderSettings(force) {
  if (!$('settings')) return;
  if (!SPAGES.includes(SUB.settings)) SUB.settings = 'workers';
  document.querySelectorAll('#settings-seg button').forEach(b => {
    b.classList.toggle('on', b.dataset.v === SUB.settings);
    const n = pendPages().includes(b.dataset.v); b.classList.toggle('dirty', n);
  });
  if (!force && busyIn('settings')) return;                  // never under the owner's fingers
  const before = lastHTML.settings;
  setHTML('settings', settingsHtml(), force);
  if (force || lastHTML.settings !== before) afterRender();  // the values of a fresh page are the baseline; then the unsaved edits go back in
  else if (SX.focus) focusRow();
}

// ---- saving: everything edited, on every page, in one go
async function saveSettings() {
  const changes = Object.assign({}, PEND);
  if (!Object.keys(changes).length || SX.busy) return;
  SX.busy = true; SX.msg = 'saving…'; updateBar();
  const r = await post('/api/settings', {changes});
  SX.busy = false;
  const errs = r.ok ? {} : (r.errors && Object.keys(r.errors).length ? r.errors : {_: r.message || r.error || 'no answer'});
  for (const k of Object.keys(ERRS)) delete ERRS[k];
  if (r.ok) {
    for (const k of Object.keys(changes)) delete PEND[k];
    SX.msg = 'Saved - applies from the next round (within 20 s)'; toast('Settings saved');
    setTimeout(() => { if (SX.msg.startsWith('Saved')) { SX.msg = ''; updateBar(); } }, 7000);
    await loadSettings(true); schedule(true); return;
  }
  const keys = Object.keys(errs).filter(k => k !== '_');
  for (const k of keys) ERRS[k] = errs[k];
  const shown = keys.slice(0, 3).map(k => (LABEL[k] || k) + ': ' + errs[k]).join(' · ');
  SX.msg = 'Not saved' + (shown ? ' - ' + shown + (keys.length > 3 ? ' …' : '') : '') + (errs._ ? (shown ? ' · ' : ' - ') + errs._ : '');
  const pg = keys.map(k => KEYPAGE[k]).filter(Boolean);
  if (pg.length && !pg.includes(SUB.settings)) { SUB.settings = pg[0]; store('af-sub', JSON.stringify(SUB)); document.querySelectorAll('#settings-seg button').forEach(b => b.classList.toggle('on', b.dataset.v === SUB.settings)); }
  if (keys.length) SX.focus = {row: 'row-' + keys[0], until: Date.now() + 3000};
  schedule(true); updateBar();
}

// ---- add a worker: an Antigravity account (signed in from the browser), an API key, or your model server
let addingWorker = false;
async function addWorkerGo() {
  if (addingWorker || SX.busy) return;       // a second click while the first is still working must not add a second worker
  addingWorker = true;
  try { await addWorker(); } finally { addingWorker = false; }
}
async function addWorker() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || SX.addRt || 'agy', r = (S.runtimes || {})[rt] || {};
  if (!r.creatable) { wstatus('This runtime cannot be added from here yet.'); return; }
  if (r.endpoint) {
    const url = ($('addw-urlin') || {value: ''}).value.trim(), model = ($('addw-model') || {value: ''}).value.trim(), changes = {};
    if (!url) { wstatus('Enter the address of your model server first.'); $('addw-urlin').focus(); return; }
    if (url !== cur('COMPAT_BASE_URL')) changes.COMPAT_BASE_URL = url;
    if (model) changes[rt === 'aider' ? 'WORKER_MODEL_AIDER' : 'WORKER_MODEL_COMPAT'] = model;
    if (Object.keys(changes).length) {
      const s = await post('/api/settings', {changes});
      if (!s.ok) { wstatus('Not saved: ' + Object.entries(s.errors || {}).map(([k, v]) => (LABEL[k] || k) + ' ' + v).join('; ')); return; }
    }
  }
  const kin = $('addw-keyin');
  if (r.key && !(S.secrets || {})[r.key]) {
    if (kin && kin.value.trim()) {
      const k = await post('/api/secret', {name: r.key, value: kin.value.trim()}); kin.value = '';
      if (!k.ok) { wstatus('The key could not be saved: ' + (k.message || k.error || '')); return; }
    } else if (!r.optional_key) { wstatus('This one needs an API key.'); if (kin) kin.focus(); return; }
  }
  SX.addOpen = false; SX.addUrl = SX.addModel = null; SX.busy = true; schedule(true);
  let j = await post('/api/workers', {action: 'add', runtime: rt});
  if (j.id) j = await jobWait(j, jj => wstatus('creating ' + (jj.worker || 'the worker') + '… (the first time it builds the image - a few minutes)'));
  SX.busy = false;
  const ok = j.state === 'done';
  wstatus(ok ? (rt === 'agy' ? j.worker + ' created - now sign it in' : j.worker + ' created and ready') : 'Failed: ' + String(j.log || j.error || j.message || 'no answer').slice(-300));
  await loadSettings(true); schedule(true);
  if (ok && rt === 'agy' && j.worker) openLogin(j.worker);
}
async function removeWorker(w) {
  if (SX.busy) return;
  if (!confirm('Remove ' + w + '? Its container, volume, account in the forge and keys are deleted. Work that is already merged stays.')) return;
  SX.busy = true; wstatus('removing ' + w + '…'); schedule(true);
  let j = await post('/api/workers', {action: 'remove', worker: w});
  if (j.id) j = await jobWait(j, () => wstatus('removing ' + w + '…'));
  SX.busy = false;
  const ok = j.state === 'done', msg = ok ? w + ' removed' : 'Could not remove ' + w + ': ' + String(j.log || j.error || j.message || 'no answer').slice(-300);
  wstatus(msg); toast(msg, !ok);
  if (ok) setTimeout(() => { if (SX.wmsg === msg) wstatus(''); }, 8000);
  await loadSettings(true); schedule(true);
}

// ---- clicks
document.addEventListener('click', async e => {
  const t = e.target, q = s => t.closest(s);
  const mv = q('[data-mv]');
  if (mv) {
    const row = mv.closest('.prov'), d = Number(mv.dataset.mv);
    if (d < 0 && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (d > 0 && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
    const box = mv.closest('[id^="set-"]'); if (box) notePend(box.id.slice(4));
    return;
  }
  const gt = q('[data-goto]'); if (gt) { e.preventDefault(); setSub('settings', gt.dataset.goto); return; }
  const gk = q('[data-goto-key]'); if (gk) { e.preventDefault(); settingsGoto('key:' + gk.dataset.gotoKey); return; }
  if (t.id === 'save-settings') { saveSettings(); return; }
  if (t.id === 'discard-settings') { discardPend(); return; }
  if (t.id === 'upload-locale-btn') { const p = $('locale-file-picker'); if (p) p.click(); return; }
  if (q('[data-retry]')) { S = null; sLoadedFor = null; window._ferr = null; loadSettings(true); schedule(true); return; }
  if (t.id === 'refresh-models') { t.disabled = true; t.textContent = 'loading…'; await post('/api/settings', {refresh_models: true}); await loadSettings(true); schedule(true); return; }
  if (t.id === 'detect-tz') {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const el = $('set-LOCAL_TZ');
    if (el && tz) {
      if (![...el.options].some(o => o.value === tz)) el.add(new Option(tz, tz));
      el.value = tz;
      notePend('LOCAL_TZ');
      toast('Time zone set to ' + tz);
    }
    return;
  }
  // workers
  if (t.id === 'add-worker') { SX.addOpen = !SX.addOpen; schedule(true); return; }
  if (t.id === 'addw-cancel') { SX.addOpen = false; SX.addUrl = SX.addModel = null; schedule(true); return; }
  if (t.id === 'addw-go') { addWorkerGo(); return; }
  const rm = q('[data-wremove]'); if (rm) { removeWorker(rm.dataset.wremove); return; }
  const af = q('[data-addw-for]'); if (af) { SX.addOpen = true; SX.addRt = af.dataset.addwFor; setSub('settings', 'workers'); return; }
  const lg = q('[data-login]'); if (lg) { openLogin(lg.dataset.login); return; }
  // runtimes and keys
  if (q('[data-rtcheck]')) { loadRuntimes(true); schedule(true); return; }
  const kf = q('[data-keyfor]'); if (kf) { SX.keyOpen = kf.dataset.keyfor; settingsGoto('key:' + kf.dataset.keyfor); return; }
  const ko = q('[data-keyopen]');
  if (ko) { SX.keyOpen = SX.keyOpen === ko.dataset.keyopen ? '' : ko.dataset.keyopen; schedule(true); setTimeout(() => { const i = $('key-val'); if (i) i.focus(); }, 60); return; }
  const ks = q('[data-keysave]');
  if (ks) {
    const v = $('key-val'); if (!v || !v.value.trim()) { if (v) v.focus(); return; }
    ks.disabled = true; const r = await post('/api/secret', {name: ks.dataset.keysave, value: v.value.trim()}); v.value = '';
    if (done(r, 'Key saved')) { SX.keyOpen = ''; await loadSettings(true); loadRuntimes(true); }
    schedule(true); return;
  }
  // knowledge
  const dc = q('[data-doc]'); if (dc) { openDoc(dc.dataset.doc); return; }
  if (q('[data-docsreload]')) { DOCS.list = null; loadDocs(true); schedule(true); return; }
  if (t.id === 'new-role') { openNewRole(); return; }
  if (t.id === 'nr-create') { createRole(); return; }
  const ds = q('[data-docsave]');
  if (ds) { ds.disabled = true; const ok = done(await post('/api/doc', {name: ds.dataset.docsave, text: $('doc-text').value}), 'Saved'); ds.disabled = false; if (ok) loadDocs(true); return; }
  const dr = q('[data-docreset]');
  if (dr) {
    const std = roleInfo(dr.dataset.docreset).standard !== false;
    if (!confirm(std ? 'Delete this project\'s version of the role and use the standard one?' : 'Delete this role?')) return;
    if (done(await post('/api/doc', {name: dr.dataset.docreset, reset: true}), std ? 'Back to the standard role' : 'Role deleted')) { await loadDocs(true); if (std) openDoc(dr.dataset.docreset); else closeSheet(); }
    return;
  }
  // project setup, login terminal, new project
  if (t.id === 'open-setup') { openSetup(); return; }
  if (t.id === 'np-create') { createProject(); return; }
  if (t.id === 'np-close') { $('newproj').classList.remove('on'); await loadProjects(true); if ($('proj').value !== project) switchProject(project); return; }
  if (t.id === 'login-done' || t.id === 'login-close') { closeLogin(t.id === 'login-done'); return; }
  if (t.id === 'su-close' || t.id === 'su-later') { $('setup').classList.remove('on'); setupDismissed[project] = true; return; }
  if (t.id === 'su-go') { setupGo(); return; }
});
document.addEventListener('change', e => {
  if (e.target.name === 'su-kind') suRefresh();
  if (e.target.name === 'addw-rt') {
    SX.addUrl = ($('addw-urlin') || {}).value; SX.addModel = ($('addw-model') || {}).value; SX.addRt = e.target.value; schedule(true);
  }
  if (e.target.id === 'locale-file-picker') {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async evt => {
      try {
        const content = JSON.parse(evt.target.result);
        const defCode = file.name.replace(/\.json$/i, '').toLowerCase().replace(/[^a-z0-9_-]/g, '_');
        const codePrompt = prompt('Enter locale code for “' + file.name + '”:', defCode);
        if (!codePrompt || !codePrompt.trim()) return;
        const code = codePrompt.trim().toLowerCase();
        const r = await post('/api/locale', {code, content});
        if (r.ok) {
          toast('Locale “' + (r.name || code) + '” uploaded and applied!');
          await loadLocalesList();
          await loadLocale(code);
        } else {
          toast('Upload failed: ' + (r.error || r.message), true);
        }
      } catch (err) {
        toast('Invalid JSON: ' + err.message, true);
      } finally {
        e.target.value = '';
      }
    };
    reader.readAsText(file);
    return;
  }
  if (e.target.id === 'ui-lang-sel') {
    if (e.target.value === '__upload') {
      const p = $('locale-file-picker'); if (p) p.click();
      e.target.value = currentLang;
      return;
    }
    if (e.target.value === '__custom') {
      const custom = prompt('Enter locale code (locale file in web/static/locales/<name>.json, e.g. sk_qwerty, de, cs):', currentLang);
      if (custom && custom.trim()) {
        loadLocale(custom.trim().toLowerCase());
      } else {
        e.target.value = currentLang;
      }
      return;
    }
    loadLocale(e.target.value);
  }
  if (e.target.id === 'ui-theme-sel') {
    applyTheme(e.target.value);
  }
});
window.addEventListener('beforeunload', e => { if (Object.keys(PEND).length) { e.preventDefault(); e.returnValue = ''; } });

projectResets.push(() => {
  S = null; sLoadedFor = null; DOCS.list = null; RT.data = RT.status = RT.rec = RT.err = null; RT.at = 0;
  for (const k of Object.keys(PEND)) delete PEND[k];
  for (const k of Object.keys(ERRS)) delete ERRS[k];
  SX.msg = SX.wmsg = SX.keyOpen = ''; SX.addOpen = false; SX.addUrl = SX.addModel = null; SX.focus = null;
});
// ---- where the code lives: internal Forgejo only, GitHub or GitLab (shown once for a new project, or from Settings → Project)
const setupDismissed = {};
const SU_URL = {github: 'https://github.com', gitlab: 'https://gitlab.com'};
function suKind() { return (document.querySelector('input[name="su-kind"]:checked') || {}).value || 'forgejo'; }
function suRefresh() {
  const k = suKind(), up = k !== 'forgejo';
  $('su-up').style.display = up ? '' : 'none';
  if (up) { const u = $('su-url'); if (!u.value || Object.values(SU_URL).includes(u.value)) u.value = SU_URL[k];
    $('su-help').textContent = k === 'github' ? 'Token: GitHub → Settings → Developer settings → Fine-grained token for this repository only, permissions Contents and Pull requests: Read and write.'
      : 'Token: GitLab → project → Settings → Access tokens, role Developer, scopes api and write_repository.'; }
}
function openSetup() {
  const v = V(), k = v.FORGE_KIND || (F && F.forge && F.forge.kind) || 'forgejo';
  document.querySelectorAll('input[name="su-kind"]').forEach(r => r.checked = r.value === (k || 'forgejo'));
  $('su-repo').value = v.UPSTREAM_REPO || (F && F.forge && F.forge.repo) || '';
  $('su-url').value = v.UPSTREAM_URL || (F && F.forge && F.forge.url) || '';
  $('su-form').style.display = ''; $('su-log').style.display = 'none'; $('su-force-row').style.display = 'none';
  suRefresh(); $('setup').classList.add('on');
}
async function maybeSetup() {
  if (setupDismissed[project] || $('setup').classList.contains('on')) return;
  setupDismissed[project] = true; if (!S) await loadSettings(true); openSetup();
}
async function setupGo() {
  const k = suKind(), log = $('su-log'), tok = $('su-token');
  for (const x of ['repo', 'url', 'token']) $('su-e-' + x).textContent = '';
  if (k !== 'forgejo' && tok.value.trim()) {
    const r = await post('/api/secret', {name: k + '.token', value: tok.value.trim()}); tok.value = '';
    if (!r.ok) { $('su-e-token').textContent = r.message || r.error || 'the token could not be saved'; return; }
  }
  let j = await post('/api/upstream', {action: 'connect', kind: k, repo: $('su-repo').value.trim(), url: $('su-url').value.trim(), force: $('su-force').checked});
  if (j.errors) { for (const x in j.errors) { const el = $('su-e-' + x); if (el) el.textContent = j.errors[x]; } return; }
  $('su-form').style.display = 'none'; log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2000)); j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + (j.state === 'done' ? '✔ Done — the team now merges through this repository.' : '✘ Failed — see the output above.');
  if (j.state !== 'done' && /already has \d+ commits/.test(j.log || '')) { $('su-form').style.display = ''; $('su-force-row').style.display = ''; }
  S = null; loadSettings(true); refreshSoon();
}

// ---- a worker's Antigravity sign-in: a real terminal (xterm.js) on the worker's `agy-run`, raw bytes both ways
let loginW = null, loginTimer = null, loginTerm = null, loginFit = null, loginPos = 0, loginSend = Promise.resolve(), loginSeq = 0;
const loginPost = body => fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(Object.assign({worker: loginW}, body))});
const b64 = u8 => { let s = ''; for (const c of u8) s += String.fromCharCode(c); return btoa(s); };
async function openLogin(w) {
  const seq = ++loginSeq;                       // a request still in flight after Close/reopen must not touch the new (or gone) terminal
  if (loginTerm) { loginTerm.dispose(); loginTerm = null; }
  loginW = w; loginPos = 0;
  $('login').classList.add('on'); $('login-title').textContent = 'Sign ' + w + ' in to Antigravity'; $('login-url').textContent = '';
  const box = $('login-term'); box.textContent = '';
  const term = loginTerm = new Terminal({fontSize: 13, cursorBlink: true, scrollback: 2000, convertEol: false, theme: {background: '#000000'}});
  loginFit = new FitAddon.FitAddon(); term.loadAddon(loginFit); term.open(box); loginFit.fit();
  const enc = new TextEncoder();
  term.onData(d => { loginSend = loginSend.then(() => loginPost({action: 'write', data: b64(enc.encode(d))})).catch(() => {}); });
  term.onResize(sz => { loginPost({action: 'size', cols: sz.cols, rows: sz.rows}).catch(() => {}); });
  await loginPost({action: 'start', cols: term.cols, rows: term.rows});
  if (seq !== loginSeq) return;
  term.focus();
  clearInterval(loginTimer);
  loginTimer = setInterval(async () => {
    let r; try { r = await (await fetch('/api/login?worker=' + encodeURIComponent(w) + '&since=' + loginPos)).json(); } catch (e) { return; }
    if (seq !== loginSeq) return;
    if (r.data) { const bin = atob(r.data), u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); term.write(u8); loginPos = r.next; }
    $('login-url').innerHTML = r.url && /accounts\.google\.com/.test(r.url) ? 'Open it and sign in with <b>the account for ' + esc(w) + '</b>: <a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.url.slice(0, 60)) + '…</a>' : '';
  }, 500);
  window.addEventListener('resize', fitLogin);
}
function fitLogin() { try { if (loginFit && $('login').classList.contains('on')) loginFit.fit(); } catch (e) {} }
async function closeLogin(check) {
  loginSeq++; clearInterval(loginTimer); window.removeEventListener('resize', fitLogin);
  if (loginTerm) { loginTerm.dispose(); loginTerm = null; }
  if (check) $('login-url').textContent = 'checking the sign-in…';
  let r = {}; try { r = await (await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'stop'})})).json(); } catch (e) {}
  if (check) alert(r.logged_in ? loginW + ' is signed in and sees its quota. Tick it in the project and save.' : loginW + ' is not signed in yet — try again.');
  $('login').classList.remove('on');
  loadSettings(true);
}

// ---- a new project from the dashboard (bin/foreman init-project + forge-bootstrap + its foreman service)
async function openNewProject() {
  $('newproj').classList.add('on'); $('np-form').style.display = ''; $('np-log').style.display = 'none';
  let st = {}; try { st = await (await fetch('/api/settings?project=default')).json(); } catch (e) {}
  const owner = {};
  for (const p of PROJ_LIST) for (const w of String(p.workers || '').split(',').filter(Boolean)) owner[w] = p.name;
  const ws = st.workers || [];
  $('np-workers').innerHTML = ws.length ? ws.map(w => '<label class="chk"><input type="checkbox" data-npw="' + esc(w.name) + '"> ' + esc(w.name) +
    ' <span class="muted">(' + esc(owner[w.name] || (st.values && !st.values.WORKERS ? 'default — all' : 'free')) + ')</span></label>').join(' ') : '<div class="empty">No workers available — add them in Settings.</div>';
}
async function createProject() {
  const v = id => $(id).value.trim();
  const body = {action: 'create', name: v('np-name'), repo: v('np-repo'), lang: v('np-lang'), vision: $('np-vision').value,
                workers: [...document.querySelectorAll('[data-npw]')].filter(c => c.checked).map(c => c.dataset.npw)};
  for (const k of ['name', 'repo', 'lang', 'workers']) $('np-e-' + k).textContent = '';
  let j = await (await fetch('/api/projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
  if (j.errors) { for (const k in j.errors) { const el = $('np-e-' + k); if (el) el.textContent = j.errors[k]; } return; }
  $('np-form').style.display = 'none';
  const log = $('np-log'); log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2500)); j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + ({done: '✔ The project is ready — pick it at the top and give it its first goal.',
    'needs-sudo': '⚠ The project exists; only its service needs root — run the command above.'}[j.state] || '✘ Failed — see the output above.');
  await loadProjects(true);
  if (j.state !== 'failed') { project = body.name; store('af-project', project); }
}
