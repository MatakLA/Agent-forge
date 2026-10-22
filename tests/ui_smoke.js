// UI smoke test (tests/test_ui.sh runs it): the real page on the demo state, in jsdom, in polling mode - every tab and
// sub-view renders, the overview controls (autopilot and foreman pause switches) match the fixture state, each kind of sheet
// opens (task, goal, run replay, search, document), the graph finds the critical path, and the Settings pages work: eight
// pages with their own content, one Save bar (unsaved edits survive a redraw, a refused value is marked at its field),
// Remove / Add worker / New role, keys typed into a password field, and keyboard reachability. No script error may happen on
// the way. Exit code 0 = all ok.
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
  const dom = await JSDOM.fromURL(base + '#tab=overview&poll=1', {runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc, beforeParse(win) { win.fetch = (u, o) => fetch(new URL(u, base), o); win.EventSource = undefined; win.HTMLCanvasElement.prototype.getContext = () => null; }});
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
    window._origOverviewState = {g: F.goals, bl: F.backlog, onb: F.onboarding, ts: F.tasks, oq: F.owner_questions, ks: F.killswitch};
    F.goals = []; F.backlog = []; F.onboarding = []; F.tasks = []; F.owner_questions = []; F.killswitch = null;
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
    F.owner_questions = window._origOverviewState.oq; F.killswitch = window._origOverviewState.ks;
    renderOverview(true);
  `);
  // backlog drag-and-drop: a drag the browser cancels, or another finger lifting, must not reorder the backlog later
  {
    w.Element.prototype.setPointerCapture = function () {};
    const realFetch = w.fetch, posts = [];
    w.fetch = (u, o) => { if (o && o.method === 'POST') posts.push(String(u)); return realFetch(u, o); };
    w.eval('window._bl = F.backlog');
    const seed = () => w.eval("F.backlog = ['alpha', 'beta', 'gamma']; renderOverview(true)");
    const pe = (type, y, id) => { const e = new w.MouseEvent(type, {bubbles: true, cancelable: true, clientY: y}); Object.defineProperty(e, 'pointerId', {value: id}); return e; };
    const rows = () => [...d.querySelectorAll('#bl-list .blrow')], handle = () => d.querySelector('#bl-list .blrow .drag');
    const moves = () => posts.filter(u => u.includes('/api/backlog')).length;
    seed();
    check('backlog drag: the backlog is drawn with a handle per row', rows().length === 3 && d.querySelectorAll('#bl-list .drag').length === 3);
    handle().dispatchEvent(pe('pointerdown', 100, 1)); handle().dispatchEvent(pe('pointermove', 112, 1));
    check('backlog drag: a handle starts a drag and the row follows the pointer', rows()[0].classList.contains('dragging') && rows()[0].style.transform === 'translateY(12px)');
    handle().dispatchEvent(pe('pointercancel', 112, 1));
    check('backlog drag: a drag the browser cancels puts the rows back', !d.querySelector('#bl-list .dragging') && rows().every(r => !r.style.transform));
    d.body.dispatchEvent(pe('pointermove', 130, 1)); d.body.dispatchEvent(pe('pointerup', 130, 1));
    await sleep(150);
    check('backlog drag: a click after a cancelled drag does not reorder the backlog', moves() === 0 && !d.querySelector('#bl-list .dragging'));
    seed();
    handle().dispatchEvent(pe('pointerdown', 100, 1)); handle().dispatchEvent(pe('pointermove', 112, 1));
    d.body.dispatchEvent(pe('pointerup', 112, 2));
    check('backlog drag: another finger lifting does not end the drag', rows()[0].classList.contains('dragging') && moves() === 0);
    handle().dispatchEvent(pe('pointerup', 112, 1));
    await sleep(150);
    check('backlog drag: letting go moves the item once', moves() === 1 && !d.querySelector('#bl-list .dragging'));
    w.fetch = realFetch;
    w.eval("F.backlog = window._bl; renderOverview(true)");
  }
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
  // Enter / Space / a click on a control: its sheet opens once (one load), and no page error follows (an SVG bar has no click())
  const fetched = [], pageFetch = w.fetch;
  w.fetch = (u, o) => { fetched.push(String(u)); return pageFetch(u, o); };
  const press = (el, key) => { el.focus(); el.dispatchEvent(new w.KeyboardEvent('keydown', {key, bubbles: true, cancelable: true})); };
  const clickEl = el => el.dispatchEvent(new w.MouseEvent('click', {bubbles: true}));
  const openOnce = async (name, kind, api, fire) => {
    const e0 = errors.length, n0 = fetched.filter(u => u.includes(api)).length, b0 = txt('jserr');
    fire(); await sleep(400);
    const loads = fetched.filter(u => u.includes(api)).length - n0, got = w.eval('SHEET.kind');
    const ok = got === kind && loads === 1 && txt('jserr') === b0 && errors.length === e0;
    check(name, ok);
    if (!ok) console.log('     sheet=' + got + ' loads=' + loads + ' banner=' + txt('jserr') + ' errors=' + errors.slice(e0).join(' | '));
    w.closeSheet();
  };
  const openBy = (name, el, fire) => { const kind = el.dataset.open.split(':')[0]; return openOnce(name, kind, '/api/' + kind + '?', fire); };
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
  const srMarks = [...d.querySelectorAll('#sr-res mark')];
  check('search: the hits are highlighted, and nothing but the hit',
        srMarks.length > 0 && srMarks.every(m => m.textContent.toLowerCase() === 'due'));
  q.value = 'xyznonexistentword'; q.dispatchEvent(new w.Event('input', {bubbles: true}));
  for (let i = 0; i < 40 && !txt('sheet').includes('No results for'); i++) await sleep(250);
  check('search: explicit empty state for zero matches',
        txt('sheet').includes('No results for'));
  {
    const find = async () => { w.openSearch(); const f = d.getElementById('sr-q'); f.value = 'due'; f.dispatchEvent(new w.Event('input', {bubbles: true}));
      for (let i = 0; i < 40 && !d.querySelector('.sr[data-open]'); i++) await sleep(250); return d.querySelector('.sr[data-open]'); };
    let row = await find();
    if (row) await openBy('search: Enter on a result opens it once', row, () => press(row, 'Enter'));
    row = await find();
    if (row) await openBy('search: a click on a result opens it once', row, () => clickEl(row));
  }
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
  if (tlBars.length) {
    await openBy('timeline: Enter on a bar opens it once, with no page error', tlBars[0], () => press(tlBars[0], 'Enter'));
    await openBy('timeline: Space on a bar opens it once, with no page error', tlBars[0], () => press(tlBars[0], ' '));
  }
  w.eval("SUB.tasks = 'board'"); w.setTab('tasks'); await sleep(800);
  const taskCards = [...d.querySelectorAll('#t-tasks .card')];
  check('tasks: board cards are keyboard reachable',
        taskCards.length > 0 && taskCards.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  if (taskCards.length) {
    await openBy('tasks: Enter on a board card opens it once', taskCards[0], () => press(taskCards[0], 'Enter'));
    await openBy('tasks: a click on a board card opens it once', taskCards[0], () => clickEl(taskCards[0]));
  }
  {
    const holder = d.createElement('div');
    holder.innerHTML = '<table><tr data-open="task:0001-x" role="button" tabindex="0"><td><a href="https://example.test/pr/1" id="kb-link">#1</a> <button id="kb-btn">retry</button></td></tr></table>';
    d.body.appendChild(holder);
    const keyOn = id => { const el = d.getElementById(id), ev = new w.KeyboardEvent('keydown', {key: 'Enter', bubbles: true, cancelable: true}); el.focus(); el.dispatchEvent(ev); return ev.defaultPrevented; };
    const kept = !keyOn('kb-link') && !keyOn('kb-btn');
    check('a link or button inside a clickable row keeps its own Enter', kept && w.eval('SHEET.kind') !== 'task');
    d.getElementById('kb-link').addEventListener('click', ev => ev.preventDefault());
    const kind0 = w.eval('SHEET.kind'), t0 = fetched.filter(u => u.includes('/api/task?')).length;
    d.getElementById('kb-link').dispatchEvent(new w.MouseEvent('click', {bubbles: true, cancelable: true})); clickEl(d.getElementById('kb-btn')); await sleep(300);
    check('a click on a link or button inside a clickable row stays with it',
          w.eval('SHEET.kind') === kind0 && fetched.filter(u => u.includes('/api/task?')).length === t0);
    holder.remove();
  }
  const boardCols = [...d.querySelectorAll('#t-tasks .col')];
  check('tasks: board header task count matches rendered cards for each column',
        boardCols.length > 0 && boardCols.every(col => {
          const m = (col.querySelector('h3 .muted') || {}).textContent || '';
          const rendered = col.querySelectorAll('.card').length;
          const num = parseInt(m.split(' ')[0], 10);
          return num === rendered;
        }));
  w.eval("SUB.tasks = 'graph'"); w.setTab('tasks'); await sleep(800);
  check('graph shows the critical path', txt('tasks-view').includes('Critical path'));
  const graphNodes = [...d.querySelectorAll('.dag g[data-open]')];
  check('tasks: graph nodes are keyboard reachable',
        graphNodes.length > 0 && graphNodes.every(el => el.getAttribute('role') === 'button' && el.tabIndex === 0));
  if (graphNodes.length) await openBy('tasks: Enter on a graph node opens it once, with no page error', graphNodes[0], () => press(graphNodes[0], 'Enter'));
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
    SHEET.kind = 'task'; SHEET.id = '0123-warn';
    renderTaskSheet();
  `);
  const sheetWarnBadge = d.querySelector('#sheet .sub .badge.warn');
  const sheetWarnOk = !!sheetWarnBadge && sheetWarnBadge.textContent.includes('crashing repeatedly - 3/5 consecutive orphan recoveries');
  check('tasks: orphan warning badge shown in detail sheet with dynamic streak count', sheetWarnOk);

  w.eval(`
    TD.id = '0123-clean';
    TD.data = {task: {id: '0123-clean', goal: 'G1', title: 'Clean task', role: 'developer', status: 'pending', orphan_warning: false, orphan_streak: 1, orphan_limit: 5}};
    SHEET.id = '0123-clean';
    renderTaskSheet();
  `);
  const sheetCleanBadge = d.querySelector('#sheet .sub .badge.warn');
  check('tasks: no orphan warning badge in detail sheet when orphan_warning is false', !sheetCleanBadge);

  w.eval(`
    F.tasks = window._origTasks0123;
    SHEET.kind = null; SHEET.id = null;
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
  // a worker may ask the proxy for any name, e.g. one that is a member of Object.prototype: the tab must still draw and tell the hosts apart
  const protoHosts = '{"health":{"ok":true,"mode":"enforce"},"allowed":{},"denied":{"constructor":2,"toString":1,"__proto__":4,"pypi.org":3},"noise":{},' +
    '"new_domains":{"worker-2 __proto__":4},"private":[],"alarms":[],"local_allowlist":[],"baseline":[],"killswitch":null}';
  let protoErr = '';
  try { w.eval(`SYS.egress = JSON.parse(${JSON.stringify(protoHosts)}); SYS.egressAt = Date.now(); renderSystem(true);`); } catch (e) { protoErr = String(e); }
  const protoRow = h => [...d.querySelectorAll('#system tr')].find(tr => (tr.querySelector('.mono') || {}).textContent === h);
  const protoState = h => { const tr = protoRow(h); return tr ? tr.children[1].textContent + '|' + tr.children[3].textContent : 'missing'; };
  const protoOk = !protoErr && txt('system').includes('Blocked (4)') && protoState('__proto__') === 'worker-2|alarm' && protoState('constructor') === '–|acknowledged' &&
    protoState('toString') === '–|acknowledged' && protoState('pypi.org') === '–|acknowledged';
  check('egress: hosts named like Object.prototype members are drawn, and only the one with an alarm raises it', protoOk);
  if (!protoOk) console.log('     ' + (protoErr || ['__proto__', 'constructor', 'toString', 'pypi.org'].map(protoState).join(' ')));
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
  {
    const det = d.getElementById('all-checks'), sm = det && det.querySelector('summary'), e0 = errors.length, b0 = txt('jserr');
    if (sm) {
      const was = det.open;
      press(sm, 'Enter'); const afterEnter = det.open;
      press(sm, ' ');
      check('system: Enter opens and Space closes "All checks" (once each)', afterEnter === !was && det.open === was && txt('jserr') === b0 && errors.length === e0);
    }
  }
  // drawing the page starts the load itself, so "nothing yet" shows as "Checking the install…" (never a blank pane)
  w.eval(`SYS.health = null; SYS.err.health = null; SYS.healthBusy = true; renderSystem(true);`);
  check('system: health view shows an explicit state (not a blank pane) while there is no answer yet',
    /Checking the install|Health check not yet run/.test(txt('system')));
  w.eval(`SYS.health = null; SYS.err.health = 'boom'; SYS.healthBusy = true; renderSystem(true);`);
  check('system: health view says so when the check failed', txt('system').includes('Health check failed: boom'));
  w.eval(`SYS.err.health = null; SYS.healthBusy = false;`);
  check('no page error shown', !txt('jserr'));
  click('nav button[data-t="settings"]'); await sleep(800);
  // ---- Settings: eight pages, each with its own content; one Save bar; Remove / add worker / add role; goto
  {
    const S_ = w.eval('S'), pages = w.eval('SPAGES'), PK = w.eval('PAGEKEYS');
    const sgo = async pg => { w.eval("setSub('settings','" + pg + "')"); await sleep(700); };
    const stxt = () => (d.getElementById('settings') || {}).textContent || '';
    const calls = [], realFetch = w.fetch;
    const stub = (re, answer) => { w.fetch = (u, o) => { if (re.test(String(u)) && o && o.method === 'POST') { calls.push({url: String(u), body: o.body ? JSON.parse(o.body) : null}); return Promise.resolve({json: async () => answer(calls[calls.length - 1])}); } return realFetch(u, o); }; };
    const unstub = () => { w.fetch = realFetch; calls.length = 0; };
    const fire = (el, type) => el.dispatchEvent(new w.Event(type, {bubbles: true}));
    const marks = {workers: 'Kill switch', runtimes: 'API keys', models: 'Who plans and reviews', project: 'Notifications', money: 'Budgets', knowledge: 'Roles', limits: 'Limits', advanced: 'Rhythm and task size'};
    check('settings: the default page is Workers and there are eight pages', w.eval('SPAGES.length') === 8 && pages[0] === 'workers' && d.querySelectorAll('#settings-seg button').length === 8);
    w.eval("S.values.FORGE_KIND = 'github'; S.values.UPSTREAM_REPO = 'me/app'");
    for (const pg of pages) {
      await sgo(pg);
      const t = stxt();
      check('settings / ' + pg + ': shows its own content, no leftover readiness cards', t.includes(marks[pg]) && !/Runtime readiness|Onboarding|Provider readiness/.test(t));
      const missing = (PK[pg] || '').split(' ').filter(Boolean).filter(k => !d.getElementById('set-' + k));
      check('settings / ' + pg + ': every setting of the page has a control (missing: ' + missing.join(' ') + ')', !missing.length);
      const raw = [...d.querySelectorAll('#settings .setrow > label')].map(l => l.firstChild && l.firstChild.textContent.trim()).filter(x => /^[A-Z][A-Z0-9_]{3,}$/.test(x));
      check('settings / ' + pg + ': no setting is labelled with its raw key name (' + raw.join(' ') + ')', !raw.length);
    }
    const noHelp = Object.values(PK).join(' ').split(' ').filter(k => !w.eval('HELP')[k] || !w.eval('LABEL')[k]);
    check('settings: every setting has a label and a description (' + noHelp.join(' ') + ')', !noHelp.length);
    check('settings: no description claims "applies live" (it is not true of most of them)', !Object.values(w.eval('HELP')).some(h => /applies live/i.test(h)));

    // workers: Remove, add picker only offers what can really be created
    await sgo('workers');
    const nW = (S_.workers || []).length;
    check('settings / workers: every worker has a Remove button', nW > 0 && d.querySelectorAll('[data-wremove]').length === nW);
    const creatable = Object.keys(S_.runtimes).filter(k => S_.runtimes[k].creatable).sort();
    d.getElementById('add-worker').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(500);
    const offered = [...d.querySelectorAll('input[name="addw-rt"]')].map(i => i.value).sort();
    check('settings / workers: the picker offers exactly the runtimes the server can create (' + offered.join(',') + ')', offered.length > 0 && JSON.stringify(offered) === JSON.stringify(creatable));
    d.getElementById('addw-cancel').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(400);
    const victim = d.querySelector('[data-wremove]').dataset.wremove;
    stub(/\/api\/workers/, () => ({id: 'j1', state: 'done', worker: victim}));
    w.confirm = () => false; d.querySelector('[data-wremove]').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(400);
    check('settings / workers: Remove asks first and does nothing when declined', !calls.length);
    w.confirm = () => true; d.querySelector('[data-wremove]').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(1500);
    check('settings / workers: Remove posts the removal of that worker', calls.length === 1 && calls[0].body.action === 'remove' && calls[0].body.worker === victim);
    unstub();

    // a second click on "Add the worker" while the first is still working must not add a second one
    const addsPosted = () => calls.filter(c => /\/api\/workers/.test(c.url) && c.body && c.body.action === 'add').length;
    stub(/\/api\/(settings|workers)/, c => /\/api\/settings/.test(c.url) ? {ok: true} : {state: 'failed', error: 'stubbed'});
    clickEl(d.getElementById('add-worker')); await sleep(500);
    const go1 = d.getElementById('addw-go'); clickEl(go1); clickEl(go1); await sleep(900);
    check('settings / workers: a double click on "Add the worker" adds one worker', addsPosted() === 1);
    calls.length = 0;
    clickEl(d.getElementById('add-worker')); await sleep(500);
    const radio = d.querySelector('input[name="addw-rt"][value="compat"]'); radio.checked = true; fire(radio, 'change'); await sleep(600);
    d.getElementById('addw-urlin').value = 'http://192.168.1.20:11434/v1';
    const go2 = d.getElementById('addw-go'); clickEl(go2); clickEl(go2); await sleep(900);
    check('settings / workers: a double click while the model server address is still being saved adds one worker', addsPosted() === 1 && calls.filter(c => /\/api\/settings/.test(c.url)).length === 1);
    unstub();

    stub(/\/api\/killswitch/, () => ({error: 'bad project'}));
    await w.killswitch('on');
    check('settings / workers: a refusal without a step list is shown under the kill switch', calls.length === 1 && /bad project/.test(txt('resume-result')));
    d.getElementById('resume-result').style.display = 'none';
    unstub();

    // knowledge: a new role can be added; an existing name is refused
    await sgo('knowledge'); await sleep(900);
    check('settings / knowledge: there is a button to add a role', !!d.getElementById('new-role'));
    const docRow = d.querySelector('#settings .docrow[data-doc]');
    if (docRow) await openOnce('settings / knowledge: Enter on a document row opens it once', 'doc', '/api/doc?', () => press(docRow, 'Enter'));
    d.getElementById('new-role').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(400);
    const nm = d.getElementById('nr-name'), ms = d.getElementById('nr-mission');
    nm.value = 'tester'; ms.value = 'x'; d.getElementById('nr-create').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(300);
    check('settings / knowledge: a role that exists is refused with a message', /already exists/.test(txt('nr-err')));
    stub(/\/api\/doc$/, () => ({ok: true}));
    nm.value = 'Data Admin'; ms.value = 'keep the schema safe'; d.getElementById('nr-create').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(900);
    check('settings / knowledge: the new role is saved as role:data-admin with its mission', calls.some(c => c.body && c.body.name === 'role:data-admin' && /\*\*Mission:\*\* keep the schema safe/.test(c.body.text)));
    unstub(); w.eval('closeSheet()');

    // the Save bar: edits from several pages are collected, survive a redraw and a page change, can be discarded or saved
    await sgo('project');
    const bar = d.getElementById('savebar'), tz = d.getElementById('set-LOCAL_TZ'), orig = tz.value;
    check('settings: the Save bar is hidden while nothing is edited', bar.hidden);
    tz.value = 'Europe/Berlin'; fire(tz, 'input');
    check('settings: editing shows the Save bar with the count', !bar.hidden && /1 unsaved change/.test(txt('save-count')));
    w.eval('schedule(true)'); await sleep(500);
    check('settings: an unsaved edit survives a redraw', d.getElementById('set-LOCAL_TZ').value === 'Europe/Berlin' && !bar.hidden);
    await sgo('money'); const mo = d.getElementById('set-API_DAILY_BUDGET_USD'); mo.value = '7'; fire(mo, 'input');
    check('settings: edits on two pages count together and name the pages', /2 unsaved changes/.test(txt('save-count')) && /Project/.test(txt('save-count')) && /Money/.test(txt('save-count')));
    await sgo('project');
    check('settings: an unsaved edit survives a change of page', d.getElementById('set-LOCAL_TZ').value === 'Europe/Berlin');
    d.getElementById('discard-settings').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(600);
    check('settings: Discard puts the saved values back and hides the bar', d.getElementById('set-LOCAL_TZ').value === orig && d.getElementById('savebar').hidden);
    const tz2 = d.getElementById('set-LOCAL_TZ'); tz2.value = 'Asia/Tokyo'; fire(tz2, 'input');
    stub(/\/api\/settings/, () => ({ok: false, errors: {LOCAL_TZ: 'unknown time zone'}}));
    d.getElementById('save-settings').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(800);
    check('settings: a refused value is marked at its field, the edit is kept and the bar says why',
          /unknown time zone/.test(txt('err-LOCAL_TZ')) && d.getElementById('set-LOCAL_TZ').value === 'Asia/Tokyo' && !d.getElementById('savebar').hidden && /Not saved/.test(txt('save-msg')));
    check('settings: only what was changed is sent', calls.length === 1 && JSON.stringify(Object.keys(calls[0].body.changes)) === '["LOCAL_TZ"]');
    unstub(); stub(/\/api\/settings/, () => ({ok: true}));
    d.getElementById('save-settings').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(1500);
    check('settings: after a successful save the bar closes', d.getElementById('savebar').hidden || /Saved/.test(txt('save-msg')));
    unstub();

    // going to one setting (the "Fix it" buttons of System use this)
    w.eval("settingsGoto('settings:ORPHAN_LIMIT')"); await sleep(1200);
    check('settings: goto a setting opens its page and shows the row', w.eval('SUB.settings') === 'advanced' && !!d.getElementById('row-ORPHAN_LIMIT'));
    w.eval("settingsGoto('key:openai_api_key')"); await sleep(1200);
    check('settings: goto a key opens the Runtimes page at that key', w.eval('SUB.settings') === 'runtimes' && !!d.getElementById('secret-openai.key'));

    // keys: the value is typed into a field that exists only while it is open and is never put into the page
    await sgo('runtimes');
    d.querySelector('[data-keyopen="gemini.key"]').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(400);
    const kv = d.getElementById('key-val'); kv.value = 'sk-test-123'; fire(kv, 'input');
    check('settings / runtimes: a key is typed into a password field', kv.type === 'password');
    stub(/\/api\/secret/, () => ({ok: true})); d.querySelector('[data-keysave]').dispatchEvent(new w.MouseEvent('click', {bubbles: true})); await sleep(1200);
    check('settings / runtimes: saving a key posts it once and clears the field', calls.length === 1 && calls[0].body.name === 'gemini.key' && calls[0].body.value === 'sk-test-123' && !d.getElementById('key-val') && !d.getElementById('settings').innerHTML.includes('sk-test-123'));
    unstub();

    // failures and empty states are said out loud, never a blank pane
    w.eval(`window._S0 = S; window._ferr0 = window._ferr; S = null; window._ferr = 'network timeout'; renderSettings(true);`);
    check('settings: when the settings cannot be loaded it says so and offers Try again',
          /Could not load settings: network timeout/.test(stxt()) && !!d.querySelector('#settings [data-retry]'));
    w.eval(`S = window._S0; window._ferr = window._ferr0; renderSettings(true);`);
    await sgo('knowledge');
    w.eval(`window._L0 = DOCS.list; DOCS.at = Date.now() + 600000; DOCS.list = []; renderSettings(true);`);
    check('settings / knowledge: no documents and no roles are said out loud', /No documents yet/.test(stxt()) && /No roles yet/.test(stxt()));
    w.eval(`DOCS.list = {error: 'boom'}; renderSettings(true);`);
    check('settings / knowledge: a failed document list says why and offers Try again',
          /Could not load the documents: boom/.test(stxt()) && !!d.querySelector('#settings [data-docsreload]'));
    w.eval(`DOCS.list = window._L0; DOCS.at = Date.now(); renderSettings(true);`);

    // runtimes: a local model is honest about its cost (no invented dollar amount)
    await sgo('runtimes');
    w.eval(`window._RS0 = RT.status; window._RD0 = RT.data; RT.data = {runtimes: {}, providers: {}}; RT.status = {runtimes: {ollama: {ready: null, reason: '', cost_model: {kind: 'self-hosted', detail: 'self-hosted'}}}}; renderSettings(true);`);
    const orow = d.querySelector('#settings [data-runtime="ollama"]');
    check('settings / runtimes: a local runtime says "your own hardware, no API cost" and shows no dollar amount',
          !!orow && /your own hardware, no API cost/.test(orow.textContent) && !/\$/.test(orow.textContent));
    check('settings / runtimes: a runtime whose readiness is unknown is never shown as ready',
          !!orow && !!orow.querySelector('.badge[data-state="unknown"]') && !orow.querySelector('.badge.ok'));
    w.eval(`RT.status = window._RS0; RT.data = window._RD0; renderSettings(true);`);

    // keyboard: every control on every page is a real button, input, select or textarea, or a link/row that can take focus
    const bad = [];
    for (const pg of pages) {
      await sgo(pg);
      d.querySelectorAll('#settings [onclick], #settings [data-open], #settings [data-doc], #settings [data-wremove], #settings [data-keyopen], #settings [data-addw-for], #settings [data-gosetting]').forEach(el => {
        const native = /^(BUTTON|A|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(el.tagName), focusable = el.getAttribute('tabindex') === '0' && el.getAttribute('role') === 'button';
        if (!native && !focusable) bad.push(pg + ':' + el.tagName.toLowerCase() + (el.id ? '#' + el.id : ''));
      });
      d.querySelectorAll('#settings input:not([type=hidden]), #settings select, #settings textarea').forEach(el => {
        if (!(el.getAttribute('aria-label') || d.querySelector('label[for="' + el.id + '"]') || el.closest('label') || el.closest('.setrow'))) bad.push(pg + ':unlabelled ' + (el.id || el.name || el.type));
      });
      if (d.querySelector('#settings [tabindex="-1"]')) bad.push(pg + ':tabindex=-1');
    }
    check('settings: every control is reachable by keyboard and has a label (' + bad.slice(0, 6).join(', ') + ')', !bad.length);
    await sgo('workers');
  }

  // translations are plain text, never markup: with a dictionary whose every value is an element, no element may appear anywhere
  // (a custom dictionary can be uploaded in Settings, and one that came from somebody else must not be able to run script)
  {
    const probe = '<i class="xss-probe">x</i>', where = [];
    let last = 0;
    const seen = what => { const n = d.querySelectorAll('.xss-probe').length; if (n > last) where.push(what + ' +' + (n - last)); last = n; };
    w.eval("window._I0 = I18N; I18N = new Proxy({}, {get: (o, k) => typeof k === 'string' && k !== 'lang.name' ? " + JSON.stringify(probe) + " : undefined}); updateStatusObjects(); applyI18n();");
    for (const tb of w.eval('TABS')) {
      w.setTab(tb); await sleep(700); seen(tb);
      for (const v of [...d.querySelectorAll('#t-' + tb + ' .seg button')].map(b => b.dataset.v)) { w.setSub(tb, v); await sleep(600); seen(tb + '/' + v); }
    }
    // everything that waits for the owner, and the sheets of a goal and of a task in each state, in one synchronous step each
    w.eval(`window._F0 = F; F = Object.assign({}, F, {
      goals: [{id: 'G20260101-000001', text: 'a goal that waits', status: 'approval', notes: 'a note', rounds: 1}].concat(F.goals || []),
      tasks: [{id: '9001-plan', goal: 'G20260101-000001', title: 'a', role: 'developer', status: 'approval', awaiting: 'plan', risk: 'risky'},
        {id: '9002-merge', goal: 'G1', title: 'b', role: 'developer', status: 'approval', awaiting: 'merge', diff: {added: 1, removed: 2}, merge_feedback: 'f'},
        {id: '9003-risky', goal: 'G1', title: 'c', role: 'developer', status: 'approval', awaiting: 'risky', spec: 's'},
        {id: '9004-blocked', goal: 'G1', title: 'd', role: 'developer', status: 'blocked', diagnosis: {summary: 's', root_cause: 'r', suggested_fix: 'f'}},
        {id: '9005-blocked', goal: 'G1', title: 'e', role: 'developer', status: 'blocked', blocked_why: 'w'}].concat(F.tasks || []),
      owner_questions: ['a question?'], killswitch: {reason: 'egress alarm: x', scope: 'project'}});`);
    w.setTab('overview'); w.eval('render(true)'); seen('overview with everything waiting');
    w.eval("GD.id = 'G20260101-000001'; GD.data = null; SHEET.kind = 'goal'; SHEET.id = GD.id; renderGoalSheet();"); seen('goal sheet');
    for (const [st, aw] of [['approval', 'merge'], ['approval', 'risky'], ['approval', 'plan'], ['running', ''], ['blocked', ''], ['cancelled', ''], ['pending', ''], ['splitting', '']]) {
      w.eval("TD.id = '9100-x'; TD.data = {task: {id: '9100-x', goal: 'G20260101-000001', title: 'x', role: 'developer', status: '" + st + "', awaiting: '" + aw +
             "', diagnosis: {summary: 's', root_cause: 'r', suggested_fix: 'f'}}}; SHEET.kind = 'task'; SHEET.id = '9100-x'; renderTaskSheet();");
      seen('task sheet ' + st + ' ' + aw);
    }
    w.eval("SHEET.kind = null; SHEET.id = null; closeSheet(); F = window._F0; I18N = window._I0; updateStatusObjects(); applyI18n(); render(true);");
    check('translations are text: a dictionary of elements shows up as text everywhere (' + where.join(', ') + ')', !where.length);
  }

  // a link that comes with the data opens a web page or is not a link at all (esc() alone lets a javascript: address through)
  {
    w.eval(`window._F1 = F; F = Object.assign({}, F, {
      goals: [{id: 'G20260101-000002', text: 'a goal with pull requests', status: 'running'}].concat(F.goals || []),
      tasks: [{id: '9200-bad', goal: 'G20260101-000002', title: 'bad link', role: 'developer', status: 'done', pr: 7, pr_url: 'javascript:alert(1)'},
        {id: '9201-ok', goal: 'G20260101-000002', title: 'good link', role: 'developer', status: 'done', pr: 8, pr_url: 'https://example.com/o/r/pull/8'}].concat(F.tasks || [])});`);
    const bad = () => [...d.querySelectorAll('a[href]')].filter(a => /^\s*(javascript|data|vbscript):/i.test(a.getAttribute('href'))).length;
    const good = () => !!d.querySelector('a[href="https://example.com/o/r/pull/8"]');
    w.eval("GD.id = 'G20260101-000002'; GD.data = null; SHEET.kind = 'goal'; SHEET.id = GD.id; renderGoalSheet();");
    const goalBad = bad(), goalGood = good();
    w.eval("TD.id = '9200-bad'; TD.data = null; SHEET.kind = 'task'; SHEET.id = '9200-bad'; renderTaskSheet();");
    const taskBad = bad();
    w.eval("TD.id = '9201-ok'; TD.data = null; SHEET.id = '9201-ok'; renderTaskSheet();");
    const taskGood = good();
    w.eval("SHEET.kind = null; SHEET.id = null; closeSheet(); F = window._F1; render(true);");
    check('links: a pull request address is a link only when it is http(s) (goal sheet: ' + goalBad + ' script links, https link ' + goalGood +
          '; task sheet: ' + taskBad + ' script links, https link ' + taskGood + ')', !goalBad && goalGood && !taskBad && taskGood);
  }

  // "copy report" on a blocked task: a copied report is confirmed by a toast; the manual-copy prompt is only for a clipboard that refuses
  {
    const prompts = [], toasts = () => d.querySelectorAll('#toasts .toast').length;
    w.prompt = (...a) => { prompts.push(a); return null; };
    const copy = async writeText => {
      Object.defineProperty(w.navigator, 'clipboard', {value: {writeText}, configurable: true});
      const before = toasts();
      w.eval("copyTaskReport({id: '9300-x', goal: 'G20260101-000001', title: 'x', role: 'developer', status: 'blocked'})");
      await sleep(100);
      return toasts() - before;
    };
    const shown = await copy(() => Promise.resolve());
    check('copy report: a copied report is confirmed by a toast, not by the manual-copy prompt', shown === 1 && !prompts.length);
    prompts.length = 0;
    const shownBad = await copy(() => Promise.reject(new Error('denied')));
    check('copy report: a clipboard that refuses falls back to the manual-copy prompt with the report', shownBad === 0 && prompts.length === 1 && String(prompts[0][1]).includes('9300-x'));
  }

  // a deep link cut off in the middle of a %-escape (a chat app does that) still opens the dashboard on the tab it names
  {
    const dom2 = await JSDOM.fromURL(base + '#tab=tasks&project=%E0%A4%A&task=%&poll=1', {runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole: vc,
      beforeParse(win) { win.fetch = (u, o) => fetch(new URL(u, base), o); win.EventSource = undefined; win.HTMLCanvasElement.prototype.getContext = () => null; }});
    await sleep(4000);
    const w2 = dom2.window;
    check('deep link: a malformed %-escape in the address bar does not stop the dashboard from starting', w2.eval('tab') === 'tasks' && w2.document.querySelector('#t-tasks').textContent.length > 40);
    w2.close();
  }

  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no JS errors');
  console.log('ui tests: ' + (failed || errors.length ? 'FAILED' : 'ALL OK'));
  w.close(); process.exit(failed || errors.length ? 1 : 0);
})().catch(e => { console.log('crash', e); console.log(errors.join('\n')); process.exit(4); });
