// A goal on one page: its plan and tasks, everything that happened to it in order, what it cost, its acceptance criteria with
// the evidence, and all of its changes in one diff.

const GD = {id: null, data: null, err: null, form: null, diffs: null, diffLoading: false, at: 0};
function openGoal(id) {
  Object.assign(GD, {id, data: null, err: null, form: null, diffs: null, diffLoading: false});
  openSheet('goal', id, '<div class="empty">loading the goal…</div>', true);
  loadGoalDetail(id);
}
async function loadGoalDetail(id) {
  try { const d = await getJSON('/api/goal', {id}); if (GD.id !== id) return; if (d.error || d.ok === false) GD.err = d.error || d.message; else { GD.data = d; GD.err = null; } }
  catch (e) { GD.err = String(e); }
  GD.at = Date.now(); renderGoalSheet();
}
const EVICON = [[/planned|roadmap|autopilot/i, '◆'], [/dispatched/i, '▶'], [/finished \(|testing \+ review/i, '◼'], [/back to the worker|rework|sent back/i, '↺'],
  [/BLOCKED|blocked|refused|failed/i, '✗'], [/done: merged|DONE|merged/i, '✓'], [/waiting for your|approve|by the owner/i, '☞']];
const evIcon = m => (EVICON.find(([r]) => r.test(m)) || [0, '·'])[1];
function goalActions(g) {
  const b = (act, label, cls) => '<button class="' + (cls || 'mini') + '" data-ga="' + act + '" data-id="' + esc(g.id) + '">' + label + '</button>';
  if (g.status === 'approval') return b('approve', 'Approve the plan', 'primary sm') + '<button class="mini" data-gform="replan">Change the plan…</button>' +
    '<button class="mini" data-gform="edit">Edit the goal…</button>' + b('cancel', 'Cancel');
  if (['done', 'cancelled'].includes(g.status)) return '';
  return (g.paused ? b('resume', 'Resume', 'primary sm') : b('pause', 'Pause')) + (g.priority ? '' : b('prioritize', 'Do this first')) +
    (g.status === 'new' ? '<button class="mini" data-gform="edit">Edit…</button>' : '') + b('cancel', 'Cancel');
}
function goalForm(g) {
  if (!GD.form) return '';
  const edit = GD.form === 'edit';
  return '<div class="fbform"><textarea id="gf-text" placeholder="' + (edit ? '' : 'What should the planner do differently?') + '">' + (edit ? esc(g.text) : '') + '</textarea>' +
    '<div class="btnrow"><button class="primary sm" data-gsend="' + GD.form + '">' + (edit ? 'Save and plan it again' : 'Plan again') + '</button><button class="mini" data-gform="">Cancel</button></div></div>';
}
function renderGoalSheet() {
  if (SHEET.kind !== 'goal') return;
  const id = GD.id, live = F && (F.goals || []).find(x => x.id === id), d = GD.data;
  if (!d && !live) return setSheet('goal', id, GD.err ? '<div class="empty">' + esc(GD.err) + '</div>' : '<div class="empty">loading the goal…</div>');
  const g = Object.assign({}, d ? d.goal : {}, live || {});
  if (live && d && (live.status !== d.goal.status || (F.tasks || []).filter(t => t.goal === id).map(t => t.status).join() !== (d.tasks || []).map(t => t.status).join())
      && Date.now() - GD.at > 4000) { GD.at = Date.now(); loadGoalDetail(id); }
  const ts = d ? d.tasks : (F.tasks || []).filter(t => t.goal === id), tot = d ? d.totals : null, orch = d ? d.orchestrator : null;
  const liveT = id => (F && (F.tasks || []).find(x => x.id === id)) || {};
  const doneN = ts.filter(t => (liveT(t.id).status || t.status) === 'done').length, reworks = ts.reduce((a, t) => a + (t.reworks || 0), 0);
  const acc = g.acceptance || [], cr = g.criteria || [];
  const crit = acc.length ? acc.map((a, i) => { const c = cr[i];
    return '<div class="critrow">' + (!c ? '<span class="cm wait">○</span>' : c.met ? '<span class="cm ok">✓</span>' : '<span class="cm no">✗</span>') + '<span>' + esc(a) +
      (c && c.evidence ? '<br><span class="muted small">' + esc(c.evidence) + '</span>' : '') + '</span></div>'; }).join('') +
    (g.criteria_at ? small('checked ' + esc(dfmt(g.criteria_at))) : small('checked when all its tasks are finished')) : small('No acceptance criteria yet - the planner writes them with the plan.');
  const plan = g.status === 'approval' ? '<h3 class="hot">The plan waiting for you</h3>' + ts.filter(t => liveT(t.id).awaiting === 'plan').map(t =>
    '<div class="plantask"><b>' + esc(t.title) + '</b> ' + roleTag(t.role) + (liveT(t.id).risk === 'risky' ? ' <span class="badge warn">risky</span>' : '') +
    '<div class="muted small">' + esc((liveT(t.id).spec || '').slice(0, 260)) + '</div></div>').join('') : '';
  const tasksT = ts.length ? '<div class="tablewrap"><table class="m click"><tr><th>task</th><th>role</th><th>status</th><th>worker</th><th class="num">reworks</th><th class="num">lines</th><th>PR</th></tr>' +
    ts.map(t => { const lt = Object.assign({}, t, liveT(t.id));
      return '<tr data-open="task:' + esc(t.id) + '"><td><span class="mono small">' + esc(t.id) + '</span><br>' + esc(lt.title) + '</td><td>' + roleTag(lt.role) + '</td><td>' + chip(lt.status) +
        '</td><td>' + esc(lt.worker || '') + '</td><td class="num">' + (t.reworks || 0) + '</td><td class="num">' + (lt.diff ? '<span class="plus">+' + lt.diff.added + '</span> <span class="minus">−' + lt.diff.removed + '</span>' : '') +
        '</td><td>' + (lt.pr_url ? '<a href="' + esc(lt.pr_url) + '" target="_blank" rel="noopener">#' + esc(lt.pr) + '</a>' : lt.pr ? '#' + esc(lt.pr) : '') + '</td></tr>'; }).join('') + '</table></div>' : small('No tasks yet.');
  let day = '';
  const evs = (d ? d.events : []).map(e => { const dd = String(e.at || '').slice(0, 10), head = dd !== day ? '<div class="evday">' + esc(dd) + '</div>' : ''; day = dd;
    return head + '<div class="ev2"><span class="ico">' + evIcon(e.msg || '') + '</span><time>' + esc(tfmt(e.at)) + '</time><span>' +
      esc(e.msg || '').replace(/\b(\d{4}-[\w-]+)\b/, m => ts.some(t => t.id === m) ? '<a data-open="task:' + m + '">' + m + '</a>' : m) + '</span></div>'; }).join('');
  const html = '<h2>' + esc(g.text) + '</h2><div class="sub">' + [GSTATUS[g.status] || g.status, g.paused ? '<b class="warn">paused</b>' : '', g.priority ? '<b>first</b>' : '',
      'created ' + esc(dfmt(g.created)), g.finished ? 'finished ' + esc(dfmt(g.finished)) : '', g.rounds > 1 ? 'round ' + g.rounds : '', g.source && g.source !== 'owner' ? esc(g.source) : '']
      .filter(Boolean).join(' · ') + '</div><div class="btnrow">' + goalActions(g) + '</div>' + goalForm(g) +
    (g.owner_notes ? '<div class="note">Your notes: ' + esc(g.owner_notes) + '</div>' : '') +
    (g.summary || g.notes ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') + plan +
    '<div class="kpis">' + '<div><b>' + doneN + '/' + ts.length + '</b><span>tasks done</span></div><div><b>' + reworks + '</b><span>sent back</span></div>' +
    (tot && tot.runs ? '<div><b>' + fmtTok(tot.tokens) + '</b><span>tokens, ' + tot.runs + ' runs</span></div>' + (tot.cost_usd ? '<div><b>' + usd(tot.cost_usd) + '</b><span>API cost</span></div>' : '') +
      (tot.week ? '<div><b>' + tot.week + ' %</b><span>of a week\'s quota</span></div>' : '') + '<div><b>' + dur(tot.duration_s) + '</b><span>worker time</span></div>' : '') +
    (tot && tot.wall_s ? '<div><b>' + dur(tot.wall_s) + '</b><span>' + (g.finished ? 'from start to done' : 'since it was created') + '</span></div>' : '') +
    (orch && orch.calls ? '<div><b>' + orch.calls + '</b><span>orchestrator calls</span></div>' : '') + '</div>' +
    '<h3>Done when</h3><div class="crit">' + crit + '</div><h3>Tasks</h3>' + tasksT +
    '<h3>What happened</h3>' + (evs ? '<div class="evlist">' + evs + '</div>' : small(d ? 'nothing yet' : 'loading…')) +
    '<h3>All changes</h3>' + (GD.diffs == null ? '<button class="mini" data-gdiff>' + (GD.diffLoading ? 'loading…' : 'Show the combined diff of its merged tasks') + '</button>' :
      GD.diffs.map(x => '<h4 class="mono">' + esc(x.id) + ' — ' + esc(x.title) + '</h4>' + diffHtml(x.text)).join('') || small('nothing merged yet'));
  setSheet('goal', id, html);
}
async function loadGoalDiffs() {
  GD.diffLoading = true; renderGoalSheet();
  const ts = ((GD.data && GD.data.tasks) || []).filter(t => t.status === 'done'), out = [];
  for (const t of ts) {
    try { out.push({id: t.id, title: t.title, text: await (await fetch('/api/diff?task=' + encodeURIComponent(t.id) + '&project=' + encodeURIComponent(project))).text()}); }
    catch (e) { out.push({id: t.id, title: t.title, text: '(could not load)'}); }
  }
  GD.diffs = out; GD.diffLoading = false; renderGoalSheet();
}
document.addEventListener('click', async e => {
  if (e.target.closest('[data-gdiff]')) { loadGoalDiffs(); return; }
  const gf = e.target.closest('[data-gform]');
  if (gf) { GD.form = gf.dataset.gform || null; $('sheet').dataset.html = ''; renderGoalSheet(); setTimeout(() => { const x = $('gf-text'); if (x) x.focus(); }, 30); return; }
  const gs = e.target.closest('[data-gsend]');
  if (gs) { const text = ($('gf-text') || {}).value || '';
    if (!text.trim()) { $('gf-text').focus(); return; }
    gs.disabled = true; const ok = await goalAction(GD.id, gs.dataset.gsend, text); gs.disabled = false;
    if (ok) { GD.form = null; $('sheet').dataset.html = ''; renderGoalSheet(); } }
});
projectResets.push(() => { GD.id = null; GD.data = null; });
