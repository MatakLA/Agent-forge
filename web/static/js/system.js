// System: Health (bin/doctor, with a button for each fix that is safe to run), Egress (what the workers reached or tried to
// reach, alarms to acknowledge), Disk (what takes the space, one-click cleanups) and Activity (the event log).

const SYS = {health: null, healthAt: 0, healthBusy: false, egress: null, egressAt: 0, disk: null, diskAt: 0, err: {}, evq: ''};
async function sysLoad(kind, path, params, maxAge) {
  if (SYS[kind + 'Busy'] || (SYS[kind] && Date.now() - SYS[kind + 'At'] < (maxAge || 60000))) return;
  SYS[kind + 'Busy'] = true;
  try { const d = await getJSON(path, params); if (d && d.error && !d.checks && !d.health && !d.runs) SYS.err[kind] = d.error; else { SYS[kind] = d; SYS.err[kind] = null; } }
  catch (e) { SYS.err[kind] = String(e); }
  SYS[kind + 'Busy'] = false; SYS[kind + 'At'] = Date.now();
  if (kind === 'health' && SYS.health) { const n = SYS.health.bad || 0; $('c-system').hidden = !n; $('c-system').textContent = n; }
  schedule(true);
}

// ---- Health
const FIXES = {'egress-up': 'Recreate the egress proxy', 'restart-foreman': 'Restart the foreman', resume: 'Resume', continue: 'Continue (kill switch off)'};
function fixButton(id) {
  if (!id) return '';
  if (FIXES[id]) return '<button class="mini" data-fix="' + esc(id) + '">' + FIXES[id] + '</button>';
  if (/^worker-up:\d+$/.test(id)) return '<button class="mini" data-fix="' + esc(id) + '">Start worker-' + esc(id.split(':')[1]) + '</button>';
  if (/^login:worker-\d+$/.test(id)) return '<button class="mini" data-login="' + esc(id.slice(6)) + '">Sign it in</button>';
  if (/^(settings|key):/.test(id)) return '<button class="mini" data-goset="' + esc(id) + '">Open the setting</button>';
  return '';
}
function healthHtml() {
  sysLoad('health', '/api/health', {}, 120000);
  const H = SYS.health;
  if (!H) return '<div class="empty">' + esc(SYS.err.health || 'checking the install…') + '</div>';
  const checks = H.checks || [], bad = checks.filter(c => c.level !== 'ok');
  const row = c => '<div class="check ' + c.level + '"><span class="ci">' + (c.level === 'ok' ? '✔' : c.level === 'warn' ? '!' : '✘') + '</span><div><div>' + esc(c.message) + '</div>' +
    (c.fix ? '<div class="muted small">→ ' + esc(c.fix) + '</div>' : '') + '</div><div>' + fixButton(c.fix_id) + '</div></div>';
  return '<div class="health-head ' + (H.bad ? 'bad' : H.warn ? 'warn' : 'ok') + '">' + (H.bad ? H.bad + ' problem(s)' : H.warn ? 'All working - ' + H.warn + ' warning(s)' : 'All good') +
    ' <span class="muted small">checked ' + ago(H.at) + ' ago · Agent-Forge ' + esc(H.version || '') + '</span>' +
    '<button class="mini" data-health="quick">Check again</button><button class="mini" data-health="full" title="also asks each Antigravity worker for its quota (slower)">Full check</button></div>' +
    (bad.length ? bad.map(row).join('') : '') + '<details' + (bad.length ? '' : ' open') + '><summary class="muted">All checks (' + checks.length + ')</summary>' +
    [...new Set(checks.map(c => c.section))].map(s => '<h4>' + esc(s) + '</h4>' + checks.filter(c => c.section === s).map(row).join('')).join('') + '</details>';
}

// ---- Egress
function egressHtml() {
  sysLoad('egress', '/api/egress', {since: '24h'}, 60000);
  const E = SYS.egress;
  if (!E) return '<div class="empty">' + esc(SYS.err.egress || 'reading the proxy log…') + '</div>';
  const h0 = E.health || {}, al = (E.alarms || []).filter(a => a.code !== 'proxy');
  const tbl = (title, obj, cls) => { const e = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]); return '<div><h4>' + title + ' (' + e.reduce((a, x) => a + x[1], 0) + ')</h4>' +
    (e.length ? '<table class="m">' + e.slice(0, 40).map(([k, v]) => '<tr><td class="mono small ' + (cls || '') + '">' + esc(k) + '</td><td class="num">' + v + '</td></tr>').join('') + '</table>' : small('none')) + '</div>'; };
  return '<div class="health-head ' + (h0.ok ? 'ok' : 'bad') + '">Proxy: ' + (h0.ok ? 'enforcing' : 'PROBLEM') + ' <span class="muted small">mode ' + esc(h0.mode || '?') +
    ' · since ' + esc(dfmt(E.since)) + '</span><button class="mini" data-egress-reload>Refresh</button></div>' +
    (h0.ok ? '' : (h0.problems || []).map(p => '<div class="check bad"><span class="ci">✘</span><div>' + esc(p) + '<div class="muted small">workers cannot run until the proxy enforces the allow-list</div></div><div>' +
      fixButton('egress-up') + '</div></div>').join('')) +
    (al.length ? '<h3 class="bad">Alarms (' + al.length + ')</h3>' + al.map(a => '<div class="check ' + (a.level === 'HIGH' ? 'bad' : 'warn') + '"><span class="ci">' + (a.level === 'HIGH' ? '✘' : '!') +
      '</span><div>' + esc(a.message) + '<div class="muted small">' + esc(a.code) + '</div></div><div></div></div>').join('') +
      '<div class="btnrow"><button class="primary sm" data-egress-ack>I have read them - acknowledge</button>' + small('A HIGH alarm stops the project (kill switch) and blocks new runs until acknowledged.') + '</div>'
      : '<div class="note ok">No unacknowledged alarms.</div>') +
    '<div class="cols3">' + tbl('Allowed', E.allowed) + tbl('Denied - new', Object.fromEntries(Object.entries(E.new_domains || {})), 'bad') + tbl('Denied - known noise', E.noise) + '</div>' +
    ((E.private || []).length ? '<h4 class="bad">Private-network attempts</h4>' + E.private.map(p => '<div class="mono small">' + esc(dfmt(p.ts)) + ' ' + esc(p.worker) + ' → ' + esc(p.target) + '</div>').join('') : '');
}

// ---- Disk
const MB = n => n >= 2 ** 30 ? (n / 2 ** 30).toFixed(1) + ' GB' : n >= 2 ** 20 ? Math.round(n / 2 ** 20) + ' MB' : Math.max(n ? 1 : 0, Math.round(n / 1024)) + ' kB';
function diskHtml() {
  sysLoad('disk', '/api/disk', {}, 120000);
  const D = SYS.disk;
  if (!D) return '<div class="empty">' + esc(SYS.err.disk || 'measuring…') + '</div>';
  const r = D.runs || {}, tot = (r.transcripts || 0) + (r.compressed || 0) + (r.other || 0), freePct = D.total ? 100 * D.free / D.total : 100;
  return '<div class="stats">' + box(MB(D.free), 'free' + (D.total ? ' of ' + MB(D.total) : ''), freePct < 10 ? 'bad' : '') + box(MB(tot), 'runs (' + (r.runs || 0) + ')') +
    box(MB(r.transcripts || 0), 'transcripts') + box(MB(r.compressed || 0), 'compressed') + '</div>' +
    '<h3>Runs</h3><div class="note">Transcripts of finished runs older than ' + D.keep_days + ' days are compressed by the maintenance automatically' +
    (r.old_uncompressed ? ' - ' + r.old_uncompressed + ' wait for it (' + MB(r.old_bytes) + ').' : '.') + ' The numbers of every run (tokens, cost, outcome) stay even when a transcript is deleted.</div>' +
    '<div class="btnrow"><button class="mini" data-clean="compress">Compress old transcripts now</button><button class="mini" data-clean="transcripts">Delete transcripts older than…</button></div>' +
    ((D.docker || []).length ? '<h3>Docker</h3><div class="tablewrap"><table class="m"><tr><th>type</th><th class="num">count</th><th class="num">size</th><th class="num">reclaimable</th></tr>' +
      D.docker.map(x => '<tr><td>' + esc(x.Type) + '</td><td class="num">' + esc(x.TotalCount) + '</td><td class="num">' + esc(x.Size) + '</td><td class="num">' + esc(x.Reclaimable) + '</td></tr>').join('') +
      '</table></div><div class="btnrow"><button class="mini" data-clean="docker">Remove unused images and the build cache</button>' + small('never a tagged image, a container or a volume') + '</div>' : '');
}

// ---- Activity: the event log, filterable
function activityHtml() {
  const q = SYS.evq.toLowerCase(), ev = ((F && F.events) || []).slice().reverse().filter(e => !q || (e.msg || '').toLowerCase().includes(q));
  return '<input type="search" class="filter" id="ev-q" placeholder="filter the recent events (older ones: search ⌕)" value="' + esc(SYS.evq) + '">' +
    (ev.map(e => { const m = e.msg || '', cls = /BLOCKED|refused|error|failed|unavailable|kill switch/i.test(m) ? 'bad' : /DONE|merged|done:/.test(m) ? 'good' : '';
      return '<div class="ev ' + cls + '"><time>' + esc(dfmt(e.at)) + '</time><div>' + esc(m).replace(/\b(\d{4}-[a-z0-9][\w-]*)\b/, '<a data-open="task:$1">$1</a>') + '</div></div>'; }).join('') ||
      '<div class="empty">Nothing yet.</div>');
}

function renderSystem(force) {
  const v = SUB.system;
  if (v === 'activity' && busyIn('system')) return;
  setHTML('system', v === 'egress' ? egressHtml() : v === 'disk' ? diskHtml() : v === 'activity' ? activityHtml() : healthHtml(), force);
}
document.addEventListener('input', e => { if (e.target.id === 'ev-q') { SYS.evq = e.target.value; const pos = e.target.selectionStart;
  setHTML('system', activityHtml(), true); const i = $('ev-q'); i.focus(); i.setSelectionRange(pos, pos); } });
document.addEventListener('click', async e => {
  const hb = e.target.closest('[data-health]');
  if (hb) { SYS.healthAt = 0; SYS.health = null; hb.disabled = true; schedule(true);
    SYS.healthBusy = true;
    try { SYS.health = await getJSON('/api/health', hb.dataset.health === 'full' ? {full: 1} : {}); } catch (err) { SYS.err.health = String(err); }
    SYS.healthBusy = false; SYS.healthAt = Date.now(); schedule(true); return; }
  const fx = e.target.closest('[data-fix]');
  if (fx) { const id = fx.dataset.fix;
    if (id === 'continue') { resumeDev(); return; }
    if (id === 'egress-up' && !confirm('Recreate the egress proxy? Running worker runs lose their network for a moment and are stopped.')) return;
    fx.disabled = true; fx.textContent = 'working…';
    const r = await post('/api/fix', {fix: id});
    done(r, 'Done'); if (r.message && !r.ok) alert(r.message);
    SYS.healthAt = 0; SYS.health = null; refreshSoon(); schedule(true); return; }
  const gs = e.target.closest('[data-goset]');
  if (gs) { const k = gs.dataset.goset; SUB.settings = /COMPAT|key:/.test(k) ? 'team' : /NOTIFY/.test(k) ? 'project' : 'team'; setTab('settings'); return; }
  if (e.target.closest('[data-egress-reload]')) { SYS.egressAt = 0; SYS.egress = null; schedule(true); return; }
  if (e.target.closest('[data-egress-ack]')) { if (!confirm('Acknowledge every alarm up to now? Only do this after reading them.')) return;
    done(await post('/api/egress', {action: 'ack'}), 'Acknowledged'); SYS.egressAt = 0; SYS.egress = null; schedule(true); return; }
  const cl = e.target.closest('[data-clean]');
  if (cl) { const act = cl.dataset.clean; let days = '';
    if (act === 'transcripts') { days = prompt('Delete the transcripts of finished runs older than how many days? (the run numbers stay)', '30'); if (!days || !/^\d+$/.test(days)) return; }
    else if (act === 'docker' && !confirm('Remove dangling Docker images and the build cache?')) return;
    cl.disabled = true; cl.textContent = 'working…';
    done(await post('/api/disk', {action: act, days})); SYS.diskAt = 0; SYS.disk = null; schedule(true); }
});
projectResets.push(() => { SYS.health = null; SYS.healthAt = 0; SYS.disk = null; SYS.diskAt = 0; });
