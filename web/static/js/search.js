// Search across this project's goals, tasks, events and worker transcripts (/ or the ⌕ button).
// Public interface: openSearch, searchResults, runSearch.
// Invariants: shows explicit empty state ("No results for ...") when no items match; all result rows
// are keyboard reachable and activatable via Enter/Space.

const SR = {q: '', res: null, err: null, busy: false, timer: null, seq: 0};
function openSearch() {
  openSheet('search', 'search', '<h2>Search</h2><input type="search" id="sr-q" class="bigsearch" placeholder="words from a goal, a task, an event or a worker\'s transcript" value="' +
    esc(SR.q) + '"><div id="sr-res">' + searchResults() + '</div>');
  setTimeout(() => { const i = $('sr-q'); if (i) { i.focus(); i.select(); } }, 30);
}
function searchResults() {
  const q = SR.q.trim();
  if (q.length < 2) return '<div class="empty">Type at least two characters to search.</div>';
  if (SR.busy) return '<div class="empty">Searching…</div>';
  if (SR.err) return '<div class="empty bad">Search error: ' + esc(SR.err) + '</div>';
  if (!SR.res || !SR.res.length) return '<div class="empty">No results for “' + esc(q) + '”.</div>';
  const groups = [['goal', 'Goals'], ['task', 'Tasks'], ['event', 'Events'], ['run', 'Transcripts']];
  const mark = s => esc(s).replace(new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
  const html = groups.map(([k, title]) => {
    const xs = SR.res.filter(r => r.kind === k);
    if (!xs.length) return '';
    return '<h3>' + title + ' (' + xs.length + ')</h3>' + xs.map(r => {
      const open = k === 'goal' ? 'goal:' + r.id : k === 'task' ? 'task:' + r.id : k === 'run' ? 'run:' + r.run_id : r.task ? 'task:' + r.task : r.goal ? 'goal:' + r.goal : '';
      const headText = k === 'goal' || k === 'task' ? (r.title + ' (' + (STATUS[r.status] || r.status) + ')') : k === 'run' ? (r.worker + ' · ' + r.task) : dfmt(r.at);
      const head = k === 'goal' || k === 'task' ? esc(r.title) + ' ' + chip(r.status) : k === 'run' ? esc(r.worker) + ' · ' + esc(r.task) + ' · ' + esc(dfmt(r.at)) : esc(dfmt(r.at));
      return '<div class="sr"' + (open ? ' data-open="' + esc(open) + '" role="button" tabindex="0" aria-label="' + esc(headText) + '"' : '') + '><div class="sr-h">' + head + '</div><div class="muted small">' + mark(r.snippet || '') + '</div></div>';
    }).join('');
  }).join('');
  return html || '<div class="empty">No results for “' + esc(q) + '”.</div>';
}
async function runSearch() {
  const q = SR.q.trim(), seq = ++SR.seq;
  if (q.length < 2) { SR.res = null; SR.err = null; const el = $('sr-res'); if (el) el.innerHTML = searchResults(); return; }
  SR.busy = true; SR.res = null; SR.err = null; const el = $('sr-res'); if (el) el.innerHTML = searchResults();
  let r;
  try {
    r = await getJSON('/api/search', {q});
    if (r && r.error) throw new Error(r.error);
  } catch (e) {
    if (seq === SR.seq) SR.err = String(e && e.message || e);
    r = [];
  }
  if (seq !== SR.seq || SHEET.kind !== 'search') return;
  SR.busy = false; SR.res = Array.isArray(r) ? r : [];
  const target = $('sr-res'); if (target) target.innerHTML = searchResults();
}
document.addEventListener('input', e => { if (e.target.id === 'sr-q') { SR.q = e.target.value; clearTimeout(SR.timer); SR.timer = setTimeout(runSearch, 350); } });
document.addEventListener('click', e => {
  const sr = e.target.closest('.sr[data-open]');
  if (sr) {
    const [kind, id] = sr.dataset.open.split(/:(.*)/s);
    if (kind === 'task') openTask(id);
    else if (kind === 'goal') openGoal(id);
    else if (kind === 'run') openRun(id);
  }
});
document.addEventListener('keydown', e => {
  if ((e.key === 'Enter' || e.key === ' ') && !typing(e.target)) {
    const sr = e.target.closest('.sr[data-open]');
    if (sr) {
      e.preventDefault();
      const [kind, id] = sr.dataset.open.split(/:(.*)/s);
      if (kind === 'task') openTask(id);
      else if (kind === 'goal') openGoal(id);
      else if (kind === 'run') openRun(id);
    }
  }
});
$('search-open').addEventListener('click', openSearch);
