// Agent-Forge dashboard - core: state, helpers, the render loop, polling, the project picker.
// Scripts share one global scope and load in this order: core, overview, tasks, live, insights, settings, main.
// every write carries X-Agent-Forge: the server refuses POSTs without it (a cross-site page cannot add it without CORS consent)
const _fetch = window.fetch.bind(window);
window.fetch = (u, o) => { o = o || {}; if ((o.method || 'GET').toUpperCase() === 'POST') o.headers = Object.assign({'X-Agent-Forge': '1'}, o.headers || {}); return _fetch(u, o); };

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tfmt = ts => { const d = typeof ts === 'number' ? new Date(ts*1000) : new Date(ts); return isNaN(d) ? '' : d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}); };
const dfmt = ts => { const d = new Date(ts); if (isNaN(d)) return ''; const today = new Date().toDateString() === d.toDateString();
  return today ? d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : d.toLocaleString([], {day:'numeric', month:'numeric', hour:'2-digit', minute:'2-digit'}); };
const ago = ts => { if (!ts) return ''; const s = Math.max(0, Math.round(Date.now()/1000 - (typeof ts === 'number' ? ts : Date.parse(ts)/1000)));
  return s < 60 ? s + ' s' : s < 3600 ? Math.round(s/60) + ' min' : Math.round(s/3600) + ' h'; };
const STATUS = {splitting:'Splitting', pending:'Waiting', running:'Running', gate:'Checking', review:'Checking', merging:'Merging', done:'Done', blocked:'Blocked', cancelled:'Cancelled'};
const RTNAME = {agy: 'Antigravity', claude: 'Claude Code', codex: 'Codex', gemini: 'Gemini CLI'};
const RTDESC = {agy: 'a Google account with a subscription, signed in from the browser', claude: 'Anthropic API key, paid per token',
                codex: 'OpenAI API key, paid per token', gemini: 'Gemini API key, paid per token'};
const roleCls = r => /secur/i.test(r) ? 'sec' : (/test/i.test(r) && !/develop/i.test(r)) ? 'test' : '';
let tab = 'overview', F = null, P = {roster:{}, panes:[]}, openTask = null, project = 'default';
try { project = localStorage.getItem('af-project') || 'default'; } catch (e) {}
const inPool = w => !F || !(F.pool || []).length || F.pool.includes(w);
try { tab = localStorage.getItem('af-tab') || 'overview'; } catch (e) {}
// deep links: #tab=queue, #task=0012-store, #project=shop (bookmarks, links in notifications)
const HASH = Object.fromEntries((location.hash || '').replace(/^#/, '').split('&').filter(Boolean).map(kv => kv.split('=').map(decodeURIComponent)));
if (['overview', 'queue', 'timeline', 'live', 'metrics', 'settings', 'events'].includes(HASH.tab)) tab = HASH.tab;
if (HASH.project && /^[a-z0-9][a-z0-9-]{0,39}$/.test(HASH.project)) project = HASH.project;

function setTab(t) {
  tab = t;
  for (const k in lastHTML) delete lastHTML[k];
  try { localStorage.setItem('af-tab', t); } catch (e) {}
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('main section').forEach(s => s.classList.toggle('on', s.id === 't-' + t));
  render();
}
document.getElementById('nav').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.t); });
document.getElementById('detail').addEventListener('click', e => { if (e.target.id === 'detail' || e.target.classList.contains('close')) { openTask = null; lastDetailHtml = ''; e.currentTarget.classList.remove('on'); } });
document.addEventListener('click', e => {
  const df = e.target.closest('[data-diff]');
  if (df) { loadDiff(df.dataset.diff); return; }
  const dg = e.target.closest('[data-dag]');
  if (dg) { const id = dg.dataset.dag; openDags.has(id) ? openDags.delete(id) : openDags.add(id); render(true); return; }
  const c = e.target.closest('[data-task]');
  if (c) { openTask = c.dataset.task; renderDetail(); }
});

// ---- only touch the DOM when a section's HTML actually changed (no flicker, no lost scroll, less battery)
const lastHTML = {};
function setHTML(id, html, force) {
  if (!force && lastHTML[id] === html) return;
  lastHTML[id] = html;
  document.getElementById(id).innerHTML = html;
}
let lastDetailHtml = '';
const openDags = new Set(), diffCache = {}, diffLoading = new Set();

function render(force) {
  setHTML('pills', pills(), force);
  setHTML('stats', stats(), force);
  const ts = (F && F.tasks) || [];
  document.getElementById('c-queue').textContent = ts.filter(t => ['pending', 'running', 'gate', 'review'].includes(t.status)).length;
  if (tab === 'overview') {
    setHTML('team', team(), force);
    setHTML('goals', goalsHtml(), force);
    const bl = (F && F.backlog) || [];
    setHTML('backlog', bl.length ? bl.map((b, i) => '<div class="goal blrow"><span class="muted">' + (i + 1) + '.</span> <span class="bltext">' + esc(b) +
        '</span><span class="blbtns">' + (i ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + i + '" title="up">↑</button>' : '') +
        (i < bl.length - 1 ? '<button class="mini" data-bl="move" data-n="' + (i + 1) + '" data-to="' + (i + 2) + '" title="down">↓</button>' : '') +
        '<button class="mini" data-bl="drop" data-n="' + (i + 1) + '" title="remove">✕</button></span></div>').join('')
      : '<div class="empty">Empty' + (F && F.autopilot ? ' — when a worker is free, the autopilot picks the next goal from the vision.' : '.') + '</div>', force);
    const qs = (F && F.owner_questions) || [];
    if (!document.getElementById('questions').contains(document.activeElement))
      setHTML('questions', qs.length ? '<h3>The planner\'s questions (' + qs.length + ')</h3>' + qs.map((q, i) => '<div class="goal qrow"><div class="qtext">' + esc(q) +
        '</div><div class="btnrow"><input type="text" class="qin" data-qn="' + (i + 1) + '" placeholder="your answer (the planner will follow it)">' +
        '<button class="mini" data-qa="' + (i + 1) + '">Answer</button><button class="mini" data-qd="' + (i + 1) + '" title="the question does not matter">Dismiss</button></div></div>').join('') : '', force);
  } else if (tab === 'queue') setHTML('board', boardHtml(), force);
  else if (tab === 'events') setHTML('events', eventsHtml(), force);
  else if (tab === 'timeline') setHTML('timeline', timelineHtml(), force);
  else if (tab === 'metrics') setHTML('metrics', metricsHtml(), force);
  else if (tab === 'settings' && (force || !document.getElementById('settings').contains(document.activeElement))) setHTML('settings', settingsHtml(), force);
  liveHtml();
  if (openTask) renderDetail();
}

let foremanFetchedAt = 0;
let projAt = 0;
async function loadProjects() {
  if (Date.now() - projAt < 30000) return;
  projAt = Date.now();
  let ps = [];
  try { ps = (await (await fetch('/api/projects')).json()).projects || []; } catch (e) { return; }
  const sel = document.getElementById('proj');
  if (!ps.some(p => p.name === project)) project = (ps[0] || {name: 'default'}).name;
  PROJ_LIST = ps;
  const html = ps.map(p => '<option value="' + esc(p.name) + '"' + (p.name === project ? ' selected' : '') + '>' + esc(p.name) +
                     (p.repo ? ' · ' + esc(p.repo) : '') + '</option>').join('') + '<option value="__new">+ New project…</option>';
  if (sel.innerHTML !== html) sel.innerHTML = html;
  sel.style.display = '';
}
let PROJ_LIST = [];

document.getElementById('proj').addEventListener('change', e => {
  if (e.target.value === '__new') { e.target.value = project; openNewProject(); return; }
  project = e.target.value;
  try { localStorage.setItem('af-project', project); } catch (err) {}
  F = null; openTask = null; lastDetailHtml = ''; document.getElementById('detail').classList.remove('on');
  for (const k in lastHTML) delete lastHTML[k];
  for (const k in diffCache) delete diffCache[k];
  for (const id in paneEls) { paneEls[id].el.remove(); delete paneEls[id]; }
  S = null; sLoadedFor = null; sErr = {}; sMsg = '';
  poll();
});
async function poll() {
  await loadProjects();
  try { const r = await (await fetch('/api/foreman?project=' + encodeURIComponent(project))).json(); F = r.foreman; window._ferr = r.error; foremanFetchedAt = r.fetched_at || 0; } catch (e) {}
  try { P = await (await fetch('/api/panes')).json(); } catch (e) {}
  if (F && F.forge && F.forge.setup_needed && !setupDismissed[project] && !document.getElementById('setup').classList.contains('on')) {
    setupDismissed[project] = true; if (!S) await loadSettings(true); openSetup(); }
  render();
}
function showJsError(msg) {
  const el = document.getElementById('jserr');
  if (el) { el.style.display = 'block'; el.textContent = 'Page error: ' + msg; }
}
window.addEventListener('error', e => showJsError((e.message || 'unknown') + ' (' + (e.lineno || '?') + ')'));
window.addEventListener('unhandledrejection', e => showJsError(String(e.reason && e.reason.message || e.reason)));
