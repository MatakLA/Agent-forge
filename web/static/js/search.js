// Search across this project's goals, tasks, events and worker transcripts (/ or the ⌕ button).

const SR = {q: '', res: null, busy: false, timer: null, seq: 0};
function openSearch() {
  openSheet('search', 'search', '<h2>Search</h2><input type="search" id="sr-q" class="bigsearch" placeholder="words from a goal, a task, an event or a worker\'s transcript" value="' +
    esc(SR.q) + '"><div id="sr-res">' + searchResults() + '</div>');
  setTimeout(() => { const i = $('sr-q'); if (i) { i.focus(); i.select(); } }, 30);
}
function searchResults() {
  if (SR.q.trim().length < 2) return small('Type at least two characters.');
  if (!SR.res) return small(SR.busy ? 'searching…' : '');
  if (!SR.res.length) return small('Nothing found.');
  const groups = [['goal', 'Goals'], ['task', 'Tasks'], ['event', 'Events'], ['run', 'Transcripts']];
  const mark = s => esc(s).replace(new RegExp('(' + SR.q.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
  return groups.map(([k, title]) => { const xs = SR.res.filter(r => r.kind === k); if (!xs.length) return '';
    return '<h3>' + title + ' (' + xs.length + ')</h3>' + xs.map(r => {
      const open = k === 'goal' ? 'goal:' + r.id : k === 'task' ? 'task:' + r.id : k === 'run' ? 'run:' + r.run_id : r.task ? 'task:' + r.task : r.goal ? 'goal:' + r.goal : '';
      const head = k === 'goal' || k === 'task' ? esc(r.title) + ' ' + chip(r.status) : k === 'run' ? esc(r.worker) + ' · ' + esc(r.task) + ' · ' + esc(dfmt(r.at)) : esc(dfmt(r.at));
      return '<div class="sr"' + (open ? ' data-open="' + esc(open) + '"' : '') + '><div class="sr-h">' + head + '</div><div class="muted small">' + mark(r.snippet || '') + '</div></div>';
    }).join(''); }).join('');
}
async function runSearch() {
  const q = SR.q.trim(), seq = ++SR.seq;
  if (q.length < 2) { SR.res = null; $('sr-res').innerHTML = searchResults(); return; }
  SR.busy = true; SR.res = null; $('sr-res').innerHTML = searchResults();
  let r; try { r = await getJSON('/api/search', {q}); } catch (e) { r = []; }
  if (seq !== SR.seq || SHEET.kind !== 'search') return;
  SR.busy = false; SR.res = Array.isArray(r) ? r : []; $('sr-res').innerHTML = searchResults();
}
document.addEventListener('input', e => { if (e.target.id === 'sr-q') { SR.q = e.target.value; clearTimeout(SR.timer); SR.timer = setTimeout(runSearch, 350); } });
$('search-open').addEventListener('click', openSearch);
