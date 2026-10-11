// UI smoke test (tests/test_ui.sh runs it): the real page on the demo state, in jsdom, in polling mode - every tab and
// sub-view renders, each kind of sheet opens (task, goal, run replay, search, document), the graph finds the critical path,
// and no script error happens on the way. Exit code 0 = all ok.
const {JSDOM, VirtualConsole} = require('jsdom');
const base = process.env.AF_URL || 'http://127.0.0.1:8171/';
let failed = 0;
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push('jsdomError: ' + (e.message || e)));
vc.on('error', e => errors.push('console.error: ' + e));
setTimeout(() => { console.log('TIMEOUT; errors so far:\n' + errors.join('\n')); process.exit(3); }, 150000);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const dom = await JSDOM.fromURL(base + '#tab=overview&poll=1', {runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse(win) { win.fetch = (u, o) => fetch(new URL(u, base), o); win.EventSource = undefined; }});
  const w = dom.window, d = w.document;
  w.addEventListener('error', e => errors.push('window.error: ' + e.message + ' ' + (e.filename || '') + ':' + e.lineno));
  await sleep(6000);
  const txt = id => (d.getElementById(id) || {}).textContent || '';
  const check = (name, ok) => { failed += !ok; console.log((ok ? 'ok   ' : 'FAIL ') + name); };
  check('overview: needs-you list rendered', txt('inbox').includes('Needs you'));
  check('overview: team shows idle reasons', txt('team').includes('waits for 0008-usage'));
  check('overview: backlog has the forecast', txt('backlog-h').includes('Backlog'));
  check('overview: each model family keeps its own weekly quota (no mixing with the account\'s other family)',
        /Gemini 72% · week 61%/.test(txt('team')) && txt('team').includes('Claude week 0%'));
  const iso = '2026-01-15T13:49:21Z', wantLocal = new Date(iso).toLocaleString([], {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
  check('quota reset timestamps are shown in the viewer\'s own time, not UTC',
        w.eval("tsloc('quota low: Gemini weekly 0% (resets " + iso + ")')") === 'quota low: Gemini weekly 0% (resets ' + wantLocal + ')');
  w.eval(`
    window._origOverviewState = {g: F.goals, bl: F.backlog, onb: F.onboarding, ts: F.tasks};
    F.goals = []; F.backlog = []; F.onboarding = []; F.tasks = [];
    renderOverview(true);
  `);
  check('overview: explicit empty states render when arrays are empty',
        txt('inbox').includes('Nothing needs your approval') &&
        txt('onboarding').includes('No onboarding steps') &&
        txt('goals').includes('No goals yet') &&
        txt('backlog').includes('Backlog is empty'));
  w.eval(`
    window._origF = F; window._origFerr = window._ferr;
    F = null; window._ferr = 'network timeout';
    renderOverview(true);
  `);
  check('overview: failed fetch shows visible error message',
        txt('inbox').includes('Failed to load') &&
        txt('team').includes('Could not load team') &&
        txt('goals').includes('Could not load goals') &&
        txt('backlog').includes('Could not load backlog'));
  w.eval(`
    F = window._origF; window._ferr = window._origFerr;
    F.goals = window._origOverviewState.g; F.backlog = window._origOverviewState.bl;
    F.onboarding = window._origOverviewState.onb; F.tasks = window._origOverviewState.ts;
    renderOverview(true);
  `);
  const overAct = [...d.querySelectorAll('#t-overview [data-open], #t-overview [data-ta], #t-overview [data-ga], #t-overview [data-bledit]')];
  check('overview: all interactive elements are buttons reachable by keyboard',
        overAct.length > 0 && overAct.every(el => el.tagName === 'BUTTON' || (el.getAttribute('role') === 'button' && el.tabIndex >= 0)));
  const click = sel => { const el = d.querySelector(sel); if (!el) { errors.push('no element ' + sel); return false; } el.dispatchEvent(new w.MouseEvent('click', {bubbles: true})); return true; };
  for (const t of ['tasks', 'live', 'timeline', 'insights', 'system', 'settings', 'overview']) {
    click('nav button[data-t="' + t + '"]'); await sleep(700);
    const segs = d.querySelectorAll('#t-' + t + ' .seg button');
    for (const b of segs) { b.dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(1500); check(t + ' / ' + b.dataset.v + ' renders', d.querySelector('#t-' + t).textContent.length > 40); }
    if (!segs.length) check(t + ' renders', d.querySelector('#t-' + t).textContent.length > 20);
  }
  click('nav button[data-t="overview"]'); await sleep(500);
  w.openTask('0010-overdue-color'); await sleep(2500);
  check('task sheet: merge actions and runs', txt('sheet').includes('Merge') && txt('sheet').includes('Send back'));
  const taskSheetAct = [...d.querySelectorAll('#sheet [data-open], #sheet [data-ta], #sheet [data-tform], #sheet [data-tdiff]')];
  check('task sheet: all interactive controls reachable by keyboard',
        taskSheetAct.length > 0 && taskSheetAct.every(el => el.tagName === 'BUTTON' || (el.getAttribute('role') === 'button' && el.tabIndex >= 0)));
  check('diff: empty or missing diff renders explicit empty state',
        w.eval("diffHtml('')").includes('No diff available') && w.eval("diffHtml('(no diff recorded for this task)')").includes('No diff available'));
  w.openGoal('G20261008-062000'); await sleep(2500);
  check('goal sheet: tasks, events, criteria', txt('sheet').includes('What happened') && txt('sheet').includes('Done when'));
  const goalSheetAct = [...d.querySelectorAll('#sheet [data-open], #sheet [data-ga], #sheet [data-gform], #sheet [data-gdiff]')];
  check('goal sheet: all interactive controls reachable by keyboard',
        goalSheetAct.length > 0 && goalSheetAct.every(el => el.tagName === 'BUTTON' || (el.getAttribute('role') === 'button' && el.tabIndex >= 0)));
  w.eval(`
    window._origGDData = GD.data;
    GD.data = Object.assign({}, GD.data, {tasks: []});
    renderGoalSheet();
  `);
  check('goal sheet: zero tasks shows explicit empty state',
        txt('sheet').includes('No tasks yet'));
  w.eval(`
    GD.data = window._origGDData;
    renderGoalSheet();
  `);
  const rid = (w.eval('F').timeline || []).find(r => r.end && r.run_id);
  if (rid) {
    w.openRun(rid.run_id); await sleep(2500);
    check('replay sheet opens', txt('sheet').includes('Run '));
    const replayAct = [...d.querySelectorAll('#sheet [data-open]')];
    check('replay sheet: interactive task links are keyboard reachable',
          replayAct.length === 0 || replayAct.every(el => el.tagName === 'BUTTON' || (el.getAttribute('role') === 'button' && el.tabIndex >= 0)));
  }
  w.openRuns(); await sleep(1500);
  const runsRows = [...d.querySelectorAll('#sheet tr[data-open]')];
  check('runs sheet: finished run rows are keyboard reachable',
        runsRows.length === 0 || runsRows.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  w.closeSheet();
  w.openSearch(); const q = d.getElementById('sr-q'); q.value = 'due'; q.dispatchEvent(new w.Event('input', {bubbles: true})); await sleep(3000);
  check('search finds tasks', txt('sheet').includes('Tasks ('));
  const srRows = [...d.querySelectorAll('.sr[data-open]')];
  check('search: result rows are keyboard reachable',
        srRows.length > 0 && srRows.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  q.value = 'xyznonexistentword'; q.dispatchEvent(new w.Event('input', {bubbles: true})); await sleep(1000);
  check('search: explicit empty state for zero matches',
        txt('sheet').includes('No results for'));
  w.openDoc('role:tester'); await sleep(2000);
  check('document editor opens', !!d.getElementById('doc-text'));
  w.closeSheet();
  w.setTab('live'); await sleep(800);
  w.eval(`
    window._origPanes = P.panes;
    P.panes = [];
    renderLive();
  `);
  check('live: explicit empty message when nothing running',
        txt('grid').includes('No AI is running right now'));
  w.eval(`
    P.panes = [{id: 'worker:worker-1', title: 'worker-1', kind: 'worker', state: 'finished', lines: [{n: 1, kind: 'tool', text: 'make test'}]}];
    renderLive();
  `);
  check('live: pane that just ended renders finished state without JS error',
        txt('grid').includes('finished'));
  w.eval(`
    P.panes = window._origPanes;
    renderLive();
  `);
  w.setTab('timeline'); await sleep(800);
  const tlBars = [...d.querySelectorAll('.tl g[data-open]')];
  check('timeline: bars are keyboard reachable',
        tlBars.length === 0 || tlBars.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  check('timeline: time axis labelled with time zone',
        txt('timeline').includes('Timezone') || !!d.querySelector('.tl .tz'));
  w.eval("SUB.tasks = 'board'"); w.setTab('tasks'); await sleep(800);
  const taskCards = [...d.querySelectorAll('#t-tasks .card')];
  check('tasks: board cards are keyboard reachable',
        taskCards.length > 0 && taskCards.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  const boardCols = [...d.querySelectorAll('#t-tasks .col')];
  check('tasks: board header task count matches rendered cards for each column',
        boardCols.length > 0 && boardCols.every(col => {
          const m = (col.querySelector('h4 .muted') || {}).textContent || '';
          const rendered = col.querySelectorAll('.card').length;
          const num = parseInt(m.split(' ')[0], 10);
          return num === rendered;
        }));
  w.eval("SUB.tasks = 'graph'"); w.setTab('tasks'); await sleep(800);
  check('graph shows the critical path', txt('tasks-view').includes('Critical path'));
  const graphNodes = [...d.querySelectorAll('.dag g[data-open]')];
  check('tasks: graph nodes are keyboard reachable',
        graphNodes.length > 0 && graphNodes.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  w.eval(`
    window._origFTasks = F.tasks;
    F.tasks = [];
    renderTasks(true);
  `);
  check('tasks: graph shows explicit empty state when zero tasks',
        txt('tasks-view').includes('No tasks yet'));
  w.eval("SUB.tasks = 'board'; renderTasks(true);");
  check('tasks: board shows explicit empty state when zero tasks',
        txt('tasks-view').includes('No tasks yet'));
  w.eval(`
    F.tasks = window._origFTasks;
    renderTasks(true);
  `);
  // the egress view with a blocked host, an alarm and the kill switch it tripped (the demo has no proxy log)
  w.eval(`SYS.egress = {health: {ok: true, mode: 'enforce'}, since: '2026-10-08T00:00:00Z', allowed: {'api.anthropic.com': 12}, denied: {'pypi.org': 3, 'stats.example.net': 1},
    noise: {}, new_domains: {'worker-2 pypi.org': 3}, private: [], alarms: [{level: 'HIGH', code: 'new-domain', message: 'worker-2 tried pypi.org'}],
    local_allowlist: ['files.example.org'], baseline: ['stats.example.net'], killswitch: {reason: 'egress alarm during run X: worker-2 tried pypi.org', scope: 'project'}};
    SYS.egressAt = Date.now(); SUB.system = 'egress';`);
  w.setTab('system'); await sleep(800);
  check('egress: the stop is explained, blocked hosts can be allowed / ignored, own entries removed',
    txt('system').includes('stopped by an egress alarm') && !!d.querySelector('[data-egact="allow"][data-host="pypi.org"]') &&
    !!d.querySelector('[data-egact="ignore"][data-host="pypi.org"]') && !d.querySelector('[data-egact="ignore"][data-host="stats.example.net"]') &&
    !!d.querySelector('[data-egact="unallow"][data-host="files.example.org"]') && !d.querySelector('[data-egact="unallow"][data-host="api.anthropic.com"]'));
  w.eval(`SUB.insights = 'money'; renderInsights(true);`);
  check('insights: money view labels figures with USD and shows as-of indicator',
    txt('insights').includes('USD') && txt('insights').includes('As of'));
  w.eval(`SUB.insights = 'experiments'; renderInsights(true);`);
  check('insights: experiments view explains confidence intervals and shows as-of indicator',
    txt('insights').includes('95') && txt('insights').includes('As of'));
  w.eval(`SYS.health = {bad: 1, checks: [{section: 'daemon', level: 'bad', message: 'egress proxy missing', fix_id: 'egress-up'}], at: Date.now()};
    SUB.system = 'health'; renderSystem(true);`);
  const fixBtn = d.querySelector('#t-system [data-fix="egress-up"]');
  check('system: health fixButton is keyboard reachable and labeled',
    !!fixBtn && fixBtn.tagName === 'BUTTON' && !!fixBtn.getAttribute('aria-label'));
  check('system: collapsible checks panel exists with summary',
    !!d.getElementById('all-checks') && !!d.querySelector('#all-checks summary'));
  w.eval(`SYS.health = null; SYS.err.health = null; SYS.healthBusy = false; renderSystem(true);`);
  check('system: health view shows explicit empty state when not yet run',
    txt('system').includes('Health check not yet run'));
  check('no page error shown', !txt('jserr'));
  click('nav button[data-t="settings"]'); await sleep(800);

  // Settings / Project: task 0024 fields
  click('#settings-seg button[data-v="project"]'); await sleep(800);
  const projKeys0024 = ['DIFF_MAX', 'MAX_GOAL_ROUNDS', 'LESSONS_MAX', 'LOOP_SEC', 'LOCAL_TZ', 'SHARED_FILES', 'VISION_FILE', 'PROJECT_DIR'];
  const projOk = projKeys0024.every(k => {
    const el = d.getElementById('set-' + k);
    const help = d.querySelector('[for="set-' + k + '"] .help');
    return !!el && !!help && help.textContent.trim().length > 0 && help.textContent.toLowerCase().includes('applies live');
  });
  check('settings / project: task 0024 fields present as inputs with help stating live reload', projOk);

  // Settings / Limits: task 0024 fields
  click('#settings-seg button[data-v="limits"]'); await sleep(800);
  const limitsKeys0024 = ['DAILY_TOKEN_BUDGET', 'MAX_RUNS_PER_DAY', 'RUN_TIMEOUT_MAX_MIN', 'QUOTA_MODEL_FILTER', 'POLL_SEC'];
  const limitsOk = limitsKeys0024.every(k => {
    const el = d.getElementById('set-' + k);
    const help = d.querySelector('[for="set-' + k + '"] .help');
    return !!el && !!help && help.textContent.trim().length > 0 && help.textContent.toLowerCase().includes('applies live');
  });
  check('settings / limits: task 0024 fields present as inputs with help stating live reload', limitsOk);

  // Settings / Models: task 0024 fields
  click('#settings-seg button[data-v="models"]'); await sleep(800);
  const oaiBase = d.getElementById('set-OPENAI_API_BASE');
  const oaiHelp = d.querySelector('[for="set-OPENAI_API_BASE"] .help');
  check('settings / models: OPENAI_API_BASE present as input with help stating live reload',
        !!oaiBase && !!oaiHelp && oaiHelp.textContent.trim().length > 0 && oaiHelp.textContent.toLowerCase().includes('applies live'));

  // Settings / Money: task 0024 PROJECT_* budget fields
  click('#settings-seg button[data-v="money"]'); await sleep(800);
  const moneyKeys0024 = ['PROJECT_DAILY_BUDGET_USD', 'PROJECT_MONTHLY_BUDGET_USD'];
  const moneyOk = moneyKeys0024.every(k => {
    const el = d.getElementById('set-' + k);
    const help = d.querySelector('[for="set-' + k + '"] .help');
    return !!el && !!help && help.textContent.trim().length > 0 && help.textContent.toLowerCase().includes('applies live');
  });
  check('settings / money: PROJECT_* budget fields present as inputs with help stating live reload', moneyOk);

  // Settings: restart text accuracy
  const restartHelpOk = w.eval("HELP.FORGE_URL.includes('restart') && HELP.FORGE_REPO.includes('restart')");
  check('settings: keys requiring service restart state so explicitly in help', restartHelpOk);

  // Settings / Knowledge: doc rows keyboard reachable
  click('#settings-seg button[data-v="knowledge"]'); await sleep(800);
  const docRows = [...d.querySelectorAll('.docrow')];
  check('settings / knowledge: document rows are keyboard reachable',
        docRows.length > 0 && docRows.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));

  // Settings: all interactive controls keyboard reachable
  const setActs = [...d.querySelectorAll('#t-settings input, #t-settings select, #t-settings button, #t-settings textarea, #t-settings [role="button"]')];
  check('settings: all interactive controls reachable by keyboard',
        setActs.length > 0 && setActs.every(el => el.tabIndex >= 0 || ['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA'].includes(el.tagName)));

  // Settings: empty states for workers and docs
  w.eval(`
    window._origS = S;
    window._origDOCS = DOCS.list;
    DOCS.list = [];
    renderSettings(true);
  `);
  check('settings / knowledge: explicit empty message when documents list is empty',
        txt('settings').includes('No documents yet') || txt('settings').includes('No roles defined yet'));

  w.eval(`
    S = Object.assign({}, window._origS, {workers: []});
    SUB.settings = 'team';
    renderSettings(true);
  `);
  check('settings / workers: explicit empty message when worker pool is empty',
        txt('settings').includes('No workers configured yet'));

  // Settings: error state on fetch failure
  w.eval(`
    S = null;
    window._origFerr = window._ferr;
    window._ferr = 'network timeout';
    renderSettings(true);
  `);
  check('settings: explicit error message when fetch failed and S is null',
        txt('settings').includes('Could not load settings') || txt('settings').includes('network timeout'));

  // Restore settings state
  w.eval(`
    S = window._origS;
    DOCS.list = window._origDOCS;
    window._ferr = window._origFerr;
    SUB.settings = 'project';
    renderSettings(true);
  `);
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no JS errors');
  console.log('ui tests: ' + (failed || errors.length ? 'FAILED' : 'ALL OK'));
  w.close(); process.exit(failed || errors.length ? 1 : 0);
})().catch(e => { console.log('crash', e); console.log(errors.join('\n')); process.exit(4); });
