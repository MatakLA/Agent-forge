// Tasks: the board, a task's detail sheet, dependency graphs, diffs, the event log.
function boardHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const cols = [['pending', 'Waiting'], ['running', 'Running'], ['gate', 'Checking (tests + review)'], ['done', 'Done'], ['blocked', 'Blocked / cancelled']];
  const inCol = (t, s) => t.status === s || (s === 'gate' && (t.status === 'review' || t.status === 'merging')) || (s === 'pending' && t.status === 'splitting');
  return cols.map(([s, lab]) => {
    let items = ts.filter(t => inCol(t, s) || (s === 'blocked' && t.status === 'cancelled'));
    if (s === 'done') items = items.slice().sort((a, b) => (b.finished || '').localeCompare(a.finished || '')).slice(0, 15);
    const phase = id => { const st = ((F.live || {}).steps || []).find(x => x.key === 'task:' + id); return st ? st.phase : ''; };
    return '<div class="col"><h4>' + lab + '<span class="muted">' + items.length + '</span></h4>' + (items.map(t => {
      const deps = (t.depends_on || []).filter(d => !ts.find(x => x.id === d && x.status === 'done'));
      return '<div class="card s-' + t.status + '" data-task="' + esc(t.id) + '"><div class="id">' + esc(t.id) + '</div><div class="t">' + esc(t.title) + '</div><div class="m">' +
        '<span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span>' + (t.worker && s !== 'pending' ? '<span>' + esc(t.worker) + '</span>' : '') +
        (t.attempts ? '<span style="color:var(--warn)">attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + '</span>' : '') +
        (t.pr ? '<span>PR #' + t.pr + '</span>' : '') + (deps.length && s === 'pending' ? '<span>waits for ' + deps.map(esc).join(', ') + '</span>' : '') +
        (t.diff ? '<span style="color:var(--ok)">+' + t.diff.added + '</span><span style="color:var(--bad)">-' + t.diff.removed + '</span>' : '') +
        (phase(t.id) ? '<span style="color:var(--accent)">' + esc(phase(t.id)) + '</span>' : '') + '</div></div>';
    }).join('') || '<div class="empty">—</div>') + '</div>';
  }).join('');
}

function renderDetail() {
  const d = document.getElementById('detail');
  const t = F && (F.tasks || []).find(x => x.id === openTask);
  if (!t) { d.classList.remove('on'); lastDetailHtml = ''; return; }
  const g = (F.goals || []).find(x => x.id === t.goal);
  const sheet = document.getElementById('sheet'), keep = sheet.scrollTop;
  const hist = (t.history || []).map(h => '<div class="hist"><b>' + esc(dfmt(h.at)) + ' · ' + esc(h.outcome) + '</b>' +
    (h.reviewer ? ' (' + esc(h.reviewer) + ')' : '') + '<br>' + esc(h.feedback || h.why || h.what || '') + '</div>').join('');
  const html = '<button class="close">Close</button><h2 class="task-title">' + esc(t.title) + '</h2>' +
    '<div class="muted" style="font-size:12.5px">' + esc(t.id) + ' · <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> · ' +
    esc(STATUS[t.status] || t.status) + ' · attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + (t.worker ? ' · ' + esc(t.worker) : '') +
    (t.pr_url ? ' · <a href="' + esc(t.pr_url) + '" target="_blank" rel="noopener">PR #' + esc(t.pr || '') + '</a>' : t.pr ? ' · PR #' + t.pr : '') +
    (t.runtime && t.runtime !== 'agy' ? ' · ' + esc(RTNAME[t.runtime] || t.runtime) : '') + (t.risk ? ' · risk: ' + esc(t.risk) : '') + (t.run_tokens ? ' · ' + (t.run_tokens/1000).toFixed(0) + 'k tokens' : '') +
    (t.diff ? ' · <span style="color:var(--ok)">+' + t.diff.added + '</span> <span style="color:var(--bad)">-' + t.diff.removed + '</span> lines' : '') +
    (t.total_lines != null ? ' · file(s) total ' + t.total_lines + ' lines' : '') + '</div>' +
    (g ? '<h3>Goal</h3><div style="font-size:13px">' + esc(g.text) + '</div>' : '') +
    (t.blocked_why ? '<h3 style="color:var(--bad)">Why blocked</h3><pre>' + esc(t.blocked_why) + '</pre>' : '') +
    (t.feedback && t.status !== 'done' ? '<h3>Latest feedback to the worker</h3><pre>' + esc(t.feedback) + '</pre>' : '') +
    (hist ? '<h3>History</h3>' + hist : '') +
    ((t.acceptance || []).length ? '<h3>Acceptance criteria</h3><ul style="font-size:13px;margin:0;padding-left:18px">' + t.acceptance.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '') +
    ((t.files || []).length ? '<h3>Files</h3><div class="muted" style="font-size:12.5px;font-family:ui-monospace,monospace">' + t.files.map(esc).join('<br>') + '</div>' : '') +
    ((t.mutations || []).length ? '<h3>Mutation tests</h3><pre>' + esc(t.mutations.join('\n')) + '</pre>' : '') +
    (t.diff || t.pr ? '<h3>Changes (diff)</h3>' + (diffCache[t.id] == null
        ? '<button class="linkbtn" data-diff="' + esc(t.id) + '">' + (diffLoading.has(t.id) ? 'loading…' : 'show diff') + '</button>'
        : diffHtml(diffCache[t.id])) : '') +
    (t.spec ? '<h3>Spec</h3><pre>' + esc(t.spec) + '</pre>' : '');
  if (html === lastDetailHtml && d.classList.contains('on')) return;
  lastDetailHtml = html;
  sheet.innerHTML = html;
  d.classList.add('on');
  sheet.scrollTop = keep;
}

function eventsHtml() {
  if (!F) return '';
  const ev = (F.events || []).slice().reverse();
  return ev.map(e => { const m = e.msg || '';
    const cls = /BLOCKED|refused|error|failed|unavailable|kill switch/i.test(m) ? 'bad' : /done|DONE|merged|planned/.test(m) ? 'good' : '';
    return '<div class="ev ' + cls + '"><time>' + esc(dfmt(e.at)) + '</time><div>' + esc(m) + '</div></div>'; }).join('') || '<div class="empty">Nothing yet.</div>';
}

// ---- 3. dependency graph of a goal
const NODECOL = {done: 'var(--ok)', running: 'var(--tool)', gate: 'var(--accent)', review: 'var(--accent)', blocked: 'var(--bad)', pending: 'var(--idle)', cancelled: 'var(--idle)'};
function dagSvg(mine) {
  const ids = new Set(mine.map(t => t.id)), depth = {};
  const d = (t, seen) => { if (depth[t.id] != null) return depth[t.id]; if (seen.has(t.id)) return 0; seen.add(t.id);
    const ps = (t.depends_on || []).filter(x => ids.has(x)).map(x => d(mine.find(m => m.id === x), seen));
    return depth[t.id] = ps.length ? 1 + Math.max(...ps) : 0; };
  mine.forEach(t => d(t, new Set()));
  const cols = {}; mine.forEach(t => (cols[depth[t.id]] = cols[depth[t.id]] || []).push(t));
  const nc = Object.keys(cols).length, maxRows = Math.max(...Object.values(cols).map(c => c.length));
  const BW = 170, BH = 34, GX = 50, GY = 12, W = nc * (BW + GX), H = maxRows * (BH + GY);
  const pos = {};
  Object.entries(cols).forEach(([c, ts]) => ts.forEach((t, r) => pos[t.id] = {x: c * (BW + GX), y: r * (BH + GY)}));
  let svg = '<svg class="dag" viewBox="0 0 ' + W + ' ' + H + '" style="max-width:' + W + 'px">';
  mine.forEach(t => (t.depends_on || []).filter(x => pos[x]).forEach(x => {
    const a = pos[x], b = pos[t.id];
    svg += '<path d="M' + (a.x + BW) + ',' + (a.y + BH / 2) + ' C' + (a.x + BW + GX / 2) + ',' + (a.y + BH / 2) + ' ' + (b.x - GX / 2) + ',' + (b.y + BH / 2) + ' ' + b.x + ',' + (b.y + BH / 2) +
           '" fill="none" stroke="var(--muted)" stroke-width="1.5"/>';
  }));
  mine.forEach(t => { const p = pos[t.id];
    svg += '<g data-task="' + esc(t.id) + '"><title>' + esc(t.title) + ' · ' + esc(t.role) + '</title><rect x="' + p.x + '" y="' + p.y + '" width="' + BW + '" height="' + BH + '" rx="6" fill="var(--card2)" stroke="' +
           (NODECOL[t.status] || 'var(--idle)') + '" stroke-width="2"/><text x="' + (p.x + 8) + '" y="' + (p.y + 14) + '">' + esc(t.id.slice(0, 24)) + '</text><text x="' + (p.x + 8) + '" y="' + (p.y + 27) +
           '" style="fill:var(--muted)">' + esc((STATUS[t.status] || t.status) + ' · ' + t.role).slice(0, 26) + '</text></g>'; });
  return svg + '</svg>';
}

// ---- 4. diff of a task (what the reviewer saw, or the merged PR)
async function loadDiff(id) {
  diffLoading.add(id); renderDetail();
  try { diffCache[id] = await (await fetch('/api/diff?task=' + encodeURIComponent(id) + '&project=' + encodeURIComponent(project))).text(); }
  catch (e) { diffCache[id] = '(could not load: ' + e + ')'; }
  diffLoading.delete(id); renderDetail();
}
function diffHtml(text) {
  const lines = text.split('\n').slice(0, 4000);
  return '<div class="diff">' + lines.map(l => {
    const c = l.startsWith('diff --git') || l.startsWith('+++') || l.startsWith('---') ? 'h' : l.startsWith('@@') ? 'at' : l.startsWith('+') ? 'a' : l.startsWith('-') ? 'r' : '';
    return '<div' + (c ? ' class="' + c + '"' : '') + '>' + (esc(l) || ' ') + '</div>'; }).join('') + '</div>';
}
