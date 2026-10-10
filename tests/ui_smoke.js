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
  const iso = '2026-01-15T13:49:21Z', wantLocal = new Date(iso).toLocaleString([], {day: 'numeric', month: 'numeric', hour: '2-digit', minute: '2-digit'});
  check('quota reset timestamps are shown in the viewer\'s own time, not UTC',
        w.eval("tsloc('quota low: Gemini weekly 0% (resets " + iso + ")')") === 'quota low: Gemini weekly 0% (resets ' + wantLocal + ')');
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
  w.openGoal('G20261008-062000'); await sleep(2500);
  check('goal sheet: tasks, events, criteria', txt('sheet').includes('What happened') && txt('sheet').includes('Done when'));
  const rid = (w.eval('F').timeline || []).find(r => r.end && r.run_id);
  if (rid) { w.openRun(rid.run_id); await sleep(2500); check('replay sheet opens', txt('sheet').includes('Run ')); }
  w.openSearch(); const q = d.getElementById('sr-q'); q.value = 'due'; q.dispatchEvent(new w.Event('input', {bubbles: true})); await sleep(3000);
  check('search finds tasks', txt('sheet').includes('Tasks ('));
  w.openDoc('role:tester'); await sleep(2000);
  check('document editor opens', !!d.getElementById('doc-text'));
  w.closeSheet();
  w.eval("SUB.tasks = 'graph'"); w.setTab('tasks'); await sleep(800);
  check('graph shows the critical path', txt('tasks-view').includes('Critical path'));
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
  check('no page error shown', !txt('jserr'));
  console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no JS errors');
  console.log('ui tests: ' + (failed || errors.length ? 'FAILED' : 'ALL OK'));
  w.close(); process.exit(failed || errors.length ? 1 : 0);
})().catch(e => { console.log('crash', e); console.log(errors.join('\n')); process.exit(4); });
