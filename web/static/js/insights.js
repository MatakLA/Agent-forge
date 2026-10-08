// Metrics.
// ---- 8. team metrics
function metricsHtml() {
  const M = F && F.metrics;
  if (!M) return '<div class="empty">Metrics need a newer foreman.</div>';
  const T = M.totals || {}, box = (v, lab) => '<div class="stat"><b>' + (v == null ? '–' : v) + '</b><span>' + lab + '</span></div>';
  let h = '<div class="stats">' + box(T.done, 'tasks done') + box(T.first_pass_pct == null ? null : T.first_pass_pct + ' %', 'approved first time') +
          box(T.reworks, 'sent back') + box(T.blocked, 'blocked') + box(T.tokens_per_done ? Math.round(T.tokens_per_done / 1000) + 'k' : null, 'tokens per done task') + '</div>';
  const days = M.merged_per_day || [], mx = Math.max(1, ...days.map(d => d[1]));
  h += '<h3>Tasks done per day</h3><div class="bars">' + (days.map(([d, n]) => '<div><span>' + n + '</span><b style="height:' + (100 * n / mx) + '%"></b><span>' + d.slice(5) + '</span></div>').join('') || '<span class="muted">nothing yet</span>') + '</div>';
  const roles = Object.entries(M.roles || {}).sort((a, b) => b[1].tasks - a[1].tasks);
  h += '<h3>By role</h3><div class="tablewrap"><table class="m"><tr><th>role</th><th class="num">tasks</th><th class="num">done</th><th class="num">1st try</th><th class="num">sent back</th><th class="num">blocked</th><th class="num">tokens/task</th><th class="num">lines</th></tr>' +
       roles.map(([r, v]) => '<tr><td>' + esc(r) + '</td><td class="num">' + v.tasks + '</td><td class="num">' + v.done + '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') +
         '</td><td class="num">' + v.reworks + '</td><td class="num">' + v.blocked + '</td><td class="num">' + (v.tokens_per_done ? Math.round(v.tokens_per_done / 1000) + 'k' : '–') + '</td><td class="num">' + v.lines_done + '</td></tr>').join('') + '</table></div>';
  const E = M.efficiency || {};
  const effTable = (title, rows, note) => rows.length ? '<h3>' + title + '</h3>' + (note ? '<div class="muted" style="font-size:12.5px">' + note + '</div>' : '') +
    '<div class="tablewrap"><table class="m"><tr><th></th><th class="num">runs</th><th class="num">ok</th><th class="num">cap</th><th class="num">quota/run</th><th class="num">quota/done task</th>' +
    '<th class="num">tools/run</th><th class="num">tokens/run</th><th class="num">done</th><th class="num">1st try</th></tr>' +
    rows.map(([k, v]) => '<tr><td>' + esc(k) + '</td><td class="num">' + v.runs + '</td><td class="num">' + v.ok_pct + ' %</td><td class="num">' + v.cap_pct + ' %</td><td class="num">' +
      (v.week_per_run == null ? '–' : v.week_per_run + ' %') + '</td><td class="num"><b>' + (v.week_per_done == null ? '–' : v.week_per_done + ' %') + '</b></td><td class="num">' +
      (v.tools_median == null ? '–' : v.tools_median) + '</td><td class="num">' + (v.tokens_median ? Math.round(v.tokens_median / 1000) + 'k' : '–') + '</td><td class="num">' + v.done +
      '</td><td class="num">' + (v.first_pass_pct == null ? '–' : v.first_pass_pct + ' %') + '</td></tr>').join('') + '</table></div>' : '';
  h += effTable('Efficiency by worker model', Object.entries(E.model || {}).sort((a, b) => b[1].runs - a[1].runs),
                'quota = % of one account\'s weekly limit (agy /usage before and after the run); lower is better');
  h += effTable('Context pack — A/B', Object.entries(E.pack || {}).sort((a, b) => b[1].runs - a[1].runs),
                'with CONTEXT_PACK=ab half the tasks get the pack; compare quota per done task and tools per run');
  const SC = E.self_check;
  if (SC && SC.runs) h += '<h3>Self-check in the run</h3><div class="stats">' + box(SC.runs, 'runs with a self-check') + box(SC.green_first, 'green at once') +
    box(SC.fixed, 'fixed in the same run') + box(SC.still_red, 'stayed red') + '</div>';
  const MT = F.maintenance;
  if (MT) h += '<h3>Maintenance of main (no AI)</h3><div class="muted maint" style="font-size:13px">' + esc(dfmt(MT.at)) + ' · ' + esc(MT.sha || '') + ' · testy: ' +
    (MT.suite || []).map(r => (r.rc === 0 ? '✓ ' : '✗ ') + esc(r.summary)).join(' / ') + (MT.nodoc ? ' · modulov bez docstringu: ' + MT.nodoc : '') + '</div>';
  const prov = Object.entries(M.providers || {}).sort((a, b) => b[1].calls - a[1].calls);
  h += '<h3>Orchestrator, last 7 days (who planned and reviewed)</h3><div class="tablewrap"><table class="m"><tr><th>provider</th><th class="num">calls</th><th class="num">failed</th><th class="num">average</th></tr>' +
       prov.map(([p, v]) => '<tr><td>' + esc(p) + '</td><td class="num">' + v.calls + '</td><td class="num">' + v.failed + '</td><td class="num">' + (v.calls ? Math.round(v.secs / v.calls) + ' s' : '–') + '</td></tr>').join('') + '</table></div>';
  return h;
}
