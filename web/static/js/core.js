// Agent-Forge dashboard - core: state, helpers, the live stream (Server-Sent Events, polling as a fallback), the render loop,
// tabs, the sheet (task / goal / run / document / search), toasts and the project picker.
// Scripts share one global scope and load in order: core, overview, tasks, goal, live, insights, system, settings, search, main.

// every write carries X-Agent-Forge: the server refuses POSTs without it (a cross-site page cannot add it without CORS consent)
const _fetch = window.fetch.bind(window);
window.fetch = (u, o) => { o = o || {}; if ((o.method || 'GET').toUpperCase() === 'POST') o.headers = Object.assign({'X-Agent-Forge': '1'}, o.headers || {}); return _fetch(u, o); };

const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const toDate = ts => typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
const tfmt = ts => { const d = toDate(ts); return isNaN(d) ? '' : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); };
const dfmt = ts => { const d = toDate(ts); if (isNaN(d)) return ''; const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : d.toLocaleString([], {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'}); };
// UTC timestamps inside a text ("(resets 2026-10-10T13:49:21Z)", straight from `agy /usage`) shown in the viewer's own time
const tsloc = s => String(s == null ? '' : s).replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?Z/g, m => {
  const d = new Date(m); if (isNaN(d)) return m;
  return new Date().toDateString() === d.toDateString() ? tfmt(d)
    : d.toLocaleString([], {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'}); });
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Math.round(Date.now() / 1000 - (typeof ts === 'number' ? ts : Date.parse(ts) / 1000)));
  return s < 60 ? s + ' s' : s < 3600 ? Math.round(s / 60) + ' min' : s < 172800 ? Math.round(s / 3600) + ' h' : Math.round(s / 86400) + ' days'; };
const dur = s => { s = Number.isFinite(+s) ? Math.max(0, Math.round(+s)) : 0; return s < 90 ? s + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : s < 172800 ? (s / 3600).toFixed(1) + ' h' : (s / 86400).toFixed(1) + ' days'; };
const fmtTok = n => n == null ? '–' : n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e3 ? Math.round(n / 1e3) + 'k' : String(n);
const usd = v => v == null ? '–' : '$' + (v > 0 && v < 1 ? Number(v).toFixed(3) : Number(v).toFixed(2));
const STATUS = {splitting: 'Splitting', pending: 'Waiting', running: 'Running', gate: 'Checking', review: 'Checking', merging: 'Merging',
  'merge-ready': 'Merging', approval: 'Needs you', done: 'Done', blocked: 'Blocked', cancelled: 'Cancelled'};
const GSTATUS = {new: 'Planning', approval: 'Plan needs you', active: 'Running', done: 'Done', blocked: 'Blocked', cancelled: 'Cancelled'};
const RTNAME = {agy: 'Antigravity', claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI', compat: 'Codex · own server', aider: 'Aider · own server'};
const RTDESC = {agy: 'a Google account with a subscription, signed in from the browser', claude: 'Anthropic API key, paid per token',
  codex: 'OpenAI API key, paid per token', gemini: 'Gemini API key, paid per token',
  compat: 'your model server (Ollama, vLLM, LM Studio, OpenRouter) through Codex CLI', aider: 'your model server through Aider (any chat API)'};
const roleCls = r => /secur/i.test(r) ? 'sec' : (/test/i.test(r) && !/develop/i.test(r)) ? 'test' : '';
const roleTag = r => '<span class="role ' + roleCls(r) + '">' + esc(r) + '</span>';
const chip = s => '<span class="st st-' + esc(s) + '">' + esc(STATUS[s] || s) + '</span>';
const small = t => '<span class="muted small">' + t + '</span>';

let tab = 'overview', F = null, P = {roster: {}, panes: []}, project = 'default', foremanFetchedAt = 0, PROJ_LIST = [];
const SUB = {tasks: 'board', insights: 'team', system: 'health', settings: 'workers'};
const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) {} return null; };

// ---- theme & localization (i18n)
let I18N = {};
let currentLang = store('af-lang') || 'en';
const t = (k, fb) => (I18N && I18N[k]) || fb || k;
const tr = t;                                                  // for scopes where a local `t` (a task) shadows t()

function updateStatusObjects() {
  STATUS.splitting = t('status.splitting', 'Splitting');
  STATUS.pending = t('status.pending', 'Waiting');
  STATUS.running = t('status.running', 'Running');
  STATUS.gate = t('status.gate', 'Checking');
  STATUS.review = t('status.review', 'Checking');
  STATUS.merging = t('status.merging', 'Merging');
  STATUS['merge-ready'] = t('status.merge_ready', 'Merging');
  STATUS.approval = t('status.approval', 'Needs you');
  STATUS.done = t('status.done', 'Done');
  STATUS.blocked = t('status.blocked', 'Blocked');
  STATUS.cancelled = t('status.cancelled', 'Cancelled');

  GSTATUS.new = t('gstatus.new', 'Planning');
  GSTATUS.approval = t('gstatus.approval', 'Plan needs you');
  GSTATUS.active = t('gstatus.active', 'Running');
  GSTATUS.done = t('gstatus.done', 'Done');
  GSTATUS.blocked = t('gstatus.blocked', 'Blocked');
  GSTATUS.cancelled = t('gstatus.cancelled', 'Cancelled');
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const k = el.dataset.i18n;
    if (I18N && I18N[k]) el.textContent = I18N[k];
  });
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    const k = el.dataset.i18nPh;
    if (I18N && I18N[k]) el.placeholder = I18N[k];
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const k = el.dataset.i18nTitle;
    if (I18N && I18N[k]) el.title = I18N[k];
  });
  const lb = $('lang-btn');
  if (lb) {
    lb.textContent = currentLang.toUpperCase().slice(0, 4);
    lb.title = 'Language: ' + (I18N['lang.name'] || currentLang) + ' (click to switch, right-click for options)';
    lb.setAttribute('aria-label', 'Language: ' + (I18N['lang.name'] || currentLang) + ' (click to change)');
  }
  document.documentElement.lang = currentLang.slice(0, 2);
  updateLocalesDropdown();
}

let LOCALES_LIST = [];
async function loadLocalesList() {
  try {
    const r = await fetch('/api/locales');
    if (r.ok) {
      const d = await r.json();
      if (Array.isArray(d.locales)) LOCALES_LIST = d.locales;
    }
  } catch (e) {}
  updateLocalesDropdown();
}

function updateLocalesDropdown() {
  const uils = $('ui-lang-sel');
  if (!uils) return;
  const list = LOCALES_LIST.length ? LOCALES_LIST : [
    {code: 'en', name: 'English'},
    {code: 'sk', name: 'Slovenčina'},
    {code: 'sk_qwerty', name: 'Slovenčina (QWERTY)'},
    {code: 'eng_us', name: 'English (US)'}
  ];
  let html = list.map(l => '<option value="' + esc(l.code) + '"' + (currentLang === l.code ? ' selected' : '') + '>' + esc(l.name) + ' (' + esc(l.code) + ')</option>').join('');
  if (!list.some(l => l.code === currentLang)) {
    html = '<option value="' + esc(currentLang) + '" selected>' + esc(currentLang) + ' (custom)</option>' + html;
  }
  html += '<option value="__upload">+ Upload locale (.json)…</option>';
  html += '<option value="__custom">+ Enter code manually…</option>';
  if (uils.innerHTML !== html) uils.innerHTML = html;
  uils.value = currentLang;
}

async function loadLocale(lang) {
  currentLang = lang;
  store('af-lang', lang);
  try {
    const r = await fetch('/static/locales/' + encodeURIComponent(lang) + '.json');
    if (r.ok) {
      I18N = await r.json();
    } else if (lang !== 'en') {
      const fb = await fetch('/static/locales/en.json');
      if (fb.ok) I18N = await fb.json();
    }
  } catch (e) {
    console.warn('Failed to load locale:', lang, e);
  }
  updateStatusObjects();
  applyI18n();
  applyTheme(currentTheme);
  for (const k in lastHTML) delete lastHTML[k];
  schedule(true);

}

function cycleLang() {
  const codes = LOCALES_LIST.length ? LOCALES_LIST.map(l => l.code) : ['en', 'sk'];
  const idx = codes.indexOf(currentLang);
  const next = codes[(idx + 1) % codes.length] || (currentLang === 'en' ? 'sk' : 'en');
  loadLocale(next);
}

const THEMES = ['auto', 'light', 'dark'];
const THEME_ICONS = {auto: '◐', light: '☀', dark: '☾'};
let currentTheme = store('af-theme') || 'auto';

function applyTheme(theme) {
  currentTheme = theme;
  store('af-theme', theme);
  if (theme === 'auto') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', theme);
  }
  const tb = $('theme-btn');
  if (tb) {
    tb.textContent = THEME_ICONS[theme] || '◐';
    tb.title = t('theme.' + theme, 'Theme: ' + theme) + ' (click to toggle)';
    tb.setAttribute('aria-label', t('theme.' + theme, 'Theme: ' + theme) + ' (click to change)');
  }
  const uits = $('ui-theme-sel');
  if (uits && uits.value !== currentTheme) uits.value = currentTheme;
}

function cycleTheme() {
  const idx = THEMES.indexOf(currentTheme);
  applyTheme(THEMES[(idx + 1) % THEMES.length]);
}

applyTheme(currentTheme);
// loadLocale() and loadLocalesList() are started by main.js: both redraw when they finish, and the later scripts must be loaded first

project = store('af-project') || 'default';
tab = store('af-tab') || 'overview';
try { Object.assign(SUB, JSON.parse(store('af-sub') || '{}')); } catch (e) {}
const inPool = w => !F || !(F.pool || []).length || F.pool.includes(w);

// deep links: #tab=tasks&project=shop&task=0012-store | goal=G… | run=… (bookmarks, links in notifications)
const TABS = ['overview', 'tasks', 'live', 'timeline', 'insights', 'system', 'settings'];
const ALIAS = {queue: 'tasks', metrics: 'insights', events: 'system'};
const HASH = Object.fromEntries((location.hash || '').replace(/^#/, '').split('&').filter(Boolean).map(kv => kv.split('=').map(decodeURIComponent)));
if (HASH.tab) { const t = ALIAS[HASH.tab] || HASH.tab; if (TABS.includes(t)) tab = t; if (HASH.tab === 'events') SUB.system = 'activity'; }
if (HASH.project && /^[a-z0-9][a-z0-9-]{0,39}$/.test(HASH.project)) project = HASH.project;
if (HASH.view && SUB[ALIAS[HASH.tab] || HASH.tab] !== undefined && /^[a-z]{2,20}$/.test(HASH.view)) SUB[ALIAS[HASH.tab] || HASH.tab] = HASH.view;
if (TABS.includes(tab) === false) tab = 'overview';

// ---- server calls
async function getJSON(path, params) {
  const q = new URLSearchParams(Object.assign({project}, params || {}));
  const r = await fetch(path + '?' + q.toString());
  if (!r.ok && r.status !== 400) throw new Error('HTTP ' + r.status);
  return r.json();
}
const post = async (url, body) => { try { return await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(Object.assign({project}, body))})).json(); } catch (e) { return {ok: false, message: String(e)}; } };

// ---- toasts: short confirmations and errors of actions, at the bottom of the screen
function toast(msg, bad) {
  const el = document.createElement('div'); el.className = 'toast' + (bad ? ' bad' : ''); el.textContent = msg; if (bad) el.setAttribute('role', 'alert');
  $('toasts').appendChild(el);
  setTimeout(() => el.classList.add('out'), bad ? 6500 : 3500); setTimeout(() => el.remove(), bad ? 7000 : 4000);
}
// an action's answer from the server -> a toast; true when it worked
function done(r, okText) {
  const ok = !!r && (r.ok === undefined ? !r.error : r.ok);
  toast(ok ? (okText || r.message || 'done') : 'Not done: ' + ((r && (r.message || r.error)) || 'no answer'), !ok);
  return ok;
}

function copyTaskReport(t) {
  if (!t) return;
  let reportText = '';
  if (t.diagnosis && t.diagnosis.report) {
    reportText = t.diagnosis.report;
  } else {
    const diag = t.diagnosis || {};
    reportText = '### 🚨 Task Failure Report for AI Assistant\n\n' +
      '**Task:** `' + (t.id || '') + '` - *' + (t.title || '') + '*\n' +
      '**Role:** `' + (t.role || '') + '` | **Goal:** `' + (t.goal || '') + '`\n' +
      '**Status:** ' + (t.status || 'blocked') + ' (' + (t.attempts || 0) + ' attempts)\n\n' +
      (diag.summary ? '#### 📋 Orchestrator Diagnosis:\n' + diag.summary + '\n\n' : '') +
      (diag.root_cause ? '**Root cause:** ' + diag.root_cause + '\n\n' : '') +
      (diag.suggested_fix ? '#### 💡 Suggested Fix:\n' + diag.suggested_fix + '\n\n' : '') +
      (t.blocked_why ? '**Error details:** ' + t.blocked_why + '\n\n' : '') +
      (t.files && t.files.length ? '#### 📁 Declared Files:\n' + t.files.map(f => '- `' + f + '`').join('\n') + '\n\n' : '') +
      (t.gate_tail ? '#### 🔍 Gate Failure Output:\n```\n' + t.gate_tail.slice(-2500) + '\n```\n\n' : '') +
      '**Instruction for AI Assistant:** Please fix this issue in the repository, ensure the test suite passes, and unblock the task.';
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(reportText).then(() => {
      toast(t('diag.copied', '📋 Report copied to clipboard! Paste it to your AI assistant.'));
    }).catch(err => {
      prompt('Copy this report manually (Ctrl+C):', reportText);
    });
  } else {
    prompt('Copy this report manually (Ctrl+C):', reportText);
  }
}

document.addEventListener('click', e => {
  const cr = e.target.closest && e.target.closest('[data-copyreport]');
  if (cr) {
    const tid = cr.dataset.copyreport;
    const t = ((F && F.tasks) || []).find(x => x.id === tid) || (TD && TD.data && TD.data.task && TD.data.task.id === tid ? TD.data.task : null);
    if (t) copyTaskReport(t);
  }
});

// ---- rendering: only touch the DOM when a section's HTML changed (no flicker, no lost scroll, less battery)
const lastHTML = {};
function setHTML(id, html, force) {
  if (!force && lastHTML[id] === html) return;
  lastHTML[id] = html;
  const el = $(id); if (el) el.innerHTML = html;
}
let renderQueued = false, renderForce = false;
function schedule(force) {
  renderForce = renderForce || !!force;
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; const f = renderForce; renderForce = false; render(f); });
}
const typing = el => !!el && (el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(el.type)) || el.isContentEditable);
// a section the owner is typing in is not re-rendered under their fingers
const busyIn = id => { const el = $(id); return !!el && el.contains(document.activeElement) && typing(document.activeElement); };

function render(force) {
  setHTML('pills', pills(), force);
  setHTML('stats', stats(), force);
  const ts = (F && F.tasks) || [];
  $('c-tasks').textContent = ts.filter(t => !['done', 'cancelled'].includes(t.status)).length;
  const inbox = F ? inboxItems().length : 0;
  $('c-inbox').hidden = !inbox; $('c-inbox').textContent = inbox;
  if (tab === 'overview') renderOverview(force);
  else if (tab === 'tasks') renderTasks(force);
  else if (tab === 'timeline') renderTimeline(force);
  else if (tab === 'insights') renderInsights(force);
  else if (tab === 'system') renderSystem(force);
  else if (tab === 'settings') renderSettings(force);
  renderLive();
  refreshSheet();
}

function setTab(t) {
  tab = TABS.includes(t) ? t : 'overview';
  for (const k in lastHTML) delete lastHTML[k];
  store('af-tab', tab);
  document.querySelectorAll('nav button').forEach(b => { b.classList.toggle('on', b.dataset.t === tab); if (b.dataset.t === tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  document.querySelectorAll('main section').forEach(s => s.classList.toggle('on', s.id === 't-' + tab));
  document.querySelectorAll('.seg').forEach(seg => { const g = seg.id.replace('-seg', '');
    seg.querySelectorAll('button').forEach(b => { b.classList.toggle('on', b.dataset.v === SUB[g]); b.setAttribute('aria-pressed', String(b.dataset.v === SUB[g])); }); });
  syncHash();
  render(true);
}
function setSub(group, v) {
  SUB[group] = v; store('af-sub', JSON.stringify(SUB));
  document.querySelectorAll('#' + group + '-seg button').forEach(b => { b.classList.toggle('on', b.dataset.v === v); b.setAttribute('aria-pressed', String(b.dataset.v === v)); });
  for (const k in lastHTML) delete lastHTML[k];
  render(true);
}
$('nav').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.t); });
document.addEventListener('click', e => { const b = e.target.closest('.seg button'); if (b) setSub(b.closest('.seg').id.replace('-seg', ''), b.dataset.v); });

// ---- the sheet: one at a time (task, goal, run replay, document, search, finished runs); Esc or a click outside closes it
const SHEET = {kind: null, id: null};
function sheetHTML(html) { return '<button class="close" data-close>' + esc(t('btn.close', 'Close')) + '</button>' + html; }
function openSheet(kind, id, html, wide) {
  SHEET.kind = kind; SHEET.id = id;
  const s = $('sheet'); s.className = 'sheet' + (wide ? ' wide' : '');
  s.dataset.html = sheetHTML(html || '<div class="empty">loading…</div>'); s.innerHTML = s.dataset.html;
  nameSheet(s);
  $('detail').classList.add('on'); s.scrollTop = 0;
  syncHash();
}
function nameSheet(s) {
  const h = s.querySelector('h2');
  if (h) { h.id = 'sheet-title'; s.setAttribute('aria-labelledby', 'sheet-title'); s.removeAttribute('aria-label'); }
  else { s.removeAttribute('aria-labelledby'); s.setAttribute('aria-label', 'Details'); }
}
function setSheet(kind, id, html) {
  if (SHEET.kind !== kind || SHEET.id !== id) return;          // the owner opened something else meanwhile
  const s = $('sheet'), next = sheetHTML(html);
  if (s.dataset.html === next) return;
  if (s.contains(document.activeElement) && typing(document.activeElement)) return;   // never under the owner's fingers
  const keep = s.scrollTop, hadFocus = s.contains(document.activeElement);
  s.dataset.html = next; s.innerHTML = next; s.scrollTop = keep;
  nameSheet(s);
  if (hadFocus && !s.contains(document.activeElement)) s.focus({preventScroll: true});
}
function closeSheet() {
  SHEET.kind = SHEET.id = null; $('detail').classList.remove('on'); $('sheet').dataset.html = ''; syncHash();
}
$('detail').addEventListener('click', e => { if (e.target.id === 'detail' || e.target.closest('[data-close]')) closeSheet(); });
function refreshSheet() {
  if (SHEET.kind === 'task') renderTaskSheet();
  else if (SHEET.kind === 'goal') renderGoalSheet();
}
function syncHash() {
  const parts = ['tab=' + tab].concat(SUB[tab] !== undefined ? ['view=' + SUB[tab]] : []);
  if (project !== 'default') parts.push('project=' + encodeURIComponent(project));
  if (['task', 'goal', 'run'].includes(SHEET.kind)) parts.push(SHEET.kind + '=' + encodeURIComponent(SHEET.id));
  if (HASH.poll === '1') parts.push('poll=1');
  try { history.replaceState(null, '', '#' + parts.join('&')); } catch (e) {}
}

// ---- header: what is abnormal gets a pill; a healthy, running project shows just one
let lastAutoRecover = 0;
function checkFreshness() {
  if (document.hidden) return;
  const staleFor = foremanFetchedAt ? (Date.now() / 1000 - foremanFetchedAt) : 0;
  if (staleFor > 30 && Date.now() - lastAutoRecover > 10000) {
    lastAutoRecover = Date.now();
    if (typeof connect === 'function') connect();
    if (typeof tickPoll === 'function') tickPoll();
  }
}
function pills() {
  if (!F) return '<span class="pill bad">foreman: ' + esc(window._ferr || 'loading') + '</span>';
  const p = [], staleFor = foremanFetchedAt ? (Date.now() / 1000 - foremanFetchedAt) : 0;
  if (staleFor > 30) {
    checkFreshness();
    return '<span class="pill bad">no fresh data for ' + Math.round(staleFor / 60) + ' min — do not trust the state below' +
      (window._ferr ? ' · cause: ' + esc(window._ferr) : '') +
      ' <button class="mini" data-act="reconnect" style="margin-left:6px">Reconnect</button></span>';
  }
  if (!F.alive) p.push('<span class="pill bad"><span class="dot bad"></span>foreman not running</span>');
  else if (F.killswitch) p.push('<span class="pill bad">kill switch ON' + (F.killswitch.scope === 'all' ? ' (all projects)' : '') + ' — ' +
    esc(F.killswitch.reason || '') + '</span><button class="primary sm" data-act="resume">Continue</button>');
  else if (F.paused) p.push('<span class="pill warn">paused</span><button class="mini" data-fix="resume">Resume</button>');
  else p.push('<span class="pill ok"><span class="dot running"></span>running</span>');
  if (F.claude_until) p.push('<span class="pill warn" title="Claude Code is out of usage; Claude via Antigravity substitutes">orchestrator: substitute until ' + tfmt(F.claude_until) + '</span>');
  if (!F.autopilot) p.push('<span class="pill" title="The team does not pick goals from the vision by itself">autopilot off</span>');
  if (F.forge && (F.forge.kind === 'github' || F.forge.kind === 'gitlab')) p.push('<span class="pill">' + (F.forge.kind === 'github' ? 'GitHub' : 'GitLab') + ': ' + esc(F.forge.repo) + '</span>');
  if (F.api && (F.api.workers || []).length) p.push('<span class="pill' + (F.api.spent_today >= F.api.budget ? ' bad' : '') + '">API today ' + usd(F.api.spent_today) + ' / ' + usd(F.api.budget) + '</span>');
  if (F.old) p.push('<span class="pill warn">old foreman version — fewer details</span>');
  return p.join(' ');
}
function stats() {
  if (!F) return '';
  const t_stat = F.today || {}, ts = F.tasks || [], gs = F.goals || [], n = (...s) => ts.filter(x => s.includes(x.status)).length;
  const box = (v, lab, cls, go) => '<div class="stat' + (cls ? ' ' + cls : '') + '"' + (go ? ' data-go="' + go + '"' : '') + '><b>' + v + '</b><span>' + lab + '</span></div>';
  const mine = inboxItems().length, blocked = n('blocked');
  const waitingTasks = ts.filter(x => ['pending', 'splitting'].includes(x.status));
  const pausedCount = waitingTasks.filter(x => gs.some(g => g.id === x.goal && (g.paused || g.status === 'paused'))).length;
  const waitLabel = t('stat.waiting', 'waiting') + (pausedCount ? ' (' + pausedCount + ' ' + t('stat.paused', 'paused') + ')' : '');
  return box(n('running'), t('stat.running', 'running'), '', 'live') +
    box(waitingTasks.length, waitLabel, pausedCount ? 'warn' : '', 'tasks') +
    box(n('gate', 'review', 'merging', 'merge-ready'), t('stat.checking', 'checking'), '', 'tasks') +
    box(t_stat.merged != null ? t_stat.merged : n('done'), t('stat.done_today', 'done today'), '', 'insights') +
    (mine ? box(mine, t('stat.need_you', 'need you'), 'hot', 'overview') : '') +
    (blocked ? box(blocked, t('stat.blocked', 'blocked'), 'bad', 'tasks') : '');
}
document.addEventListener('click', e => { const g = e.target.closest('.stat[data-go]'); if (g) setTab(g.dataset.go); });

// ---- live data: Server-Sent Events (the foreman's state as deltas, new transcript lines only); polling when SSE does not work
let ES = null, pollTimer = null, sseFails = 0;
function onForeman(m) {
  if (m.full) F = m.full;
  else if (F && m.delta) { Object.assign(F, m.delta); (m.removed || []).forEach(k => delete F[k]); }
  window._ferr = m.error; foremanFetchedAt = m.fetched_at || 0;
  if (F && F.owner_lang && !store('af-lang')) {
    const defaultCode = (F.owner_lang.toLowerCase().startsWith('slovak') || F.owner_lang.toLowerCase().startsWith('sk')) ? 'sk' : 'en';
    if (currentLang !== defaultCode) loadLocale(defaultCode);
  }
  if (F && F.forge && F.forge.setup_needed) maybeSetup();
  schedule();
}
function applyPanes(m) {
  if (m.roster) P.roster = m.roster;
  for (const p of m.panes || []) {
    const old = P.panes.find(x => x.id === p.id);
    if (!old || p.reset) {
      const np = {id: p.id, title: p.title, kind: p.kind, state: p.state, lines: p.lines || []};
      if (old) Object.assign(old, np); else P.panes.push(np);
      continue;
    }
    old.state = p.state; old.title = p.title;
    old.lines = old.lines.concat(p.lines || []).slice(-400);
  }
  if ((m.gone || []).length) P.panes = P.panes.filter(x => !m.gone.includes(x.id));
  const order = {orchestrator: 0, worker: 1};
  P.panes.sort((a, b) => ((order[a.kind] ?? 9) - (order[b.kind] ?? 9)) || a.title.localeCompare(b.title));
}
function connect() {
  if (ES) { ES.close(); ES = null; }
  // #poll=1: no live stream (screenshots, proxies that buffer event streams)
  if (!window.EventSource || HASH.poll === '1') { startPoll(); return; }
  ES = new EventSource('/api/stream?project=' + encodeURIComponent(project));
  ES.addEventListener('foreman', e => { try { onForeman(JSON.parse(e.data)); } catch (err) {} });
  ES.addEventListener('panes', e => { try { applyPanes(JSON.parse(e.data)); schedule(); } catch (err) {} });
  ES.onopen = () => { sseFails = 0; stopPoll(); };
  ES.onerror = () => {
    if (++sseFails >= 3 && ES) {                        // an older server without /api/stream, or a proxy that buffers it
      ES.close(); ES = null; startPoll();
      setTimeout(() => { if (!ES) { sseFails = 0; connect(); } }, 120000);
    }
  };
}
async function poll() {
  try { const r = await getJSON('/api/foreman'); onForeman({full: r.foreman, error: r.error, fetched_at: r.fetched_at}); } catch (e) { window._ferr = (e && e.message) || String(e); schedule(); }
  try { const p = await (await fetch('/api/panes')).json(); P.roster = p.roster || {}; P.panes = p.panes || []; schedule(); } catch (e) {}
}
let busyPoll = false;
async function tickPoll() { if (document.hidden || busyPoll) return; busyPoll = true; try { await poll(); } finally { busyPoll = false; } }
function startPoll() { if (pollTimer) return; tickPoll(); pollTimer = setInterval(tickPoll, 2500); }
function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
// for actions: show their effect at once instead of waiting for the next push
const refreshSoon = () => setTimeout(() => { tickPoll(); }, 700);

// mobile / tab wake-up and auto-recovery
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    const staleFor = foremanFetchedAt ? (Date.now() / 1000 - foremanFetchedAt) : 0;
    if (staleFor > 15) {
      if (staleFor > 30 || !ES || ES.readyState !== EventSource.OPEN) connect();
      tickPoll();
    }
  }
});
window.addEventListener('online', () => { connect(); tickPoll(); });
document.addEventListener('click', e => {
  if (e.target.closest('[data-act="reconnect"]')) { connect(); tickPoll(); }
});

// ---- projects
let projAt = 0;
async function loadProjects(force) {
  if (!force && Date.now() - projAt < 30000) return;
  projAt = Date.now();
  let ps = [];
  try { ps = (await (await fetch('/api/projects')).json()).projects || []; } catch (e) { return; }
  if (!ps.some(p => p.name === project)) project = (ps[0] || {name: 'default'}).name;
  PROJ_LIST = ps;
  const sel = $('proj'), html = ps.map(p => '<option value="' + esc(p.name) + '"' + (p.name === project ? ' selected' : '') + '>' + esc(p.name) +
    (p.repo ? ' · ' + esc(p.repo) : '') + '</option>').join('') + '<option value="__new">+ New project…</option>';
  if (sel.innerHTML !== html) sel.innerHTML = html;
  sel.style.display = '';
}
function switchProject(name) {
  project = name; store('af-project', project);
  F = null; P = {roster: {}, panes: []}; closeSheet();
  for (const k in lastHTML) delete lastHTML[k];
  resetProjectState();
  syncHash(); connect(); schedule(true);
}
$('proj').addEventListener('change', e => {
  if (e.target.value === '__new') { e.target.value = project; openNewProject(); return; }
  switchProject(e.target.value);
});
const projectResets = [];          // each tab registers what it caches per project
function resetProjectState() { projectResets.forEach(f => f()); }

// ---- errors of this page are shown, not swallowed
function showJsError(msg) { const el = $('jserr'); if (el) { el.style.display = 'block'; el.textContent = 'Page error: ' + msg; } }
window.addEventListener('error', e => showJsError((e.message || 'unknown') + ' (' + String(e.filename || '').split('/').pop().split('?')[0] + ':' + (e.lineno || '?') + ')'));
window.addEventListener('unhandledrejection', e => showJsError(String(e.reason && e.reason.message || e.reason)));
// ---- dialogs (the sheet and the three process dialogs): they are toggled by a class on .detail, so one observer handles all
const reducedMotion = () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
const DIALOGS = ['detail', 'login', 'newproj', 'setup'].map($).filter(Boolean);
let dialogOpener = null;
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
const openDialog = () => DIALOGS.find(d => d.classList.contains('on'));
const dialogFocusables = d => [...d.querySelectorAll(FOCUSABLE)].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
function dialogsChanged() {
  const d = openDialog();
  for (const el of document.querySelectorAll('body > header, body > main, body > .skip')) { if (d) el.setAttribute('inert', ''); else el.removeAttribute('inert'); }
  if (d) {
    if (!dialogOpener) dialogOpener = document.activeElement;
    const box = d.querySelector('.sheet');
    setTimeout(() => { if (box.contains(document.activeElement)) return; const f = dialogFocusables(box).find(e => !e.classList.contains('close')) || box; f.focus({preventScroll: true}); }, 30);
  } else if (!d && dialogOpener) {
    const back = dialogOpener; dialogOpener = null;
    (document.body.contains(back) && back !== document.body ? back : $('main')).focus({preventScroll: true});
  }
}
DIALOGS.forEach(d => new MutationObserver(dialogsChanged).observe(d, {attributes: true, attributeFilter: ['class']}));
document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const d = openDialog(); if (!d) return;
  const box = d.querySelector('.sheet'), list = dialogFocusables(box);
  if (!list.length) { e.preventDefault(); box.focus(); return; }
  const first = list[0], last = list[list.length - 1];
  if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
const skip = document.querySelector('.skip');
if (skip) skip.addEventListener('click', e => { e.preventDefault(); $('main').focus(); });

// keyboard: / opens search, Esc closes the sheet
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && SHEET.kind) { closeSheet(); return; }
  if (e.key === '/' && !typing(document.activeElement) && !e.ctrlKey && !e.metaKey) { e.preventDefault(); openSearch(); }
});

const themeBtn = $('theme-btn');
if (themeBtn) themeBtn.addEventListener('click', cycleTheme);
const langBtn = $('lang-btn');
if (langBtn) {
  langBtn.addEventListener('click', cycleLang);
  langBtn.addEventListener('contextmenu', e => {
    e.preventDefault();
    const custom = prompt('Enter locale code (e.g. sk_qwerty, sk, en):', currentLang);
    if (custom && custom.trim()) loadLocale(custom.trim().toLowerCase());
  });
}

