// Settings tab: kill switch, workers (add, sign in), keys, models, project settings; where the code lives; new projects.
// ---- kill switch (per project; "all" = every project)
let ksBusy = false;
async function killswitch(action, opts) {
  opts = opts || {};
  if (ksBusy) return;
  const hard = !!opts.hard, all = !!opts.all;
  if (action === 'on' && !confirm((all ? 'Stop ALL projects' : 'Stop project “' + project + '”') + (hard ? ' HARD (also stops the worker containers)' : '') + '?')) return;
  if (action === 'off' && F && F.killswitch && F.killswitch.hard && !confirm('The kill switch is in hard mode. Start the workers again and continue?')) return;
  ksBusy = true;
  const box = document.getElementById('resume-result');
  box.style.display = 'block'; box.innerHTML = '<div class="ev">' + (action === 'on' ? 'stopping…' : 'continuing…') + '</div>';
  let res;
  try { res = await (await fetch('/api/killswitch', {method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify({project, action, hard, all})})).json(); }
  catch (e) { res = {ok: false, steps: [{label: 'spojenie', ok: false, output: String(e)}]}; }
  ksBusy = false;
  box.innerHTML = (res.steps || []).map(s => '<div class="ev ' + (s.ok ? 'good' : 'bad') + '">' + (s.ok ? '✓ ' : '✗ ') +
    esc(s.label) + (s.output ? ': ' + esc(s.output) : '') + '</div>').join('') || esc(res.error || '');
  poll();
}
function resumeDev() { killswitch('off', {all: !!(F && F.killswitch && F.killswitch.scope === 'all')}); }

// ---- settings tab
let S = null, sLoadedFor = null, sErr = {}, sMsg = '';
const LABEL = {PLAN_PROVIDERS: 'Who plans (in order)', REVIEW_PROVIDERS: 'Who reviews (in order)', CLAUDE_MODEL: 'Claude Code model',
  AGY_PLAN_MODEL: 'Antigravity model for planning', AGY_REVIEW_MODEL: 'Antigravity model for reviews', AGY_FALLBACK_MODEL: 'Antigravity fallback (Gemini)',
  WORKER_MODEL: 'Worker model', WORKER_MODEL_RISKY: 'Worker model for risky tasks', ANTHROPIC_MODEL: 'Anthropic API model (paid)',
  OPENAI_MODEL: 'OpenAI-compatible model (paid)', GEMINI_MODEL: 'Gemini API model (paid)', OWNER_LANG: 'Your language',
  AUTOPILOT: 'Autopilot (picks the next goals itself)', AUTO_MERGE: 'Automatic merge', CONTINUE_CONVERSATIONS: 'Continue the conversation on a rework',
  MAX_REWORK: 'Max. reworks per task', MAX_ACTIVE_GOALS: 'Max. active goals', SPLIT_AFTER_CAPS: 'Split a task after N token caps',
  TASK_TOKEN_BUDGET: 'Target task size (tokens)', MAX_PARALLEL: 'Max. parallel runs', RUN_TOKEN_CAP: 'Token cap per run',
  RUN_TIMEOUT_MIN: 'Run time limit (min)', QUOTA_MIN_5H_PCT: 'Worker\'s minimum 5-hour quota (%)', QUOTA_MIN_WEEK_PCT: 'Minimum weekly quota (%)',
  CONSEC_FAIL_TRIP: 'Kill switch after N failures in a row',
  CONTEXT_PACK: 'Context pack in each task (on / off / ab = half the tasks, to measure)', CONTEXT_PACK_CHARS: 'Max. pack size (characters)',
  SELF_CHECK_ROUNDS: 'Fix failing tests in the same run (max. rounds)', MAX_TASK_FILES: 'Max. files per task (more = split up front)',
  WORKER_MODEL_LIGHT: 'Model for light roles', LIGHT_ROLES: 'Light roles (comma-separated)', AB_MODEL: 'A/B test: alternative worker model',
  AB_SHARE: 'A/B test: share of tasks on it (%)', PLAN_AHEAD: 'Plan ahead while the workers wait for quota',
  MAINTENANCE: 'LLM-free maintenance of main while idle (tests 3×, docstrings)', TEST_CMD: 'Test command (empty = pytest)',
  GATE_IMAGE: 'Test image (empty = the worker image)', NOTIFY_URL: 'Notifications: URL (ntfy topic, Slack/Discord webhook)',
  NOTIFY_FORMAT: 'Notifications: format', NOTIFY_EVENTS: 'Notifications: events', RUNS_KEEP_DAYS: 'Compress run transcripts after (days)',
  API_WORKERS_POLICY: 'When they get work', WORKER_MODEL_CLAUDE: 'Claude Code model (empty = default)',
  WORKER_MODEL_CODEX: 'Codex model (empty = default)', WORKER_MODEL_GEMINI: 'Gemini CLI model (empty = default)',
  API_RUN_BUDGET_USD: 'Cap per run (USD)', API_DAILY_BUDGET_USD: 'Cap per day, all projects (USD)',
  RUN_MAX_TOOL_CALLS: 'Max. tool calls per run', PRICE_CODEX: 'Codex price: input, cached, output (USD / 1M tokens)',
  PRICE_GEMINI: 'Gemini price: input, cached, output (USD / 1M tokens)'};
const PROVLABEL = {'claude-cli': 'Claude Code (subscription)', 'agy-claude': 'Claude via Antigravity', 'agy-gemini': 'Gemini via Antigravity',
  'anthropic-api': 'Anthropic API (paid)', 'openai-api': 'OpenAI API (paid)', 'gemini-api': 'Gemini API (paid)'};
async function loadSettings(force) {
  if (!force && sLoadedFor === project && S) return;
  sLoadedFor = project;
  try { S = await (await fetch('/api/settings?project=' + encodeURIComponent(project))).json(); } catch (e) { S = {error: String(e)}; }
  render(true);
}
function sval(k) { const el = document.getElementById('set-' + k); if (!el) return null;
  if (el.dataset.kind === 'prov') return [...el.querySelectorAll('.prov')].filter(r => r.querySelector('input').checked).map(r => r.dataset.p).join(',');
  if (el.dataset.kind === 'pool') return [...el.querySelectorAll('input:checked')].map(i => i.value).join(',');
  if (el.type === 'checkbox') return el.checked ? '1' : '0';
  return el.value.trim(); }
function provEditor(k, v) {
  const on = String(v || '').split(',').filter(Boolean), all = on.concat((S.providers || []).filter(p => !on.includes(p)));
  return '<div class="provlist" id="set-' + k + '" data-kind="prov">' + all.map(p => '<div class="prov" data-p="' + esc(p) + '"><label><input type="checkbox"' +
    (on.includes(p) ? ' checked' : '') + '> ' + esc(PROVLABEL[p] || p) + '</label><span><button class="mini" data-mv="-1">↑</button><button class="mini" data-mv="1">↓</button></span></div>').join('') + '</div>';
}
function modelSel(k, v, allowEmpty, emptyLabel) {
  const ms = (S.models || []).map(m => m.id), opts = (allowEmpty ? [''] : []).concat(ms.includes(v) || !v ? ms : [v].concat(ms));
  return '<select id="set-' + k + '">' + opts.map(m => '<option value="' + esc(m) + '"' + (m === v ? ' selected' : '') + '>' +
    esc(m ? ((S.models || []).find(x => x.id === m) || {name: m}).name + ' (' + m + ')' : emptyLabel) + '</option>').join('') + '</select>';
}
function field(k, inner) { return '<div class="setrow"><label for="set-' + k + '">' + esc(LABEL[k] || k) + '</label><div>' + inner +
  (sErr[k] ? '<div class="seterr">' + esc(sErr[k]) + '</div>' : '') + '</div></div>'; }
function settingsHtml() {
  if (!S) { loadSettings(); return '<div class="empty">loading…</div>'; }
  if (S.error) return '<div class="empty">Settings are not available: ' + esc(S.error) + '</div>';
  const V = S.values || {}, num = k => '<input type="number" id="set-' + k + '" value="' + esc(V[k]) + '">',
        txt = k => '<input type="text" id="set-' + k + '" value="' + esc(V[k]) + '">', tog = k => '<input type="checkbox" id="set-' + k + '"' + (String(V[k]) === '1' ? ' checked' : '') + '>';
  const ks = F && F.killswitch, pool = String(V.WORKERS || '').split(',').filter(Boolean);
  let h = '<div class="setcard"><h3>Kill switch</h3><div class="muted" style="font-size:12.5px;margin-bottom:8px">' +
    (ks ? 'ON' + (ks.scope === 'all' ? ' for all projects' : ' for this project') + (ks.hard ? ' (hard)' : '') + ' — ' + esc(ks.reason || '') : 'off — the project is running') + '</div>' +
    '<div class="btnrow">' + (ks ? '<button class="resume-btn" data-act="resume">Continue</button>' :
      '<button class="warnbtn" data-act="ks-on">Stop project</button><button class="badbtn" data-act="ks-hard">Stop hard</button>') +
    '<button class="badbtn" data-act="ks-all">Stop all projects</button></div></div>';
  h += '<div class="setcard"><h3>This project\'s workers</h3><div class="muted" style="font-size:12.5px">Ticked workers belong to project “' + esc(project) +
    '”. None ticked = all. Each worker is its own Antigravity account or API key.</div><div id="set-WORKERS" data-kind="pool" class="pool">' +
    (S.workers || []).map(w => '<div class="poolrow"><label><input type="checkbox" value="' + esc(w.name) + '"' + (pool.includes(w.name) ? ' checked' : '') + '> ' + esc(w.name) +
      ' <span class="rt rt-' + esc(w.runtime || 'agy') + '">' + esc(RTNAME[w.runtime || 'agy'] || w.runtime) + '</span> <span class="muted">(' + esc(w.state) + ')</span></label>' +
      ((w.runtime || 'agy') === 'agy' ? '<button class="mini" data-login="' + esc(w.name) + '">sign in to Antigravity</button>' : '') + '</div>').join('') +
    '</div>' + (sErr.WORKERS ? '<div class="seterr">' + esc(sErr.WORKERS) + '</div>' : '') +
    '<div class="btnrow"><button class="mini" id="add-worker">+ Add worker</button><span id="addw-status" class="muted" style="font-size:12.5px"></span></div>' +
    '<div id="addw-form" style="display:none" class="addw"><div class="muted" style="font-size:12.5px;margin:8px 0 4px">What will the new worker run on?</div>' +
    Object.entries(S.runtimes || {agy: {label: 'Antigravity'}}).map(([k, r], i) => '<label class="chk" style="display:flex"><input type="radio" name="addw-rt" value="' + esc(k) + '"' +
      (i ? '' : ' checked') + '> ' + esc(RTNAME[k] || k) + ' <span class="muted">— ' + esc(RTDESC[k] || r.label) + '</span></label>').join('') +
    '<div id="addw-key" style="display:none;margin-top:6px"><input type="password" id="addw-keyin" autocomplete="off" placeholder="API key (stored on the server only, in the secrets directory)" style="width:100%"></div>' +
    '<div class="btnrow"><button class="resume-btn" id="addw-go">Add</button><button class="mini" id="addw-cancel">Cancel</button></div></div></div>';
  const R = S.runtimes || {}, sec = S.secrets || {};
  h += '<div class="setcard"><h3>API workers</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">Workers with an API key pay per token. ' +
    'Spent today: <b>$' + Number(S.api_spent_today || 0).toFixed(2) + '</b> of $' + esc(V.API_DAILY_BUDGET_USD) + '. Keys: ' +
    ['anthropic.key', 'openai.key', 'gemini.key'].map(k => esc(k.split('.')[0]) + ' ' + (sec[k] ? '✓' : '—')).join(', ') +
    ' <button class="mini" id="key-change">change a key…</button></div>' +
    '<div id="key-form" style="display:none" class="btnrow"><select id="key-name"><option>anthropic.key</option><option>openai.key</option><option>gemini.key</option></select>' +
    '<input type="password" id="key-val" autocomplete="off" placeholder="new key" style="flex:1;min-width:180px"><button class="mini" id="key-save">Save key</button></div>' +
    field('API_WORKERS_POLICY', '<select id="set-API_WORKERS_POLICY">' + [['overflow', 'only when the subscription workers cannot'], ['always', 'always first']].map(([v, l]) =>
      '<option value="' + v + '"' + (v === V.API_WORKERS_POLICY ? ' selected' : '') + '>' + l + '</option>').join('') + '</select>') +
    field('WORKER_MODEL_CLAUDE', txt('WORKER_MODEL_CLAUDE')) + field('WORKER_MODEL_CODEX', txt('WORKER_MODEL_CODEX')) + field('WORKER_MODEL_GEMINI', txt('WORKER_MODEL_GEMINI')) +
    field('API_RUN_BUDGET_USD', txt('API_RUN_BUDGET_USD')) + field('API_DAILY_BUDGET_USD', txt('API_DAILY_BUDGET_USD')) + field('RUN_MAX_TOOL_CALLS', num('RUN_MAX_TOOL_CALLS')) +
    field('PRICE_CODEX', txt('PRICE_CODEX')) + field('PRICE_GEMINI', txt('PRICE_GEMINI')) + '</div>';
  h += '<div class="setcard"><h3>Orchestrator</h3>' + field('PLAN_PROVIDERS', provEditor('PLAN_PROVIDERS', V.PLAN_PROVIDERS)) +
    field('REVIEW_PROVIDERS', provEditor('REVIEW_PROVIDERS', V.REVIEW_PROVIDERS)) +
    field('CLAUDE_MODEL', '<select id="set-CLAUDE_MODEL">' + ['sonnet', 'opus', 'haiku', 'fable'].concat(['sonnet', 'opus', 'haiku', 'fable'].includes(V.CLAUDE_MODEL) ? [] : [V.CLAUDE_MODEL])
      .map(m => '<option' + (m === V.CLAUDE_MODEL ? ' selected' : '') + '>' + esc(m) + '</option>').join('') + '</select>') +
    field('AGY_PLAN_MODEL', modelSel('AGY_PLAN_MODEL', V.AGY_PLAN_MODEL)) + field('AGY_REVIEW_MODEL', modelSel('AGY_REVIEW_MODEL', V.AGY_REVIEW_MODEL)) +
    field('AGY_FALLBACK_MODEL', modelSel('AGY_FALLBACK_MODEL', V.AGY_FALLBACK_MODEL)) +
    '<details><summary class="muted">Paid APIs (only if ticked above and you have a key)</summary>' + field('ANTHROPIC_MODEL', txt('ANTHROPIC_MODEL')) +
    field('OPENAI_MODEL', txt('OPENAI_MODEL')) + field('GEMINI_MODEL', txt('GEMINI_MODEL')) + '</details></div>';
  h += '<div class="setcard"><h3>Workers — models</h3>' + field('WORKER_MODEL', modelSel('WORKER_MODEL', V.WORKER_MODEL, true, 'default (' + (S.limits_worker_model || '') + ')')) +
    field('WORKER_MODEL_RISKY', modelSel('WORKER_MODEL_RISKY', V.WORKER_MODEL_RISKY, true, 'same as routine tasks')) +
    '<div class="btnrow"><button class="mini" id="refresh-models">refresh the model list from Antigravity</button></div></div>';
  const fk = V.FORGE_KIND || '';
  h += '<div class="setcard"><h3>Project</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">How the project is tested and where the team notifies you.</div>' +
    '<div class="setrow"><label>Where the code lives</label><div>' + (fk === 'github' || fk === 'gitlab' ? esc(fk === 'github' ? 'GitHub' : 'GitLab') + ': <b>' + esc(V.UPSTREAM_REPO) + '</b>' :
      fk === 'forgejo' ? 'internal Forgejo only' : 'not chosen') + ' <button class="mini" id="open-setup">change…</button></div></div>' +
    field('TEST_CMD', txt('TEST_CMD')) + field('GATE_IMAGE', txt('GATE_IMAGE')) + field('NOTIFY_URL', txt('NOTIFY_URL')) +
    field('NOTIFY_FORMAT', '<select id="set-NOTIFY_FORMAT">' + ['ntfy', 'slack', 'discord', 'json'].map(x => '<option' + (x === V.NOTIFY_FORMAT ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('NOTIFY_EVENTS', txt('NOTIFY_EVENTS')) + field('RUNS_KEEP_DAYS', num('RUNS_KEEP_DAYS')) + '</div>';
  h += '<div class="setcard"><h3>Efficiency</h3><div class="muted" style="font-size:12.5px;margin-bottom:6px">How the team saves quota. The measurements are in the Metrics tab.</div>' +
    field('CONTEXT_PACK', '<select id="set-CONTEXT_PACK">' + ['on', 'ab', 'off'].map(x => '<option' + (x === V.CONTEXT_PACK ? ' selected' : '') + '>' + x + '</option>').join('') + '</select>') +
    field('CONTEXT_PACK_CHARS', num('CONTEXT_PACK_CHARS')) + field('SELF_CHECK_ROUNDS', num('SELF_CHECK_ROUNDS')) + field('MAX_TASK_FILES', num('MAX_TASK_FILES')) +
    field('WORKER_MODEL_LIGHT', modelSel('WORKER_MODEL_LIGHT', V.WORKER_MODEL_LIGHT, true, 'same as routine tasks')) + field('LIGHT_ROLES', txt('LIGHT_ROLES')) +
    field('AB_MODEL', modelSel('AB_MODEL', V.AB_MODEL, true, 'no A/B test')) + field('AB_SHARE', num('AB_SHARE')) +
    field('PLAN_AHEAD', tog('PLAN_AHEAD')) + field('MAINTENANCE', tog('MAINTENANCE')) + '</div>';
  h += '<div class="setcard"><h3>Behaviour</h3>' + field('AUTOPILOT', tog('AUTOPILOT')) + field('AUTO_MERGE', tog('AUTO_MERGE')) +
    field('CONTINUE_CONVERSATIONS', tog('CONTINUE_CONVERSATIONS')) + field('OWNER_LANG', txt('OWNER_LANG')) + field('MAX_REWORK', num('MAX_REWORK')) +
    field('MAX_ACTIVE_GOALS', num('MAX_ACTIVE_GOALS')) + field('SPLIT_AFTER_CAPS', num('SPLIT_AFTER_CAPS')) + field('TASK_TOKEN_BUDGET', num('TASK_TOKEN_BUDGET')) + '</div>';
  h += '<div class="setcard"><h3>Limity <span class="pill warn">apply to ALL projects</span></h3>' +
    ['MAX_PARALLEL', 'RUN_TOKEN_CAP', 'RUN_TIMEOUT_MIN', 'QUOTA_MIN_5H_PCT', 'QUOTA_MIN_WEEK_PCT', 'CONSEC_FAIL_TRIP'].map(k => field(k, num(k))).join('') +
    ((S.workers || []).length > Number(V.MAX_PARALLEL) ? '<div class="seterr">There are more workers (' + S.workers.length + ') than MAX_PARALLEL — some will idle.</div>' : '') + '</div>';
  h += '<div class="btnrow sticky"><button class="resume-btn" id="save-settings">Save changes</button><span class="muted" style="font-size:12.5px">' + esc(sMsg) + '</span></div>';
  return h;
}
async function saveSettings() {
  const V = S.values || {}, changes = {};
  for (const k of Object.keys(V)) { const v = sval(k); if (v !== null && String(v) !== String(V[k])) changes[k] = v; }
  if (!Object.keys(changes).length) { sMsg = 'nothing changed'; render(true); return; }
  let r;
  try { r = await (await fetch('/api/settings', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project, changes})})).json(); }
  catch (e) { r = {ok: false, errors: {_: String(e)}}; }
  sErr = r.errors || {};
  sMsg = r.ok ? 'saved: ' + Object.keys(changes).join(', ') + ' — applies from the next round (within 20 s)' : 'not saved — fix the marked fields' + (sErr._ ? ': ' + sErr._ : '');
  if (r.ok) { await loadSettings(true); return; }
  // keep everything the owner typed: only mark the bad fields
  document.querySelectorAll('#settings .seterr.live').forEach(x => x.remove());
  for (const [k, msg] of Object.entries(sErr)) {
    const el = document.getElementById('set-' + k);
    if (el) el.insertAdjacentHTML('afterend', '<div class="seterr live">' + esc(msg) + '</div>');
  }
  const st = document.querySelector('#settings .btnrow.sticky .muted'); if (st) st.textContent = sMsg;
  sErr = {};
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
  if (e.target.id === 'add-worker') { addWorker(); return; }
  if (e.target.id === 'np-create') { createProject(); return; }
  if (e.target.id === 'np-close') { document.getElementById('newproj').classList.remove('on'); projAt = 0; loadProjects().then(() => {
    const sel = document.getElementById('proj'); if (sel.value !== project) { sel.value = project; sel.dispatchEvent(new Event('change')); } }); return; }
  const lg = e.target.closest('[data-login]'); if (lg) { openLogin(lg.dataset.login); return; }
  if (e.target.id === 'login-send') { const i = document.getElementById('login-in');
    await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'input', text: i.value})}); i.value = ''; return; }
  if (e.target.id === 'login-done' || e.target.id === 'login-close') { closeLogin(e.target.id === 'login-done'); return; }
});

// ---- add a worker, then log it in
async function addWorker() {
  const f = document.getElementById('addw-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; addwRt();
}
function addwRt() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', R = (S && S.runtimes) || {};
  const key = (R[rt] || {}).key, have = key && ((S.secrets || {})[key]);
  document.getElementById('addw-key').style.display = key && !have ? '' : 'none';
}
async function addWorkerGo() {
  const rt = (document.querySelector('input[name="addw-rt"]:checked') || {}).value || 'agy', st = document.getElementById('addw-status');
  const key = ((S.runtimes || {})[rt] || {}).key, kin = document.getElementById('addw-keyin');
  if (key && !(S.secrets || {})[key]) {
    if (!kin.value.trim()) { kin.focus(); return; }
    const r = await post('/api/secret', {name: key, value: kin.value.trim()}); kin.value = '';
    if (!r.ok) { st.textContent = 'the key could not be saved: ' + (r.message || r.error || ''); return; }
  }
  document.getElementById('addw-form').style.display = 'none';
  let j = await post('/api/workers', {action: 'add', runtime: rt});
  while (j.state === 'running') { st.textContent = 'creating ' + (j.worker || 'the worker') + '… (the first time takes a few minutes)'; await new Promise(r => setTimeout(r, 3000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  st.textContent = j.state !== 'done' ? 'failed: ' + (j.log || j.error || '').slice(-300) : rt === 'agy' ? j.worker + ' created — now sign it in' : j.worker + ' created and ready';
  await loadSettings(true);
  if (j.state === 'done' && rt === 'agy') openLogin(j.worker);
}
document.addEventListener('change', e => { if (e.target.name === 'addw-rt') addwRt(); });
document.addEventListener('click', async e => {
  if (e.target.id === 'addw-go') { addWorkerGo(); return; }
  if (e.target.id === 'addw-cancel') { document.getElementById('addw-form').style.display = 'none'; return; }
  if (e.target.id === 'key-change') { const f = document.getElementById('key-form'); f.style.display = f.style.display === 'none' ? '' : 'none'; return; }
  if (e.target.id === 'key-save') { const v = document.getElementById('key-val');
    if (!v.value.trim()) { v.focus(); return; }
    const r = await post('/api/secret', {name: document.getElementById('key-name').value, value: v.value.trim()}); v.value = '';
    sMsg = r.ok ? 'key saved' : 'key not saved: ' + (r.message || r.error || ''); loadSettings(true); return; }
  if (e.target.id === 'open-setup') { openSetup(); return; }
  if (e.target.id === 'su-close' || e.target.id === 'su-later') { document.getElementById('setup').classList.remove('on'); setupDismissed[project] = true; return; }
  if (e.target.id === 'su-go') { setupGo(); return; }
});

// ---- where the code lives: internal Forgejo only, GitHub or GitLab (shown once for a new project, or from Settings → Project)
const setupDismissed = {};
const SU_URL = {github: 'https://github.com', gitlab: 'https://gitlab.com'};
function suKind() { return (document.querySelector('input[name="su-kind"]:checked') || {}).value || 'forgejo'; }
function suRefresh() {
  const k = suKind(), up = k !== 'forgejo';
  document.getElementById('su-up').style.display = up ? '' : 'none';
  if (up) { const u = document.getElementById('su-url'); if (!u.value || Object.values(SU_URL).includes(u.value)) u.value = SU_URL[k];
    document.getElementById('su-help').textContent = k === 'github'
      ? 'Token: GitHub → Settings → Developer settings → Fine-grained token for this repository only, permissions Contents and Pull requests: Read and write.'
      : 'Token: GitLab → project → Settings → Access tokens, role Developer, scopes api and write_repository.'; }
}
document.addEventListener('change', e => { if (e.target.name === 'su-kind') suRefresh(); });
function openSetup() {
  const V = (S && S.values) || {}, m = document.getElementById('setup');
  const k = V.FORGE_KIND || (F && F.forge && F.forge.kind) || 'forgejo';
  document.querySelectorAll('input[name="su-kind"]').forEach(r => r.checked = r.value === (k || 'forgejo'));
  document.getElementById('su-repo').value = V.UPSTREAM_REPO || (F && F.forge && F.forge.repo) || '';
  document.getElementById('su-url').value = V.UPSTREAM_URL || (F && F.forge && F.forge.url) || '';
  document.getElementById('su-form').style.display = ''; document.getElementById('su-log').style.display = 'none';
  document.getElementById('su-force-row').style.display = 'none';
  suRefresh(); m.classList.add('on');
}
async function setupGo() {
  const k = suKind(), log = document.getElementById('su-log'), tok = document.getElementById('su-token');
  for (const x of ['repo', 'url', 'token']) document.getElementById('su-e-' + x).textContent = '';
  if (k !== 'forgejo' && tok.value.trim()) {
    const r = await post('/api/secret', {name: k + '.token', value: tok.value.trim()}); tok.value = '';
    if (!r.ok) { document.getElementById('su-e-token').textContent = r.message || r.error || 'the token could not be saved'; return; }
  }
  let j = await post('/api/upstream', {action: 'connect', kind: k, repo: document.getElementById('su-repo').value.trim(),
                                       url: document.getElementById('su-url').value.trim(), force: document.getElementById('su-force').checked});
  if (j.errors) { for (const x in j.errors) { const el = document.getElementById('su-e-' + x); if (el) el.textContent = j.errors[x]; } return; }
  document.getElementById('su-form').style.display = 'none'; log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2000));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + (j.state === 'done' ? '✔ Done — the team now merges through this repository.' : '✘ Failed — see the output above.');
  if (j.state !== 'done' && /already has \d+ commits/.test(j.log || '')) {
    document.getElementById('su-form').style.display = ''; document.getElementById('su-force-row').style.display = ''; }
  S = null; loadSettings(true); setTimeout(poll, 1000);
}
let loginW = null, loginTimer = null;
async function openLogin(w) {
  loginW = w;
  const m = document.getElementById('login'); m.classList.add('on');
  document.getElementById('login-title').textContent = 'Sign ' + w + ' in to Antigravity';
  document.getElementById('login-out').textContent = 'starting…';
  await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: w, action: 'start'})});
  clearInterval(loginTimer);
  loginTimer = setInterval(async () => {
    let r; try { r = await (await fetch('/api/login?worker=' + encodeURIComponent(w))).json(); } catch (e) { return; }
    const o = document.getElementById('login-out'), atBottom = o.scrollHeight - o.scrollTop - o.clientHeight < 30;
    o.textContent = r.output || '(waiting for output…)'; if (atBottom) o.scrollTop = o.scrollHeight;
    const u = document.getElementById('login-url');
    u.innerHTML = r.url ? 'Open it and sign in with <b>the account for ' + esc(w) + '</b>: <a href="' + esc(r.url) + '" target="_blank" rel="noopener">' + esc(r.url.slice(0, 60)) + '…</a>' : '';
  }, 1500);
}
async function closeLogin(check) {
  clearInterval(loginTimer);
  const st = document.getElementById('login-url');
  if (check) st.textContent = 'checking the sign-in…';
  let r = {}; try { r = await (await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({worker: loginW, action: 'stop'})})).json(); } catch (e) {}
  if (check) { alert(r.logged_in ? loginW + ' is signed in and sees its quota. Tick it in the project and save.' : loginW + ' is not signed in yet — try again.'); }
  document.getElementById('login').classList.remove('on');
  loadSettings(true);
}

// ---- a new project from the dashboard (bin/foreman init-project + forge-bootstrap + its foreman service)
async function openNewProject() {
  const m = document.getElementById('newproj'); m.classList.add('on');
  document.getElementById('np-form').style.display = ''; document.getElementById('np-log').style.display = 'none';
  const box = document.getElementById('np-workers');
  let st = {}; try { st = await (await fetch('/api/settings?project=default')).json(); } catch (e) {}
  const owner = {};
  for (const p of PROJ_LIST) for (const w of String(p.workers || '').split(',').filter(Boolean)) owner[w] = p.name;
  const ws = st.workers || [];
  box.innerHTML = ws.length ? ws.map(w => '<label class="chk"><input type="checkbox" data-npw="' + esc(w) + '"> ' + esc(w) +
      ' <span class="muted">(' + esc(owner[w] || (st.values && !st.values.WORKERS ? 'default — all' : 'free')) + ')</span></label>').join(' ')
    : 'no workers — add them in Settings';
}
async function createProject() {
  const v = id => document.getElementById(id).value.trim();
  const body = {action: 'create', name: v('np-name'), repo: v('np-repo'), lang: v('np-lang'), vision: document.getElementById('np-vision').value,
                workers: [...document.querySelectorAll('[data-npw]')].filter(c => c.checked).map(c => c.dataset.npw)};
  for (const k of ['name', 'repo', 'lang', 'workers']) document.getElementById('np-e-' + k).textContent = '';
  let j = await (await fetch('/api/projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})).json();
  if (j.errors) { for (const k in j.errors) { const el = document.getElementById('np-e-' + k); if (el) el.textContent = j.errors[k]; } return; }
  document.getElementById('np-form').style.display = 'none';
  const log = document.getElementById('np-log'); log.style.display = '';
  while (j.state === 'running') { log.textContent = (j.log || '') + '\n… working'; await new Promise(r => setTimeout(r, 2500));
    j = await (await fetch('/api/jobs/' + encodeURIComponent(j.id))).json(); }
  log.textContent = (j.log || '') + '\n' + ({done: '✔ The project is ready — pick it at the top and enter its first goal.',
    'needs-sudo': '⚠ The project exists; only its service needs root — run the command above.'}[j.state] || '✘ Failed — see the output above.');
  projAt = 0; await loadProjects();
  if (j.state !== 'failed') { project = body.name; try { localStorage.setItem('af-project', project); } catch (e) {} }
}

// buttons rendered as HTML (no inline handlers: the page's CSP allows scripts from this server only)
document.addEventListener('click', e => {
  const a = e.target.closest('[data-act]'); if (!a) return;
  const act = {resume: resumeDev, 'ks-on': () => killswitch('on'), 'ks-hard': () => killswitch('on', {hard: true}),
               'ks-all': () => killswitch('on', {all: true})}[a.dataset.act];
  if (act) act();
});
