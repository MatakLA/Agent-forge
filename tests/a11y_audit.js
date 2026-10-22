// Accessibility audit (tests/test_a11y.sh runs it): the real dashboard on the demo state in headless Chrome, axe-core
// (WCAG 2.2 A/AA + best practices) on every tab, sub-view, sheet and dialog in light and dark, plus keyboard checks
// (skip link, focus moves into a sheet and back, Esc closes, Tab stays inside) and reduced motion.
// Exit code 0 = no violation. AF_URL = dashboard URL; AF_CHROME = chromium binary; AF_REPORT=1 lists every node.
const puppeteer = require('puppeteer-core');
const axeSource = require('axe-core').source;
const base = process.env.AF_URL || 'http://127.0.0.1:8171/';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];
const problems = [];
let scenes = 0;

async function axe(page, scene) {
  for (const scheme of ['light', 'dark']) {
    await page.emulateMediaFeatures([{name: 'prefers-color-scheme', value: scheme}]);
    await sleep(150);
    const res = await page.evaluate(async tags => axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']}), TAGS);
    for (const v of res.violations) {
      for (const n of v.nodes) problems.push({scene: scene + ' [' + scheme + ']', rule: v.id, impact: v.impact, target: n.target.join(' '), why: (n.failureSummary || '').split("\n").slice(0, 3).join(' | ').slice(0, 400)});
    }
  }
  scenes++;
}


const fail = (scene, rule, why, target = '') => problems.push({scene, rule, impact: 'serious', target, why});
const active = page => page.evaluate(() => { const a = document.activeElement; return a ? {tag: a.tagName, id: a.id, cls: a.className && a.className.baseVal === undefined ? String(a.className) : '', inSheet: !!a.closest('#sheet'), inDetail: !!a.closest('#detail')} : null; });

async function keyboardChecks(page) {
  // skip link: the first Tab stop, and it moves focus to the main region
  await page.goto('about:blank');
  await page.goto(base + '#tab=overview&poll=1', {waitUntil: 'networkidle2'}); await sleep(2500);
  await page.keyboard.press('Tab');
  let a = await active(page);
  if (!/skip/.test(a.cls)) fail('keyboard', 'skip-link', 'the first Tab stop is not the skip link', JSON.stringify(a));
  await page.keyboard.press('Enter'); await sleep(100);
  a = await active(page);
  if (a.id !== 'main') fail('keyboard', 'skip-link', 'the skip link does not move focus to <main>', JSON.stringify(a));
  // current tab and view are exposed
  const cur = await page.$$eval('nav button[aria-current="page"]', b => b.length);
  if (cur !== 1) fail('keyboard', 'aria-current', 'expected exactly one nav button with aria-current, found ' + cur);
  const pressed = await page.$$eval('#t-tasks .seg button[aria-pressed="true"]', b => b.length);
  if (pressed !== 1) fail('keyboard', 'aria-pressed', 'expected one pressed view button in Tasks, found ' + pressed);
  // every tab stop shows a focus indicator
  for (const tab of ['overview', 'tasks', 'settings']) {
    await page.click('nav button[data-t="' + tab + '"]'); await sleep(900);
    await page.focus('nav button[data-t="' + tab + '"]');
    for (let i = 0; i < 45; i++) {
      await page.keyboard.press('Tab');
      const r = await page.evaluate(() => {
        const el = document.activeElement; if (!el || el === document.body) return null;
        const vis = x => { const c = getComputedStyle(x); return c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) >= 2 && c.outlineColor !== 'rgba(0, 0, 0, 0)'; };
        const ok = vis(el) || (el.matches('.switch input') && el.nextElementSibling && vis(el.nextElementSibling)) || getComputedStyle(el).boxShadow !== 'none';
        return ok ? null : (el.tagName + '#' + el.id + '.' + String(el.className && el.className.baseVal === undefined ? el.className : '').slice(0, 40));
      });
      if (r) fail('keyboard ' + tab, 'focus-visible', 'no visible focus indicator on ' + r, r);
    }
  }
  // a sheet opened from the keyboard: focus goes in, Tab stays in, Esc closes and gives focus back
  await page.click('nav button[data-t="tasks"]'); await sleep(900);
  await page.click('#t-tasks .seg button[data-v="board"]'); await sleep(600);
  const opener = await page.$('#t-tasks [data-open^="task:"]');
  if (opener) {
    await opener.focus(); await page.keyboard.press('Enter'); await sleep(1500);
    a = await active(page);
    if (!a.inDetail) fail('task sheet', 'dialog-focus', 'focus did not move into the opened sheet', JSON.stringify(a));
    const lab = await page.$eval('#sheet', e => ({role: e.getAttribute('role'), modal: e.getAttribute('aria-modal'), name: e.getAttribute('aria-labelledby') ? (document.getElementById(e.getAttribute('aria-labelledby')) || {}).textContent : e.getAttribute('aria-label')}));
    if (lab.role !== 'dialog' || lab.modal !== 'true' || !lab.name) fail('task sheet', 'dialog-name', 'the sheet is not a named modal dialog: ' + JSON.stringify(lab));
    const inertMain = await page.$eval('main', e => e.hasAttribute('inert'));
    if (!inertMain) fail('task sheet', 'dialog-inert', 'the page behind the open sheet is not inert');
    for (let i = 0; i < 60; i++) { await page.keyboard.press(i % 7 === 6 ? 'Tab' : 'Tab'); a = await active(page); if (!a.inDetail) { fail('task sheet', 'focus-trap', 'Tab left the open sheet at step ' + i, JSON.stringify(a)); break; } }
    for (let i = 0; i < 20; i++) { await page.keyboard.down('Shift'); await page.keyboard.press('Tab'); await page.keyboard.up('Shift'); a = await active(page); if (!a.inDetail) { fail('task sheet', 'focus-trap', 'Shift+Tab left the open sheet at step ' + i, JSON.stringify(a)); break; } }
    await page.keyboard.press('Escape'); await sleep(300);
    const back = await page.evaluate(() => { const e = document.activeElement; return e && e.dataset ? e.dataset.open : null; });
    if (!back || !back.startsWith('task:')) fail('task sheet', 'focus-return', 'Esc did not return focus to the element that opened the sheet', String(back));
    if (await page.$eval('main', e => e.hasAttribute('inert'))) fail('task sheet', 'dialog-inert', 'the page is still inert after the sheet closed');
  }
  // reduced motion: nothing pulses
  await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'reduce'}]);
  await page.click('nav button[data-t="overview"]'); await sleep(900);
  const anim = await page.evaluate(() => [...document.querySelectorAll('.dot.running, .dot.think')].map(e => parseFloat(getComputedStyle(e).animationDuration)).filter(d => d > 0.01));
  if (anim.length) fail('reduced motion', 'animation', anim.length + ' element(s) still animate with prefers-reduced-motion: reduce');
  await page.emulateMediaFeatures([{name: 'prefers-reduced-motion', value: 'no-preference'}]);
  // the language of the page follows the dashboard language
  await page.evaluate(() => loadLocale('sk')); await sleep(800);
  const lang = await page.evaluate(() => document.documentElement.lang);
  if (lang !== 'sk') fail('keyboard', 'html-lang', 'the <html lang> did not change with the dashboard language');
  // reflow: at 320 CSS px wide nothing scrolls sideways
  await page.setViewport({width: 320, height: 640});
  for (const tab of ['overview', 'tasks', 'live', 'insights', 'system', 'settings']) {
    await page.click('nav button[data-t="' + tab + '"]'); await sleep(900);
    const w = await page.evaluate(() => ({doc: document.documentElement.scrollWidth - innerWidth, main: document.querySelector('main').scrollWidth - document.querySelector('main').clientWidth}));
    if (w.doc > 1 || w.main > 1) fail('reflow 320px ' + tab, 'reflow', 'content scrolls sideways (page +' + w.doc + 'px, main +' + w.main + 'px)');
  }
  await page.setViewport({width: 1280, height: 900});
}

(async () => {
  const browser = await puppeteer.launch({executablePath: process.env.AF_CHROME || '/usr/bin/chromium-browser', headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']});
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 900});
  const jsErrors = [];
  page.on('pageerror', e => jsErrors.push(String(e)));
  await page.goto(base + '#tab=overview&poll=1', {waitUntil: 'networkidle2'});
  await page.evaluate(axeSource);
  await sleep(5000);

  const tabs = await page.$$eval('nav button[data-t]', bs => bs.map(b => b.dataset.t));
  for (const tab of tabs) {
    await page.click('nav button[data-t="' + tab + '"]');
    await sleep(1200);
    const views = await page.$$eval('#t-' + tab + ' .seg button[data-v]', bs => bs.map(b => b.dataset.v));
    if (!views.length) { await axe(page, 'tab ' + tab); continue; }
    for (const v of views) {
      await page.click('#t-' + tab + ' .seg button[data-v="' + v + '"]');
      await sleep(900);
      await axe(page, 'tab ' + tab + ' / ' + v);
    }
  }

  const sheetScenes = [
    ['tasks', 'board', '[data-open^="task:"]', 'task sheet'],
    ['overview', null, '[data-open^="goal:"]', 'goal sheet'],
    ['live', null, '#runs-open', 'finished runs'],
  ];
  for (const [tab, view, sel, name] of sheetScenes) {
    await page.click('nav button[data-t="' + tab + '"]'); await sleep(900);
    if (view) { await page.click('#t-' + tab + ' .seg button[data-v="' + view + '"]'); await sleep(700); }
    const el = await page.$(sel);
    if (!el) { problems.push({scene: name, rule: 'scene', impact: 'serious', target: sel, why: 'nothing to open in the demo state'}); continue; }
    await el.evaluate(e => e.click()); await sleep(1500);
    await axe(page, name);
    await page.keyboard.press('Escape'); await sleep(300);
  }
  await page.click('nav button[data-t="overview"]'); await sleep(500);
  await page.keyboard.press('/'); await sleep(500);
  await axe(page, 'search');
  await page.keyboard.press('Escape'); await sleep(300);
  for (const [id, name] of [['login', 'sign-in dialog'], ['newproj', 'new project dialog'], ['setup', 'repository setup dialog']]) {
    await page.evaluate(i => document.getElementById(i).classList.add('on'), id); await sleep(300);
    await axe(page, name);
    await page.evaluate(i => document.getElementById(i).classList.remove('on'), id);
  }
  await keyboardChecks(page);
  if (jsErrors.length) problems.push({scene: 'page', rule: 'script-error', impact: 'serious', target: '', why: jsErrors.join(' | ').slice(0, 300)});

  await browser.close();
  const byRule = {};
  for (const p of problems) (byRule[p.rule + ' (' + p.impact + ')'] ||= []).push(p);
  console.log('axe: ' + scenes + ' scenes x light/dark, ' + problems.length + ' violating nodes');
  for (const [r, list] of Object.entries(byRule)) {
    console.log('  ' + r + ': ' + list.length + ' nodes in ' + new Set(list.map(p => p.scene)).size + ' scenes');
    for (const p of (process.env.AF_REPORT ? list : list.slice(0, 3))) console.log('      ' + p.scene + ' :: ' + p.target + ' :: ' + p.why);
  }
  process.exit(problems.length ? 1 : 0);
})().catch(e => { console.log('audit crashed: ' + (e && e.stack || e)); process.exit(2); });
