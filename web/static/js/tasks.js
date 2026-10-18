// Tasks tab: the board and the project-wide dependency graph (with the critical path); a task's sheet: what it is, what it
// cost, its runs (each one can be replayed), and only the actions that make sense in its state.

const orphanBadge = t => t && t.orphan_warning ? '<span class="badge warn" title="crashing repeatedly">crashing repeatedly - ' + esc(t.orphan_streak) + '/' + esc(t.orphan_limit || ((S && S.values && S.values.ORPHAN_LIMIT) || 5)) + ' consecutive orphan recoveries</span>' : '';

function whyWaiting(t) {
  if (t.status !== 'pending' && t.status !== 'splitting') return null;
  const g = F && (F.goals || []).find(x => x.id === t.goal);
  if (g && (g.paused || g.status === 'paused')) {
    return {
      code: 'paused',
      short: 'Goal paused',
      graphLabel: 'Goal paused',
      detail: 'Goal "' + (g.text ? g.text.slice(0, 70) + '…' : g.id) + '" is paused by the owner. Tasks of paused goals do not start until the goal is resumed.',
      goalId: g.id
    };
  }
  const done = new Set(((F && F.tasks) || []).filter(x => x.status === 'done').map(x => x.id));
  const unmet = (t.depends_on || []).filter(d => !done.has(d));
  if (unmet.length) {
    return {
      code: 'deps',
      short: 'Needs ' + unmet.slice(0, 2).join(', '),
      graphLabel: 'Needs ' + unmet[0].slice(0, 10),
      detail: 'Waits for dependency task(s) to finish and merge first: ' + unmet.join(', ') + '.'
    };
  }
  const running = ((F && F.tasks) || []).filter(x => x.status === 'running');
  const conflict = running.find(r => (r.files || []).some(f => (t.files || []).includes(f)));
  if (conflict) {
    const shared = (t.files || []).filter(f => (conflict.files || []).includes(f));
    return {
      code: 'file_conflict',
      short: 'File conflict (' + conflict.id + ')',
      graphLabel: 'Conflict (' + conflict.id.slice(0, 8) + ')',
      detail: 'Waits because task ' + conflict.id + ' is currently running on ' + (conflict.worker || 'a worker') + ' and editing the same file(s): ' + shared.join(', ') + '. Tasks with overlapping files cannot run in parallel.'
    };
  }
  if (t.not_before && t.not_before * 1000 > Date.now()) {
    const mins = Math.max(1, Math.round((t.not_before * 1000 - Date.now()) / 60000));
    return {
      code: 'backoff',
      short: 'Retry in ' + mins + 'm',
      graphLabel: 'Retry ' + mins + 'm',
      detail: 'Temporary retry backoff. Will be retried automatically in ' + mins + ' minute(s).'
    };
  }
  if (t.pin_worker) {
    return {
      code: 'assigned',
      short: 'Assigned to ' + t.pin_worker,
      graphLabel: '→ ' + t.pin_worker,
      detail: 'Assigned to ' + t.pin_worker + '. Starts when ' + t.pin_worker + ' becomes free.'
    };
  }
  return {
    code: 'ready',
    short: 'Ready to dispatch',
    graphLabel: 'Ready',
    detail: 'Ready to run! Waiting for an idle worker to pick it up.'
  };
}

// ---- board
function boardHtml() {
  const ts = F.tasks || [];
  if (!ts.length) return '<div class="empty">No tasks yet — when goals are planned, their tasks appear here.</div>';
  const est = t => t.estimate ? '<span title="estimate: ' + esc(t.estimate.basis) + '">~' + fmtTok(t.estimate.tokens) + ' tok</span>' : '';
  const phase = id => { const st = ((F.live || {}).steps || []).find(x => x.key === 'task:' + id); return st ? st.phase : ''; };
  const cols = [['wait', 'Waiting', t => ['pending', 'splitting', 'approval'].includes(t.status)], ['run', 'Running', t => t.status === 'running'],
    ['check', 'Checking (tests, review, merge)', t => ['gate', 'review', 'merging', 'merge-ready'].includes(t.status)], ['done', 'Done', t => t.status === 'done'],
    ['stop', 'Blocked / cancelled', t => t.status === 'blocked' || t.status === 'cancelled']];
  const renderedTotal = cols.reduce((sum, [k, _, f]) => { const count = ts.filter(f).length; return sum + (k === 'done' || k === 'stop' ? Math.min(count, 15) : count); }, 0);
  const summary = '<div class="muted small" style="margin-bottom:6px">' + (renderedTotal === ts.length ? ts.length + ' task(s)' : renderedTotal + ' of ' + ts.length + ' task(s) shown') + '. Click a task to open it.</div>';
  return summary + '<div class="board">' + cols.map(([k, lab, f]) => {
    let items = ts.filter(f);
    if (k === 'done') items = items.slice().sort((a, b) => (b.finished || '').localeCompare(a.finished || ''));
    if (k === 'stop') items = items.slice().sort((a, b) => (a.status === 'blocked' ? 0 : 1) - (b.status === 'blocked' ? 0 : 1) || (b.updated || '').localeCompare(a.updated || ''));
    const total = items.length, shown = k === 'done' || k === 'stop' ? items.slice(0, 15) : items;
    const countStr = total > shown.length ? shown.length + ' of ' + total : String(shown.length);
    return '<div class="col"><h4>' + lab + '<span class="muted">' + countStr + '</span></h4>' + (shown.map(t => {
      const deps = (t.depends_on || []).filter(d => !ts.find(x => x.id === d && x.status === 'done'));
      const ww = whyWaiting(t);
      const waitBadge = (ww && k === 'wait') ? (ww.code === 'paused' ? ' <span class="badge warn" title="' + esc(ww.detail) + '">⏸ goal paused</span>' :
        ww.code === 'file_conflict' ? ' <span class="badge" title="' + esc(ww.detail) + '">⚡ ' + esc(ww.short) + '</span>' :
        ww.code === 'ready' ? ' <span class="badge ok" title="' + esc(ww.detail) + '">▶ ready</span>' :
        ' <span class="badge" title="' + esc(ww.detail) + '">' + esc(ww.short) + '</span>') : '';
      return '<div class="card s-' + esc(t.status) + '" data-open="task:' + esc(t.id) + '" role="button" tabindex="0" aria-label="Task ' + esc(t.id) + ': ' + esc(t.title) + ' (' + esc(STATUS[t.status] || t.status) + ')">' +
        '<div class="id">' + esc(t.id) + (t.status === 'approval' ? ' <span class="badge hot">needs you</span>' : '') +
        (t.pin_worker ? ' <span class="badge" title="assigned by you">→ ' + esc(t.pin_worker) + '</span>' : '') +
        (t.orphan_warning ? ' ' + orphanBadge(t) : '') + '</div><div class="t">' + esc(t.title) + '</div><div class="m">' +
        roleTag(t.role) + (t.worker && k !== 'wait' ? '<span>' + esc(t.worker) + '</span>' : '') +
        (t.attempts ? '<span class="warn">attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + '</span>' : '') +
        (t.pr ? '<span>PR #' + esc(t.pr) + '</span>' : '') + (deps.length && k === 'wait' ? '<span>waits for ' + deps.map(esc).join(', ') + '</span>' : '') +
        waitBadge + (k === 'wait' ? est(t) : '') + (t.diff ? '<span class="plus" title="+' + t.diff.added + ' lines added">+' + t.diff.added + '</span><span class="minus" title="−' + t.diff.removed + ' lines removed">−' + t.diff.removed + '</span>' : '') +
        (phase(t.id) ? '<span class="accent">' + esc(phase(t.id)) + '</span>' : '') + '</div></div>';
    }).join('') || '<div class="empty">—</div>') + (total > shown.length ? '<div class="muted small center">… ' + (total - shown.length) + ' more (search finds them)</div>' : '') + '</div>';
  }).join('') + '</div>';
}

// ---- the project's dependency graph: open work and what it waits for; the critical path is the longest chain of remaining work
const NODECOL = {done: 'var(--ok)', running: 'var(--tool)', gate: 'var(--accent)', review: 'var(--accent)', merging: 'var(--accent)', 'merge-ready': 'var(--accent)',
  blocked: 'var(--bad)', pending: 'var(--idle)', splitting: 'var(--idle)', approval: 'var(--warn)', cancelled: 'var(--idle)'};
function criticalPath(nodes) {
  // longest path (by estimated tokens, 1 per task without an estimate) through the tasks that are not done
  const byId = Object.fromEntries(nodes.map(t => [t.id, t])), memo = {};
  const w = t => t.status === 'done' ? 0 : (t.estimate ? t.estimate.tokens : 300000);
  const best = (id, seen) => { if (memo[id]) return memo[id]; if (seen.has(id)) return {len: 0, path: []}; seen.add(id);
    const t = byId[id]; let b = {len: 0, path: []};
    for (const d of (t.depends_on || []).filter(x => byId[x])) { const r = best(d, seen); if (r.len > b.len) b = r; }
    return memo[id] = {len: b.len + w(t), path: b.path.concat([id])}; };
  let top = {len: 0, path: []};
  nodes.filter(t => t.status !== 'done').forEach(t => { const r = best(t.id, new Set()); if (r.len > top.len) top = r; });
  return top;
}
function dagSvg(nodes, crit) {
  const ids = new Set(nodes.map(t => t.id)), depth = {}, onPath = new Set(crit ? crit.path : []);
  const d = (t, seen) => { if (depth[t.id] != null) return depth[t.id]; if (seen.has(t.id)) return 0; seen.add(t.id);
    const ps = (t.depends_on || []).filter(x => ids.has(x)).map(x => d(nodes.find(m => m.id === x), seen));
    return depth[t.id] = ps.length ? 1 + Math.max(...ps) : 0; };
  nodes.forEach(t => d(t, new Set()));
  const cols = {}; nodes.forEach(t => (cols[depth[t.id]] = cols[depth[t.id]] || []).push(t));
  const nc = Object.keys(cols).length, maxRows = Math.max(1, ...Object.values(cols).map(c => c.length));
  const BW = 176, BH = 38, GX = 46, GY = 12, W = Math.max(1, nc) * (BW + GX), H = maxRows * (BH + GY);
  const pos = {};
  Object.entries(cols).forEach(([c, ts]) => ts.forEach((t, r) => pos[t.id] = {x: c * (BW + GX), y: r * (BH + GY)}));
  let svg = '<svg class="dag" viewBox="0 0 ' + W + ' ' + H + '" style="max-width:' + W + 'px">';
  nodes.forEach(t => (t.depends_on || []).filter(x => pos[x]).forEach(x => {
    const a = pos[x], b = pos[t.id], hot = onPath.has(x) && onPath.has(t.id);
    svg += '<path d="M' + (a.x + BW) + ',' + (a.y + BH / 2) + ' C' + (a.x + BW + GX / 2) + ',' + (a.y + BH / 2) + ' ' + (b.x - GX / 2) + ',' + (b.y + BH / 2) + ' ' + b.x + ',' + (b.y + BH / 2) +
           '" fill="none" stroke="' + (hot ? 'var(--warn)' : 'var(--muted)') + '" stroke-width="' + (hot ? 2.5 : 1.3) + '"/>';
  }));
  nodes.forEach(t => { const p = pos[t.id], hot = onPath.has(t.id);
    const ww = whyWaiting(t);
    const isPaused = ww && ww.code === 'paused';
    const strokeCol = isPaused ? 'var(--warn)' : (NODECOL[t.status] || 'var(--idle)');
    const dashArr = isPaused ? ' stroke-dasharray="4,3"' : '';
    const statusText = isPaused ? '⏸ Paused (goal)' : (ww && ww.code === 'file_conflict' ? '⚡ ' + ww.graphLabel : (STATUS[t.status] || t.status));
    svg += '<g data-open="task:' + esc(t.id) + '" role="button" tabindex="0" focusable="true" aria-label="Task ' + esc(t.id) + ': ' + esc(t.title) + ' (' + esc(statusText) + ')">' +
      '<title>' + esc(t.title) + ' · ' + esc(t.role) + (ww ? ' · ' + esc(ww.detail) : ' · ' + esc(STATUS[t.status] || t.status)) + '</title><rect x="' + p.x + '" y="' + p.y +
      '" width="' + BW + '" height="' + BH + '" rx="7" fill="var(--card2)" stroke="' + strokeCol + '" stroke-width="' + (hot ? 3 : 1.8) + '"' + dashArr +
      (t.status === 'done' ? ' opacity=".55"' : '') + '/><text x="' + (p.x + 8) + '" y="' + (p.y + 15) + '">' + esc(t.id.slice(0, 24)) + '</text><text x="' + (p.x + 8) + '" y="' + (p.y + 29) +
      '" class="sub">' + esc((statusText + ' · ' + t.title)).slice(0, 27) + '</text></g>'; });
  return svg + '</svg>';
}
function graphHtml() {
  const allTasks = F.tasks || [];
  if (!allTasks.length) return '<div class="empty">No tasks yet — the graph shows the work that is left and what it waits for.</div>';
  const ts = allTasks.filter(t => t.status !== 'cancelled'), open = ts.filter(t => t.status !== 'done');
  if (!open.length) return '<div class="empty">No open tasks — the graph shows the work that is left and what it waits for.</div>';
  const need = new Set(open.flatMap(t => t.depends_on || []));
  const nodes = ts.filter(t => t.status !== 'done' || need.has(t.id));
  const crit = criticalPath(nodes);
  const left = crit.path.filter(id => (nodes.find(t => t.id === id) || {}).status !== 'done');
  const depCount = nodes.length - open.length;
  const pausedTasks = open.filter(t => (whyWaiting(t) || {}).code === 'paused');
  const pausedBanner = pausedTasks.length ? ' <span class="badge warn" title="Tasks of paused goals do not dispatch until resumed">⏸ ' + pausedTasks.length + ' task(s) wait because their goal is paused</span>' : '';
  return '<div class="muted small" style="margin-bottom:6px">' + nodes.length + ' task(s) in graph (' + open.length + ' open' + (depCount ? ', ' + depCount + ' finished dependencies' : '') + ').' + pausedBanner + ' <b class="warn">Critical path</b>: ' + left.length + ' task(s) in a row (' +
    left.map(esc).join(' → ') + ') — about ' + fmtTok(crit.len) + ' tokens of work that cannot run in parallel. Click a task to open it.</div>' +
    '<div class="tablewrap pad">' + dagSvg(nodes, crit) + '</div>' +
    '<div class="legend">' + [['pending', 'waiting'], ['running', 'running'], ['gate', 'checking'], ['approval', 'needs you'], ['blocked', 'blocked'], ['done', 'done (a dependency)'], ['paused', 'goal paused']]
      .map(([k, l]) => '<span><i style="background:' + (k === 'paused' ? 'var(--warn)' : NODECOL[k]) + (k === 'paused' ? ';outline:1px dashed var(--warn)' : '') + '"></i>' + l + '</span>').join('') + '</div>';
}
function renderTasks(force) {
  if (!F) return setHTML('tasks-view', window._ferr ? '<div class="empty bad">Could not load tasks: ' + esc(window._ferr) + '</div>' : '<div class="empty">loading…</div>', force);
  setHTML('tasks-view', SUB.tasks === 'graph' ? graphHtml() : boardHtml(), force);
}

// ---- a task's sheet: details come from /api/task when it opens (runs, costs, full history), the state from the live data
const TD = {id: null, data: null, err: null, diff: null, diffLoading: false, form: null, at: 0};
function openTask(id) {
  Object.assign(TD, {id, data: null, err: null, diff: null, diffLoading: false, form: null});
  openSheet('task', id, '<div class="empty">loading ' + esc(id) + '…</div>');
  loadTaskDetail(id);
}
async function loadTaskDetail(id) {
  try { const d = await getJSON('/api/task', {id}); if (TD.id !== id) return; if (d.error || d.ok === false) TD.err = d.error || d.message; else { TD.data = d; TD.err = null; } }
  catch (e) { TD.err = String(e); }
  TD.at = Date.now(); renderTaskSheet();
}
function taskActions(t) {
  const b = (act, label, cls) => '<button class="' + (cls || 'mini') + '" data-ta="' + act + '" data-id="' + esc(t.id) + '">' + label + '</button>';
  const f = (key, label) => '<button class="mini" data-tform="' + key + '">' + label + '</button>';
  if (t.status === 'approval' && t.awaiting === 'merge') return b('approve', 'Merge', 'primary sm') + f('feedback', 'Send back…');
  if (t.status === 'approval' && t.awaiting === 'risky') return b('approve', 'Start it', 'primary sm') + b('cancel', 'Cancel');
  if (t.status === 'approval' && t.awaiting === 'plan') return '<button class="mini" data-open="goal:' + esc(t.goal) + '">Its plan waits for you — open the goal</button>';
  if (t.status === 'running') return t.cancel_requested ? small('stopping its run…') : b('cancel', 'Stop and cancel');
  if (t.status === 'blocked') return b('retry', 'Retry', 'primary sm') + f('feedback', 'Send back with my words…') + b('cancel', 'Cancel');
  if (t.status === 'cancelled') return b('retry', 'Queue it again');
  if (t.status === 'pending' || t.status === 'splitting') return f('feedback', (t.attempts || (t.history || []).length) ? 'Send my words…' : 'Add a note…') +
    f('assign', 'Assign to…') + f('model', 'Model…') + b('cancel', 'Cancel');
  return '';
}
function taskForm(t) {
  if (TD.form === 'feedback') return '<div class="fbform"><textarea id="tf-text" placeholder="' + (t.status === 'pending' && !t.attempts ? 'A note the worker gets with the task' :
    'What should the worker change? It continues from its branch.') + '"></textarea><div class="btnrow"><button class="primary sm" data-tsend="feedback">Send</button>' +
    '<button class="mini" data-tform="">Cancel</button></div></div>';
  if (TD.form === 'assign') return '<div class="fbform"><select id="tf-target"><option value="">any worker</option>' + ((F && F.pool) || []).map(w =>
    '<option' + (w === t.pin_worker ? ' selected' : '') + '>' + esc(w) + '</option>').join('') + '</select><div class="btnrow"><button class="primary sm" data-tsend="assign">Assign</button>' +
    '<button class="mini" data-tform="">Cancel</button></div></div>';
  if (TD.form === 'model') return '<div class="fbform"><input type="text" id="tf-target" list="tf-models" placeholder="model for its next run (empty = the usual choice)" value="' + esc(t.model_override || '') + '">' +
    '<datalist id="tf-models">' + (((S && S.models) || []).map(m => '<option value="' + esc(m.id) + '">' + esc(m.name) + '</option>').join('')) + '</datalist>' +
    '<div class="btnrow"><button class="primary sm" data-tsend="model">Use it</button><button class="mini" data-tform="">Cancel</button></div></div>';
  return '';
}
function runsTable(runs) {
  if (!runs.length) return small('No finished run yet.');
  return '<div class="tablewrap"><table class="m"><tr><th>when</th><th>worker</th><th>model</th><th>outcome</th><th class="num">tokens</th><th class="num">cost / quota</th><th class="num">time</th><th></th></tr>' +
    runs.map(r => '<tr><td>' + esc(dfmt(r.at)) + '</td><td>' + esc(r.worker) + '</td><td>' + esc(r.runtime !== 'agy' ? (RTNAME[r.runtime] || r.runtime) : (r.model || '')) + '</td><td>' +
      esc(r.reason) + '</td><td class="num">' + fmtTok(r.tokens) + '</td><td class="num">' + (r.cost_usd ? usd(r.cost_usd) : r.week != null ? r.week + ' % quota' : '–') +
      '</td><td class="num">' + dur(r.duration_s) + '</td><td><button class="linkbtn" data-open="run:' + esc(r.run_id) + '">replay</button></td></tr>').join('') + '</table></div>';
}
function renderTaskSheet() {
  if (SHEET.kind !== 'task') return;
  const id = TD.id, live = F && (F.tasks || []).find(x => x.id === id), d = TD.data;
  if (!d && !live) return setSheet('task', id, TD.err ? '<div class="empty bad">Could not load task: ' + esc(TD.err) + '</div>' : '<div class="empty">loading ' + esc(id) + '…</div>');
  const t = Object.assign({}, d ? d.task : {}, live || {});            // live state wins (status, worker)
  if (live && d && live.updated && live.updated !== d.task.updated && Date.now() - TD.at > 3000) { TD.at = Date.now(); loadTaskDetail(id); }
  const g = F && (F.goals || []).find(x => x.id === t.goal), tot = d ? d.totals : null, orch = d ? d.orchestrator : null;
  const hist = (t.history || []).slice().reverse().map(h => '<div class="hist"><b>' + esc(dfmt(h.at)) + ' · ' + esc(h.outcome) + '</b>' + (h.reviewer ? ' (' + esc(h.reviewer) + ')' : '') +
    '<br>' + esc(tsloc(h.feedback || h.why || h.what || h.note || '')) + '</div>').join('');
  const prLink = t.pr_url ? '<a href="' + esc(t.pr_url) + '" target="_blank" rel="noopener">PR #' + esc(t.pr || '') + '</a>' : t.pr ? 'PR #' + esc(t.pr) : '';
  const est = (d && d.estimate) || t.estimate;
  const subMeta = [esc(id), roleTag(t.role || ''), chip(t.status), t.worker ? esc(t.worker) : '',
      t.runtime && t.runtime !== 'agy' ? esc(RTNAME[t.runtime] || t.runtime) : '', prLink, t.risk === 'risky' ? '<span class="badge warn">risky</span>' : '',
      orphanBadge(t),
      t.pin_worker ? '<span class="badge">→ ' + esc(t.pin_worker) + '</span>' : '', t.model_override ? '<span class="badge">model ' + esc(t.model_override) + '</span>' : '',
      'attempt ' + ((t.attempts || 0) + 1) + '/' + (t.max_attempts || 4),
  const ww = whyWaiting(t);
  const whyCard = ww ? '<div class="banner ' + (ww.code === 'paused' ? 'warn' : 'info') + '" style="margin:10px 0;padding:8px 12px;border-radius:8px">' +
    '<b>' + (ww.code === 'paused' ? '⏸ ' : '⏳ ') + esc(ww.short) + '</b>: ' + esc(ww.detail) +
    (ww.code === 'paused' && ww.goalId ? '<div style="margin-top:6px"><button class="mini primary" data-ga="resume" data-id="' + esc(ww.goalId) + '">Resume this goal</button></div>' : '') +
    '</div>' : '';
  const html = '<h2>' + esc(t.title || id) + '</h2><div class="sub">' + subMeta + '</div>' +
    whyCard +
    '<div class="btnrow">' + taskActions(t) + '</div>' + taskForm(t) +
    (g ? '<div class="goalref">Goal: <button class="linkbtn" data-open="goal:' + esc(g.id) + '" style="font:inherit;text-align:left">' + esc(g.text.slice(0, 140)) + '</button></div>' : '') +
    (t.status === 'approval' && t.awaiting === 'merge' && t.merge_feedback ? '<h3>The review says</h3><div class="note">' + esc(tsloc(t.merge_feedback)) + '</div>' : '') +
    (t.blocked_why ? '<h3 class="bad">Why blocked</h3><pre>' + esc(tsloc(t.blocked_why)) + '</pre>' : '') +
    (t.feedback && !['done', 'cancelled'].includes(t.status) ? '<h3>Latest feedback to the worker</h3><pre>' + esc(tsloc(t.feedback)) + '</pre>' : '') +
    (t.owner_notes ? '<h3>Your notes</h3><pre>' + esc(tsloc(t.owner_notes)) + '</pre>' : '') +
    '<h3>Cost</h3><div class="kpis">' +
      (est && !['done', 'cancelled'].includes(t.status) ? '<div><b>~' + fmtTok(est.tokens) + '</b><span>estimated tokens<br>(' + esc(est.basis) + ')</span></div>' : '') +
      (tot && tot.runs ? '<div><b>' + tot.runs + '</b><span>runs</span></div><div><b>' + fmtTok(tot.tokens) + '</b><span>tokens</span></div>' +
        (tot.cost_usd ? '<div><b>' + usd(tot.cost_usd) + '</b><span>API cost</span></div>' : '') + (tot.week ? '<div><b>' + tot.week + ' %</b><span>of a week\'s quota</span></div>' : '') +
        '<div><b>' + dur(tot.duration_s) + '</b><span>worker time</span></div>' : '') +
      (orch && orch.calls ? '<div><b>' + orch.calls + '</b><span>orchestrator calls' + (orch.cost_usd ? ' · ' + usd(orch.cost_usd) : '') + '</span></div>' : '') + '</div>' +
    (d ? runsTable(d.runs || []) : small('loading the runs…')) +
    ((t.acceptance || []).length ? '<h3>Acceptance criteria</h3><ul class="plain">' + t.acceptance.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>' : '') +
    (t.spec ? '<details' + (['done', 'cancelled'].includes(t.status) ? '' : ' open') + '><summary>Spec</summary><pre>' + esc(t.spec) + '</pre></details>' : '') +
    ((t.files || []).length ? '<h3>Files (' + t.files.length + ')</h3><div class="mono small">' + t.files.map(esc).join('<br>') + '</div>' : '') +
    ((t.mutations || []).length ? '<h3>Mutation tests</h3><pre>' + esc(t.mutations.join('\n')) + '</pre>' : '') +
    (t.gate_tail ? '<details><summary>Last gate output</summary><pre>' + esc(t.gate_tail) + '</pre></details>' : '') +
    (hist ? '<h3>History</h3>' + hist : '') +
    (t.diff || t.pr || t.status === 'approval' ? '<h3>Changes</h3>' + (TD.diff == null ? '<button class="mini" data-tdiff' + (TD.diffLoading ? ' disabled' : '') + '>' + (TD.diffLoading ? 'loading diff…' : 'Show the diff') + '</button>' : diffHtml(TD.diff)) : '');
  setSheet('task', id, html);
}
async function loadTaskDiff() {
  TD.diffLoading = true; renderTaskSheet();
  try { TD.diff = await (await fetch('/api/diff?task=' + encodeURIComponent(TD.id) + '&project=' + encodeURIComponent(project))).text(); }
  catch (e) { TD.diff = '(could not load: ' + e + ')'; }
  TD.diffLoading = false; renderTaskSheet();
}
// a diff, file by file, each file foldable; long files start folded
function diffHtml(text) {
  if (!text || !text.trim() || text.startsWith('(no diff') || text === 'no changes') {
    return '<div class="empty">No diff available</div>';
  }
  if (text.startsWith('(could not load') || text.startsWith('(error:') || text.startsWith('(invalid task')) {
    return '<div class="empty bad">' + esc(text) + '</div>';
  }
  const files = [], lines = text.split('\n').slice(0, 8000);
  let cur = null;
  for (const l of lines) {
    if (l.startsWith('diff --git')) { cur = {name: l.replace(/^diff --git a\/(\S+) b\/.*/, '$1'), lines: [], add: 0, del: 0}; files.push(cur); continue; }
    if (!cur) { cur = {name: '', lines: [], add: 0, del: 0}; files.push(cur); }
    if (l.startsWith('+') && !l.startsWith('+++')) cur.add++;
    if (l.startsWith('-') && !l.startsWith('---')) cur.del++;
    if (!l.startsWith('+++') && !l.startsWith('---') && !l.startsWith('index ')) cur.lines.push(l);
  }
  const validFiles = files.filter(f => f.name || f.add || f.del || f.lines.length);
  if (!validFiles.length) return '<div class="empty">No diff available</div>';
  return validFiles.map(f => '<details class="dfile"' + (f.lines.length < 400 ? ' open' : '') + '><summary><span class="mono">' + esc(f.name || '(diff)') + '</span> <span class="plus">+' + f.add +
    '</span> <span class="minus">−' + f.del + '</span></summary><div class="diff">' + f.lines.map(l => {
      const c = l.startsWith('@@') ? 'at' : l.startsWith('+') ? 'a' : l.startsWith('-') ? 'r' : '';
      return '<div' + (c ? ' class="' + c + '"' : '') + '>' + (esc(l) || ' ') + '</div>'; }).join('') + '</div></details>').join('');
}
document.addEventListener('click', async e => {
  if (e.target.closest('[data-tdiff]')) { loadTaskDiff(); return; }
  const tf = e.target.closest('[data-tform]');
  if (tf) { TD.form = tf.dataset.tform || null; if (TD.form === 'model' && !S) loadSettings(true); $('sheet').dataset.html = ''; renderTaskSheet();
    setTimeout(() => { const x = $('tf-text') || $('tf-target'); if (x) x.focus(); }, 30); return; }
  const ts = e.target.closest('[data-tsend]');
  if (ts) {
    const act = ts.dataset.tsend, text = ($('tf-text') || {}).value || '', target = (($('tf-target') || {}).value || '').trim();
    if (act === 'feedback' && !text.trim()) { $('tf-text').focus(); return; }
    ts.disabled = true;
    const ok = await taskAction(TD.id, act, act === 'feedback' ? {text} : {target});
    ts.disabled = false;
    if (ok) { TD.form = null; $('sheet').dataset.html = ''; renderTaskSheet(); }
  }
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && !typing(e.target)) {
    const op = e.target.closest('[data-open]');
    if (op) {
      e.preventDefault();
      const [kind, id] = op.dataset.open.split(/:(.*)/s);
      if (kind === 'task') openTask(id);
      else if (kind === 'goal') openGoal(id);
      else if (kind === 'run') openRun(id);
      return;
    }
    const btn = e.target.closest('[role="button"]');
    if (btn) {
      e.preventDefault();
      if (typeof btn.click === 'function') btn.click();
      else btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
      return;
    }
  }
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    if (e.target.id === 'tf-text' || e.target.id === 'tf-target') {
      const sendBtn = document.querySelector('[data-tsend]');
      if (sendBtn) { e.preventDefault(); sendBtn.click(); }
    }
  }
});
projectResets.push(() => { TD.id = null; TD.data = null; });
