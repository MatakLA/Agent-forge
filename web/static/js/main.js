// Start: the first render, a deep link (#task=… / #goal=… / #run=…), the live connection.
loadLocalesList();
setTab(tab);
loadProjects(true).then(() => {
  connect();
  if (HASH.task && /^\d{4}-[\w-]+$/.test(HASH.task)) openTask(HASH.task);
  else if (HASH.goal && /^G\d{8}-\d{6}[\w-]*$/.test(HASH.goal)) openGoal(HASH.goal);
  else if (HASH.run && /^\d{8}T\d{6}Z-worker-\d+$/.test(HASH.run)) openRun(HASH.run);
});
setInterval(() => loadProjects(), 30000);
setInterval(() => schedule(), 15000);            // "x min ago" texts keep moving even without new data
