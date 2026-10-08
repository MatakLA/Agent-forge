// Settings, in six short pages: Workers (kill switch, the pool, adding workers, keys and your model server), Models (who plans
// and reviews, worker models, routing, experiments), Project (where the code lives, approvals, behaviour, tests,
// notifications), Money (budgets and prices), Knowledge (vision, team rules, decisions, lessons, roles) and Limits (all
// projects). Also: where the code lives (dialog), a worker's Antigravity sign-in, a new project.

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
  bx.innerHTML = (res.steps || []).map(s => '<div class="ev ' + (s.ok ? 'good' : 'bad') + '">' + (s.ok ? '✓ ' : '✗ ') + esc(s.label) + (s.output ? ': ' + esc(s.output) : '') + '</div>').join('') +
    '<button class="linkbtn" data-hide-result>hide</button>' || esc(res.error || '');
  refreshSoon();
}
function resumeDev() { killswitch('off', {all: !!(F && F.killswitch && F.killswitch.scope === 'all')}); }
document.addEventListener('click', e => {
  if (e.target.closest('[data-hide-result]')) { $('resume-result').style.display = 'none'; return; }
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = {resume: resumeDev, 'ks-on': () => killswitch('on'), 'ks-hard': () => killswitch('on', {hard: true}), 'ks-all': () => killswitch('on', {all: true})}[a.dataset.act];
  if (act) act();
});

// ---- settings data
let S = null, sLoadedFor = null, sMsg = '';
const LABEL = {PLAN_PROVIDERS: 'Who plans (in order)', REVIEW_PROVIDERS: 'Who reviews (in order)', CLAUDE_MODEL: 'Claude Code model',
  AGY_PLAN_MODEL: 'Antigravity model for planning', AGY_REVIEW_MODEL: 'Antigravity model for reviews', AGY_FALLBACK_MODEL: 'Antigravity fallback (Gemini)',
  WORKER_MODEL: 'Worker model', WORKER_MODEL_RISKY: 'Model for risky tasks', ANTHROPIC_MODEL: 'Anthropic API model (paid)',
  OPENAI_MODEL: 'OpenAI-compatible model (paid)', GEMINI_MODEL: 'Gemini API model (paid)', OWNER_LANG: 'Your language',
  AUTOPILOT: 'Autopilot - picks the next goal from the vision when idle', AUTO_MERGE: 'Merge automatically', CONTINUE_CONVERSATIONS: 'A rework continues the worker\'s conversation',
  MAX_REWORK: 'Reworks per task, at most', MAX_ACTIVE_GOALS: 'Active goals, at most', SPLIT_AFTER_CAPS: 'Split a task after N token caps',
  TASK_TOKEN_BUDGET: 'Target task size (tokens)', MAX_PARALLEL: 'Parallel runs, at most', RUN_TOKEN_CAP: 'Token cap per run',
  RUN_TIMEOUT_MIN: 'Time limit per run (min)', QUOTA_MIN_5H_PCT: 'A worker\'s minimum 5-hour quota (%)', QUOTA_MIN_WEEK_PCT: 'Minimum weekly quota (%)',
  CONSEC_FAIL_TRIP: 'Kill switch after N failed runs in a row', CONTEXT_PACK: 'Context pack in each task', CONTEXT_PACK_CHARS: 'Pack size, at most (characters)',
  SELF_CHECK_ROUNDS: 'Fix failing tests in the same run (rounds)', MAX_TASK_FILES: 'Files per task, at most (more = split first)',
  WORKER_MODEL_LIGHT: 'Model for light roles', LIGHT_ROLES: 'Light roles', AB_MODEL: 'A/B test: alternative worker model',
  AB_SHARE: 'A/B test: share of tasks on it (%)', PLAN_AHEAD: 'Plan ahead while the workers wait for quota', MAINTENANCE: 'Check main without AI while idle',
  TEST_CMD: 'Test command (empty = pytest)', GATE_IMAGE: 'Test image (empty = the worker image)', NOTIFY_URL: 'Where to (ntfy topic URL, Slack / Discord webhook)',
  NOTIFY_FORMAT: 'Format', NOTIFY_EVENTS: 'When', RUNS_KEEP_DAYS: 'Compress run transcripts after (days)', API_WORKERS_POLICY: 'When API workers get work',
  WORKER_MODEL_CLAUDE: 'Claude Code model', WORKER_MODEL_CODEX: 'Codex model', WORKER_MODEL_GEMINI: 'Gemini CLI model',
  WORKER_MODEL_COMPAT: 'Model on your server (Codex)', WORKER_MODEL_AIDER: 'Model on your server (Aider)', COMPAT_BASE_URL: 'Your model server (OpenAI-compatible base URL)',
  PRICE_COMPAT: 'Your model server: input, cached, output (USD / 1M tokens)', API_RUN_BUDGET_USD: 'Per run', API_DAILY_BUDGET_USD: 'Per day, all projects',
  API_MONTHLY_BUDGET_USD: 'Per month, all projects (0 = none)', PROJECT_DAILY_BUDGET_USD: 'Per day, this project (0 = none)',
  PROJECT_MONTHLY_BUDGET_USD: 'Per month, this project (0 = none)', WORKER_DAILY_BUDGET_USD: 'Per day, per worker',
  RUN_MAX_TOOL_CALLS: 'Tool calls per run, at most', PRICE_CODEX: 'Codex: input, cached, output (USD / 1M tokens)', PRICE_GEMINI: 'Gemini: input, cached, output (USD / 1M tokens)',
  APPROVE: 'Wait for my OK before…', MODEL_ROUTING: 'Model routing', ROUTING_MIN_TASKS: 'Routing: merged tasks a model needs per role',
  ROUTING_MIN_FIRST_PASS: 'Routing: approved first time, at least (%)'};
const PROVLABEL = {'claude-cli': 'Claude Code (subscription)', 'agy-claude': 'Claude via Antigravity', 'agy-gemini': 'Gemini via Antigravity',
  'anthropic-api': 'Anthropic API (paid)', 'openai-api': 'OpenAI API (paid)', 'gemini-api': 'Gemini API (paid)'};
async function loadSettings(force) {
  if (!force && sLoadedFor === project && S) return;
  sLoadedFor = project;
  try { S = await (await fetch('/api/settings?project=' + encodeURIComponent(project))).json(); } catch (e) { S = {error: String(e)}; }
  schedule(true);
}
function sval(k) {
  const el = $('set-' + k); if (!el) return null;
  if (el.dataset.kind === 'prov') return [...el.querySelectorAll('.prov')].filter(r => r.querySelector('input').checked).map(r => r.dataset.p).join(',');
  if (el.dataset.kind === 'pool' || el.dataset.kind === 'multi') return [...el.querySelectorAll('input:checked')].map(i => i.value).join(',');
  if (el.dataset.kind === 'wbudget') {
    const keep = Object.fromEntries(String((S.values || {})[k] || '').split(',').filter(x => x.includes('=')).map(x => x.split('=')));
    el.querySelectorAll('input').forEach(i => { if (i.value.trim() && Number(i.value) > 0) keep[i.dataset.w] = i.value.trim(); else delete keep[i.dataset.w]; });
    return Object.entries(keep).map(([w, v]) => w + '=' + v).join(',');
  }
  if (el.type === 'checkbox') return el.checked ? '1' : '0';
  return el.value.trim();
}
const V = () => (S && S.values) || {};
const num = k => '<input type="number" id="set-' + k + '" value="' + esc(V()[k]) + '">';
const txt = (k, ph) => '<input type="text" id="set-' + k + '" value="' + esc(V()[k]) + '"' + (ph ? ' placeholder="' + esc(ph) + '"' : '') + '>';
const tog = k => '<label class="switch"><input type="checkbox" id="set-' + k + '"' + (String(V()[k]) === '1' ? ' checked' : '') + '><i></i></label>';
const sel = (k, opts) => '<select id="set-' + k + '">' + opts.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o];
  return '<option value="' + esc(v) + '"' + (String(v) === String(V()[k]) ? ' selected' : '') + '>' + esc(l) + '</option>'; }).join('') + '</select>';
const multi = (k, opts) => { const on = String(V()[k] || '').split(',').filter(Boolean);
  return '<div id="set-' + k + '" data-kind="multi" class="multi">' + opts.map(([v, l]) => '<label class="chk"><input type="checkbox" value="' + esc(v) + '"' + (on.includes(v) ? ' checked' : '') + '> ' + esc(l) + '</label>').join('') + '</div>'; };
function provEditor(k) {
  const on = String(V()[k] || '').split(',').filter(Boolean), all = on.concat((S.providers || []).filter(p => !on.includes(p)));
  return '<div class="provlist" id="set-' + k + '" data-kind="prov">' + all.map(p => '<div class="prov" data-p="' + esc(p) + '"><label><input type="checkbox"' +
    (on.includes(p) ? ' checked' : '') + '> ' + esc(PROVLABEL[p] || p) + '</label><span><button class="iconbtn sm" data-mv="-1">↑</button><button class="iconbtn sm" data-mv="1">↓</button></span></div>').join('') + '</div>';
}
function modelSel(k, allowEmpty, emptyLabel) {
  const v = V()[k], ms = (S.models || []).map(m => m.id), opts = (allowEmpty ? [''] : []).concat(ms.includes(v) || !v ? ms : [v].concat(ms));
  return '<select id="set-' + k + '">' + opts.map(m => '<option value="' + esc(m) + '"' + (m === v ? ' selected' : '') + '>' +
    esc(m ? ((S.models || []).find(x => x.id === m) || {name: m}).name + ' (' + m + ')' : emptyLabel) + '</option>').join('') + '</select>';
}
const field = (k, inner, help) => '<div class="setrow"><label for="set-' + k + '">' + esc(LABEL[k] || k) + (help ? '<span class="help">' + help + '</span>' : '') + '</label><div>' + inner + '</div></div>';
const card = (title, body, intro) => '<div class="setcard"><h3>' + title + '</h3>' + (intro ? '<div class="muted small intro">' + intro + '</div>' : '') + body + '</div>';

// ---- the six pages
function pageWorkers() {
  const ks = F && F.killswitch, pool = String(V().WORKERS || '').split(',').filter(Boolean), sec = S.secrets || {}, R = S.runtimes || {};
  let h = card('Kill switch', '<div class="btnrow">' + (ks ? '<button class="primary sm" data-act="resume">Continue</button>' :
      '<button class="warnbtn" data-act="ks-on">Stop this project</button><button class="badbtn" data-act="ks-hard">Stop hard</button>') +
      '<button class="badbtn ghost" data-act="ks-all">Stop all projects</button></div>',
    ks ? 'ON' + (ks.scope === 'all' ? ' for all projects' : ' for this project') + (ks.hard ? ' (hard)' : '') + ' — ' + esc(ks.reason || '') : 'Off - the project is running. Hard also stops the worker containers.');
  h += card('This project\'s workers', '<div id="set-WORKERS" data-kind="pool" class="pool">' + (S.workers || []).map(w => '<div class="poolrow"><label><input type="checkbox" value="' + esc(w.name) +
      '"' + (pool.includes(w.name) ? ' checked' : '') + '> ' + esc(w.name) + ' <span class="rt rt-' + esc(w.runtime || 'agy') + '">' + esc(RTNAME[w.runtime || 'agy'] || w.runtime) +
      '</span> <span class="muted">(' + esc(w.state) + ')</span></label>' + ((w.runtime || 'agy') === 'agy' ? '<button class="mini" data-login="' + esc(w.name) + '">sign in…</button>' : '') + '</div>').join('') +
      '</div><div class="btnrow"><button class="mini" id="add-worker">+ Add a worker</button><span id="addw-status" class="muted small"></span></div>' + addWorkerForm(),
    'Ticked workers belong to project “' + esc(project) + '” (none ticked = all). Each is its own Antigravity account, API key or connection to your model server.');
  h += card('API keys and your model server', '<div class="keys">' + ['anthropic.key', 'openai.key', 'gemini.key', 'compat.key'].map(k => '<span>' + esc(k.split('.')[0] === 'compat' ? 'your server' : k.split('.')[0]) +
      ' ' + (sec[k] ? '<b class="plus">✓</b>' : '<span class="muted">—</span>') + '</span>').join('') + '<button class="mini" id="key-change">set a key…</button></div>' +
    '<div id="key-form" style="display:none" class="btnrow"><select id="key-name"><option>anthropic.key</option><option>openai.key</option><option>gemini.key</option><option value="compat.key">compat.key (your server)</option></select>' +
    '<input type="password" id="key-val" autocomplete="off" placeholder="the key (stored on the server only)" style="flex:1;min-width:180px"><button class="mini" id="key-save">Save key</button></div>' +
    field('COMPAT_BASE_URL', txt('COMPAT_BASE_URL', 'http://192.168.1.20:11434/v1  or  https://openrouter.ai/api/v1'),
      'Ollama, vLLM, LM Studio, OpenRouter. On your network or without TLS, the workers may reach exactly this address and port.') +
    field('WORKER_MODEL_COMPAT', txt('WORKER_MODEL_COMPAT', 'e.g. qwen3-coder:30b')) + field('WORKER_MODEL_AIDER', txt('WORKER_MODEL_AIDER', 'e.g. qwen3-coder:30b')) +
    field('WORKER_MODEL_CLAUDE', txt('WORKER_MODEL_CLAUDE', 'sonnet')) + field('WORKER_MODEL_CODEX', txt('WORKER_MODEL_CODEX', 'e.g. gpt-5.1-codex')) +
    field('WORKER_MODEL_GEMINI', txt('WORKER_MODEL_GEMINI', 'the CLI\'s default')) +
    field('API_WORKERS_POLICY', sel('API_WORKERS_POLICY', [['overflow', 'only when no subscription worker can take the task'], ['always', 'first']])),
    'Keys are stored on the server (mode 0600) and reach only the agent\'s environment. Budgets: the Money page.');
  return h;
}
function addWorkerForm() {
  const R = S.runtimes || {}, sec = S.secrets || {};
  return '<div id="addw-form" style="display:none" class="addw"><div class="muted small">What will the new worker run on?</div>' +
    Object.entries(R).map(([k, r], i) => '<label class="chk block"><input type="radio" name="addw-rt" value="' + esc(k) + '"' + (i ? '' : ' checked') + '> <b>' + esc(RTNAME[k] || k) +
      '</b> <span class="muted">— ' + esc(RTDESC[k] || r.label) + '</span></label>').join('') +
    '<div id="addw-url" style="display:none"><input type="text" id="addw-urlin" placeholder="your model server, e.g. http://192.168.1.20:11434/v1" value="' + esc(V().COMPAT_BASE_URL || '') + '">' +
    '<input type="text" id="addw-model" placeholder="model name on it, e.g. qwen3-coder:30b"></div>' +
    '<div id="addw-key" style="display:none"><input type="password" id="addw-keyin" autocomplete="off" placeholder="API key (stored on the server only)"></div>' +
    '<div class="btnrow"><button class="primary sm" id="addw-go">Add</button><button class="mini" id="addw-cancel">Cancel</button></div></div>';
}
function pageModels() {
  return card('Orchestrator - who plans and reviews', field('PLAN_PROVIDERS', provEditor('PLAN_PROVIDERS')) + field('REVIEW_PROVIDERS', provEditor('REVIEW_PROVIDERS')) +
      field('CLAUDE_MODEL', sel('CLAUDE_MODEL', [...new Set(['sonnet', 'opus', 'haiku', 'fable', V().CLAUDE_MODEL].filter(Boolean))])) +
      field('AGY_PLAN_MODEL', modelSel('AGY_PLAN_MODEL')) + field('AGY_REVIEW_MODEL', modelSel('AGY_REVIEW_MODEL')) + field('AGY_FALLBACK_MODEL', modelSel('AGY_FALLBACK_MODEL')) +
      '<details><summary class="muted">Paid APIs (only when ticked above and a key is set)</summary>' + field('ANTHROPIC_MODEL', txt('ANTHROPIC_MODEL')) +
      field('OPENAI_MODEL', txt('OPENAI_MODEL')) + field('GEMINI_MODEL', txt('GEMINI_MODEL')) + '</details>',
      'Tried in order; one out of quota is skipped until its reset.') +
    card('Worker models (Antigravity)', field('WORKER_MODEL', modelSel('WORKER_MODEL', true, 'default (' + (S.limits_worker_model || '') + ')')) +
      field('WORKER_MODEL_RISKY', modelSel('WORKER_MODEL_RISKY', true, 'same as the others')) + field('WORKER_MODEL_LIGHT', modelSel('WORKER_MODEL_LIGHT', true, 'same as the others')) +
      field('LIGHT_ROLES', txt('LIGHT_ROLES')) + '<div class="btnrow"><button class="mini" id="refresh-models">Refresh the model list from Antigravity</button></div>') +
    card('Model routing', field('MODEL_ROUTING', sel('MODEL_ROUTING', [['off', 'off - the rules above'], ['auto', 'auto - per role, the cheapest model that has proven itself']])) +
      field('ROUTING_MIN_TASKS', num('ROUTING_MIN_TASKS')) + field('ROUTING_MIN_FIRST_PASS', num('ROUTING_MIN_FIRST_PASS')),
      'With auto, each new task uses the worker model with the least quota per merged task among those that merged enough tasks of its role here and were approved first time often enough. Risky tasks keep their model.') +
    card('Experiments', field('AB_MODEL', modelSel('AB_MODEL', true, 'no A/B test')) + field('AB_SHARE', num('AB_SHARE')) +
      field('CONTEXT_PACK', sel('CONTEXT_PACK', [['on', 'on - every task'], ['ab', 'ab - half the tasks, to measure it'], ['off', 'off']])) + field('CONTEXT_PACK_CHARS', num('CONTEXT_PACK_CHARS')),
      'Results with 95 % intervals and a button to adopt the winner: Insights → Experiments.');
}
function pageProject() {
  const fk = V().FORGE_KIND || '';
  return card('Where the code lives', '<div class="setrow"><label>Repository</label><div>' + (fk === 'github' || fk === 'gitlab' ? esc(fk === 'github' ? 'GitHub' : 'GitLab') + ': <b>' +
      esc(V().UPSTREAM_REPO) + '</b>' : fk === 'forgejo' ? 'internal Forgejo only' : 'not chosen yet') + ' <button class="mini" id="open-setup">change…</button></div></div>') +
    card('Approvals', field('APPROVE', multi('APPROVE', [['plan', 'a goal\'s plan is carried out'], ['risky', 'a risky task starts'], ['merge', 'a reviewed branch is merged']])),
      'What you tick waits for you under Overview → Needs you (and a notification, if set). Nothing ticked = fully automatic.') +
    card('Behaviour', field('AUTOPILOT', tog('AUTOPILOT')) + field('AUTO_MERGE', tog('AUTO_MERGE')) + field('CONTINUE_CONVERSATIONS', tog('CONTINUE_CONVERSATIONS')) +
      field('PLAN_AHEAD', tog('PLAN_AHEAD')) + field('MAINTENANCE', tog('MAINTENANCE')) + field('OWNER_LANG', txt('OWNER_LANG')) +
      '<details><summary class="muted">Task size and retries</summary>' + field('TASK_TOKEN_BUDGET', num('TASK_TOKEN_BUDGET')) + field('MAX_TASK_FILES', num('MAX_TASK_FILES')) +
      field('SELF_CHECK_ROUNDS', num('SELF_CHECK_ROUNDS')) + field('MAX_REWORK', num('MAX_REWORK')) + field('SPLIT_AFTER_CAPS', num('SPLIT_AFTER_CAPS')) +
      field('MAX_ACTIVE_GOALS', num('MAX_ACTIVE_GOALS')) + '</details>') +
    card('Tests', field('TEST_CMD', txt('TEST_CMD', 'python3 -m pytest -q')) + field('GATE_IMAGE', txt('GATE_IMAGE')) + field('RUNS_KEEP_DAYS', num('RUNS_KEEP_DAYS')),
      'The same command runs in the gate, in the worker\'s self-check and in the maintenance of main.') +
    card('Notifications', field('NOTIFY_URL', txt('NOTIFY_URL', 'https://ntfy.sh/your-topic')) + field('NOTIFY_FORMAT', sel('NOTIFY_FORMAT', ['ntfy', 'slack', 'discord', 'json'])) +
      field('NOTIFY_EVENTS', multi('NOTIFY_EVENTS', [['goal_done', 'a goal is done'], ['approval', 'something waits for my approval'], ['task_blocked', 'a task is blocked'],
        ['questions', 'the planner has questions'], ['killswitch', 'the kill switch tripped'], ['quota_out', 'all workers are out of quota'], ['auth', 'an API key was rejected']])));
}
function pageMoney() {
  const api = (S.workers || []).filter(w => (S.runtimes || {})[w.runtime] && (S.runtimes[w.runtime] || {}).api);
  const wb = Object.fromEntries(String(V().WORKER_DAILY_BUDGET_USD || '').split(',').filter(x => x.includes('=')).map(x => x.split('=')));
  return card('Budgets (USD)', field('API_RUN_BUDGET_USD', txt('API_RUN_BUDGET_USD')) + field('API_DAILY_BUDGET_USD', txt('API_DAILY_BUDGET_USD')) +
      field('API_MONTHLY_BUDGET_USD', txt('API_MONTHLY_BUDGET_USD')) + field('PROJECT_DAILY_BUDGET_USD', txt('PROJECT_DAILY_BUDGET_USD')) +
      field('PROJECT_MONTHLY_BUDGET_USD', txt('PROJECT_MONTHLY_BUDGET_USD')) +
      field('WORKER_DAILY_BUDGET_USD', api.length ? '<div id="set-WORKER_DAILY_BUDGET_USD" data-kind="wbudget" class="wbudget">' + api.map(w => '<label>' + esc(w.name) +
        ' <input type="number" min="0" step="0.5" data-w="' + esc(w.name) + '" value="' + esc(wb[w.name] || '') + '" placeholder="no cap"></label>').join('') + '</div>' : small('no API workers')),
      'Spent today: <b>' + usd(S.api_spent_today) + '</b>, this month: <b>' + usd(S.api_spent_month) + '</b>. Every check assumes the new run and each running API run cost a full run budget. ' +
      'The day and month budgets count all projects; details in Insights → Money.') +
    card('Caps per run', field('RUN_MAX_TOOL_CALLS', num('RUN_MAX_TOOL_CALLS'))) +
    card('Prices for the cost estimate', field('PRICE_CODEX', txt('PRICE_CODEX')) + field('PRICE_GEMINI', txt('PRICE_GEMINI')) + field('PRICE_COMPAT', txt('PRICE_COMPAT')),
      'Codex, Gemini CLI and Aider report tokens, not money - set your models\' prices. 0,0,0 for your own hardware.');
}
function pageLimits() {
  return card('Limits <span class="pill warn">all projects</span>', ['MAX_PARALLEL', 'RUN_TOKEN_CAP', 'RUN_TIMEOUT_MIN', 'QUOTA_MIN_5H_PCT', 'QUOTA_MIN_WEEK_PCT', 'CONSEC_FAIL_TRIP']
    .map(k => field(k, num(k))).join('') + ((S.workers || []).length > Number(V().MAX_PARALLEL) ? '<div class="seterr">There are more workers (' + S.workers.length +
    ') than parallel runs allowed - some will always wait.</div>' : ''));
}
// ---- Knowledge: the team's documents, edited here (a role is saved as this project's own version)
const DOCS = {list: null, at: 0};
async function loadDocs(force) {
  if (!force && DOCS.list && Date.now() - DOCS.at < 30000) return;
  try { DOCS.list = await getJSON('/api/docs'); } catch (e) { DOCS.list = {error: String(e)}; }
  DOCS.at = Date.now(); schedule(true);
}
function pageKnowledge() {
  loadDocs();
  const L = DOCS.list;
  if (!L) return '<div class="empty">loading…</div>';
  if (!Array.isArray(L)) return '<div class="empty">' + esc(L.error || 'not available') + '</div>';
  const main = L.filter(d => !d.name.startsWith('role:')), roles = L.filter(d => d.name.startsWith('role:'));
  return card('What the team knows', main.map(d => '<div class="docrow" data-doc="' + esc(d.name) + '"><b>' + esc(d.title) + '</b> ' + small(esc(d.about)) +
      (d.exists ? '' : ' <span class="badge">empty</span>') + '</div>').join(''), 'Every plan, review and task reads these. Edit them like you would brief a new colleague.') +
    card('Roles (' + roles.length + ')', '<div class="roles">' + roles.map(d => '<button class="rolebtn" data-doc="' + esc(d.name) + '">' + esc(d.title) +
      (d.customized ? ' <span class="badge">yours</span>' : '') + '</button>').join('') + '</div>',
      'Each worker gets its role\'s full playbook with the task. Saving a role keeps your version for this project; “reset” brings back the standard one.');
}
async function openDoc(name) {
  openSheet('doc', name, '<div class="empty">loading…</div>', true);
  let d; try { d = await getJSON('/api/doc', {name}); } catch (e) { d = {error: String(e)}; }
  if (d.error) return setSheet('doc', name, '<div class="empty">' + esc(d.error) + '</div>');
  setSheet('doc', name, '<h2>' + esc(name.startsWith('role:') ? 'Role: ' + name.slice(5) : name.replace('-', ' ')) + '</h2>' +
    (d.customized ? '<div class="note">This project uses its own version of this role.</div>' : '') +
    '<textarea class="doced" id="doc-text" spellcheck="false">' + esc(d.text) + '</textarea>' +
    '<div class="btnrow"><button class="primary sm" data-docsave="' + esc(name) + '">Save</button>' +
    (d.customized ? '<button class="mini" data-docreset="' + esc(name) + '">Reset to the standard role</button>' : '') + small('Markdown. Applies from the next plan / review / task.') + '</div>');
}
document.addEventListener('click', async e => {
  const d = e.target.closest('[data-doc]'); if (d) { openDoc(d.dataset.doc); return; }
  const s = e.target.closest('[data-docsave]');
  if (s) { s.disabled = true; const ok = done(await post('/api/doc', {name: s.dataset.docsave, text: $('doc-text').value}), 'Saved'); s.disabled = false; if (ok) { loadDocs(true); } return; }
  const r = e.target.closest('[data-docreset]');
  if (r) { if (!confirm('Delete this project\'s version of the role and use the standard one?')) return;
    if (done(await post('/api/doc', {name: r.dataset.docreset, reset: true}), 'Back to the standard role')) { loadDocs(true); openDoc(r.dataset.docreset); } }
});

function settingsHtml() {
  if (!S) { loadSettings(); return '<div class="empty">loading…</div>'; }
  if (S.error) return '<div class="empty">Settings are not available: ' + esc(S.error) + '</div>';
  const p = SUB.settings;
  const body = p === 'models' ? pageModels() : p === 'project' ? pageProject() : p === 'money' ? pageMoney() : p === 'knowledge' ? pageKnowledge() : p === 'limits' ? pageLimits() : pageWorkers();
  return body + (p === 'knowledge' ? '' : '<div class="btnrow sticky"><button class="primary" id="save-settings">Save changes</button><span class="muted small" id="save-msg">' + esc(sMsg) + '</span></div>');
}
function renderSettings(force) {
  const el = $('settings');
  if (!force && el.contains(document.activeElement) && document.activeElement !== document.body) return;   // never under the owner's fingers
  setHTML('settings', settingsHtml(), force);
}
async function saveSettings() {
  const changes = {};
  for (const k of Object.keys(V())) { const v = sval(k); if (v !== null && String(v) !== String(V()[k])) changes[k] = v; }
  if (!Object.keys(changes).length) { sMsg = 'nothing changed'; $('save-msg').textContent = sMsg; return; }
  let r;
  try { r = await (await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, changes})})).json(); }
  catch (e) { r = {ok: false, errors: {_: String(e)}}; }
  const errs = r.errors || {};
  sMsg = r.ok ? 'saved: ' + Object.keys(changes).join(', ') + ' — applies from the next round (within 20 s)' : 'not saved — fix the marked fields' + (errs._ ? ': ' + errs._ : '');
  if (r.ok) { toast('Settings saved'); document.activeElement && document.activeElement.blur(); await loadSettings(true); return; }
  // keep everything the owner typed: only mark the bad fields
  document.querySelectorAll('#settings .seterr.live').forEach(x => x.remove());
  for (const [k, msg] of Object.entries(errs)) { const el = $('set-' + k); if (el) el.insertAdjacentHTML('afterend', '<div class="seterr live">' + esc(msg) + '</div>'); }
  $('save-msg').textContent = sMsg;
}
document.addEventListener('click', async e => {
  const mv = e.target.closest('[data-mv]');
  if (mv) { const row = mv.closest('.prov'), d = Number(mv.dataset.mv);
    if (d < 0 && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
    if (d > 0 && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row); return; }
  if (e.target.id === 'save-settings') { saveSettings(); return; }
  if (e.target.id === 'refresh-models') { e.target.textContent = 'loading…';
    try { await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, refresh_models: true})}); } catch (err) {}
    loadSettings(true); return; }
  if (e.target.id === 'add-worker') { const f = $('addw-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; addwRt(); return; }
  if (e.target.id === 'addw-go') { addWorkerGo(); return; }
  if (e.target.id === 'addw-cancel') { $('addw-form').style.display = 'none'; return; }
  if (e.target.id === 'key-change') { const f = $('key-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; return; }
  if (e.target.id === 'key-save') { const v = $('key-val');
    if (!v.value.trim()) { v.focus(); return; }
    const r = await post('/api/secret', {name: $('key-name').value, value: v.value.trim()}); v.value = '';
    done(r, 'Key saved'); loadSettings(true); return; }
  if (e.target.id === 'open-setup') { openSetup(); return; }
  if (e.target.id === 'np-create') { createProject(); return; }
  if (e.target.id === 'np-close') { $('newproj').classList.remove('on'); await loadProjects(true); if ($('proj').value !== project) switchProject(project); return; }
  const lg = e.target.closest('[data-login]'); if (lg) { openLogin(lg.dataset.login); return; }
  if (e.target.id === 'login-send') { const i = $('login-in');
    await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'input', text: i.value})}); i.value = ''; return; }
  if (e.target.id === 'login-done' || e.target.id === 'login-close') { closeLogin(e.target.id === 'login-done'); return; }
  if (e.target.id === 'su-close' || e.target.id === 'su-later') { $('setup').classList.remove('on'); setupDismissed[project] = true; return; }
  if (e.target.id === 'su-go') { setupGo(); return; }
});

// ---- add a worker: an Antigravity account (signed in from the browser), an API key, or your model server
function addwRt() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', R = (S && S.runtimes) || {}, r = R[rt] || {};
  $('addw-url').style.display = r.endpoint ? '' : 'none';
  $('addw-key').style.display = r.key && !(S.secrets || {})[r.key] ? '' : 'none';
  $('addw-keyin').placeholder = r.optional_key ? 'API key, only if your server wants one (OpenRouter does, Ollama does not)' : 'API key (stored on the server only)';
}
document.addEventListener('change', e => { if (e.target.name === 'addw-rt') addwRt(); if (e.target.name === 'su-kind') suRefresh(); });
async function addWorkerGo() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', st = $('addw-status'), r = (S.runtimes || {})[rt] || {};
  if (r.endpoint) {
    const url = $('addw-urlin').value.trim(), model = $('addw-model').value.trim(), changes = {};
    if (!url) { $('addw-urlin').focus(); return; }
    if (url !== V().COMPAT_BASE_URL) changes.COMPAT_BASE_URL = url;
    if (model) changes[rt === 'aider' ? 'WORKER_MODEL_AIDER' : 'WORKER_MODEL_COMPAT'] = model;
    if (Object.keys(changes).length) { const s = await post('/api/settings', {changes});
      if (!s.ok) { st.textContent = 'not saved: ' + Object.entries(s.errors || {}).map(([k, v]) => k + ' ' + v).join('; '); return; } }
  }
  const kin = $('addw-keyin');
  if (r.key && !(S.secrets || {})[r.key] && kin.value.trim()) {
    const k = await post('/api/secret', {name: r.key, value: kin.value.trim()}); kin.value = '';
    if (!k.ok) { st.textContent = 'the key could not be saved: ' + (k.message || k.error || ''); return; }
  } else if (r.key && !r.optional_key && !(S.secrets || {})[r.key]) { kin.focus(); return; }
  $('addw-form').style.display = 'none';
  let j = await post('/api/workers', {action: 'add', runtime: rt});
  while (j.state === 'running') { st.textContent = 'creating ' + (j.worker || 'the worker') + '… (the first time builds its image - a few minutes)'; await new Promise(res => setTimeout(res, 3000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  st.textContent = j.state !== 'done' ? 'failed: ' + (j.log || j.error || '').slice(-300) : rt === 'agy' ? j.worker + ' created — now sign it in' : j.worker + ' created and ready';
  await loadSettings(true);
  if (j.state === 'done' && rt === 'agy') openLogin(j.worker);
}

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

// ---- a worker's Antigravity sign-in, in its terminal
let loginW = null, loginTimer = null;
async function openLogin(w) {
  loginW = w;
  $('login').classList.add('on'); $('login-title').textContent = 'Sign ' + w + ' in to Antigravity'; $('login-out').textContent = 'starting…';
  await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: w, action: 'start'})});
  clearInterval(loginTimer);
  loginTimer = setInterval(async () => {
    let r; try { r = await (await fetch('/api/login?worker=' + encodeURIComponent(w))).json(); } catch (e) { return; }
    const o = $('login-out'), atBottom = o.scrollHeight - o.scrollTop - o.clientHeight < 30;
    o.textContent = r.output || '(waiting for output…)'; if (atBottom) o.scrollTop = o.scrollHeight;
    $('login-url').innerHTML = r.url ? 'Open it and sign in with <b>the account for ' + esc(w) + '</b>: <a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.url.slice(0, 60)) + '…</a>' : '';
  }, 1500);
}
async function closeLogin(check) {
  clearInterval(loginTimer);
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
    ' <span class="muted">(' + esc(owner[w.name] || (st.values && !st.values.WORKERS ? 'default — all' : 'free')) + ')</span></label>').join(' ') : 'no workers — add them in Settings';
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
projectResets.push(() => { S = null; sLoadedFor = null; sMsg = ''; DOCS.list = null; });
