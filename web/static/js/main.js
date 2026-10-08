// Start: first render, deep link, polling.
setTab(tab);
if (HASH.task && /^\d{4}-[\w-]+$/.test(HASH.task)) { openTask = HASH.task; }
let busyPoll = false;
async function tickPoll() { if (document.hidden || busyPoll) return; busyPoll = true; try { await poll(); } finally { busyPoll = false; } }
document.addEventListener('visibilitychange', () => { if (!document.hidden) tickPoll(); });
tickPoll(); setInterval(tickPoll, 2500);
