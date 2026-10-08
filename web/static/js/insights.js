// Insights: Team (what gets done and how well, trends by week), Money & quota (spend, caps, forecasts), Experiments (A/B
// results judged on 95 % intervals, with a button to adopt the winner).

const box = (v, lab, cls) => '<div class="stat' + (cls ? ' ' + cls : '') + '"><b>' + (v == null ? '–' : v) + '</b><span>' + lab + '</span></div>';
function sparkBars(vals, fmt, hi) {
  const mx = Math.max(1e-9, ...vals.map(v => v.v || 0));
  return '<div class="bars">' + vals.map(v => '<div title="' + esc(v.title || '') + '"><span>' + (v.v == null ? '' : fmt(v.v)) + '</span><b style="height:' +
    (100 * (v.v || 0) / mx) + '%' + (hi ? ';background:' + hi : '') + '"></b><span>' + esc(v.label) + '</span></div>').join('') + '</div>';
}

// ---- Team
function teamInsights() {
  const M = F.metrics;
  if (!M) return '<div class="empty">Metrics need a newer foreman.</div>';
  const T = M.totals || {};
  let h = '<div class="stats">' + box(T.done, 'tasks done') + box(T.first_pass_pct == null ? null : T.first_pass_pct + ' %', 'approved first time') +
    box(T.reworks, 'sent back') + box(T.blocked, 'blocked') + box(T.tokens_per_done ? fmtTok(T.tokens_per_done) : null, 'tokens per done task') + '</div>';
  h += '<h3>Tasks done per day</h3>' + sparkBars((M.merged_per_day || []).map(([d, n]) => ({v: n, label: d.slice(5)})), v => v);
  const W = M.weekly || [];
  if (W.length) {
    h += '<h3>Trend by week</h3><div class="tablewrap"><table class="m"><tr><th>week</th><th class="num">done</th><th class="num">approved first time</th>' +
      '<th class="num">tokens / done</th><th class="num">$ / done</th><th class="num">quota / done</th></tr>' +
      W.map((w, i) => { const p = W[i - 1], arrow = (a, b, goodUp) => a == null || b == null || a === b ? '' : ((a > b) === goodUp ? ' <span class="plus">▲</span>' : ' <span class="minus">▼</span>');
        return '<tr><td>' + esc(w.week) + '</td><td class="num">' + w.done + '</td><td class="num">' + w.first_pass_pct + ' %' + arrow(w.first_pass_pct, p && p.first_pass_pct, true) +
          '</td><td class="num">' + fmtTok(w.tokens_per_done) + arrow(w.tokens_per_done, p && p.tokens_per_done, false) + '</td><td class="num">' + usd(w.cost_per_done) +
          arrow(w.cost_per_done, p && p.cost_per_done, false) + '</td><td class="num">' + (w.week_per_done == null ? '–' : w.week_per_done + ' %') + '</td></tr>'; }).join('') + '</table></div>';
  }
  const roles = Object.entries(M.roles || {}).sort((a, b) => b[1].tasks - a[1].tasks);
  h += '<h3>By role</h3><div class="tablewrap"><table class="m"><tr><th>role</th><th class="num">tasks</th><th class="num">done</th><th class="num">1st time</th><th class="num">sent back</th>' +
    '<th class="num">blocked</th><th class="num">tokens/task</th><th class="num">lines</th></tr>' + roles.map(([r, v]) => '<tr><td>' + esc(r) + '</td><td class="num">' + v.tasks +
      '</td><td class="num">' + v.done + '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') + '</td><td class="num">' + v.reworks + '</td><td class="num">' +
      v.blocked + '</td><td class="num">' + fmtTok(v.tokens_per_done) + '</td><td class="num">' + v.lines_done + '</td></tr>').join('') + '</table></div>';
  const prov = Object.entries(M.providers || {}).sort((a, b) => b[1].calls - a[1].calls);
  if (prov.length) h += '<h3>Orchestrator, last 7 days</h3><div class="tablewrap"><table class="m"><tr><th>provider</th><th class="num">calls</th><th class="num">failed</th>' +
    '<th class="num">average</th></tr>' + prov.map(([p, v]) => '<tr><td>' + esc(p) + '</td><td class="num">' + v.calls + '</td><td class="num">' + v.failed + '</td><td class="num">' +
      (v.calls ? Math.round(v.secs / v.calls) + ' s' : '–') + '</td></tr>').join('') + '</table></div>';
  const MT = F.maintenance;
  if (MT) h += '<h3>Maintenance of main (no AI)</h3><div class="muted small">' + esc(dfmt(MT.at)) + ' · ' + esc(MT.sha || '') + ' · tests: ' +
    (MT.suite || []).map(r => (r.rc === 0 ? '✓ ' : '✗ ') + esc(r.summary)).join(' / ') + (MT.nodoc ? ' · modules without a docstring: ' + MT.nodoc : '') + '</div>';
  return h;
}

// ---- Money & quota
const MONEY = {data: null, at: 0, loading: false, err: null};
async function loadMoney(force) {
  if (MONEY.loading || (!force && Date.now() - MONEY.at < 60000)) return;
  MONEY.loading = true;
  try { const d = await getJSON('/api/money'); if (d.error) MONEY.err = d.error; else { MONEY.data = d; MONEY.err = null; } } catch (e) { MONEY.err = String(e); }
  MONEY.loading = false; MONEY.at = Date.now(); schedule(true);
}
const RTCOL = {claude: '#d97757', codex: 'var(--ok)', gemini: 'var(--accent)', compat: '#9b7be0', aider: '#5bc0be', agy: 'var(--idle)'};
function capBar(label, spent, cap) {
  if (!cap) return '';
  const p = Math.min(100, 100 * spent / cap), cls = p >= 100 ? 'bad' : p >= 80 ? 'warn' : '';
  return '<div class="cap"><div class="cap-l">' + esc(label) + '<span>' + usd(spent) + ' of ' + usd(cap) + '</span></div><div class="cap-b ' + cls + '"><i style="width:' + p + '%"></i></div></div>';
}
function quotaHtml(m) {
  const R = P.roster || {}, rows = [];
  for (const [k, e] of Object.entries(R)) {
    if (!k.startsWith('worker:') || !(e.quota || []).length || !inPool(k.slice(7))) continue;
    const w = k.slice(7), q = e.quota, fam = x => /claude/i.test(x.model) ? 'Claude' : /gemini/i.test(x.model) ? 'Gemini' : x.model;
    const wk = q.filter(x => /week/i.test(x.label)), five = q.filter(x => /five hour|5h/i.test(x.label));
    const burn = (m && m.quota_burn || {})[w], left = wk.length ? Math.min(...wk.map(x => x.pct)) : null;
    const lasts = burn && left != null ? left / burn : null;
    rows.push('<tr><td>' + esc(w) + '</td><td>' + five.map(x => fam(x) + ' ' + x.pct.toFixed(0) + '%' + (x.reset ? ' <span class="muted">(' + esc(String(x.reset).replace(/^resets?\s*/i, '')) + ')</span>' : '')).join('<br>') +
      '</td><td>' + wk.map(x => fam(x) + ' ' + x.pct.toFixed(0) + '%' + (x.reset ? ' <span class="muted">(' + esc(String(x.reset).replace(/^resets?\s*/i, '')) + ')</span>' : '')).join('<br>') +
      '</td><td class="num">' + (burn ? burn.toFixed(1) + ' %/day' : '–') + '</td><td>' + (lasts == null ? '–' : lasts > 14 ? 'more than 2 weeks' : '≈ ' + lasts.toFixed(1) + ' days') + '</td></tr>');
  }
  if (!rows.length) return '';
  return '<h3>Antigravity quota</h3><div class="tablewrap"><table class="m"><tr><th>worker</th><th>5-hour window</th><th>week</th><th class="num">used per day</th><th>lasts at this pace</th></tr>' +
    rows.join('') + '</table></div><div class="muted small">The weekly window resets on its own schedule (shown); when every worker is below its floor, the team plans ahead and waits.</div>';
}
function moneyInsights() {
  loadMoney();
  const m = MONEY.data;
  if (!m) return '<div class="empty">' + esc(MONEY.err || 'loading…') + '</div>' + quotaHtml(null);
  const c = m.caps || {};
  let h = '<div class="stats">' + box(usd(m.today), 'API today') + box(usd(m.week), 'last 7 days') + box(usd(m.month), 'this month') +
    box(m.rate_per_hour ? usd(m.rate_per_hour) + '/h' : '–', 'pace (last 3 h)') + '</div>';
  if (m.runs_out_at) h += '<div class="banner warn">At this pace today\'s API budget runs out at <b>' + esc(tfmt(m.runs_out_at)) + '</b>. API workers then wait until tomorrow (UTC) - raise API_DAILY_BUDGET_USD in Settings → Money if that is too early.</div>';
  h += '<h3>Budgets</h3><div class="caps">' + capBar('Today, all projects', m.today, c.day) + capBar('This month, all projects', m.month, c.month) +
    capBar('Today, project ' + project, m.project_today, c.project_day) + capBar('This month, project ' + project, m.project_month, c.project_month) +
    Object.entries(c.workers || {}).map(([w, cap]) => capBar('Today, ' + w, (m.workers_today || {})[w] || 0, cap)).join('') + '</div>' +
    (!c.month && !c.project_day && !Object.keys(c.workers || {}).length ? '<div class="muted small">Only the daily budget is set. Monthly, per-project and per-worker caps: Settings → Money.</div>' : '');
  const days = m.per_day || [], rts = [...new Set(days.flatMap(d => Object.keys(d).filter(k => k !== 'date')))];
  if (rts.length) {
    const mx = Math.max(1e-9, ...days.map(d => rts.reduce((a, r) => a + (d[r] || 0), 0)));
    h += '<h3>Spend per day</h3><div class="bars stacked">' + days.map(d => { const tot = rts.reduce((a, r) => a + (d[r] || 0), 0);
      return '<div title="' + esc(d.date + ': ' + rts.map(r => r + ' ' + usd(d[r] || 0)).join(', ')) + '"><span>' + (tot ? usd(tot) : '') + '</span><div class="stack" style="height:' + (100 * tot / mx) + '%">' +
        rts.map(r => d[r] ? '<b style="flex:' + d[r] + ';background:' + (RTCOL[r] || 'var(--accent)') + '"></b>' : '').join('') + '</div><span>' + esc(d.date.slice(5)) + '</span></div>'; }).join('') + '</div>' +
      '<div class="legend">' + rts.map(r => '<span><i style="background:' + (RTCOL[r] || 'var(--accent)') + '"></i>' + esc(RTNAME[r] || r) + '</span>').join('') + '</div>';
    const tbl = (title, obj, nameFn) => { const e = Object.entries(obj || {}).filter(x => x[1]).sort((a, b) => b[1] - a[1]); return e.length ? '<div><h4>' + title + '</h4><table class="m">' +
      e.map(([k, v]) => '<tr><td>' + esc(nameFn ? nameFn(k) : (k || 'default')) + '</td><td class="num">' + usd(v) + '</td></tr>').join('') + '</table></div>' : ''; };
    h += '<h3>This month</h3><div class="cols3">' + tbl('By runtime', m.by_runtime, k => RTNAME[k] || k) + tbl('By project', m.by_project) + tbl('By model (this project)', m.by_model) + '</div>';
    if ((m.top_tasks || []).length) h += '<h3>Most expensive tasks this month</h3><div class="tablewrap"><table class="m click">' + m.top_tasks.map(t => '<tr data-open="task:' + esc(t.task) +
      '"><td>' + esc(t.task) + '</td><td class="num">' + usd(t.usd) + '</td></tr>').join('') + '</table></div>';
  } else h += '<div class="muted small" style="margin-top:10px">No API spend in the last 14 days' + ((F.api && (F.api.workers || []).length) ? '.' : ' - no API workers in this project.') + '</div>';
  return h + quotaHtml(m);
}

// ---- Experiments
function ciTxt(ci, unit) {
  if (!ci) return '–';
  const f = v => unit === '$' ? usd(v) : Number(v).toFixed(2) + unit;
  return f(ci[0]) + ' <span class="muted">(' + f(Math.max(0, ci[1])) + '–' + f(ci[2]) + ')</span>';
}
function experimentsInsights() {
  const E = (F.metrics || {}).efficiency || {}, V = E.verdicts || [];
  let h = '';
  if (V.length) h += V.map(v => {
    const adopt = v.winner ? (v.kind === 'model' ? '<button class="primary sm" data-adopt="model" data-v="' + esc(v.winner) + '">Make ' + esc(v.winner) + ' the worker model</button>'
      : '<button class="primary sm" data-adopt="pack" data-v="' + (v.winner === 'pack' ? 'on' : 'off') + '">' + (v.winner === 'pack' ? 'Keep the context pack on for every task' : 'Turn the context pack off') + '</button>') : '';
    return '<div class="verdict' + (v.winner ? ' win' : '') + '"><div class="v-head">' + (v.kind === 'model' ? 'Worker model: ' + esc(v.a) + ' vs ' + esc(v.b) : 'Context pack: off vs on') + '</div>' +
      '<div>' + esc(v.text) + (v.need_more ? ' — about <b>' + v.need_more + '</b> more merged tasks each to tell.' : '') + '</div>' + (adopt ? '<div class="btnrow">' + adopt + '</div>' : '') + '</div>';
  }).join('');
  else h += '<div class="note">No experiment is running. Start one in Settings → Models: an alternative worker model for a share of the tasks (A/B), or ' +
    'the context pack for half of them. The verdict appears here once the 95 % intervals separate.</div>';
  if (F.routing === 'auto') h += '<div class="note">Model routing is on: for each role the cheapest worker model that has proven itself here is used (Settings → Models).</div>';
  const effTable = (title, rows) => rows.length ? '<h3>' + title + '</h3><div class="tablewrap"><table class="m"><tr><th></th><th class="num">runs</th><th class="num">done</th>' +
    '<th class="num">approved first time (95 %)</th><th class="num">quota / done</th><th class="num">$ / done</th><th class="num">token cap</th><th class="num">tools/run</th></tr>' +
    rows.map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="num">' + v.runs + '</td><td class="num">' + v.done + '</td><td class="num">' +
      (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %' + (v.first_pass_ci && v.first_pass_ci[0] != null ? ' <span class="muted">(' + v.first_pass_ci[0] + '–' + v.first_pass_ci[1] + ')</span>' : '')) +
      '</td><td class="num">' + ciTxt(v.week_done_ci, ' %') + '</td><td class="num">' + ciTxt(v.cost_done_ci, '$') + '</td><td class="num">' + v.cap_pct + ' %</td><td class="num">' +
      (v.tools_median == null ? '–' : v.tools_median) + '</td></tr>').join('') + '</table></div>' : '';
  h += effTable('By worker model', Object.entries(E.model || {}).sort((a, b) => b[1].runs - a[1].runs));
  h += effTable('Context pack', Object.entries(E.pack || {}).sort((a, b) => b[1].runs - a[1].runs));
  const SC = E.self_check;
  if (SC && SC.runs) h += '<h3>Self-check in the run</h3><div class="stats">' + box(SC.runs, 'runs with a self-check') + box(SC.green_first, 'green at once') +
    box(SC.fixed, 'fixed in the same run') + box(SC.still_red, 'stayed red') + '</div>';
  return h + '<div class="muted small">Quota = % of one account\'s weekly limit, from agy /usage before and after each run. Intervals are 95 %; a verdict needs them to separate.</div>';
}
document.addEventListener('click', async e => {
  const a = e.target.closest('[data-adopt]'); if (!a) return;
  const changes = a.dataset.adopt === 'model' ? {WORKER_MODEL: a.dataset.v, AB_SHARE: '0'} : {CONTEXT_PACK: a.dataset.v};
  if (!confirm('Change the settings: ' + Object.entries(changes).map(([k, v]) => k + '=' + v).join(', ') + '?')) return;
  const r = await post('/api/settings', {changes});
  done(r.ok ? r : {ok: false, message: Object.values(r.errors || {}).join('; ')}, 'Saved - applies from the next round');
});

function renderInsights(force) {
  if (!F) return setHTML('insights', '<div class="empty">loading…</div>', force);
  setHTML('insights', SUB.insights === 'money' ? moneyInsights() : SUB.insights === 'experiments' ? experimentsInsights() : teamInsights(), force);
}
projectResets.push(() => { MONEY.data = null; MONEY.at = 0; });
