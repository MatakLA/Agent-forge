// Overview tab: what needs the owner (approvals, questions, blocked tasks), autopilot and foreman pause/resume switches,
// the first steps of a new install, new goals, the team (with why an idle worker is idle), the goals and the backlog (drag
// to reorder, click to edit, forecast).

// ---- "Needs you": everything that waits for the owner, most urgent first
function inboxItems() {
  if (!F) return [];
  const ts = F.tasks || [], out = [];
  for (const g of (F.goals || []).filter(g => g.status === 'approval'))
    out.push({kind: 'plan', key: 'goal:' + g.id, goal: g, tasks: ts.filter(t => t.goal === g.id && t.awaiting === 'plan')});
  for (const t of ts.filter(t => t.status === 'approval' && t.awaiting === 'merge')) out.push({kind: 'merge', key: 'task:' + t.id, task: t});
  for (const t of ts.filter(t => t.status === 'approval' && t.awaiting === 'risky')) out.push({kind: 'risky', key: 'task:' + t.id, task: t});
  if (F.killswitch && /egress/i.test(F.killswitch.reason || '')) out.unshift({kind: 'egress', key: 'egress', reason: F.killswitch.reason});
  (F.owner_questions || []).forEach((q, i) => out.push({kind: 'question', key: 'q:' + i, n: i + 1, q}));
  for (const t of ts.filter(t => t.status === 'blocked')) out.push({kind: 'blocked', key: 'task:' + t.id, task: t});
  return out;
}
const FB_OPEN = new Set(), FB_DRAFT = {};          // inline "send back with my words" forms that are open, and what is typed in them
function fbForm(key, placeholder, sendLabel) {
  if (!FB_OPEN.has(key)) return '';
  return '<div class="fbform"><textarea data-fbdraft="' + esc(key) + '" placeholder="' + esc(placeholder) + '" aria-label="' + esc(placeholder) + '">' + esc(FB_DRAFT[key] || '') + '</textarea>' +
    '<div class="btnrow"><button class="primary sm" data-fbsend="' + esc(key) + '">' + esc(sendLabel) + '</button><button class="mini" data-fbtoggle="' + esc(key) + '">Cancel</button></div></div>';
}
function inboxHtml() {
  if (!F) return window._ferr ? '<div class="banner bad">Failed to load overview data: ' + esc(window._ferr) + '</div>' : '';
  const items = inboxItems();
  if (!items.length) return '<h3>' + t('head.needs_you', 'Needs you') + '</h3><div class="empty">' + t('msg.nothing_needs_you', 'Nothing needs your approval right now.') + '</div>';
  const card = (cls, head, body, btns, form) => '<div class="inbox-item ' + cls + '"><div class="ib-head">' + head + '</div>' + (body ? '<div class="ib-body">' + body + '</div>' : '') +
    '<div class="btnrow">' + btns + '</div>' + (form || '') + '</div>';
  const open = (kind, id, label) => '<button class="mini" data-open="' + kind + ':' + esc(id) + '">' + (label || t('btn.open', 'Open')) + '</button>';
  const html = items.map(it => {
    if (it.kind === 'plan') {
      const list = it.tasks.slice(0, 6).map(t_task => '<li>' + esc(t_task.title) + ' ' + roleTag(t_task.role) + (t_task.risk === 'risky' ? ' <span class="badge warn">risky</span>' : '') + '</li>').join('') +
        (it.tasks.length > 6 ? '<li class="muted">… ' + (it.tasks.length - 6) + ' more</li>' : '');
      return card('plan', '<span class="badge">plan</span> ' + esc(it.goal.text.slice(0, 160)), '<ul class="ib-list">' + list + '</ul>' + (it.goal.notes ? small(esc(it.goal.notes)) : ''),
        '<button class="primary sm" data-ga="approve" data-id="' + esc(it.goal.id) + '">' + t('btn.approve_plan', 'Approve the plan') + '</button><button class="mini" data-fbtoggle="' + esc(it.key) + '">' + t('btn.change_it', 'Change it…') + '</button>' + open('goal', it.goal.id),
        fbForm(it.key, 'What should the planner do differently? (it plans again with your note)', 'Plan again'));
    }
    if (it.kind === 'merge') {
      const t_task = it.task;
      return card('merge', '<span class="badge ok">' + t('status.merge_ready', 'ready to merge') + '</span> ' + esc(t_task.title) + ' ' + small(esc(t_task.id)),
        (t_task.merge_feedback ? esc(t_task.merge_feedback) + ' ' : '') + (t_task.diff ? '<span class="plus">+' + t_task.diff.added + '</span> <span class="minus">−' + t_task.diff.removed + '</span>' : ''),
        '<button class="primary sm" data-ta="approve" data-id="' + esc(t_task.id) + '">' + t('btn.approve_merge', 'Approve merge') + '</button><button class="mini" data-fbtoggle="' + esc(it.key) + '">' + t('btn.send_back', 'Send back…') + '</button>' + open('task', t_task.id, 'Diff & details'),
        fbForm(it.key, 'What should the worker change?', 'Send back'));
    }
    if (it.kind === 'risky') {
      const t_task = it.task;
      const sp = String(t_task.spec || '').replace(/\s+/g, ' ');
      return card('risky', '<span class="badge warn">risky task</span> ' + esc(t_task.title) + ' ' + roleTag(t_task.role), esc(sp.slice(0, 220)) + (sp.length > 220 ? '…' : ''),
        '<button class="primary sm" data-ta="approve" data-id="' + esc(t_task.id) + '">' + t('btn.approve_task', 'Approve task') + '</button><button class="mini" data-ta="cancel" data-id="' + esc(t_task.id) + '">' + t('btn.cancel', 'Cancel') + '</button>' + open('task', t_task.id));
    }
    if (it.kind === 'egress')
      return card('blocked', '<span class="badge bad">stopped</span> A worker tried to reach a host that is not allowed', esc(it.reason),
        '<button class="primary sm" data-goegress>Review the blocked hosts</button>');
    if (it.kind === 'question') {
      return card('question', '<span class="badge">question</span> ' + esc(it.q), '',
        '<input type="text" class="qin" data-qn="' + it.n + '" placeholder="your answer - the planner follows it from now on" aria-label="your answer"><button class="primary sm" data-qa="' + it.n +
        '">Answer</button><button class="mini" data-qd="' + it.n + '" title="the question does not matter">Dismiss</button>');
    }
    const t_task = it.task;
    const diag = t_task.diagnosis;
    const whyText = diag ? (diag.summary + (diag.suggested_fix ? '\n\n' + t('diag.suggested_fix', '💡 Suggested fix:') + ' ' + diag.suggested_fix : '')) : String(t_task.blocked_why || '');
    return card('blocked', '<span class="badge bad">' + esc(STATUS.blocked || 'blocked') + '</span> ' + esc(t_task.title) + ' ' + small(esc(t_task.id)), esc(whyText.slice(0, 360)),
      '<button class="primary sm" data-ta="retry" data-id="' + esc(t_task.id) + '">' + t('btn.retry', 'Retry') + '</button>' +
      '<button type="button" class="mini" data-copyreport="' + esc(t_task.id) + '" title="' + esc(t('btn.copy_report', '📋 Copy report for AI')) + '">' + t('btn.copy_report', '📋 Copy report for AI') + '</button>' +
      '<button class="mini" data-fbtoggle="' + esc(it.key) + '">' + t('btn.send_back_words', 'Send back with my words…') + '</button>' +
      open('task', t_task.id) + '<button class="mini ghost" data-ta="cancel" data-id="' + esc(t_task.id) + '">' + t('btn.cancel', 'Cancel') + '</button>',
      fbForm(it.key, 'What should the worker do differently?', 'Send back'));
  }).join('');
  return '<h3 class="hot">' + t('head.needs_you', 'Needs you') + ' (' + items.length + ')</h3><div class="inbox">' + html + '</div>';
}

// ---- the first steps of a new install (until done or dismissed)
function onboardingHtml() {
  if (store('af-onb-' + project) === 'off') return '';
  if (!F) return window._ferr ? '<div class="empty bad">Could not load onboarding: ' + esc(window._ferr) + '</div>' : '';
  const st = F.onboarding || [];
  if (!st.length) return '<div class="empty">No onboarding steps.</div>';
  if (st.every(s => s.done)) {
    return '<div class="onboard done"><div class="ob-head"><b>Get started</b> ' + small('all done ✓') +
      '<button class="linkbtn" data-onb-off aria-label="hide onboarding">hide</button></div></div>';
  }
  const n = st.filter(s => s.done).length;
  return '<div class="onboard"><div class="ob-head"><b>Get started</b> ' + small(n + ' of ' + st.length + ' done') +
    '<button class="linkbtn" data-onb-off aria-label="hide onboarding">hide</button></div><div class="ob-bar"><i style="width:' + (100 * n / st.length) + '%"></i></div><ol>' +
    st.map(s => '<li class="' + (s.done ? 'done' : '') + '">' + (s.done ? '✓ ' : '') + (s.done ? esc(s.title) : '<button class="linkbtn" data-onb="' + esc(s.key) + '">' + esc(s.title) + '</button>') + '</li>').join('') +
    '</ol></div>';
}

// ---- the team: the orchestrator and every worker - what it does now, or why it is idle
function teamHtml() {
  if (!F) return window._ferr ? '<div class="empty bad">Could not load team: ' + esc(window._ferr) + '</div>' : '<div class="empty">loading…</div>';
  const R = P.roster || {}, ts = F.tasks || [], live = F.live || {};
  const steps = (live.steps || []).map(s => {
    const [kind, id] = s.key.split(':');
    const t = ts.find(x => x.id === id), g = (F.goals || []).find(x => x.id === id);
    const what = s.phase || (kind === 'goal' ? 'planning' : kind === 'task' ? 'checking' : kind);
    const obj = t ? '<button class="linkbtn" data-open="task:' + esc(t.id) + '">' + esc(t.id) + '</button>' : g ? '<button class="linkbtn" data-open="goal:' + esc(g.id) + '">„' + esc(g.text.slice(0, 60)) + '“</button>' : '';
    return '<div class="step"><span class="dot think"></span>' + esc(what) + (obj ? ' — ' + obj : '') + (s.since ? ' <span class="muted">(' + ago(s.since) + ')</span>' : '') + '</div>';
  }).join('');
  const orch = '<div class="node orch"><div class="node-title"><span class="dot ' + (steps ? 'think' : '') + '"></span>Orchestrator</div>' +
    '<div class="node-sub">' + (F.claude_until ? 'Claude via Antigravity (substitute)' : 'Claude Code') + ' · plans, reviews, merges</div>' +
    (steps || '<div class="node-sub">idle — waiting for the workers</div>') +
    (live.unavailable ? '<div class="step warn">waiting: ' + esc(live.unavailable) + '</div>' : '') + '</div>';
  const names = new Set([...(F.pool || []), ...Object.keys(R).filter(k => k.startsWith('worker:')).map(k => k.slice(7))].filter(inPool));
  ts.forEach(t => t.worker && t.status === 'running' && inPool(t.worker) && names.add(t.worker));
  const rts = (F.api && F.api.runtimes) || {};
  const ws = [...names].sort((a, b) => Number(a.split('-')[1]) - Number(b.split('-')[1])).map(w => {
    const e = R['worker:' + w] || {}, t = ts.find(x => x.worker === w && x.status === 'running');
    const q = (e.quota || []);
    const fam = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
    // one entry per model family, its 5-hour window and its own week: the worker runs on the worker model's family, while the
    // account's other family (Claude/GPT, for the substitute orchestrator) has its own, unrelated budget
    const low = (rows, re) => { const v = rows.filter(x => re.test(x.label)); return v.length ? Math.min(...v.map(x => x.pct)) : null; };
    const parts = [...new Set(q.map(x => fam(x.model)))].map(f => {
      const rows = q.filter(x => fam(x.model) === f), h5 = low(rows, /five hour|5h/i), wk = low(rows, /week/i);
      const bits = [h5 == null ? '' : h5.toFixed(0) + '%', wk == null ? '' : 'week ' + wk.toFixed(0) + '%'].filter(Boolean);
      return bits.length ? f + ' ' + bits.join(' · ') : '';
    }).filter(Boolean);
    const quota = parts.length ? '<div class="node-quota">' + esc(parts.join(' · ')) + '</div>' : '';
    const idle = (live.idle || {})[w];
    const body = t ? '<button class="node-task linkbtn" data-open="task:' + esc(t.id) + '" style="text-align:left;color:var(--text);display:block;width:100%">' + roleTag(t.role) + ' ' + esc(t.title) + '</button><div class="node-sub">' + esc(t.id) +
        ' · attempt ' + ((t.attempts || 0) + 1) + '/' + (t.max_attempts || 4) + (t.dispatched ? ' · ' + ago(t.dispatched) : '') + (e.tool_calls ? ' · ' + e.tool_calls + ' tools' : '') + '</div>'
      : '<div class="node-sub idle-why">' + esc(tsloc(idle) || ((live.reserved || []).includes(w) ? 'lent to the orchestrator' : 'free')) + '</div>';
    const acc = ((window.S && window.S.workers || []).find(x => x.name === w) || {}).account || (e && e.account) || '';
    const accLine = acc ? '<div class="node-sub muted small" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="' + esc(acc) + '">' + esc(acc) + '</div>' : '';
    const rt = rts[w];
    return '<div class="node' + (t ? ' busy' : '') + '"><div class="node-title"><span class="dot ' + (t ? 'running' : '') + '"></span>' + esc(w) +
      (rt && rt !== 'agy' ? ' <span class="rt rt-' + esc(rt) + '">' + esc(RTNAME[rt] || rt) + '</span>' : '') + '</div>' + accLine + body + quota + '</div>';
  }).join('');
  return orch + (ws ? '<div class="branch"></div><div class="workers">' + ws + '</div>' : '<div class="empty">No workers yet — Settings → Workers → Add worker.</div>');
}

// ---- goals: open ones in full, finished ones folded
const OPEN_MENU = {id: null};
function goalCard(g, ts) {
  const col = {done: 'var(--ok)', gate: 'var(--accent)', review: 'var(--accent)', merging: 'var(--accent)', 'merge-ready': 'var(--accent)', running: 'var(--tool)',
    blocked: 'var(--bad)', approval: 'var(--warn)'};
  const mine = ts.filter(t => t.goal === g.id && t.status !== 'cancelled'), n = mine.length || 1, done = mine.filter(t => t.status === 'done').length;
  const seg = Object.keys(col).map(s => { const k = mine.filter(t => t.status === s).length; return k ? '<i style="width:' + (100 * k / n) + '%;background:' + col[s] + '"></i>' : ''; }).join('');
  const acc = g.acceptance || [], cr = g.criteria || [];
  const crit = acc.length ? '<div class="crit">' + acc.map((a, i) => {
    const c = cr[i] || cr.find(x => x.criterion && x.criterion.slice(0, 40) === String(a).slice(0, 40));
    const mark = !c ? '<span class="cm wait" title="not checked yet">○</span>' : c.met ? '<span class="cm ok">✓</span>' : '<span class="cm no">✗</span>';
    return '<div class="critrow">' + mark + '<span>' + esc(a) + (c && c.evidence ? '<span class="muted"> — ' + esc(c.evidence) + '</span>' : '') + '</span></div>';
  }).join('') + '</div>' : '';
  const metCount = acc.filter((a, i) => {
    const c = cr[i] || cr.find(x => x.criterion && x.criterion.slice(0, 40) === String(a).slice(0, 40));
    return c && c.met;
  }).length;
  const openG = !['done', 'cancelled'].includes(g.status);
  const menu = openG ? '<div class="menuwrap"><button class="iconbtn sm" data-gmenu="' + esc(g.id) + '" aria-label="goal actions">⋯</button>' + (OPEN_MENU.id === g.id ?
    '<div class="menu">' + (g.paused ? '<button data-ga="resume" data-id="' + esc(g.id) + '">Resume</button>' : '<button data-ga="pause" data-id="' + esc(g.id) + '">Pause</button>') +
    (g.priority ? '' : '<button data-ga="prioritize" data-id="' + esc(g.id) + '">Do this first</button>') +
    '<button data-open="goal:' + esc(g.id) + '">Open the goal page</button>' +
    '<button class="danger" data-ga="cancel" data-id="' + esc(g.id) + '">Cancel the goal…</button></div>' : '') + '</div>' : '';
  return '<div class="goal' + (g.paused ? ' paused' : '') + '"><div class="goal-head"><button class="goal-text linkbtn" data-open="goal:' + esc(g.id) + '" style="text-align:left">' + esc(g.text) + '</button>' + menu + '</div>' +
    '<div class="goal-meta">' + esc(GSTATUS[g.status] || g.status) + (g.paused ? ' · <b class="warn">paused</b>' : '') + (g.priority ? ' · <b>first</b>' : '') +
    (mine.length ? ' · tasks ' + done + '/' + mine.length : '') + (acc.length ? ' · criteria ' + metCount + '/' + acc.length : '') +
    (g.rounds > 1 ? ' · round ' + g.rounds : '') + (g.source && g.source !== 'owner' ? ' · ' + esc(g.source) : '') + '</div>' +
    (mine.length ? '<div class="bar">' + seg + '</div>' : '') + ((g.summary || g.notes) ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') + crit +
    '<div class="chips">' + mine.map(t => '<button class="chip st-' + esc(t.status) + '" data-open="task:' + esc(t.id) + '" style="border:0;font:inherit">' + esc(STATUS[t.status] || t.status) + ' · ' + esc(t.id) + '</button>').join('') + '</div></div>';
}
let openFin = false;
function goalsHtml() {
  if (!F) return window._ferr ? '<div class="empty bad">Could not load goals: ' + esc(window._ferr) + '</div>' : '<div class="empty">loading…</div>';
  const ts = F.tasks || [], gs = (F.goals || []).filter(g => g.status !== 'cancelled').slice().reverse();
  if (!gs.length) return '<div class="empty">No goals yet — write the first one above.</div>';
  const open = gs.filter(g => ['new', 'active', 'blocked', 'approval'].includes(g.status)).sort((a, b) => (b.priority || 0) - (a.priority || 0));
  const fin = gs.filter(g => !open.includes(g));
  return (open.map(g => goalCard(g, ts)).join('') || '<div class="empty">Nothing is running.</div>') +
    (fin.length ? '<details' + (openFin ? ' open' : '') + ' id="fin-goals"><summary class="muted">Finished goals (' + fin.length + ')</summary>' +
      fin.slice(0, 30).map(g => goalCard(g, ts)).join('') + '</details>' : '');
}
document.addEventListener('toggle', e => { if (e.target.id === 'fin-goals') openFin = e.target.open; }, true);

// ---- backlog: drag to reorder, click a goal to edit it, ✕ to drop it; the forecast says when it is all done at the recent pace
let BL_EDIT = null;
function backlogHead() {
  const f = F && F.forecast, bl = (F && F.backlog) || [];
  let s = 'Backlog' + (bl.length ? ' (' + bl.length + ')' : '');
  if (f && f.eta_days != null && f.remaining) s += ' <span class="muted norm">· all done in ≈ ' + (f.eta_days < 1 ? Math.round(f.eta_days * 24) + ' h' : f.eta_days + ' days') +
    ' at ' + f.per_day + ' tasks/day</span>';
  return s;
}
function backlogHtml() {
  if (!F) return window._ferr ? '<div class="empty bad">Could not load backlog: ' + esc(window._ferr) + '</div>' : '<div class="empty">loading…</div>';
  const bl = F.backlog || [];
  if (!bl.length) return '<div class="empty">Backlog is empty' + (F.autopilot ? ' — when a worker is free, the autopilot picks the next goal from the vision.' : ' — add goals using the form above.') + '</div>';
  return '<div class="bl" id="bl-list">' + bl.map((b, i) => BL_EDIT === i
    ? '<div class="blrow editing" data-i="' + i + '"><textarea id="bl-edit-text" aria-label="Edit backlog item">' + esc(b) + '</textarea><div class="btnrow"><button class="primary sm" data-bledit-save="' + i + '">Save</button>' +
      '<button class="mini" data-bledit-cancel>Cancel</button></div></div>'
    : '<div class="blrow" data-i="' + i + '"><span class="drag" title="drag to reorder" aria-label="drag">⋮⋮</span><span class="blnum">' + (i + 1) + '</span>' +
      '<button class="bltext linkbtn" data-bledit="' + i + '" title="click to edit" aria-label="edit: ' + esc(b) + '" style="text-align:left;color:var(--text);font:inherit">' + esc(b) + '</button><span class="blbtns">' +
      (i ? '<button class="iconbtn sm" data-bl="top" data-n="' + (i + 1) + '" title="to the top" aria-label="move to top">⤒</button>' : '') +
      '<button class="iconbtn sm" data-bl="drop" data-n="' + (i + 1) + '" title="remove" aria-label="remove">✕</button></span></div>').join('') + '</div>';
}
// pointer-based dragging: works with a mouse and on a phone
let DRAG = null;
document.addEventListener('pointerdown', e => {
  const h = e.target.closest('.drag'); if (!h) return;
  const row = h.closest('.blrow'), list = $('bl-list'); if (!row || !list) return;
  e.preventDefault();
  const rows = [...list.querySelectorAll('.blrow')], r = row.getBoundingClientRect();
  DRAG = {row, from: Number(row.dataset.i), to: Number(row.dataset.i), y0: e.clientY, h: r.height + 6, rows};
  row.classList.add('dragging'); h.setPointerCapture(e.pointerId);
});
document.addEventListener('pointermove', e => {
  if (!DRAG) return;
  const dy = e.clientY - DRAG.y0, n = DRAG.rows.length;
  const to = Math.max(0, Math.min(n - 1, DRAG.from + Math.round(dy / DRAG.h)));
  DRAG.to = to;
  DRAG.row.style.transform = 'translateY(' + dy + 'px)';
  DRAG.rows.forEach((r, i) => { if (r === DRAG.row) return;
    const shift = (DRAG.from < to && i > DRAG.from && i <= to) ? -DRAG.h : (DRAG.from > to && i < DRAG.from && i >= to) ? DRAG.h : 0;
    r.style.transform = shift ? 'translateY(' + shift + 'px)' : ''; });
});
document.addEventListener('pointerup', async () => {
  if (!DRAG) return;
  const {from, to, rows} = DRAG; DRAG = null;
  rows.forEach(r => { r.style.transform = ''; r.classList.remove('dragging'); });
  if (from === to) return;
  const bl = F.backlog.slice(), [x] = bl.splice(from, 1); bl.splice(to, 0, x); F.backlog = bl;     // show it at once
  delete lastHTML['backlog']; schedule(true);
  done(await post('/api/backlog', {action: 'move', n: String(from + 1), to: String(to + 1)}), 'Backlog reordered');
  refreshSoon();
});

// ---- controls: autopilot and foreman pause/resume switches
function killswitchReason(ks) {
  if (!ks) return '';
  if (typeof ks === 'object') return String(ks.reason || '').trim();
  if (typeof ks === 'string') return ks.trim();
  return '';
}

function controlsHtml(model) {
  const m = model || F || {};
  const apOn = Boolean(m.autopilot);
  const apAction = apOn ? 'autopilot_off' : 'autopilot_on';
  const apLabel = apOn ? t('ctl.autopilot_on', 'Autopilot: on') : t('ctl.autopilot_off', 'Autopilot: off');
  const apTip = apOn
    ? t('ctl.autopilot_tip_on', 'Autopilot on: when a worker would idle and the backlog is empty, the orchestrator proposes new goals from the vision.')
    : t('ctl.autopilot_tip_off', 'Autopilot off: when a worker would idle and the backlog is empty, the orchestrator does not propose new goals from the vision.');

  const isAlive = m.alive !== false;
  const isPaused = Boolean(m.paused || m.killswitch);
  const fmAction = isPaused ? 'resume' : 'pause';
  const fmLabel = !isAlive ? t('ctl.foreman_stopped', 'Foreman: not running') :
    (isPaused ? t('ctl.foreman_paused', 'Foreman: paused') : t('ctl.foreman_running', 'Foreman: running'));

  const ks = m.killswitch;
  const ksBlocked = isPaused && Boolean(ks);
  let fmTip = '';
  if (!isAlive) {
    fmTip = t('ctl.foreman_tip_stopped', 'Foreman background service is not running or is restarting.');
  } else if (ksBlocked) {
    const ksReason = killswitchReason(ks);
    fmTip = 'Resume is blocked while the kill switch is engaged' + (ksReason ? ': ' + ksReason : '');
  } else if (isPaused) {
    fmTip = t('ctl.foreman_tip_paused', 'Paused: no new tasks are dispatched; tasks already running continue to completion.');
  } else {
    fmTip = t('ctl.foreman_tip_running', 'Running: tasks are dispatched to workers as they become available.');
  }

  return '<div class="control-wrap" title="' + esc(apTip) + '">' +
    '<button id="autopilot-toggle" class="control-toggle mini" data-control="' + esc(apAction) + '" title="' + esc(apTip) + '">' + esc(apLabel) + '</button>' +
    '<span class="hint">' + esc(apTip) + '</span>' +
  '</div>' +
  '<div class="control-wrap" title="' + esc(fmTip) + '">' +
    '<button id="foreman-pause-toggle" class="control-toggle mini" data-control="' + esc(fmAction) + '" title="' + esc(fmTip) + '"' + (ksBlocked ? ' disabled' : '') + '>' + esc(fmLabel) + '</button>' +
    '<span class="hint">' + esc(fmTip) + '</span>' +
  '</div>';
}

function ensureControlsContainer() {
  const sec = $('t-overview');
  if (sec && !$('overview-controls')) {
    const ctl = document.createElement('div');
    ctl.id = 'overview-controls';
    ctl.className = 'overview-controls';
    sec.insertBefore(ctl, sec.firstChild);
  }
}
ensureControlsContainer();

function renderOverview(force) {
  ensureControlsContainer();
  setHTML('overview-controls', controlsHtml(), force);
  if (!busyIn('inbox')) setHTML('inbox', inboxHtml(), force);
  setHTML('onboarding', onboardingHtml(), force);
  setHTML('team', F ? teamHtml() : window._ferr ? '<div class="empty bad">Could not load team: ' + esc(window._ferr) + '</div>' : '<div class="empty">loading…</div>', force);
  setHTML('goals', goalsHtml(), force);
  setHTML('backlog-h', backlogHead(), force);
  if (!DRAG && !busyIn('backlog')) setHTML('backlog', backlogHtml(), force);
}

// ---- the owner's input
async function addGoal(mode) {
  const ta = $('goal-text'), msg = $('goal-msg'), text = ta.value.trim();
  if (!text) { ta.focus(); return; }
  msg.textContent = 'sending…';
  const r = await post('/api/goal', {text, mode});
  msg.textContent = r.ok ? {now: 'Queued — the planner splits it into tasks within half a minute.', backlog: 'Added to the backlog.',
    roadmap: 'The planner is preparing a roadmap — its goals will appear in the backlog.'}[mode] : 'Not done: ' + (r.message || r.error || '');
  if (r.ok) ta.value = '';
  refreshSoon();
}
$('goal-now').addEventListener('click', () => addGoal('now'));
$('goal-backlog').addEventListener('click', () => addGoal('backlog'));
$('goal-roadmap').addEventListener('click', () => addGoal('roadmap'));
$('goal-text').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) addGoal('now'); });

// control actions: data-control="autopilot_on|autopilot_off|pause|resume"
async function controlAction(action) {
  if (action === 'resume' && F && F.killswitch) return false;
  const okText = {
    autopilot_on: 'Autopilot turned on',
    autopilot_off: 'Autopilot turned off',
    pause: 'Foreman paused',
    resume: 'Foreman resumed'
  }[action];
  const r = await post('/api/control', {project, action});
  const ok = done(r, okText);
  refreshSoon();
  return ok;
}

// task / goal actions anywhere on the page: data-ta="approve|cancel|retry" data-id, data-ga="approve|pause|..." data-id
async function taskAction(id, action, extra) {
  if (action === 'cancel' && !confirm('Cancel task ' + id + '?' + ((F.tasks || []).find(t => t.id === id && t.status === 'running') ? ' Its run is stopped; the work stays on its branch.' : ''))) return false;
  const ok = done(await post('/api/task', Object.assign({id, action}, extra || {})));
  refreshSoon(); if (SHEET.kind === 'task' && SHEET.id === id) loadTaskDetail(id);
  return ok;
}
async function goalAction(id, action, text) {
  if (action === 'cancel' && !confirm('Cancel this goal and its open tasks? Running runs are stopped.')) return false;
  OPEN_MENU.id = null;
  const ok = done(await post('/api/goalctl', {id, action, text: text || ''}));
  refreshSoon(); if (SHEET.kind === 'goal' && SHEET.id === id) loadGoalDetail(id);
  return ok;
}
document.addEventListener('click', async e => {
  const ctl = e.target.closest('[data-control]');
  if (ctl) {
    if (ctl.disabled) return;
    ctl.disabled = true;
    await controlAction(ctl.dataset.control);
    ctl.disabled = false;
    return;
  }
  const ta = e.target.closest('[data-ta]'); if (ta) { ta.disabled = true; await taskAction(ta.dataset.id, ta.dataset.ta); ta.disabled = false; return; }
  const ga = e.target.closest('[data-ga]'); if (ga) { ga.disabled = true; await goalAction(ga.dataset.id, ga.dataset.ga); ga.disabled = false; return; }
  const op = e.target.closest('[data-open]');
  if (op) { const [kind, id] = op.dataset.open.split(/:(.*)/s); if (kind === 'task') openTask(id); else if (kind === 'goal') openGoal(id); else if (kind === 'run') openRun(id); return; }
  const gm = e.target.closest('[data-gmenu]');
  if (gm) { OPEN_MENU.id = OPEN_MENU.id === gm.dataset.gmenu ? null : gm.dataset.gmenu; schedule(true); return; }
  if (OPEN_MENU.id && !e.target.closest('.menu')) { OPEN_MENU.id = null; schedule(true); }
  const fbt = e.target.closest('[data-fbtoggle]');
  if (fbt) { const k = fbt.dataset.fbtoggle; FB_OPEN.has(k) ? FB_OPEN.delete(k) : FB_OPEN.add(k); for (const x in lastHTML) delete lastHTML[x]; render(true);
    const t = document.querySelector('[data-fbdraft="' + k + '"]'); if (t) t.focus(); return; }
  const fbs = e.target.closest('[data-fbsend]');
  if (fbs) {
    const k = fbs.dataset.fbsend, text = (FB_DRAFT[k] || '').trim(), [kind, id] = k.split(/:(.*)/s);
    if (!text) { const t = document.querySelector('[data-fbdraft="' + k + '"]'); if (t) t.focus(); return; }
    const ok = kind === 'goal' ? await goalAction(id, 'replan', text) : await taskAction(id, 'feedback', {text});
    if (ok) { FB_OPEN.delete(k); delete FB_DRAFT[k]; document.activeElement && document.activeElement.blur(); schedule(true); }
    return;
  }
  const b = e.target.closest('[data-bl]');
  if (b) { if (b.dataset.bl === 'drop' && !confirm('Remove this goal from the backlog?')) return;
    done(await post('/api/backlog', {action: b.dataset.bl, n: b.dataset.n}), b.dataset.bl === 'top' ? 'Moved to the top' : 'Removed'); refreshSoon(); return; }
  const be = e.target.closest('[data-bledit]');
  if (be) { BL_EDIT = Number(be.dataset.bledit); delete lastHTML['backlog']; schedule(true); setTimeout(() => { const t = $('bl-edit-text'); if (t) t.focus(); }, 30); return; }
  if (e.target.closest('[data-bledit-cancel]')) { BL_EDIT = null; delete lastHTML['backlog']; schedule(true); return; }
  const bs = e.target.closest('[data-bledit-save]');
  if (bs) { const text = ($('bl-edit-text') || {}).value || '';
    if (done(await post('/api/backlog', {action: 'edit', n: String(Number(bs.dataset.bleditSave) + 1), text}), 'Saved')) { BL_EDIT = null; delete lastHTML['backlog']; refreshSoon(); }
    return; }
  const qa = e.target.closest('[data-qa]'), qd = e.target.closest('[data-qd]');
  if (qa || qd) {
    const n = (qa || qd).dataset[qa ? 'qa' : 'qd'], inp = document.querySelector('.qin[data-qn="' + n + '"]');
    if (qa && !inp.value.trim()) { inp.focus(); return; }
    done(await post('/api/answer', {n, answer: qa ? inp.value.trim() : ''}), qa ? 'Answered - the planner follows it' : 'Dismissed');
    document.activeElement && document.activeElement.blur(); refreshSoon(); return;
  }
  if (e.target.closest('[data-goegress]')) { SUB.system = 'egress'; setTab('system'); return; }
  if (e.target.closest('[data-onb-off]')) { store('af-onb-' + project, 'off'); schedule(true); return; }
  const ob = e.target.closest('[data-onb]');
  if (ob) { const k = ob.dataset.onb;
    if (k === 'forge') { if (!S) await loadSettings(true); openSetup(); }
    else if (k === 'workers') { SUB.settings = 'workers'; setTab('settings'); }
    else if (k === 'notify') { SUB.settings = 'project'; setTab('settings'); }
    else if (k === 'goal') { setTab('overview'); $('goal-text').focus(); }
    return; }
});
document.addEventListener('input', e => { const d = e.target.closest('[data-fbdraft]'); if (d) FB_DRAFT[d.dataset.fbdraft] = d.value; });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (OPEN_MENU.id) { OPEN_MENU.id = null; schedule(true); return; }
    if (BL_EDIT !== null) { BL_EDIT = null; delete lastHTML['backlog']; schedule(true); return; }
  }
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[role="button"]')) {
    e.preventDefault(); e.target.click(); return;
  }
  if (e.target.id === 'bl-edit-text' && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    const btn = document.querySelector('[data-bledit-save]');
    if (btn) btn.click();
  }
});

// ---- ask the team (one orchestrator call, answered from the code)
async function askTeam() {
  const q = $('ask-q').value.trim(), btn = $('ask-btn'), out = $('ask-a');
  if (!q) return;
  btn.disabled = true; btn.textContent = 'The orchestrator is thinking… (1–2 min)';
  let r;
  try { r = await (await fetch('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q, project})})).json(); }
  catch (e) { r = {answer: '(connection error: ' + e + ')'}; }
  btn.disabled = false; btn.textContent = 'Ask';
  out.innerHTML = '<div class="answer"><b>' + esc(q) + '</b>\n\n' + esc(r.answer || r.error || '(no answer)') + '</div>' + out.innerHTML;
}
$('ask-btn').addEventListener('click', askTeam);
$('ask-q').addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) askTeam(); });
projectResets.push(() => { FB_OPEN.clear(); BL_EDIT = null; OPEN_MENU.id = null; });
