// Live transcripts of the orchestrator and the workers, and the 24-hour timeline.
const paneEls = {};
function liveHtml() {
  const grid = document.getElementById('grid');
  const panes = P.panes || [];
  document.getElementById('c-live').textContent = panes.length;
  if (tab !== 'live') return;
  if (!panes.length) { setHTML('grid', '<div class="empty">No AI is running right now — the transcript appears when one runs.</div>'); for (const k in paneEls) delete paneEls[k]; return; }
  if (grid.querySelector('.empty')) { grid.innerHTML = ''; lastHTML['grid'] = null; }
  const ts = (F && F.tasks) || [];
  const seen = new Set();
  for (const p of panes) {
    if (p.id.startsWith('worker:') ? !inPool(p.id.slice(7)) : project !== 'default') continue;
    seen.add(p.id);
    let pe = paneEls[p.id];
    if (!pe) {
      const el = document.createElement('div'); el.className = 'pane';
      const h = document.createElement('h2'); const log = document.createElement('div'); log.className = 'log';
      el.appendChild(h); el.appendChild(log); grid.appendChild(el);
      pe = paneEls[p.id] = {el, h, log, n: 0, first: null, last: null, head: ''};
    }
    const w = p.id.startsWith('worker:') ? p.id.slice(7) : null, t = w && ts.find(x => x.worker === w && x.status === 'running');
    const head = '<span class="dot ' + (p.state === 'running' ? 'running' : '') + '"></span>' + esc(p.title) +
      (t ? ' <span class="role ' + roleCls(t.role) + '">' + esc(t.role) + '</span> <span class="muted" style="font-weight:400;font-size:13px">' + esc(t.id) + '</span>' :
           ' <span class="muted" style="font-weight:400;font-size:13px">(' + esc(p.state) + ')</span>');
    if (head !== pe.head) { pe.h.innerHTML = head; pe.head = head; }
    const L = p.lines, firstTxt = L.length ? L[0].text : null, lastTxt = L.length ? L[L.length - 1].text : null;
    if (L.length === pe.n && firstTxt === pe.first && lastTxt === pe.last) continue;
    const atBottom = pe.log.scrollHeight - pe.log.scrollTop - pe.log.clientHeight < 40;
    const line = l => '<div class="line l-' + l.kind + '">' + esc(l.text) + '</div>';
    if (firstTxt === pe.first && L.length > pe.n) pe.log.insertAdjacentHTML('beforeend', L.slice(pe.n).map(line).join(''));
    else pe.log.innerHTML = L.map(line).join('');
    pe.n = L.length; pe.first = firstTxt; pe.last = lastTxt;
    if (atBottom) pe.log.scrollTop = pe.log.scrollHeight;
  }
  for (const id in paneEls) if (!seen.has(id)) { paneEls[id].el.remove(); delete paneEls[id]; }
}

// ---- 1. timeline: one lane per worker, one bar per run, last 24 h
const RUNCOL = {ok: 'var(--ok)', running: 'var(--accent)', 'token-cap': 'var(--warn)', timeout: 'var(--warn)', failed: 'var(--bad)',
                'setup-failed': 'var(--bad)', killswitch: 'var(--idle)'};
function timelineHtml() {
  if (!F || !F.timeline) return '<div class="empty">The timeline needs a newer foreman.</div>';
  const now = Date.now() / 1000, span = 24 * 3600, t0 = now - span, W = 1000, LW = 90, lane = 30;
  const runs = F.timeline;
  const ws = [...new Set(runs.map(r => r.worker).concat(Object.keys((P && P.roster) || {}).filter(k => k.startsWith('worker:')).map(k => k.slice(7))))].filter(Boolean).sort();
  if (!ws.length) return '<div class="empty">No runs in the last 24 h.</div>';
  const x = t => LW + Math.max(0, Math.min(1, (t - t0) / span)) * (W - LW);
  let svg = '<svg class="tl" viewBox="0 0 ' + W + ' ' + (ws.length * lane + 24) + '" preserveAspectRatio="none">';
  for (let h = 0; h <= 24; h += 3) {
    const tx = x(t0 + h * 3600), d = new Date((t0 + h * 3600) * 1000);
    svg += '<line x1="' + tx + '" x2="' + tx + '" y1="0" y2="' + ws.length * lane + '" stroke="var(--line)"/>' +
           '<text x="' + tx + '" y="' + (ws.length * lane + 16) + '" text-anchor="middle">' + d.getHours() + ':00</text>';
  }
  const util = {};
  ws.forEach((w, i) => {
    const y = i * lane;
    svg += '<text class="lane" x="4" y="' + (y + 19) + '">' + esc(w) + '</text>';
    let busy = 0;
    for (const r of runs.filter(r => r.worker === w)) {
      const s0 = Math.max(r.start, t0), e0 = Math.min(r.end || now, now);
      if (e0 <= s0) continue;
      busy += e0 - s0;
      const tip = esc(r.task) + ' · ' + esc(r.reason) + ' · ' + Math.round((e0 - s0) / 60) + ' min · ' + Math.round((r.tokens || 0) / 1000) + 'k tokens';
      svg += '<g data-task="' + esc(r.task.replace(/\.md$/, '')) + '"><title>' + tip + '</title><rect class="run" x="' + x(s0) + '" y="' + (y + 5) + '" width="' +
             Math.max(2, x(e0) - x(s0)) + '" height="' + (lane - 10) + '" rx="3" fill="' + (RUNCOL[r.reason] || 'var(--idle)') + '"' +
             (r.reason === 'running' ? ' opacity=".8"><animate attributeName="opacity" values=".5;1;.5" dur="2s" repeatCount="indefinite"/></rect>' : '/>') + '</g>';
    }
    util[w] = Math.round(100 * busy / span);
  });
  svg += '</svg>';
  const legend = '<div class="legend">' + [['ok','done'],['running','running'],['token-cap','token cap / time'],['failed','failed'],['killswitch','kill switch']]
    .map(([k, l]) => '<span><i style="background:' + RUNCOL[k] + '"></i>' + l + '</span>').join('') + '</div>';
  const u = '<div class="legend">' + ws.map(w => '<span>' + esc(w) + ': <b style="color:var(--text)">' + util[w] + ' %</b> of the time working</span>').join('') + '</div>';
  return '<h3>Last 24 hours</h3>' + legend + '<div class="tablewrap" style="padding:8px">' + svg + '</div>' + u +
         '<div class="muted" style="font-size:12px;margin-top:6px">Gaps = the worker was idle. Click a block to open its task.</div>';
}
