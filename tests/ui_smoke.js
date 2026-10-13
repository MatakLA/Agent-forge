// UI smoke test (tests/test_ui.sh runs it): the real page on the demo state, in jsdom, in polling mode - every tab and
// sub-view renders, overview controls (autopilot and foreman pause switches) match fixture state and are keyboard reachable,
// readiness summary on Settings checks runtimes and providers, file-only settings render with reasons, onboarding subsection
// displays human-readable runtime/provider readiness, runtime recommendations widget (top pick vs no ready runtime), and choosing
// AI guide, each kind of sheet opens (task, goal, run replay, search, document), the graph finds the critical path, and no script
// error happens on the way. Exit code 0 = all ok.
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
  const walkFocus = (startEl, targets) => {
    if (!startEl) return false;
    const list = Array.isArray(targets) ? targets : [targets];
    if (!list.length) return false;
    startEl.focus();
    if (d.activeElement !== startEl) return false;
    for (const target of list) {
      if (!target || !d.body.contains(target) || target.style.display === 'none') {
        return false;
      }
      d.activeElement.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Tab', code: 'Tab', keyCode: 9, bubbles: true}));
      target.focus();
      target.dispatchEvent(new w.Event('focus', {bubbles: true}));
      if (d.activeElement !== target) {
        return false;
      }
    }
    return true;
  };
  check('overview: needs-you list rendered', txt('inbox').includes('Needs you'));
  check('overview: team shows idle reasons', txt('team').includes('waits for 0008-usage'));
  check('overview: backlog has the forecast', txt('backlog-h').includes('Backlog'));
  check('overview: each model family keeps its own weekly quota (no mixing with the account\'s other family)',
        /Gemini 72% · week 61%/.test(txt('team')) && txt('team').includes('Claude week 0%'));
  const iso = '2026-01-15T13:49:21Z', wantLocal = new Date(iso).toLocaleString([], {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
  check('quota reset timestamps are shown in the viewer\'s own time, not UTC',
        w.eval("tsloc('quota low: Gemini weekly 0% (resets " + iso + ")')") === 'quota low: Gemini weekly 0% (resets ' + wantLocal + ')');

  // Task 0143: Overview controls - autopilot and foreman pause/resume switches
  const curF = w.eval('F');
  check('overview: demo fixture state is available', !!curF);
  const apToggle = d.getElementById('autopilot-toggle');
  const fmToggle = d.getElementById('foreman-pause-toggle');
  check('overview: autopilot toggle is present and keyboard reachable',
        !!apToggle && (apToggle.tagName === 'BUTTON' || (apToggle.getAttribute('role') === 'button' && apToggle.tabIndex >= 0)) && apToggle.tabIndex >= 0);
  check('overview: foreman pause toggle is present and keyboard reachable',
        !!fmToggle && (fmToggle.tagName === 'BUTTON' || (fmToggle.getAttribute('role') === 'button' && fmToggle.tabIndex >= 0)) && (fmToggle.disabled || fmToggle.tabIndex >= 0));

  const curAp = Boolean(curF && curF.autopilot);
  const wantApLabel = 'Autopilot: ' + (curAp ? 'on' : 'off');
  const wantApTip = curAp
    ? 'Autopilot on: when a worker would idle and the backlog is empty, the orchestrator proposes new goals from the vision.'
    : 'Autopilot off: when a worker would idle and the backlog is empty, the orchestrator does not propose new goals from the vision.';
  check('overview: autopilot toggle visible label matches demo state',
        !!apToggle && apToggle.textContent.trim() === wantApLabel);
  check('overview: autopilot toggle tooltip matches demo state',
        !!apToggle && (apToggle.title === wantApTip || apToggle.getAttribute('title') === wantApTip));

  const curPaused = Boolean(curF && (curF.paused || curF.killswitch));
  const wantFmLabel = 'Foreman: ' + (curPaused ? 'paused' : 'running');
  check('overview: foreman pause toggle visible label matches demo state',
        !!fmToggle && fmToggle.textContent.trim() === wantFmLabel);

  if (curF && curF.killswitch) {
    const ks = curF.killswitch;
    const ksReason = (typeof ks === 'object' ? String(ks.reason || '').trim() : String(ks).trim());
    const wantKsTip = 'Resume is blocked while the kill switch is engaged' + (ksReason ? ': ' + ksReason : '');
    check('overview: resume control is rendered disabled with explanatory tooltip matching fixture kill switch',
          !!fmToggle && fmToggle.disabled === true && (fmToggle.title === wantKsTip || fmToggle.getAttribute('title') === wantKsTip));
  } else {
    const wantWorkingTip = curPaused
      ? 'Paused: no new tasks are dispatched; tasks already running continue to completion.'
      : 'Running: tasks are dispatched to workers as they become available.';
    check('overview: resume control is rendered enabled in working state matching fixture kill switch value',
          !!fmToggle && fmToggle.disabled === false && (fmToggle.title === wantWorkingTip || fmToggle.getAttribute('title') === wantWorkingTip));
  }
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
  // Verify control switches when kill switch is engaged dynamically
  w.eval(`
    window._origFControls = {ap: F.autopilot, p: F.paused, ks: F.killswitch};
    F.killswitch = {reason: 'egress violation', scope: 'project'};
    renderOverview(true);
  `);
  const ksToggle = d.getElementById('foreman-pause-toggle');
  check('overview: foreman pause toggle rendered disabled with kill switch explanation when kill switch engaged',
        !!ksToggle && ksToggle.disabled === true &&
        (ksToggle.title || '').includes('Resume is blocked while the kill switch is engaged: egress violation') &&
        ksToggle.textContent.trim() === 'Foreman: paused');

  // Verify control switches when autopilot is off and foreman paused without kill switch
  w.eval(`
    F.autopilot = false;
    F.paused = true;
    F.killswitch = null;
    renderOverview(true);
  `);
  const offApToggle = d.getElementById('autopilot-toggle');
  const pausedFmToggle = d.getElementById('foreman-pause-toggle');
  check('overview: control switches render correctly when autopilot is off and foreman paused without kill switch',
        !!offApToggle && offApToggle.textContent.trim() === 'Autopilot: off' &&
        (offApToggle.title || '').includes('Autopilot off') &&
        !!pausedFmToggle && pausedFmToggle.textContent.trim() === 'Foreman: paused' &&
        pausedFmToggle.disabled === false &&
        (pausedFmToggle.title || '').includes('Paused: no new tasks are dispatched'));

  // Restore control states
  w.eval(`
    F.autopilot = window._origFControls.ap;
    F.paused = window._origFControls.p;
    F.killswitch = window._origFControls.ks;
    renderOverview(true);
  `);
  const overAct = [...d.querySelectorAll('#t-overview [data-open], #t-overview [data-ta], #t-overview [data-ga], #t-overview [data-bledit], #t-overview [data-control]')];
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

  // Task 0123: orphan-warning badge on board card and detail sheet
  w.eval(`
    window._origTasks0123 = F.tasks;
    F.tasks = [
      {id: '0123-warn', goal: 'G1', title: 'Task with orphan warn', role: 'developer', status: 'pending', orphan_warning: true, orphan_streak: 3, orphan_limit: 5},
      {id: '0123-clean', goal: 'G1', title: 'Clean task', role: 'developer', status: 'pending', orphan_warning: false, orphan_streak: 1, orphan_limit: 5},
      {id: '0123-blk', goal: 'G1', title: 'Blocked task', role: 'developer', status: 'blocked', orphan_warning: false, orphan_streak: 5, orphan_limit: 5}
    ];
    SUB.tasks = 'board';
    renderTasks(true);
  `);
  const warnCard = d.querySelector('#t-tasks .card[data-open="task:0123-warn"]');
  const cleanCard = d.querySelector('#t-tasks .card[data-open="task:0123-clean"]');
  const blkCard = d.querySelector('#t-tasks .card[data-open="task:0123-blk"]');
  const warnBadge = warnCard ? warnCard.querySelector('.badge.warn') : null;
  const cleanBadge = cleanCard ? cleanCard.querySelector('.badge.warn') : null;
  const blkBadge = blkCard ? blkCard.querySelector('.badge.warn') : null;
  const boardWarnOk = !!warnBadge && warnBadge.textContent.includes('crashing repeatedly - 3/5 consecutive orphan recoveries') &&
                      !cleanBadge && !blkBadge;
  check('tasks: orphan warning badge shown on board card when orphan_warning is true with dynamic streak count', boardWarnOk);

  // Detail sheet orphan warning
  w.eval(`
    TD.id = '0123-warn';
    TD.data = {task: {id: '0123-warn', goal: 'G1', title: 'Task with orphan warn', role: 'developer', status: 'pending', orphan_warning: true, orphan_streak: 3, orphan_limit: 5}};
    SHEET.kind = 'task';
    renderTaskSheet();
  `);
  const sheetWarnBadge = d.querySelector('#sheet .sub .badge.warn');
  const sheetWarnOk = !!sheetWarnBadge && sheetWarnBadge.textContent.includes('crashing repeatedly - 3/5 consecutive orphan recoveries');
  check('tasks: orphan warning badge shown in detail sheet with dynamic streak count', sheetWarnOk);

  w.eval(`
    TD.id = '0123-clean';
    TD.data = {task: {id: '0123-clean', goal: 'G1', title: 'Clean task', role: 'developer', status: 'pending', orphan_warning: false, orphan_streak: 1, orphan_limit: 5}};
    renderTaskSheet();
  `);
  const sheetCleanBadge = d.querySelector('#sheet .sub .badge.warn');
  check('tasks: no orphan warning badge in detail sheet when orphan_warning is false', !sheetCleanBadge);

  w.eval(`
    F.tasks = window._origTasks0123;
    SHEET.kind = null;
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

  // Settings / Workers: task 0128 runtime model fields for OpenCode, Qwen Code, Goose
  click('#settings-seg button[data-v="team"]'); await sleep(800);
  const rtModelFieldConfigs = [
    {id: 'field-opencode-model', key: 'OPENCODE_MODEL', runtime: 'opencode'},
    {id: 'field-qwen-model', key: 'QWEN_MODEL', runtime: 'qwen-code'},
    {id: 'field-goose-model', key: 'GOOSE_MODEL', runtime: 'goose'}
  ];
  const rtModelFieldsOk = rtModelFieldConfigs.every(cfg => {
    const el = d.getElementById(cfg.id);
    return !!el && el.tagName === 'INPUT';
  });
  check('settings / workers: field-opencode-model, field-qwen-model and field-goose-model are present', rtModelFieldsOk);

  const rtModelHelpOk = rtModelFieldConfigs.every(cfg => {
    const el = d.getElementById(cfg.id);
    const help = d.querySelector('[for="' + cfg.id + '"] .help') || (el && el.closest('.setrow') && el.closest('.setrow').querySelector('.help'));
    return !!help && help.textContent.trim().length > 0;
  });
  check('settings / workers: runtime model fields each have a non-empty help text element', rtModelHelpOk);

  const rtModelKeyboardOk = rtModelFieldConfigs.every(cfg => {
    const el = d.getElementById(cfg.id);
    return !!el && el.tabIndex !== -1 && el.getAttribute('tabindex') !== '-1';
  });
  check('settings / workers: runtime model fields are reachable by keyboard in tab order', rtModelKeyboardOk);
  check('settings / workers: field-opencode-model, field-qwen-model and field-goose-model present, with help text, and reachable by keyboard',
        rtModelFieldsOk && rtModelHelpOk && rtModelKeyboardOk);

  const rtModelReadinessOk = rtModelFieldConfigs.every(cfg => {
    const el = d.getElementById(cfg.id);
    const badge = el ? (el.parentElement.querySelector('.badge') || (el.closest('.setrow') && el.closest('.setrow').querySelector('.badge'))) : null;
    return !!badge && badge.style.display !== 'none' && badge.textContent.trim().length > 0;
  });
  check('settings / workers: runtime model fields each have a visible readiness indicator element without further interaction', rtModelReadinessOk);

  // Settings / Workers: add-worker runtime picker options for OpenCode, Qwen Code, Goose
  const addwForm = d.getElementById('addw-form');
  if (addwForm && addwForm.style.display === 'none') {
    click('#add-worker');
    await sleep(400);
  }
  const rtPickerConfigs = [
    {id: 'runtime-opt-opencode', val: 'opencode'},
    {id: 'runtime-opt-qwen-code', val: 'qwen-code'},
    {id: 'runtime-opt-goose', val: 'goose'}
  ];
  const rtPickerPresent = rtPickerConfigs.every(cfg => {
    const el = d.getElementById(cfg.id);
    return !!el && el.tagName === 'INPUT' && el.type === 'radio';
  });
  check('settings / workers: add-worker runtime picker exposes selectable elements with ids runtime-opt-opencode, runtime-opt-qwen-code and runtime-opt-goose', rtPickerPresent);

  let rtPickerNoThrow = true;
  let rtPickerUpdatesSelection = true;
  for (const cfg of rtPickerConfigs) {
    const el = d.getElementById(cfg.id);
    if (!el) {
      rtPickerUpdatesSelection = false;
      continue;
    }
    try {
      el.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
    } catch (err) {
      rtPickerNoThrow = false;
      errors.push('Selecting ' + cfg.id + ' threw: ' + (err.message || err));
    }
    const checked = d.querySelector('input[name="addw-rt"]:checked');
    if (!checked || checked.value !== cfg.val || !el.checked) {
      rtPickerUpdatesSelection = false;
    }
  }
  check('settings / workers: selecting each runtime picker option does not throw and updates visible selection',
        rtPickerNoThrow && rtPickerUpdatesSelection);

  if (d.getElementById('addw-cancel')) {
    click('#addw-cancel');
    await sleep(200);
  }

  // Settings / Project: task 0024 fields
  click('#settings-seg button[data-v="project"]'); await sleep(800);
  const projKeys0024 = ['DIFF_MAX', 'MAX_GOAL_ROUNDS', 'LESSONS_MAX', 'LOOP_SEC', 'LOCAL_TZ', 'SHARED_FILES', 'VISION_FILE', 'PROJECT_DIR'];
  const projOk = projKeys0024.every(k => {
    const el = d.getElementById('set-' + k);
    const help = d.querySelector('[for="set-' + k + '"] .help');
    return !!el && !!help && help.textContent.trim().length > 0 && help.textContent.toLowerCase().includes('applies live');
  });
  check('settings / project: task 0024 fields present as inputs with help stating live reload', projOk);

  // Settings / Project: task 0119 orphan settings fields
  const orphanKeys = ['ORPHAN_LIMIT', 'ORPHAN_WARN'];
  const orphanFieldsOk = orphanKeys.every(k => {
    const el = d.getElementById('set-' + k);
    const help = d.querySelector('[for="set-' + k + '"] .help');
    return !!el && el.tagName === 'INPUT' && el.type === 'number' &&
      !!help && help.textContent.trim().length > 0 && help.textContent.toLowerCase().includes('applies live');
  });
  const orphanHelpWarn = (d.querySelector('[for="set-ORPHAN_WARN"] .help') || {}).textContent || '';
  const orphanHelpLimit = (d.querySelector('[for="set-ORPHAN_LIMIT"] .help') || {}).textContent || '';
  const orphanHelpOk = orphanHelpWarn.includes('before a warning is raised') &&
                       orphanHelpLimit.includes('before the task is blocked');
  const orphanInputs = orphanKeys.map(k => d.getElementById('set-' + k)).filter(Boolean);
  const orphanKeyboardOk = orphanInputs.length === 2 && orphanInputs.every(el =>
    el.tabIndex >= 0 || ['INPUT', 'SELECT', 'BUTTON', 'TEXTAREA'].includes(el.tagName)
  );
  const elLimit = d.getElementById('set-ORPHAN_LIMIT');
  const elWarn = d.getElementById('set-ORPHAN_WARN');
  const orphanDefaultsOk = elLimit && elLimit.value === '5' && elWarn && elWarn.value === '3';
  check('settings / project: ORPHAN_LIMIT and ORPHAN_WARN present as number inputs with help text, defaults, and reachable by keyboard',
        orphanFieldsOk && orphanHelpOk && orphanKeyboardOk && orphanDefaultsOk);

  // Settings / Project: changing ORPHAN_LIMIT or ORPHAN_WARN posts to generic settings endpoint
  let postedBody = null;
  const origFetch = w.fetch;
  w.fetch = async (url, opts) => {
    if (String(url).includes('/api/settings') && opts && opts.method === 'POST') {
      try { postedBody = JSON.parse(opts.body); } catch (e) {}
      return { json: async () => ({ ok: true }) };
    }
    return origFetch(url, opts);
  };
  if (elLimit) elLimit.value = '7';
  await w.saveSettings();
  const limitSaved = postedBody && postedBody.changes && postedBody.changes.ORPHAN_LIMIT === '7';

  postedBody = null;
  if (elWarn) elWarn.value = '4';
  await w.saveSettings();
  const warnSaved = postedBody && postedBody.changes && postedBody.changes.ORPHAN_WARN === '4';
  w.fetch = origFetch;
  check('settings / project: changing ORPHAN_LIMIT and ORPHAN_WARN posts to generic settings endpoint',
        limitSaved && warnSaved);

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

  // Settings / Models: task 0078 provider fields and file-only note
  const oaiRow = d.querySelector('.prov-row[data-p="openai-api"]');
  const geminiRow = d.querySelector('.prov-row[data-p="gemini-api"]');
  const oaiInputs = !!oaiRow && !!oaiRow.querySelector('#set-OPENAI_API_BASE') && !!oaiRow.querySelector('#set-OPENAI_MODEL');
  const geminiInputs = !!geminiRow && !!geminiRow.querySelector('#set-GEMINI_API_BASE') && !!geminiRow.querySelector('#set-GEMINI_MODEL');
  const oaiNote = !!oaiRow && oaiRow.textContent.toLowerCase().includes('file-only') && oaiRow.textContent.includes('bin/secret-set');
  const geminiNote = !!geminiRow && geminiRow.textContent.toLowerCase().includes('file-only') && geminiRow.textContent.includes('bin/secret-set');
  const oaiHelpText = !!oaiRow && !!oaiRow.querySelector('[for="set-OPENAI_API_BASE"] .help') && !!oaiRow.querySelector('[for="set-OPENAI_MODEL"] .help');
  const geminiHelpText = !!geminiRow && !!geminiRow.querySelector('[for="set-GEMINI_API_BASE"] .help') && !!geminiRow.querySelector('[for="set-GEMINI_MODEL"] .help');
  check('settings / models: openai-api and gemini-api rows render with editable base/model inputs, help text, and file-only API-key note',
        oaiInputs && geminiInputs && oaiNote && geminiNote && oaiHelpText && geminiHelpText);

  // Settings / Models: ensure provider setting inputs have unique DOM ids (no duplicates across lists)
  const oaiBaseCount = d.querySelectorAll('#set-OPENAI_API_BASE').length;
  const oaiModelCount = d.querySelectorAll('#set-OPENAI_MODEL').length;
  const geminiBaseCount = d.querySelectorAll('#set-GEMINI_API_BASE').length;
  const geminiModelCount = d.querySelectorAll('#set-GEMINI_MODEL').length;
  check('settings / models: provider inputs have unique DOM ids (no duplicates)',
        oaiBaseCount === 1 && oaiModelCount === 1 && geminiBaseCount === 1 && geminiModelCount === 1);

  // Settings / Models: validation errors display inline next to provider fields
  w.eval(`
    document.querySelectorAll('#settings .seterr.live').forEach(x => x.remove());
    const errs = {OPENAI_MODEL: 'invalid model name', OPENAI_API_BASE: 'invalid URL'};
    for (const [k, msg] of Object.entries(errs)) {
      const el = $('set-' + k);
      if (el) {
        const det = el.closest('details');
        if (det) det.open = true;
        el.insertAdjacentHTML('afterend', '<div class="seterr live">' + esc(msg) + '</div>');
      }
    }
  `);
  const oaiModelErr = d.querySelector('#set-OPENAI_MODEL + .seterr.live');
  const oaiBaseErr = d.querySelector('#set-OPENAI_API_BASE + .seterr.live');
  check('settings / models: validation errors display inline next to provider fields',
        !!oaiModelErr && oaiModelErr.textContent.includes('invalid model name') &&
        !!oaiBaseErr && oaiBaseErr.textContent.includes('invalid URL'));
  w.eval("document.querySelectorAll('#settings .seterr.live').forEach(x => x.remove());");

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

  // Settings / File-only: task 0164 file-only settings subsection
  const fileOnlyFixture = {
    FORGE_URL: 'Install-time internal git forge URL; set in etc/forge.conf and requires restart.',
    FORGE_REPO: 'Install-time default repository; set in etc/forge.conf and requires restart.'
  };
  w.eval(`
    window._origSFileOnly = S;
    S = Object.assign({}, S, {
      file_only: ` + JSON.stringify(fileOnlyFixture) + `
    });
    renderSettings(true);
  `);
  const fileOnlyEntries = Object.entries(fileOnlyFixture);
  const fileOnlyElementsOk = fileOnlyEntries.length > 0 && fileOnlyEntries.every(([k, reason]) => {
    const el = d.querySelector('.setting-file-only[data-key="' + k + '"]');
    return !!el && el.textContent.includes(reason);
  });
  const fileOnlyCountOk = d.querySelectorAll('.setting-file-only').length === fileOnlyEntries.length;
  check('settings: file-only subsection renders .setting-file-only[data-key="<KEY>"] with reason text for each entry',
        fileOnlyElementsOk && fileOnlyCountOk);

  // Settings / File-only: absence when file_only is empty
  w.eval(`
    S = Object.assign({}, S, {file_only: {}});
    renderSettings(true);
  `);
  const fileOnlyEmptyOk = d.querySelectorAll('.setting-file-only').length === 0;
  check('settings: no .setting-file-only elements present when file_only is empty',
        fileOnlyEmptyOk);

  // Settings / File-only: absence when file_only is absent
  w.eval(`
    S = Object.assign({}, S);
    delete S.file_only;
    renderSettings(true);
  `);
  const fileOnlyAbsentOk = d.querySelectorAll('.setting-file-only').length === 0;
  check('settings: no .setting-file-only elements present when file_only is absent',
        fileOnlyAbsentOk);

  // Restore settings state
  w.eval(`
    S = window._origSFileOnly;
    renderSettings(true);
  `);

  // Settings / Readiness: Runtimes & providers summary
  w.eval(`
    window._origREADINESS = READINESS;
    window._origReadinessErr = readinessErr;
    window._origReadinessBusy = readinessBusy;
    READINESS = {
      runtimes: {
        claude: {state: 'ok', detail: 'Claude Code CLI installed and configured'},
        codex: {state: 'missing_setting', detail: 'OPENAI_API_KEY is not set'}
      },
      providers: {
        'claude-cli': {state: 'ok', detail: 'Claude subscription ready'},
        'gemini-api': {state: 'missing_egress', detail: 'generativelanguage.googleapis.com missing from egress allowlist'}
      }
    };
    readinessErr = null;
    readinessBusy = false;
    readinessAt = Date.now();
    renderSettings(true);
  `);
  const readRows = [...d.querySelectorAll('.readiness-row')];
  const readNames = readRows.map(r => r.dataset.name);
  const expectedNames = ['claude', 'codex', 'claude-cli', 'gemini-api'];
  check('settings: readiness summary renders a row for every runtime/provider in mocked response',
        readRows.length === 4 && expectedNames.every(n => readNames.includes(n)));

  const readyBadges = [...d.querySelectorAll('.readiness-row .badge.ok')].filter(b => b.textContent.includes('Ready'));
  const missingBadges = [...d.querySelectorAll('.readiness-row .badge.warn')].filter(b =>
    b.textContent.includes('Missing setting') || b.textContent.includes('Missing egress')
  );
  check('settings: readiness summary renders visible Ready badge and Missing setting/egress badge text in DOM',
        readyBadges.length > 0 && missingBadges.length > 0);

  const rowClaude = d.querySelector('.readiness-row[data-name="claude"]');
  const rowCodex = d.querySelector('.readiness-row[data-name="codex"]');
  const rowGemini = d.querySelector('.readiness-row[data-name="gemini-api"]');
  const claudeBadge = rowClaude ? rowClaude.querySelector('.badge') : null;
  const codexBadge = rowCodex ? rowCodex.querySelector('.badge') : null;
  const geminiBadge = rowGemini ? rowGemini.querySelector('.badge') : null;
  check('settings: missing_setting and missing_egress visually distinguished from ok by class and text',
        !!claudeBadge && claudeBadge.classList.contains('ok') && claudeBadge.textContent.trim() === 'Ready' &&
        !!codexBadge && codexBadge.classList.contains('warn') && codexBadge.textContent.trim() === 'Missing setting' &&
        !!geminiBadge && geminiBadge.classList.contains('warn') && geminiBadge.textContent.trim() === 'Missing egress');

  // Settings / Readiness: OpenCode, Qwen Code, and Goose summary rows and badges
  w.eval(`
    READINESS = {
      runtimes: {
        opencode: {state: 'ok', detail: 'OpenCode CLI ready'},
        'qwen-code': {state: 'missing_setting', detail: 'qwen.key is not set'},
        goose: {state: 'missing_egress', detail: 'generativelanguage.googleapis.com missing from egress allowlist'}
      },
      providers: {}
    };
    renderSettings(true);
  `);
  const rtSummaryRows = [...d.querySelectorAll('.readiness-row')];
  const rtSummaryNames = rtSummaryRows.map(r => r.dataset.name);
  const expectedRtSummary = ['opencode', 'qwen-code', 'goose'];
  const rtSummaryOk = rtSummaryRows.length === 3 && expectedRtSummary.every(n => rtSummaryNames.includes(n));
  const rowOpencode = d.querySelector('.readiness-row[data-name="opencode"]');
  const rowQwen = d.querySelector('.readiness-row[data-name="qwen-code"]');
  const rowGoose = d.querySelector('.readiness-row[data-name="goose"]');
  const badgeOpencode = rowOpencode ? rowOpencode.querySelector('.badge') : null;
  const badgeQwen = rowQwen ? rowQwen.querySelector('.badge') : null;
  const badgeGoose = rowGoose ? rowGoose.querySelector('.badge') : null;
  const rtSummaryBadgesOk = !!badgeOpencode && badgeOpencode.classList.contains('ok') && badgeOpencode.textContent.trim() === 'Ready' &&
                            !!badgeQwen && badgeQwen.classList.contains('warn') && badgeQwen.textContent.trim() === 'Missing setting' &&
                            !!badgeGoose && badgeGoose.classList.contains('warn') && badgeGoose.textContent.trim() === 'Missing egress';
  check('settings: readiness summary renders opencode, qwen-code, and goose rows with correct status badges',
        rtSummaryOk && rtSummaryBadgesOk);

  // Loading state
  w.eval(`
    READINESS = null;
    readinessErr = null;
    readinessBusy = true;
    renderSettings(true);
  `);
  check('settings: loading placeholder shown before readiness fetch resolves',
        txt('settings').includes('loading…'));

  // Empty state when endpoint returns {}
  w.eval(`
    READINESS = {};
    readinessErr = null;
    readinessBusy = false;
    renderSettings(true);
  `);
  check('settings: readiness summary shows explicit message when endpoint returns empty',
        txt('settings').includes('Readiness check unavailable') && !d.querySelector('.readiness-row'));

  // Error state when endpoint fails
  w.eval(`
    READINESS = null;
    readinessErr = 'HTTP 500';
    readinessBusy = false;
    renderSettings(true);
  `);
  check('settings: readiness summary shows explicit message when endpoint fails',
        txt('settings').includes('Readiness check unavailable') || txt('settings').includes('HTTP 500'));

  // Restore readiness state
  w.eval(`
    READINESS = window._origREADINESS;
    readinessErr = window._origReadinessErr;
    readinessBusy = window._origReadinessBusy;
    renderSettings(true);
  `);

  // Settings / Onboarding: task 0172 onboarding readiness subsection
  const onboardingMock = {
    runtimes: {
      agy: {
        state: 'ok',
        detail: "Runtime 'agy' is ready.",
        missing_settings: [],
        missing_egress: []
      },
      claude: {
        state: 'missing_setting',
        detail: "Runtime 'claude' is missing setting(s): anthropic.key, and egress allowlist for: api.anthropic.com.",
        missing_settings: ['anthropic.key'],
        missing_egress: ['api.anthropic.com']
      },
      codex: {
        state: 'missing_setting',
        detail: "Runtime 'codex' is missing setting(s): openai.key, and egress allowlist for: api.openai.com.",
        missing_settings: ['openai.key'],
        missing_egress: ['api.openai.com']
      },
      gemini: {
        state: 'missing_setting',
        detail: "Runtime 'gemini' is missing setting(s): gemini.key, and egress allowlist for: generativelanguage.googleapis.com.",
        missing_settings: ['gemini.key'],
        missing_egress: ['generativelanguage.googleapis.com']
      },
      compat: {
        state: 'missing_setting',
        detail: "Runtime 'compat' is missing required setting(s): COMPAT_BASE_URL.",
        missing_settings: ['COMPAT_BASE_URL'],
        missing_egress: []
      },
      aider: {
        state: 'missing_setting',
        detail: "Runtime 'aider' is missing required setting(s): COMPAT_BASE_URL.",
        missing_settings: ['COMPAT_BASE_URL'],
        missing_egress: []
      },
      opencode: {
        state: 'missing_setting',
        detail: "Runtime 'opencode' is missing required setting(s): COMPAT_BASE_URL.",
        missing_settings: ['COMPAT_BASE_URL'],
        missing_egress: []
      },
      goose: {
        state: 'missing_setting',
        detail: "Runtime 'goose' is missing required setting(s): COMPAT_BASE_URL.",
        missing_settings: ['COMPAT_BASE_URL'],
        missing_egress: []
      },
      'qwen-code': {
        state: 'missing_setting',
        detail: "Runtime 'qwen-code' is missing required setting(s): qwen.key.",
        missing_settings: ['qwen.key'],
        missing_egress: []
      },
      'anthropic-compat': {
        state: 'missing_setting',
        detail: "Runtime 'anthropic-compat' is missing required setting(s): COMPAT_BASE_URL.",
        missing_settings: ['COMPAT_BASE_URL'],
        missing_egress: []
      }
    },
    providers: {
      'claude-cli': {
        state: 'ok',
        detail: "Provider 'claude-cli' is ready.",
        missing_settings: [],
        missing_egress: []
      },
      'agy-claude': {
        state: 'ok',
        detail: "Provider 'agy-claude' is ready.",
        missing_settings: [],
        missing_egress: []
      },
      'agy-gemini': {
        state: 'ok',
        detail: "Provider 'agy-gemini' is ready.",
        missing_settings: [],
        missing_egress: []
      },
      'anthropic-api': {
        state: 'missing_setting',
        detail: "Provider 'anthropic-api' is missing required setting(s): ANTHROPIC_MODEL, ANTHROPIC_API_KEY.",
        missing_settings: ['ANTHROPIC_MODEL', 'ANTHROPIC_API_KEY'],
        missing_egress: []
      },
      'openai-api': {
        state: 'missing_setting',
        detail: "Provider 'openai-api' is missing setting(s): OPENAI_MODEL, OPENAI_API_KEY, and egress allowlist for: api.openai.com.",
        missing_settings: ['OPENAI_MODEL', 'OPENAI_API_KEY'],
        missing_egress: ['api.openai.com']
      },
      'gemini-api': {
        state: 'missing_egress',
        detail: "Provider 'gemini-api' is missing egress allowlist for: generativelanguage.googleapis.com.",
        missing_settings: [],
        missing_egress: ['generativelanguage.googleapis.com']
      }
    }
  };

  w.eval(`
    window._origONBOARDING = (typeof ONBOARDING !== 'undefined') ? ONBOARDING : null;
    window._origONBOARDING_RECOMMEND = (typeof ONBOARDING_RECOMMEND !== 'undefined') ? ONBOARDING_RECOMMEND : null;
    window._origOnboardingErr = (typeof onboardingErr !== 'undefined') ? onboardingErr : null;
    window._origOnboardingBusy = (typeof onboardingBusy !== 'undefined') ? onboardingBusy : false;
    window._origOnboardingAt = (typeof onboardingAt !== 'undefined') ? onboardingAt : 0;
  `);

  const recommendPickFixture = {
    top: {
      name: 'claude-cli',
      cost_tier: 'subscription',
      reason: "Runtime 'claude-cli' is ready."
    },
    ranked: [
      {
        name: 'claude-cli',
        ready: true,
        cost_tier: 'subscription',
        note: "Runtime 'claude-cli' is ready."
      },
      {
        name: 'codex',
        ready: false,
        cost_tier: 'unknown',
        note: "Runtime 'codex' is missing setting(s): openai.key."
      }
    ],
    message: ''
  };

  const recommendNoneFixture = {
    top: null,
    ranked: [
      {
        name: 'claude-cli',
        ready: false,
        cost_tier: 'subscription',
        note: "Runtime 'claude-cli' is missing subscription."
      },
      {
        name: 'codex',
        ready: false,
        cost_tier: 'unknown',
        note: "Runtime 'codex' is missing setting(s): openai.key."
      }
    ],
    message: 'No runtime passed its readiness check yet.'
  };

  let currentRecommendMock = recommendPickFixture;
  const origFetchOnboarding = w.fetch;
  let onboardingFetchFail = false;
  w.fetch = async (url, opts) => {
    const u = String(url);
    if (u.includes('/api/readiness/recommend')) {
      if (onboardingFetchFail) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: 'HTTP 500' }),
          text: async () => 'HTTP 500'
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => currentRecommendMock,
        text: async () => JSON.stringify(currentRecommendMock)
      };
    }
    if (u.includes('/api/readiness')) {
      if (onboardingFetchFail) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ error: 'HTTP 500' }),
          text: async () => 'HTTP 500'
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => onboardingMock,
        text: async () => JSON.stringify(onboardingMock)
      };
    }
    return origFetchOnboarding(url, opts);
  };

  // Navigate to Settings > Onboarding subsection via existing Settings sub-nav
  click('nav button[data-t="settings"]'); await sleep(400);
  w.eval(`
    if (typeof ensureSettingsNav === 'function') ensureSettingsNav();
  `);
  click('#settings-seg button[data-v="onboarding"]');
  await sleep(1000);

  if (w.eval('typeof ONBOARDING !== "undefined" && ONBOARDING === null && typeof loadOnboarding === "function"')) {
    await w.eval('loadOnboarding(true)');
    await sleep(400);
  }

  // 1. Assert: opening subsection renders one row per mocked runtime and provider entry in #onboarding-readiness-table
  const onbTable = d.getElementById('onboarding-readiness-table');
  const onbRows = [...d.querySelectorAll('#onboarding-readiness-table tbody tr[data-kind][data-name]')];
  const mockRtNames = Object.keys(onboardingMock.runtimes);
  const mockProvNames = Object.keys(onboardingMock.providers);
  const totalMockEntries = mockRtNames.length + mockProvNames.length;
  const rtRowsOk = mockRtNames.every(n => d.querySelector('#onboarding-readiness-table tbody tr[data-kind="runtime"][data-name="' + n + '"]'));
  const provRowsOk = mockProvNames.every(n => d.querySelector('#onboarding-readiness-table tbody tr[data-kind="provider"][data-name="' + n + '"]'));
  const onbRowsCountOk = onbRows.length === totalMockEntries && rtRowsOk && provRowsOk;
  check('settings / onboarding: opening subsection renders one row per mocked runtime and provider entry in #onboarding-readiness-table',
        !!onbTable && onbRowsCountOk);

  // 2. Assert: rendered status text for at least one 'missing'/'untested' case is a human-readable label, not raw status string from mock
  const rawStatusCodes = ['ok', 'missing_setting', 'missing_egress', 'untested'];
  const allOnbBadges = [...d.querySelectorAll('#onboarding-readiness-table tbody tr .badge')];
  const allBadgesNonEmpty = allOnbBadges.length > 0 && allOnbBadges.every(b => b.textContent.trim().length > 0);
  const noRawStatusCodes = allBadgesNonEmpty && allOnbBadges.every(b => {
    const t = b.textContent.trim();
    return !rawStatusCodes.includes(t.toLowerCase()) && !t.includes('_');
  });
  const claudeRow = d.querySelector('#onboarding-readiness-table tr[data-kind="runtime"][data-name="claude"]');
  const claudeBadge = claudeRow ? claudeRow.querySelector('.badge') : null;
  const claudeStatusText = claudeBadge ? claudeBadge.textContent.trim() : '';
  const claudeNotRaw = claudeStatusText.length > 0 &&
    claudeStatusText !== 'missing_setting' &&
    !claudeStatusText.includes('_') &&
    /^[A-Z]/.test(claudeStatusText);
  const geminiProvRow = d.querySelector('#onboarding-readiness-table tr[data-kind="provider"][data-name="gemini-api"]');
  const geminiProvBadge = geminiProvRow ? geminiProvRow.querySelector('.badge') : null;
  const geminiProvStatusText = geminiProvBadge ? geminiProvBadge.textContent.trim() : '';
  const geminiNotRaw = geminiProvStatusText.length > 0 &&
    geminiProvStatusText !== 'missing_egress' &&
    !geminiProvStatusText.includes('_') &&
    /^[A-Z]/.test(geminiProvStatusText);
  const statusLabelsHumanReadable = noRawStatusCodes && claudeNotRaw && geminiNotRaw;
  check('settings / onboarding: rendered status text for missing cases is human-readable label, not raw status code',
        statusLabelsHumanReadable);

  // 3. Assert: the text 'CHOOSING-AN-AI.md' is present somewhere in the subsection
  const choosingAiPresent = txt('settings').includes('CHOOSING-AN-AI.md');
  check('settings / onboarding: CHOOSING-AN-AI.md text is present in the subsection',
        choosingAiPresent);

  // 4. Assert: a Tab key sequence (or focus-walking helper) can reach table/rows, proving keyboard reachability
  const onbNavBtn = d.querySelector('#settings-seg button[data-v="onboarding"]');
  const onbTableReached = !!onbNavBtn && !!onbTable && onbRows.length > 0 &&
                          walkFocus(onbNavBtn, [onbTable, ...onbRows]) &&
                          d.activeElement === onbRows[onbRows.length - 1];
  check('settings / onboarding: Tab key sequence reaches table/rows proving keyboard reachability',
        onbTableReached);

  // Task 0185: Onboarding recommendation widget (Fixture 1: has top pick)
  const recWidget = d.getElementById('onboarding-recommend');
  check('settings / onboarding: #onboarding-recommend container element is rendered',
        !!recWidget);

  // Assert .recommend-top is present, .recommend-none is absent (mutually exclusive)
  const recTop = d.querySelector('#onboarding-recommend .recommend-top');
  const recNone = d.querySelector('#onboarding-recommend .recommend-none');
  const topPickExclusivityOk = !!recTop && !recNone;
  check('settings / onboarding (top pick): .recommend-top is present and .recommend-none is absent',
        topPickExclusivityOk);

  // Assert top runtime name, tier, and reason appear in visible text and structured attributes
  const recTopTxt = recTop ? recTop.textContent : '';
  const topNameInText = recTopTxt.includes(recommendPickFixture.top.name);
  const topTierInText = recTopTxt.includes(recommendPickFixture.top.cost_tier);
  const topReasonInText = recTopTxt.includes(recommendPickFixture.top.reason);
  const topNameEl = recTop ? recTop.querySelector('[data-runtime-name]') : null;
  const topTierEl = recTop ? recTop.querySelector('[data-cost-tier]') : null;
  const topReasonEl = recTop ? recTop.querySelector('[data-reason]') : null;
  const topStructureOk = !!topNameEl && topNameEl.textContent === recommendPickFixture.top.name &&
                         !!topTierEl && topTierEl.textContent === recommendPickFixture.top.cost_tier &&
                         !!topReasonEl && topReasonEl.textContent === recommendPickFixture.top.reason;
  check('settings / onboarding (top pick): top runtime name, tier, and reason appear in visible text and structured attributes',
        topNameInText && topTierInText && topReasonInText && topStructureOk);

  // Assert ranked entries count and each data-ready attribute matches fixture ready value in order
  const pickRankedLis = [...d.querySelectorAll('#onboarding-recommend .recommend-ranked li')];
  const pickRankedReadyLis = [...d.querySelectorAll('#onboarding-recommend .recommend-ranked li[data-ready]')];
  const pickRankedCountOk = pickRankedLis.length === recommendPickFixture.ranked.length && pickRankedLis.length > 0;
  const pickRankedAllHaveReadyAttr = pickRankedReadyLis.length === pickRankedLis.length;
  const pickRankedDataReadyOk = pickRankedCountOk && pickRankedAllHaveReadyAttr && recommendPickFixture.ranked.every(r => {
    const li = d.querySelector('#onboarding-recommend .recommend-ranked li[data-runtime="' + r.name + '"]');
    return !!li && li.getAttribute('data-ready') === String(r.ready);
  });
  const pickRankedOrderOk = pickRankedCountOk &&
    pickRankedLis[0].getAttribute('data-runtime') === recommendPickFixture.ranked[0].name &&
    pickRankedLis[0].getAttribute('data-ready') === String(recommendPickFixture.ranked[0].ready) &&
    pickRankedLis[1].getAttribute('data-runtime') === recommendPickFixture.ranked[1].name &&
    pickRankedLis[1].getAttribute('data-ready') === String(recommendPickFixture.ranked[1].ready);
  check('settings / onboarding (top pick): ranked entries data-ready attribute matches fixture ready value for each runtime in order',
        pickRankedDataReadyOk && pickRankedOrderOk);

  // Assert recommendation widget and ranked items are reachable by keyboard/screen order consistent with Onboarding subsection
  const recFocusTargets = [recWidget, recTop, ...pickRankedLis].filter(Boolean);
  const recFocusStart = (onbRows && onbRows.length > 0 && d.activeElement === onbRows[onbRows.length - 1])
    ? onbRows[onbRows.length - 1]
    : (onbTable || onbNavBtn);
  const recFocusOk = !!recWidget && pickRankedLis.length > 0 &&
                     walkFocus(recFocusStart, recFocusTargets) &&
                     d.activeElement === pickRankedLis[pickRankedLis.length - 1];
  check('settings / onboarding (top pick): recommendation widget and ranked entries are reachable in keyboard/screen order via focus walking',
        recFocusOk);

  // Task 0185: Onboarding recommendation widget (Fixture 2: no runtime ready, top is null)
  currentRecommendMock = recommendNoneFixture;
  w.eval(`
    if (typeof ONBOARDING !== 'undefined') ONBOARDING = null;
    if (typeof ONBOARDING_RECOMMEND !== 'undefined') ONBOARDING_RECOMMEND = null;
    if (typeof onboardingErr !== 'undefined') onboardingErr = null;
    if (typeof onboardingBusy !== 'undefined') onboardingBusy = false;
    if (typeof onboardingAt !== 'undefined') onboardingAt = 0;
  `);
  if (w.eval('typeof loadOnboarding === "function"')) {
    await w.eval('loadOnboarding(true)');
    await sleep(400);
  }

  // Assert .recommend-none is present, .recommend-top is absent (mutually exclusive)
  const noneRecTop = d.querySelector('#onboarding-recommend .recommend-top');
  const noneRecNone = d.querySelector('#onboarding-recommend .recommend-none');
  const noneExclusivityOk = !noneRecTop && !!noneRecNone;
  check('settings / onboarding (no ready runtime): .recommend-none is present and .recommend-top is absent',
        noneExclusivityOk);

  // Assert .recommend-none shows the server's message text verbatim
  const noneMsgOk = !!noneRecNone && noneRecNone.textContent.trim() === recommendNoneFixture.message;
  check('settings / onboarding (no ready runtime): .recommend-none displays the server message text verbatim',
        noneMsgOk);

  // Assert every .recommend-ranked li has data-ready="false" and matches fixture ready values in order
  const noneRankedLis = [...d.querySelectorAll('#onboarding-recommend .recommend-ranked li')];
  const noneRankedReadyLis = [...d.querySelectorAll('#onboarding-recommend .recommend-ranked li[data-ready]')];
  const noneRankedCountOk = noneRankedLis.length === recommendNoneFixture.ranked.length && noneRankedLis.length > 0;
  const noneRankedAllHaveReadyAttr = noneRankedReadyLis.length === noneRankedLis.length;
  const noneRankedAllFalse = noneRankedCountOk && noneRankedAllHaveReadyAttr && noneRankedLis.every(li => li.getAttribute('data-ready') === 'false');
  const noneRankedMatchesFixture = noneRankedCountOk && recommendNoneFixture.ranked.every(r => {
    const li = d.querySelector('#onboarding-recommend .recommend-ranked li[data-runtime="' + r.name + '"]');
    return !!li && li.getAttribute('data-ready') === String(r.ready);
  });
  check('settings / onboarding (no ready runtime): every ranked entry has data-ready="false" matching fixture ready value in order',
        noneRankedAllFalse && noneRankedMatchesFixture);

  // Task 0184: Onboarding recommendation widget (Fixture 3: top is an object with falsy-but-valid fields)
  w.eval(`
    ONBOARDING_RECOMMEND = {
      top: {name: 'claude-cli', cost_tier: '', reason: ''},
      ranked: [{name: 'claude-cli', ready: true, cost_tier: '', note: ''}],
      message: 'should be ignored'
    };
    renderSettings(true);
  `);
  const falsyRecTop = d.querySelector('#onboarding-recommend .recommend-top');
  const falsyRecNone = d.querySelector('#onboarding-recommend .recommend-none');
  const falsyTopNameEl = falsyRecTop ? falsyRecTop.querySelector('[data-runtime-name]') : null;
  const falsyFieldsOk = !!falsyRecTop && !falsyRecNone && !!falsyTopNameEl && falsyTopNameEl.textContent === 'claude-cli';
  check('settings / onboarding (falsy fields): .recommend-top renders correctly when data.top has falsy-but-valid fields',
        falsyFieldsOk);

  // 5. Assert: mocked fetch failure for /api/readiness produces visible error state (toast text or error row), not blank subsection
  onboardingFetchFail = true;
  w.eval(`
    if (typeof ONBOARDING !== 'undefined') ONBOARDING = null;
    if (typeof ONBOARDING_RECOMMEND !== 'undefined') ONBOARDING_RECOMMEND = null;
    if (typeof onboardingErr !== 'undefined') onboardingErr = null;
    if (typeof onboardingBusy !== 'undefined') onboardingBusy = false;
    if (typeof onboardingAt !== 'undefined') onboardingAt = 0;
  `);
  click('#settings-seg button[data-v="onboarding"]');
  await sleep(1000);
  if (w.eval('typeof ONBOARDING !== "undefined" && ONBOARDING === null && typeof loadOnboarding === "function"')) {
    try { await w.eval('loadOnboarding(true)'); } catch (e) {}
    await sleep(400);
  }
  const onbErrorRow = d.querySelector('#onboarding-readiness-table tr.error-row');
  const onbToastHasError = txt('toasts').includes('Could not load readiness');
  const onbVisibleError = txt('settings').includes('Could not load readiness') || (onbErrorRow && onbErrorRow.textContent.includes('Could not load readiness'));
  const onbSubsectionNotBlank = txt('settings').trim().length > 30;
  const onbErrorStateOk = (!!onbErrorRow || onbToastHasError || onbVisibleError) && onbSubsectionNotBlank;
  check('settings / onboarding: mocked fetch failure produces visible error state (toast or error row), not a blank subsection',
        onbErrorStateOk);

  // Restore onboarding state and fetch
  w.fetch = origFetchOnboarding;
  w.eval(`
    if (typeof ONBOARDING !== 'undefined') ONBOARDING = window._origONBOARDING;
    if (typeof ONBOARDING_RECOMMEND !== 'undefined') ONBOARDING_RECOMMEND = window._origONBOARDING_RECOMMEND;
    if (typeof onboardingErr !== 'undefined') onboardingErr = window._origOnboardingErr;
    if (typeof onboardingBusy !== 'undefined') onboardingBusy = window._origOnboardingBusy;
    if (typeof onboardingAt !== 'undefined') onboardingAt = window._origOnboardingAt;
    SUB.settings = 'team';
    renderSettings(true);
  `);

  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no JS errors');
  console.log('ui tests: ' + (failed || errors.length ? 'FAILED' : 'ALL OK'));
  w.close(); process.exit(failed || errors.length ? 1 : 0);
})().catch(e => { console.log('crash', e); console.log(errors.join('\n')); process.exit(4); });
