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

// ---- Egress: what the workers reached and what was blocked; the owner decides per blocked host (allow it, or accept it as
// harmless noise), then acknowledges the alarms and lets the project continue
const isIp = h => /^[\d.]+$|^\[?[0-9a-f:]+\]?$/i.test(h);
function egressHtml() {
  sysLoad('egress', '/api/egress', {since: '24h'}, 60000);
  const E = SYS.egress;
  if (!E) return '<div class="empty">' + esc(SYS.err.egress || 'reading the proxy log…') + '</div>';
  const h0 = E.health || {}, al = (E.alarms || []).filter(a => a.code !== 'proxy'), ks = E.killswitch || (F && F.killswitch);
  const byEgress = ks && /egress/i.test(ks.reason || '');
  // blocked hosts: who tried them, how often, and whether they still raise an alarm
  const who = {};
  Object.keys(E.new_domains || {}).forEach(k => { const [w, h] = k.split(' '); (who[h] = who[h] || new Set()).add(w); });
  const base = new Set(E.baseline || []), priv = new Set((E.private || []).map(p => String(p.target).split(':')[0]));
  const blocked = Object.entries(E.denied || {}).filter(([h]) => !isIp(h) && !priv.has(h)).sort((x, y) => (who[y[0]] ? 1 : 0) - (who[x[0]] ? 1 : 0) || y[1] - x[1]);
  const local = new Set(E.local_allowlist || []);
  const allowed = Object.assign(Object.fromEntries([...local].map(h => [h, 0])), E.allowed || {});
  const act = (a, h, label, cls) => '<button class="' + (cls || 'mini') + '" data-egact="' + a + '" data-host="' + esc(h) + '">' + label + '</button>';
  let html = '';
  if (byEgress) html += '<div class="inbox-item blocked"><div class="ib-head"><span class="badge bad">stopped</span> The project was stopped by an egress alarm</div>' +
    '<div class="ib-body">' + esc(ks.reason || '') + '</div><div class="ib-body">1. Decide about each blocked host below - <b>Allow</b> it if the work really needs it, or <b>Ignore</b> it ' +
    '(it stays blocked, only the alarm stops). 2. Then continue: the alarms are acknowledged and the workers start again.</div>' +
    '<div class="btnrow"><button class="primary sm" data-act="resume">Acknowledge and continue</button></div></div>';
  html += '<div class="health-head ' + (h0.ok ? 'ok' : 'bad') + '">Proxy: ' + (h0.ok ? 'enforcing' : 'PROBLEM') + ' <span class="muted small">mode ' + esc(h0.mode || '?') +
    ' · last 24 h</span><button class="mini" data-egress-reload>Refresh</button></div>' +
    (h0.ok ? '' : (h0.problems || []).map(p => '<div class="check bad"><span class="ci">✘</span><div>' + esc(p) + '<div class="muted small">workers cannot run until the proxy enforces the allow-list</div></div><div>' +
      fixButton('egress-up') + '</div></div>').join(''));
  if (al.length && !byEgress) html += '<div class="note">' + al.length + ' unacknowledged alarm(s) - they block new runs. Decide about the hosts below, then ' +
    '<button class="primary sm" data-egress-ack>acknowledge</button></div>';
  html += '<h3' + (Object.keys(who).length ? ' class="bad"' : '') + '>Blocked (' + blocked.length + ')</h3>' + (blocked.length ? '<div class="tablewrap"><table class="m"><tr><th>host</th><th>tried by</th>' +
    '<th class="num">times</th><th>state</th><th></th></tr>' + blocked.map(([h, n]) => '<tr><td class="mono">' + esc(h) + '</td><td>' + esc([...(who[h] || [])].join(', ') || '–') +
      '</td><td class="num">' + n + '</td><td>' + (who[h] ? '<span class="badge bad">alarm</span>' : base.has(h) ? '<span class="badge">ignored</span>' : '<span class="badge">acknowledged</span>') +
      '</td><td class="right">' + act('allow', h, 'Allow') + (base.has(h) ? '' : ' ' + act('ignore', h, 'Ignore')) + '</td></tr>').join('') + '</table></div>' +
    small('Allow = the workers may send anything to that host from now on (exact name, all its paths). Ignore = it stays blocked and stops raising alarms - the safer choice for telemetry and update checks.')
    : '<div class="note ok">Nothing was blocked.</div>');
  html += '<h3>Allowed (' + Object.keys(allowed).length + ')</h3><div class="tablewrap"><table class="m"><tr><th>host</th><th class="num">tunnels (24 h)</th><th></th></tr>' +
    Object.entries(allowed).sort((x, y) => y[1] - x[1]).map(([h, n]) => '<tr><td class="mono">' + esc(h) + (local.has(h) ? ' <span class="badge">added by you</span>' : '') + '</td><td class="num">' + n +
      '</td><td class="right">' + (local.has(h) ? act('unallow', h, 'Remove') : '') + '</td></tr>').join('') + '</table></div>' +
    small('Hosts in egress/allowlist.txt (the agents\' own APIs) are part of Agent-Forge and cannot be removed here.');
  if ((E.private || []).length) html += '<h3 class="bad">Attempts to reach a private network</h3>' + E.private.map(p => '<div class="mono small">' + esc(dfmt(p.ts)) + ' ' +
    esc(p.worker) + ' → ' + esc(p.target) + '</div>').join('') + small('These can never be allowed here (your own model server is set in Settings → Workers).');
  return html;
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-egact]'); if (!b) return;
  const h = b.dataset.host, a = b.dataset.egact;
  const q = {allow: 'Allow the workers to reach ' + h + '?\n\nThey can then send anything to it - code, the task, output. Only allow hosts the work really needs (e.g. a package index).',
    ignore: 'Keep ' + h + ' blocked and stop raising alarms for it?', unallow: 'Remove ' + h + ' - the workers can no longer reach it?'}[a];
  if (!confirm(q)) return;
  b.disabled = true;
  const r = await post('/api/egress', {action: a, host: h});
  done(r); if (r.ok && /recreate it once/.test(r.message || '')) alert(r.message);
  SYS.egressAt = 0; SYS.egress = null; schedule(true);
});

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
