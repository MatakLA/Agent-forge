// Live: each running AI's transcript as it works (new lines are pushed by the server), the replay of any finished run, and
// the 24-hour timeline of runs per worker.

const paneEls = {};
function renderLive() {
  const panes = (P.panes || []).filter(p => p.id.startsWith('worker:') ? inPool(p.id.slice(7)) : project === 'default');
  $('c-live').textContent = panes.filter(p => p.state === 'running').length || panes.length;
  if (tab !== 'live') return;
  const grid = $('grid'), ts = (F && F.tasks) || [];
  $('live-note').textContent = panes.length ? '' : 'No AI is running right now — a transcript appears here when one starts.';
  const seen = new Set();
  for (const p of panes) {
    seen.add(p.id);
    let pe = paneEls[p.id];
    if (!pe || !grid.contains(pe.el)) {
      const el = document.createElement('div'); el.className = 'pane';
      const h = document.createElement('h2'), log = document.createElement('div'); log.className = 'log';
      el.appendChild(h); el.appendChild(log); grid.appendChild(el);
      pe = paneEls[p.id] = {el, h, log, last: 0, head: ''};
    }
    const w = p.id.startsWith('worker:') ? p.id.slice(7) : null, t = w && ts.find(x => x.worker === w && x.status === 'running');
    const head = '<span class="dot ' + (p.state === 'running' ? 'running' : '') + '"></span>' + esc(p.title) +
      (t ? ' ' + roleTag(t.role) + ' <a class="muted norm" data-open="task:' + esc(t.id) + '">' + esc(t.id) + '</a>' : ' <span class="muted norm">(' + esc(p.state) + ')</span>');
    if (head !== pe.head) { pe.h.innerHTML = head; pe.head = head; }
    const L = p.lines || [], lastN = L.length ? (L[L.length - 1].n || L.length) : 0;
    if (lastN === pe.last && pe.log.childElementCount) continue;
    const atBottom = pe.log.scrollHeight - pe.log.scrollTop - pe.log.clientHeight < 40;
    const fresh = L.filter(l => (l.n || 0) > pe.last);
    const line = l => '<div class="line l-' + esc(l.kind) + '">' + esc(l.text) + '</div>';
    if (pe.last && fresh.length && fresh.length < L.length && L[0].n) pe.log.insertAdjacentHTML('beforeend', fresh.map(line).join(''));
    else pe.log.innerHTML = L.map(line).join('');
    while (pe.log.childElementCount > 400) pe.log.firstElementChild.remove();
    pe.last = lastN;
    if (atBottom) pe.log.scrollTop = pe.log.scrollHeight;
  }
  for (const id in paneEls) if (!seen.has(id)) { paneEls[id].el.remove(); delete paneEls[id]; }
}
projectResets.push(() => { for (const id in paneEls) { paneEls[id].el.remove(); delete paneEls[id]; } });

// ---- replay of a finished run: step through it, filter it, find text, copy a command
const RD = {id: null, data: null, err: null, filter: 'all', q: '', pos: -1};
function openRun(id) {
  Object.assign(RD, {id, data: null, err: null, filter: 'all', q: '', pos: -1});
  openSheet('run', id, '<div class="empty">loading the transcript…</div>', true);
  getJSON('/api/run', {run: id}).then(d => { if (RD.id !== id) return; if (d.error) RD.err = d.error; else RD.data = d; renderRun(true); })
    .catch(e => { RD.err = String(e); renderRun(true); });
}
const RFILTERS = {all: () => true, tools: l => l.kind === 'tool' || l.kind === 'tool_done', talk: l => ['user', 'assistant', 'result'].includes(l.kind),
  errors: l => /error|fail|traceback|denied|refused|exit [1-9]/i.test(l.text) || l.kind === 'result'};
function renderRun(full) {
  if (SHEET.kind !== 'run') return;
  if (!RD.data) return setSheet('run', RD.id, '<div class="empty">' + esc(RD.err || 'loading…') + '</div>');
  const m = RD.data.meta || {}, q = RD.q.toLowerCase();
  const lines = (RD.data.lines || []).map((l, i) => Object.assign({i}, l)).filter(RFILTERS[RD.filter] || RFILTERS.all).filter(l => !q || l.text.toLowerCase().includes(q));
  const body = lines.map(l => '<div class="line l-' + esc(l.kind) + '" data-ri="' + l.i + '">' + (l.kind === 'tool' ? '<button class="copy" data-copy="' + l.i + '" title="copy">⧉</button>' : '') +
    esc(l.text) + '</div>').join('') || '<div class="empty">' + (RD.data.missing ? 'The transcript is gone (cleaned up) - the numbers above are kept.' : 'nothing matches') + '</div>';
  if (!full && $('rp-lines')) { $('rp-lines').innerHTML = body; return; }
  setSheet('run', RD.id, '<h2>Run ' + esc(RD.id) + '</h2><div class="sub">' + [esc(m.worker), m.task ? '<a data-open="task:' + esc(String(m.task).replace(/\.md$/, '')) + '">' +
      esc(String(m.task).replace(/\.md$/, '')) + '</a>' : '', m.runtime && m.runtime !== 'agy' ? esc(RTNAME[m.runtime] || m.runtime) : esc(m.model || ''), esc(m.reason || ''),
      m.tokens ? fmtTok(m.tokens) + ' tokens' : '', m.tool_calls ? m.tool_calls + ' tool calls' : '', m.duration_s ? dur(m.duration_s) : '', m.cost_usd ? usd(m.cost_usd) : '']
      .filter(Boolean).join(' · ') + '</div>' +
    '<div class="rp-bar"><div class="seg sm" id="rp-seg">' + [['all', 'All'], ['tools', 'Commands'], ['talk', 'Messages'], ['errors', 'Errors']].map(([k, l]) =>
      '<button data-rf="' + k + '"' + (RD.filter === k ? ' class="on"' : '') + '>' + l + '</button>').join('') + '</div>' +
    '<input type="search" id="rp-q" placeholder="find in the transcript" value="' + esc(RD.q) + '"><button class="mini" data-rnext>Next command ↓</button></div>' +
    '<div class="log replay" id="rp-lines">' + body + '</div>');
}
document.addEventListener('click', async e => {
  if (e.target.closest('#runs-open')) { openRuns(); return; }
  const rf = e.target.closest('[data-rf]'); if (rf) { RD.filter = rf.dataset.rf; renderRun(true); return; }
  const cp = e.target.closest('[data-copy]');
  if (cp) { const l = RD.data.lines[Number(cp.dataset.copy)], text = String(l.text).replace(/^[\w.-]+: /, '');
    try { await navigator.clipboard.writeText(text); toast('Copied'); } catch (err) { prompt('Copy:', text); } return; }
  if (e.target.closest('[data-rnext]')) {
    const box = $('rp-lines'); if (!box) return;
    const tools = [...box.querySelectorAll('.l-tool')], top = box.scrollTop + 8;
    const next = tools.find(el => el.offsetTop - box.offsetTop > top) || tools[0];
    if (next) { box.scrollTop = next.offsetTop - box.offsetTop; next.classList.add('flash'); setTimeout(() => next.classList.remove('flash'), 900); }
  }
});
document.addEventListener('input', e => { if (e.target.id === 'rp-q') { RD.q = e.target.value; renderRun(false); } });

// ---- finished runs of the last 24 hours (to pick one for a replay)
function openRuns() {
  const runs = ((F && F.timeline) || []).filter(r => r.end && r.run_id).sort((a, b) => b.end - a.end);
  openSheet('runs', 'runs', '<h2>Finished runs, last 24 hours</h2>' + (runs.length ? '<div class="tablewrap"><table class="m click"><tr><th>ended</th><th>worker</th><th>task</th>' +
    '<th>outcome</th><th class="num">tokens</th><th class="num">time</th></tr>' + runs.map(r => '<tr data-open="run:' + esc(r.run_id) + '"><td>' + esc(tfmt(r.end)) + '</td><td>' +
    esc(r.worker) + '</td><td>' + esc(r.task) + '</td><td>' + esc(r.reason) + '</td><td class="num">' + fmtTok(r.tokens) + '</td><td class="num">' + dur(r.end - r.start) + '</td></tr>').join('') +
    '</table></div><div class="muted small">Older runs: search for words from their transcript (⌕).</div>' : '<div class="empty">No finished runs in the last 24 hours.</div>'));
}

// ---- timeline: one lane per worker, one bar per run, last 24 h; a bar opens the run's replay
const RUNCOL = {ok: 'var(--ok)', running: 'var(--accent)', 'token-cap': 'var(--warn)', timeout: 'var(--warn)', failed: 'var(--bad)',
  'setup-failed': 'var(--bad)', killswitch: 'var(--idle)', quota: 'var(--idle)', stopped: 'var(--idle)'};
function timelineHtml() {
  if (!F || !F.timeline) return '<div class="empty">The timeline needs a newer foreman.</div>';
  const now = Date.now() / 1000, span = 24 * 3600, t0 = now - span, W = 1000, LW = 90, lane = 30, runs = F.timeline;
  const ws = [...new Set(runs.map(r => r.worker).concat((F.pool || [])))].filter(Boolean).sort((a, b) => Number(a.split('-')[1]) - Number(b.split('-')[1]));
  if (!ws.length) return '<div class="empty">No runs in the last 24 h.</div>';
  const x = t => LW + Math.max(0, Math.min(1, (t - t0) / span)) * (W - LW);
  let svg = '<svg class="tl" viewBox="0 0 ' + W + ' ' + (ws.length * lane + 24) + '" preserveAspectRatio="none">';
  for (let h = 0; h <= 24; h += 3) {
    const tx = x(t0 + h * 3600), d = new Date((t0 + h * 3600) * 1000);
    svg += '<line x1="' + tx + '" x2="' + tx + '" y1="0" y2="' + ws.length * lane + '" stroke="var(--line)"/><text x="' + tx + '" y="' + (ws.length * lane + 16) + '" text-anchor="middle">' + d.getHours() + ':00</text>';
  }
  const util = {};
  ws.forEach((w, i) => {
    const y = i * lane; let busy = 0;
    svg += '<text class="lane" x="4" y="' + (y + 19) + '">' + esc(w) + '</text>';
    for (const r of runs.filter(r => r.worker === w)) {
      const s0 = Math.max(r.start, t0), e0 = Math.min(r.end || now, now);
      if (e0 <= s0) continue;
      busy += e0 - s0;
      const tip = esc(r.task) + ' · ' + esc(r.reason) + ' · ' + Math.round((e0 - s0) / 60) + ' min · ' + fmtTok(r.tokens || 0) + ' tokens';
      const open = r.run_id && r.end ? 'run:' + r.run_id : 'task:' + String(r.task).replace(/\.md$/, '');
      svg += '<g data-open="' + esc(open) + '"><title>' + tip + '</title><rect class="run" x="' + x(s0) + '" y="' + (y + 5) + '" width="' + Math.max(2, x(e0) - x(s0)) +
        '" height="' + (lane - 10) + '" rx="3" fill="' + (RUNCOL[r.reason] || 'var(--idle)') + '"' +
        (r.reason === 'running' ? ' opacity=".8"><animate attributeName="opacity" values=".5;1;.5" dur="2s" repeatCount="indefinite"/></rect>' : '/>') + '</g>';
    }
    util[w] = Math.round(100 * busy / span);
  });
  svg += '</svg>';
  const legend = '<div class="legend">' + [['ok', 'done'], ['running', 'running'], ['token-cap', 'token cap / time'], ['failed', 'failed'], ['killswitch', 'stopped']]
    .map(([k, l]) => '<span><i style="background:' + RUNCOL[k] + '"></i>' + l + '</span>').join('') + '</div>';
  const u = '<div class="legend">' + ws.map(w => '<span>' + esc(w) + ': <b class="txt">' + util[w] + ' %</b> busy</span>').join('') + '</div>';
  return legend + '<div class="tablewrap pad">' + svg + '</div>' + u + '<div class="muted small">Gaps = the worker was idle. Click a run to replay its transcript.</div>';
}
function renderTimeline(force) { setHTML('timeline', timelineHtml(), force); }
