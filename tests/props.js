// Property-based tests (fast-check) of the dashboard's pure helpers in web/static/js/core.js: esc() is what keeps owner-supplied
// and model-supplied text from becoming markup, dur() / fmtTok() / usd() / ago() print the numbers on the cost and time views,
// tsloc() rewrites timestamps inside a text. A failing run prints its seed and the smallest counter-example.
// tests/test_props.sh runs this against the real file; AF_CORE_JS points at another copy, FC_RUNS sets the cases per property.
'use strict';
const fc = require('fast-check');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const file = process.env.AF_CORE_JS || path.join(__dirname, '..', 'web', 'static', 'js', 'core.js');
const src = fs.readFileSync(file, 'utf8');
const from = src.indexOf('const esc = '), to = src.indexOf('const STATUS = ');
assert.ok(from >= 0 && to > from, 'core.js: the helper block (from "const esc" up to "const STATUS") moved - update tests/props.js');
const ctx = vm.createContext({});
vm.runInContext(src.slice(from, to), ctx);
const {esc, tsloc, ago, dur, fmtTok, usd} = vm.runInContext('({esc, tsloc, ago, dur, fmtTok, usd})', ctx);

const runs = Number(process.env.FC_RUNS) || 1000;
let failed = 0;
const prop = (name, arb, pred) => {
  try {
    fc.assert(fc.property(arb, pred), {numRuns: runs});
    console.log('ok   ' + name);
  } catch (e) {
    failed++;
    console.log('FAIL ' + name + '\n' + e.message);
  }
};
const num = v => Number(v);
const text = fc.oneof(fc.string(), fc.string({unit: 'binary'}), fc.constantFrom('<script>', '"><img onerror=1>', "'", '&amp;', '&lt;b&gt;'));
const anyValue = fc.oneof(text, fc.integer(), fc.double(), fc.boolean(), fc.constant(null), fc.constant(undefined));
const unesc = s => s.replace(/&(amp|lt|gt|quot|#39);/g, (_, e) => ({amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'"}[e]));

prop('esc: no markup character survives, whatever the value', anyValue, v => !/[<>"']/.test(esc(v)));
prop('esc: only known entities contain an ampersand', anyValue, v => !/&(?!(amp|lt|gt|quot|#39);)/.test(esc(v)));
prop('esc: unescaping gives the original text back', text, s => unesc(esc(s)) === s);
prop('esc: a missing value is an empty string', fc.constantFrom(null, undefined), v => esc(v) === '');
prop('esc: text that has nothing to escape is unchanged', text.filter(s => !/[&<>"']/.test(s)), s => esc(s) === s);

const secs = {s: 1, min: 60, h: 3600, days: 86400};
const back = out => { const m = /^(\d+(?:\.\d)?) (s|min|h|days)$/.exec(out); return m && num(m[1]) * secs[m[2]]; };
const tol = (out, step) => step[out.split(' ')[1]];
const durStep = {s: 1, min: 31, h: 181, days: 4321}, agoStep = {s: 2, min: 31, h: 1801, days: 43201};      // half a printed unit
prop('dur: prints a number with a unit that is within rounding of the input', fc.double({min: 0, max: 1e8, noNaN: true}), s => {
  const out = dur(s), b = back(out);
  return b !== null && Math.abs(b - s) <= tol(out, durStep);
});
prop('dur: a negative, infinite or missing value (clock skew) never prints -3 s, NaN or Infinity', fc.oneof(fc.double({min: -1e12, max: 1e12, noNaN: true}), fc.constantFrom(NaN, Infinity, -Infinity, undefined, null)),
  s => /^\d+(\.\d)? (s|min|h|days)$/.test(dur(s)));
prop('ago: prints how long ago, within rounding', fc.integer({min: 1, max: 4e7}), k => {
  const out = ago(Date.now() / 1000 - k), b = back(out);
  return b !== null && Math.abs(b - k) <= tol(out, agoStep);
});
prop('ago: no timestamp, no text', fc.constantFrom(0, null, undefined, ''), v => ago(v) === '');

prop('fmtTok: a token count stays within rounding of itself', fc.integer({min: 0, max: 5e9}), n => {
  const out = fmtTok(n);
  let m;
  if ((m = /^(\d+)$/.exec(out))) return n < 1e3 && num(m[1]) === n;
  if ((m = /^(\d+)k$/.exec(out))) return n >= 1e3 && Math.abs(num(m[1]) * 1e3 - n) <= 500;
  if ((m = /^(\d+\.\d)M$/.exec(out))) return n >= 1e6 && Math.abs(num(m[1]) * 1e6 - n) <= 5e4;
  return false;
});
prop('fmtTok: no value, a dash', fc.constantFrom(null, undefined), v => fmtTok(v) === '–');
prop('usd: an amount stays within a rounding step of itself', fc.double({min: 0, max: 1e9, noNaN: true}), v => {
  const out = usd(v), m = /^\$(\d+\.\d{2,3})$/.exec(out);
  return m !== null && Math.abs(num(m[1]) - v) <= (v > 0 && v < 1 ? 0.0006 : 0.006);
});
prop('usd: no value, a dash', fc.constantFrom(null, undefined), v => usd(v) === '–');

const ts = /\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
prop('tsloc: text without a timestamp is unchanged', text.filter(s => !ts.test(s)), s => tsloc(s) === s);
const words = fc.stringMatching(/^[A-Za-z ()-]{0,20}$/);
prop('tsloc: a UTC timestamp is replaced, the text around it stays', fc.tuple(words, fc.date({min: new Date('2000-01-01T00:00:00Z'), max: new Date('2099-12-31T00:00:00Z'), noInvalidDate: true}), words),
  ([a, d, b]) => {
    const iso = d.toISOString().replace(/\.\d+Z$/, 'Z'), out = tsloc(a + iso + b);
    return out.startsWith(a) && out.endsWith(b) && !out.includes(iso) && out.length > a.length + b.length;
  });

if (failed) { console.log(failed + ' property test(s) FAILED'); process.exit(1); }
console.log('property tests: ALL OK');
