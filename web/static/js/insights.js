// Insights: Team (what gets done and how well, trends by week), Money & quota (spend, caps, forecasts), Experiments (A/B
// results judged on 95 % intervals, with a button to adopt the winner). All units (USD, tokens, % quota) are explicitly labelled.

const box = (v, lab, cls) => '<div class="stat' + (cls ? ' ' + cls : '') + '"><b>' + (v == null ? '–' : v) + '</b><span>' + lab + '</span></div>';
function sparkBars(vals, fmt, hi) {
  if (!vals || !vals.length) return '<div class="empty small">No data yet.</div>';
  const mx = Math.max(1e-9, ...vals.map(v => v.v || 0));
  return '<div class="bars">' + vals.map(v => '<div title="' + esc(v.title || (v.label + ': ' + fmt(v.v || 0))) + '"><span>' + (v.v == null ? '' : fmt(v.v)) + '</span><b style="height:' +
    (100 * (v.v || 0) / mx) + '%' + (hi ? ';background:' + hi : '') + '"></b><span>' + esc(v.label) + '</span></div>').join('') + '</div>';
}

// ---- Team
function teamInsights() {
  const M = F && F.metrics;
  if (!M) return '<div class="empty">No team metrics available yet.</div>';
  const T = M.totals || {};
  let h = '<div class="muted small" style="margin-bottom:8px">As of ' + (F && F.now ? dfmt(F.now) : ago(foremanFetchedAt) + ' ago') + '</div>';
  h += '<div class="stats">' + box(T.done != null ? T.done : 0, 'tasks done') + box(T.first_pass_pct == null ? '–' : T.first_pass_pct + ' %', 'approved first time') +
    box(T.reworks != null ? T.reworks : 0, 'sent back') + box(T.blocked != null ? T.blocked : 0, 'blocked') + box(T.tokens_per_done ? fmtTok(T.tokens_per_done) + ' tokens' : '–', 'tokens / done task') + '</div>';
  const days = M.merged_per_day || [];
  h += '<h3>Tasks done per day</h3>' + (days.length ? sparkBars(days.map(([d, n]) => ({v: n, label: d.slice(5), title: d + ': ' + n + ' task(s)'})), v => v) : '<div class="empty small">No completed tasks yet.</div>');
  const W = M.weekly || [];
  h += '<h3>Trend by week</h3>';
  if (W.length) {
    const arrow = (a, b, goodUp) => {
      if (a == null || b == null || a === b) return '';
      const up = a > b;
      const good = up === goodUp;
      const cls = good ? 'plus' : 'minus';
      const sym = up ? '▲' : '▼';
      const dir = up ? 'increased' : 'decreased';
      const evalTxt = good ? 'improved' : 'worsened';
      return ' <span class="' + cls + '" title="' + dir + ' from previous week (' + evalTxt + ')" aria-label="' + dir + ' (' + evalTxt + ')">' + sym + '</span>';
    };
    h += '<div class="tablewrap"><table class="m"><tr><th>week</th><th class="num">done</th><th class="num">approved 1st time (%)</th>' +
      '<th class="num">tokens / done</th><th class="num">cost / done (USD)</th><th class="num">quota / done (% of week)</th></tr>' +
      W.map((w, i) => { const p = W[i - 1];
        return '<tr><td>' + esc(w.week) + '</td><td class="num">' + w.done + '</td><td class="num">' + (w.first_pass_pct != null ? w.first_pass_pct + ' %' : '–') + arrow(w.first_pass_pct, p && p.first_pass_pct, true) +
          '</td><td class="num">' + (w.tokens_per_done ? fmtTok(w.tokens_per_done) + ' tokens' : '–') + arrow(w.tokens_per_done, p && p.tokens_per_done, false) +
          '</td><td class="num">' + (w.cost_per_done != null ? usd(w.cost_per_done) + ' USD' : '–') + arrow(w.cost_per_done, p && p.cost_per_done, false) +
          '</td><td class="num">' + (w.week_per_done == null ? '–' : w.week_per_done + ' % of week') + arrow(w.week_per_done, p && p.week_per_done, false) + '</td></tr>'; }).join('') + '</table></div>' +
      '<div class="muted small">Trend vs previous week: ▲ higher, ▼ lower (<span class="plus">green</span> = improved, <span class="minus">red</span> = worsened). Quota is % of one account\'s weekly limit.</div>';
  } else {
    h += '<div class="empty small">No weekly trend data recorded yet.</div>';
  }
  const roles = Object.entries(M.roles || {}).sort((a, b) => b[1].tasks - a[1].tasks);
  h += '<h3>By role</h3>';
  if (roles.length) {
    h += '<div class="tablewrap"><table class="m"><tr><th>role</th><th class="num">tasks</th><th class="num">done</th><th class="num">1st time (%)</th><th class="num">sent back</th>' +
      '<th class="num">blocked</th><th class="num">tokens / task</th><th class="num">lines</th></tr>' + roles.map(([r, v]) => '<tr><td>' + esc(r) + '</td><td class="num">' + v.tasks +
        '</td><td class="num">' + v.done + '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') + '</td><td class="num">' + v.reworks + '</td><td class="num">' +
        v.blocked + '</td><td class="num">' + (v.tokens_per_done ? fmtTok(v.tokens_per_done) + ' tokens' : '–') + '</td><td class="num">' + v.lines_done + '</td></tr>').join('') + '</table></div>';
  } else {
    h += '<div class="empty small">No role activity recorded yet.</div>';
  }
  const prov = Object.entries(M.providers || {}).sort((a, b) => b[1].calls - a[1].calls);
  h += '<h3>Orchestrator, last 7 days</h3>';
  if (prov.length) {
    h += '<div class="tablewrap"><table class="m"><tr><th>provider</th><th class="num">calls</th><th class="num">failed</th>' +
      '<th class="num">avg duration (s)</th></tr>' + prov.map(([p, v]) => '<tr><td>' + esc(p) + '</td><td class="num">' + v.calls + '</td><td class="num">' + v.failed + '</td><td class="num">' +
        (v.calls ? Math.round(v.secs / v.calls) + ' s' : '–') + '</td></tr>').join('') + '</table></div>';
  } else {
    h += '<div class="empty small">No orchestrator LLM calls recorded in the last 7 days.</div>';
  }
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
  return '<div class="cap"><div class="cap-l">' + esc(label) + '<span>' + usd(spent) + ' of ' + usd(cap) + ' USD (' + p.toFixed(0) + '%)</span></div><div class="cap-b ' + cls + '"><i style="width:' + p + '%"></i></div></div>';
}
function quotaHtml(m) {
  const R = P.roster || {}, rows = [];
  for (const [k, e] of Object.entries(R)) {
    if (!k.startsWith('worker:') || !(e.quota || []).length || !inPool(k.slice(7))) continue;
    const w = k.slice(7), q = e.quota, fam = x => /claude/i.test(x.model) ? 'Claude' : /gemini/i.test(x.model) ? 'Gemini' : x.model;
    const wk = q.filter(x => /week/i.test(x.label)), five = q.filter(x => /five hour|5h/i.test(x.label));
    const burn = (m && m.quota_burn || {})[w];
    // Match remaining weekly quota to the active worker model family (not mixing the account's other family e.g. Claude 0%)
    const wFam = five.length ? fam(five[0].model) : (wk.find(x => /gemini/i.test(x.model)) ? 'Gemini' : (wk[0] ? fam(wk[0].model) : null));
    const wWk = wk.find(x => fam(x.model) === wFam) || wk[0];
    const left = wWk ? wWk.pct : null;
    const lasts = burn && left != null ? left / burn : null;
    const acc = e.account || ((typeof S !== 'undefined' && S && S.workers || []).find(x => x.name === w) || {}).account || '';
    rows.push('<tr><td><b>' + esc(w) + '</b></td><td class="muted small">' + esc(acc || '–') + '</td><td>' + (five.length ? five.map(x => fam(x) + ' ' + x.pct.toFixed(0) + '% remaining' + (x.reset ? ' <span class="muted">(' + esc(tsloc(String(x.reset).replace(/^resets?\s*/i, ''))) + ')</span>' : '')).join('<br>') : '–') +
      '</td><td>' + (wk.length ? wk.map(x => fam(x) + ' ' + x.pct.toFixed(0) + '% remaining' + (x.reset ? ' <span class="muted">(' + esc(tsloc(String(x.reset).replace(/^resets?\s*/i, ''))) + ')</span>' : '')).join('<br>') : '–') +
      '</td><td class="num">' + (burn ? burn.toFixed(1) + ' %/day' : '–') + '</td><td>' + (lasts == null ? '–' : lasts > 14 ? '> 2 weeks (est.)' : '≈ ' + lasts.toFixed(1) + ' days (est.)') + '</td></tr>');
  }
  if (!rows.length) return '<h3>Antigravity quota</h3><div class="empty">No Antigravity subscription workers configured or reporting quota yet.</div>';
  return '<h3>Antigravity quota</h3><div class="tablewrap"><table class="m"><tr><th>worker</th><th>account</th><th>5-hour window (% remaining)</th><th>weekly quota (% remaining)</th><th class="num">burn pace (% of week / day)</th><th class="num">lasts at this pace (estimate)</th></tr>' +
    rows.join('') + '</table></div><div class="muted small">Quota = % of subscription limit remaining from agy /usage. Burn pace and duration are estimates for the active worker model; when every worker is below its floor, the team plans ahead and waits.</div>';
}
function moneyInsights() {
  loadMoney();
  const m = MONEY.data;
  if (!m) return '<div class="empty">' + esc(MONEY.err ? 'Failed to load money data: ' + MONEY.err : 'Loading money and budget data…') + '</div>' + quotaHtml(null);
  const c = m.caps || {};
  let h = '<div class="muted small" style="margin-bottom:8px">As of ' + (MONEY.at ? tfmt(MONEY.at) : ago(foremanFetchedAt) + ' ago') + '</div>';
  h += '<div class="stats">' + box(usd(m.today) + ' USD', 'API today') + box(usd(m.week) + ' USD', 'last 7 days') + box(usd(m.month) + ' USD', 'this month') +
    box(m.rate_per_hour ? usd(m.rate_per_hour) + ' USD/h' : '–', 'pace (last 3 h, estimated)') + '</div>';
  if (m.runs_out_at) h += '<div class="banner warn">At this pace today\'s API budget runs out at ~<b>' + esc(tfmt(m.runs_out_at)) + '</b> (estimated pace). API workers then wait until tomorrow (UTC) — raise API_DAILY_BUDGET_USD in Settings → Money if that is too early.</div>';
  h += '<h3>Budgets</h3><div class="caps">' + capBar('Today, all projects', m.today, c.day) + capBar('This month, all projects', m.month, c.month) +
    capBar('Today, project ' + project, m.project_today, c.project_day) + capBar('This month, project ' + project, m.project_month, c.project_month) +
    Object.entries(c.workers || {}).map(([w, cap]) => capBar('Today, ' + w, (m.workers_today || {})[w] || 0, cap)).join('') + '</div>' +
    (!c.day && !c.month && !c.project_day && !c.project_month && !Object.keys(c.workers || {}).length ? '<div class="empty small">No budget caps set. Set caps in Settings → Money.</div>' :
     (!c.month && !c.project_day && !Object.keys(c.workers || {}).length ? '<div class="muted small">Only the daily budget is set. Monthly, per-project and per-worker caps: Settings → Money.</div>' : ''));
  const days = m.per_day || [], rts = [...new Set(days.flatMap(d => Object.keys(d).filter(k => k !== 'date')))];
  if (rts.length) {
    const mx = Math.max(1e-9, ...days.map(d => rts.reduce((a, r) => a + (d[r] || 0), 0)));
    h += '<h3>Spend per day (USD)</h3><div class="bars stacked">' + days.map(d => { const tot = rts.reduce((a, r) => a + (d[r] || 0), 0);
      return '<div title="' + esc(d.date + ': ' + rts.map(r => (RTNAME[r] || r) + ' ' + usd(d[r] || 0) + ' USD').join(', ')) + '"><span>' + (tot ? usd(tot) : '') + '</span><div class="stack" style="height:' + (100 * tot / mx) + '%">' +
        rts.map(r => d[r] ? '<b style="flex:' + d[r] + ';background:' + (RTCOL[r] || 'var(--accent)') + '"></b>' : '').join('') + '</div><span>' + esc(d.date.slice(5)) + '</span></div>'; }).join('') + '</div>' +
      '<div class="legend">' + rts.map(r => '<span><i style="background:' + (RTCOL[r] || 'var(--accent)') + '"></i>' + esc(RTNAME[r] || r) + '</span>').join('') + '</div>';
  } else h += '<div class="empty small" style="margin-top:10px">No API spend recorded in the last 14 days' + ((F.api && (F.api.workers || []).length) ? '.' : ' — no API workers in this project.') + '</div>';
  const cms = m.cost_models || {};
  const rtKeys = [...new Set([...Object.keys(cms), ...Object.keys(m.by_runtime || {})])];
  rtKeys.sort((a, b) => {
    const va = (m.by_runtime || {})[a] || 0, vb = (m.by_runtime || {})[b] || 0;
    return (vb - va) || a.localeCompare(b);
  });
  const rtTbl = rtKeys.length ? '<div><h4>By runtime (USD)</h4><table class="m">' +
    rtKeys.map(k => {
      const cm = cms[k];
      const v = (m.by_runtime || {})[k];
      let cell;
      if (cm && cm.kind === 'quota') {
        cell = '<span class="badge quota" title="' + esc(cm.detail || '') + '">quota</span>';
      } else if (cm && cm.kind === 'unknown') {
        cell = '<span class="badge unknown" title="' + esc(cm.detail || '') + '">unknown</span>';
      } else {
        cell = usd(v || 0) + ' USD';
      }
      return '<tr data-runtime="' + esc(k) + '"><td>' + esc(RTNAME[k] || k) + '</td><td class="num">' + cell + '</td></tr>';
    }).join('') + '</table></div>' : '';
  const tbl = (title, obj, nameFn) => { const e = Object.entries(obj || {}).filter(x => x[1]).sort((a, b) => b[1] - a[1]); return e.length ? '<div><h4>' + title + '</h4><table class="m">' +
    e.map(([k, v]) => '<tr><td>' + esc(nameFn ? nameFn(k) : (k || 'default')) + '</td><td class="num">' + usd(v) + ' USD</td></tr>').join('') + '</table></div>' : ''; };
  const monthHtml = rtTbl + tbl('By project (USD)', m.by_project) + tbl('By model (this project, USD)', m.by_model);
  if (monthHtml) h += '<h3>This month</h3><div class="cols3">' + monthHtml + '</div>';
  if ((m.top_tasks || []).length) h += '<h3>Most expensive tasks this month</h3><div class="tablewrap"><table class="m click"><tr><th>task</th><th class="num">cost (USD)</th></tr>' + m.top_tasks.map(t => '<tr data-open="task:' + esc(t.task) +
    '" tabindex="0" role="button" aria-label="Open task ' + esc(t.task) + '"><td>' + esc(t.task) + '</td><td class="num">' + usd(t.usd) + ' USD</td></tr>').join('') + '</table></div>';
  return h + quotaHtml(m);
}

// ---- Experiments
function ciTxt(ci, unit) {
  if (!ci) return '–';
  const f = v => unit === '$' ? usd(v) + ' USD' : Number(v).toFixed(2) + (unit.trim() ? ' ' + unit.trim() : '');
  const mean = f(ci[0]);
  const low = f(Math.max(0, ci[1]));
  const high = f(ci[2]);
  return mean + ' <span class="muted" title="95% confidence interval: ' + low + ' to ' + high + '" aria-label="95% confidence interval: ' + low + ' to ' + high + '">(' + low + '–' + high + ')</span>';
}
function experimentsInsights() {
  const E = (F.metrics || {}).efficiency || {}, V = E.verdicts || [];
  let h = '<div class="muted small" style="margin-bottom:8px">As of ' + (F && F.now ? dfmt(F.now) : ago(foremanFetchedAt) + ' ago') + '</div>';
  if (V.length) h += V.map(v => {
    const adopt = v.winner ? (v.kind === 'model' ? '<button class="primary sm" data-adopt="model" data-v="' + esc(v.winner) + '">Make ' + esc(v.winner) + ' the worker model</button>'
      : '<button class="primary sm" data-adopt="pack" data-v="' + (v.winner === 'pack' ? 'on' : 'off') + '">' + (v.winner === 'pack' ? 'Keep the context pack on for every task' : 'Turn the context pack off') + '</button>') : '';
    return '<div class="verdict' + (v.winner ? ' win' : '') + '"><div class="v-head">' + (v.kind === 'model' ? 'Worker model: ' + esc(v.a) + ' vs ' + esc(v.b) : 'Context pack: off vs on') + '</div>' +
      '<div>' + esc(v.text) + (v.need_more ? ' — about <b>' + v.need_more + '</b> more merged tasks each to tell.' : '') + '</div>' + (adopt ? '<div class="btnrow">' + adopt + '</div>' : '') + '</div>';
  }).join('');
  else h += '<div class="note">No experiment is running. Start one in Settings → Models: an alternative worker model for a share of the tasks (A/B), or ' +
    'the context pack for half of them. The verdict appears here once the 95 % confidence intervals separate.</div>';
  if (F.routing === 'auto') h += '<div class="note">Model routing is on: for each role the cheapest worker model that has proven itself here is used (Settings → Models).</div>';
  const effTable = (title, rows) => rows.length ? '<h3>' + title + '</h3><div class="tablewrap"><table class="m"><tr><th>variant</th><th class="num">runs</th><th class="num">done</th>' +
    '<th class="num" title="Mean and 95% confidence interval">approved 1st time (%, 95% CI)</th><th class="num" title="Quota used per done task (% of weekly limit, mean and 95% confidence interval)">quota / done (% of week, 95% CI)</th>' +
    '<th class="num" title="Cost per done task in USD, mean and 95% confidence interval">cost / done (USD, 95% CI)</th><th class="num">token cap (%)</th><th class="num">tools / run (median)</th></tr>' +
    rows.map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="num">' + v.runs + '</td><td class="num">' + v.done + '</td><td class="num">' +
      (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %' + (v.first_pass_ci && v.first_pass_ci[0] != null ? ' <span class="muted" title="95% confidence interval: ' + v.first_pass_ci[0] + '% to ' + v.first_pass_ci[1] + '%" aria-label="95% confidence interval">(' + v.first_pass_ci[0] + '–' + v.first_pass_ci[1] + ' %)</span>' : '')) +
      '</td><td class="num">' + ciTxt(v.week_done_ci, ' % of week') + '</td><td class="num">' + ciTxt(v.cost_done_ci, '$') + '</td><td class="num">' + (v.cap_pct != null ? v.cap_pct + ' %' : '–') + '</td><td class="num">' +
      (v.tools_median == null ? '–' : v.tools_median) + '</td></tr>').join('') + '</table></div>' : '';
  const modelRows = Object.entries(E.model || {}).sort((a, b) => b[1].runs - a[1].runs);
  const packRows = Object.entries(E.pack || {}).sort((a, b) => b[1].runs - a[1].runs);
  if (modelRows.length || packRows.length) {
    h += effTable('By worker model', modelRows);
    h += effTable('Context pack', packRows);
  } else {
    h += '<div class="empty" style="margin-top:12px">No experiment run data recorded yet. Results will appear here as tasks finish.</div>';
  }
  const SC = E.self_check;
  if (SC && SC.runs) h += '<h3>Self-check in the run</h3><div class="stats">' + box(SC.runs, 'runs with a self-check') + box(SC.green_first, 'green at once') +
    box(SC.fixed, 'fixed in same run') + box(SC.still_red, 'stayed red') + '</div>';
  return h + '<div class="muted small">Quota = % of one account\'s weekly limit, from agy /usage before and after each run. Parenthesized ranges are 95% confidence intervals; an A/B verdict requires intervals to separate without overlap.</div>';
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
