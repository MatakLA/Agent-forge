// Overview tab: new goals, the planner's questions, the team, goals and the backlog; the header pills and stats.
document.getElementById('ask-btn').addEventListener('click', askTeam);

// ---- the owner's input: new goals, backlog order, answers to the planner's questions
const post = async (url, body) => { try { return await (await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(Object.assign({project}, body))})).json(); } catch (e) { return {ok: false, message: String(e)}; } };
async function addGoal(mode) {
  const ta = document.getElementById('goal-text'), msg = document.getElementById('goal-msg'), text = ta.value.trim();
  if (!text) { ta.focus(); return; }
  msg.textContent = 'sending…';
  const r = await post('/api/goal', {text, mode});
  msg.textContent = r.ok ? {now: 'Goal queued — the planner splits it into tasks within half a minute.', backlog: 'Added to the backlog.',
    roadmap: 'The planner is preparing a roadmap — its goals will appear in the backlog.'}[mode] : 'Nepodarilo sa: ' + (r.message || r.error || '');
  if (r.ok) ta.value = '';
  setTimeout(poll, 1500);
}
document.getElementById('goal-now').addEventListener('click', () => addGoal('now'));
document.getElementById('goal-backlog').addEventListener('click', () => addGoal('backlog'));
document.getElementById('goal-roadmap').addEventListener('click', () => addGoal('roadmap'));
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-bl]');
  if (b) { if (b.dataset.bl === 'drop' && !confirm('Remove this goal from the backlog?')) return;
    await post('/api/backlog', {action: b.dataset.bl, n: b.dataset.n, to: b.dataset.to || ''}); setTimeout(poll, 800); return; }
  const qa = e.target.closest('[data-qa]'), qd = e.target.closest('[data-qd]');
  if (qa || qd) {
    const n = (qa || qd).dataset[qa ? 'qa' : 'qd'], inp = document.querySelector('.qin[data-qn="' + n + '"]');
    if (qa && !inp.value.trim()) { inp.focus(); return; }
    await post('/api/answer', {n, answer: qa ? inp.value.trim() : ''});
    document.activeElement && document.activeElement.blur(); setTimeout(poll, 800);
  }
});

function pills() {
  if (!F) return '<span class="pill bad">foreman: ' + esc(window._ferr || 'loading') + '</span>';
  const p = [];
  const staleFor = foremanFetchedAt ? (Date.now()/1000 - foremanFetchedAt) : 0;
  if (staleFor > 30) {
    p.push('<span class="pill bad">the dashboard has no fresh data (' + Math.round(staleFor/60) + ' min old) - do not trust the state below' +
           (window._ferr ? ' · cause: ' + esc(window._ferr) : '') + '</span>');
    return p.join(' ');
  }
  if (!F.alive) p.push('<span class="pill bad"><span class="dot bad"></span>foreman not running</span>');
  else if (F.killswitch) {
    p.push('<span class="pill bad">kill switch ON — ' + esc(F.killswitch.reason || '') + '</span>');
    p.push((F.killswitch.scope === 'all' ? '<span class="pill bad">all projects</span>' : '') +
           (F.killswitch.hard ? '<span class="pill bad">hard — workers stopped</span>' : '') +
           '<button class="resume-btn" data-act="resume" id="resume-btn">Continue</button>');
  }
  else if (F.paused) p.push('<span class="pill warn">foreman paused</span>');
  else p.push('<span class="pill ok"><span class="dot running"></span>foreman running</span>');
  if (F.claude_until) p.push('<span class="pill warn">orchestrator: Claude via Antigravity (Claude Code back ' + tfmt(F.claude_until) + ')</span>');
  else p.push('<span class="pill ok">orchestrator: Claude Code</span>');
  const agyDown = Object.keys(F.agy_cooldown || {}).length;
  if (agyDown) p.push('<span class="pill warn">' + agyDown + ' worker(s) temporarily without a substitute orchestrator</span>');
  p.push('<span class="pill">autopilot ' + (F.autopilot ? 'on' : 'off') + '</span>');
  if (F.forge && (F.forge.kind === 'github' || F.forge.kind === 'gitlab')) p.push('<span class="pill">' + (F.forge.kind === 'github' ? 'GitHub' : 'GitLab') + ': ' + esc(F.forge.repo) + '</span>');
  if (F.api && (F.api.workers || []).length) p.push('<span class="pill' + (F.api.spent_today >= F.api.budget ? ' bad' : '') + '">API today $' + Number(F.api.spent_today).toFixed(2) + ' / $' + Number(F.api.budget).toFixed(0) + '</span>');
  if (F.old) p.push('<span class="pill warn">old foreman version — fewer details</span>');
  return p.join(' ');
}

function stats() {
  if (!F) return '';
  const t = F.today || {}, l = F.llm_today || {}, ts = F.tasks || [];
  const n = s => ts.filter(x => x.status === s).length;
  const box = (v, lab) => '<div class="stat"><b>' + v + '</b><span>' + lab + '</span></div>';
  const blocked = n('blocked');
  return box(n('running'), 'running') + box(n('pending'), 'waiting') + box(n('gate') + n('review'), 'checking') +
         box(t.merged != null ? t.merged : n('done'), 'done today') +
         (blocked ? box('<span style="color:var(--bad)">' + blocked + '</span>', 'blocked') : '') +
         (t.tokens ? box((t.tokens/1e6).toFixed(1) + 'M', 'tokens today') : '') +
         (l.cost_usd ? box('$' + l.cost_usd.toFixed(2), 'orchestrator today') : '');
}

function team() {
  const R = P.roster || {}, ts = (F && F.tasks) || [], live = (F && F.live) || {};
  const steps = (live.steps || []).map(s => {
    const [kind, id] = s.key.split(':');
    const t = ts.find(x => x.id === id), g = F && (F.goals || []).find(x => x.id === id);
    const what = s.phase || (kind === 'goal' ? 'planning' : kind === 'task' ? 'checking' : kind);
    const obj = t ? esc(t.id) : g ? '„' + esc(g.text.slice(0, 60)) + '“' : '';
    return '<div class="step"><span class="dot think"></span>' + esc(what) + (obj ? ' — ' + obj : '') + ' <span class="muted">(' + ago(s.since) + ')</span></div>';
  }).join('');
  let orch = '<div class="node orch"><div class="node-title"><span class="dot ' + (steps ? 'think' : '') + '"></span>Orchestrator (lead)</div>' +
    '<div class="node-sub">' + (F && F.claude_until ? 'Claude via Antigravity (substitute)' : 'Claude Code') + ' · plans, reviews, merges</div>' +
    (steps || '<div class="node-sub">idle — waiting for the workers</div>') +
    (live.unavailable ? '<div class="step" style="color:var(--warn)">waiting: ' + esc(live.unavailable) + '</div>' : '') + '</div>';
  const names = new Set(Object.keys(R).filter(k => k.startsWith('worker:')).map(k => k.slice(7)).filter(inPool));
  ts.forEach(t => t.worker && inPool(t.worker) && names.add(t.worker));
  const ws = [...names].sort().map(w => {
    const e = R['worker:' + w] || {}, t = ts.find(x => x.worker === w && x.status === 'running');
    const cool = live.cooldown && live.cooldown[w];
    const state = t ? 'running' : '';
    const q5h = (e.quota || []).filter(q => /five hour/i.test(q.label));
    const shortModel = m => /claude/i.test(m) ? 'Claude' : /gemini/i.test(m) ? 'Gemini' : m;
    const quota = q5h.length ? '<div class="node-quota">' + q5h.map(q => shortModel(q.model) + ' ' + q.pct.toFixed(0) + '%').join(' · ') + '</div>' : '';
    const task = t ? '<div class="node-task" data-task="' + esc(t.id) + '" style="cursor:pointer"><span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> ' +
      esc(t.title) + '</div><div class="node-sub">' + esc(t.id) + ' · attempt ' + (t.attempts + 1) + '/' + (t.max_attempts || 4) + ' · running ' + ago(t.dispatched) +
      (e.tool_calls ? ' · ' + e.tool_calls + ' tools' : '') + '</div>'
      : '<div class="node-sub">' + (cool ? 'pauza do ' + tfmt(cool.until) + ': ' + esc(cool.reason || 'unknown reason') : (live.reserved || []).includes(w) ? 'lent to the orchestrator (Claude via Antigravity)' : 'free') + '</div>';
    const rt = (F && F.api && (F.api.workers || []).includes(w)) ? ((ts.find(x => x.worker === w && x.runtime) || {}).runtime || 'API') : '';
    return '<div class="node"><div class="node-title"><span class="dot ' + state + '"></span>' + esc(w) + (rt ? ' <span class="rt rt-' + esc(rt) + '">' + esc(RTNAME[rt] || rt) + '</span>' : '') +
      '</div>' + task + quota + '</div>';
  }).join('');
  return orch + (ws ? '<div class="branch"></div><div class="workers">' + ws + '</div>' : '');
}

function goalCard(g, ts) {
  const col = {done:'var(--ok)', gate:'var(--accent)', review:'var(--accent)', running:'var(--tool)', blocked:'var(--bad)'};
  const mine = ts.filter(t => t.goal === g.id && t.status !== 'cancelled'), n = mine.length || 1;
  const seg = ['done','gate','review','running','blocked'].map(s => { const k = mine.filter(t => t.status === s).length;
    return k ? '<i style="width:' + (100*k/n) + '%;background:' + col[s] + '"></i>' : ''; }).join('');
  const done = mine.filter(t => t.status === 'done').length;
  const lines = mine.reduce((a, t) => a + (t.status === 'done' && t.diff ? t.diff.added + t.diff.removed : 0), 0);
  const st = {new:'planning', active:'running', done:'done', blocked:'blocked'}[g.status] || g.status;
  // acceptance criteria of the whole goal, checked by the orchestrator when all its tasks finished (B4)
  const acc = g.acceptance || [], cr = g.criteria || [];
  const crit = acc.length ? '<div class="crit">' + acc.map((a, i) => {
      const c = cr[i] || cr.find(x => x.criterion && x.criterion.slice(0, 40) === String(a).slice(0, 40));
      const mark = !c ? '<span class="cm wait" title="not checked yet">○</span>' : c.met ? '<span class="cm ok">✓</span>' : '<span class="cm no">✗</span>';
      return '<div class="critrow">' + mark + '<span>' + esc(a) + (c && c.evidence ? '<span class="muted"> — ' + esc(c.evidence) + '</span>' : '') + '</span></div>';
    }).join('') + (g.criteria_at ? '<div class="muted" style="font-size:11.5px">checked: ' + esc(dfmt(g.criteria_at)) + '</div>' : '') + '</div>' : '';
  const metCount = cr.filter(c => c.met).length;
  return '<div class="goal"><div class="goal-head"><div class="goal-text">' + esc(g.text) + '</div><div class="goal-meta">' + esc(st) +
    (mine.length ? ' · tasks ' + done + '/' + mine.length + ' (' + Math.round(100 * done / n) + ' %)' : '') +
    (acc.length ? ' · criteria ' + metCount + '/' + acc.length : '') + (g.rounds > 1 ? ' · round ' + g.rounds : '') +
    (lines ? ' · ' + lines + ' lines' : '') + (g.source && g.source !== 'owner' ? ' · ' + esc(g.source) : '') + '</div></div>' +
    '<div class="bar">' + seg + '</div>' + ((g.summary || g.notes) ? '<div class="goal-sum">' + esc(g.summary || g.notes) + '</div>' : '') + crit +
    '<div class="chips">' + mine.map(t => '<span class="chip" data-task="' + esc(t.id) + '">' + esc(STATUS[t.status] || t.status) + ' · ' + esc(t.id) +
      (t.diff ? ' · +' + t.diff.added + '/-' + t.diff.removed : '') + '</span>').join('') + '</div>' +
    (mine.length > 1 ? '<button class="linkbtn" data-dag="' + esc(g.id) + '">' + (openDags.has(g.id) ? 'hide dependency graph' : 'dependency graph') + '</button>' +
      (openDags.has(g.id) ? dagSvg(mine, ts) : '') : '') + '</div>';
}
function goalsHtml() {
  if (!F) return '';
  const ts = F.tasks || [];
  const gs = (F.goals || []).filter(g => g.status !== 'cancelled').slice().reverse();
  if (!gs.length) return '<div class="empty">No goals yet — enter the first one above under “New goal”.</div>';
  const open = gs.filter(g => g.status === 'new' || g.status === 'active' || g.status === 'blocked'), fin = gs.filter(g => !open.includes(g));
  return (open.map(g => goalCard(g, ts)).join('') || '<div class="empty">Nothing is running.</div>') +
    (fin.length ? '<details' + (openFin ? ' open' : '') + ' id="fin-goals"><summary class="muted">Finished goals (' + fin.length + ')</summary>' +
      fin.slice(0, 30).map(g => goalCard(g, ts)).join('') + '</details>' : '');
}
let openFin = false;
document.addEventListener('toggle', e => { if (e.target.id === 'fin-goals') openFin = e.target.open; }, true);

// ---- 10. ask the team
async function askTeam() {
  const q = document.getElementById('ask-q').value.trim(), btn = document.getElementById('ask-btn'), out = document.getElementById('ask-a');
  if (!q) return;
  btn.disabled = true; btn.textContent = 'The orchestrator is thinking… (1–2 min)';
  let r;
  try { r = await (await fetch('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question: q, project})})).json(); }
  catch (e) { r = {answer: '(connection error: ' + e + ')'}; }
  btn.disabled = false; btn.textContent = 'Ask';
  out.innerHTML = '<div class="answer"><b>' + esc(q) + '</b>\n\n' + esc(r.answer || r.error || '(no answer)') + '</div>' + out.innerHTML;
}
