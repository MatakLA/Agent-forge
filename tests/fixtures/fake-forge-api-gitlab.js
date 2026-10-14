// Fake GitLab forge API HTTP server for tests/test_issues_intake_duplicates.sh (no key, no network, no cost).
// Public interface: node tests/fixtures/fake-forge-api-gitlab.js [PORT]
// Invariants:
// - Self-contained Node http server using only Node.js stdlib (no external dependencies).
// - Serves GitLab-style /api/v4/projects/:id/issues endpoint returning the same repeatable issue.
// - Starts on configured port (process.env.PORT or argv[2] or 8097) and logs all requests.
// - Shuts down cleanly on SIGTERM/SIGINT.

const http = require('http');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8097);

const ISSUE = {
  id: 5001,
  iid: 1,
  project_id: 101,
  title: 'Prevent duplicate goals from repeated issue intake',
  description: 'Ensure that intake runs do not create duplicate goals for identical issues.',
  state: 'opened',
  labels: ['agent-forge', 'bug'],
  web_url: 'https://gitlab.example.com/acme/shop/-/issues/1',
  author: {
    id: 1,
    name: 'QA Tester',
    username: 'qa-tester'
  },
  created_at: '2026-10-13T09:00:00Z',
  updated_at: '2026-10-13T09:00:00Z'
};

const server = http.createServer((req, res) => {
  log(req.method, req.url);

  if (req.url === '/health' || req.url === '/healthz') {
    res.writeHead(200, {
      'content-type': 'application/json',
      'connection': 'close'
    });
    res.end(JSON.stringify({ status: 'ok' }));
    return;
  }

  // Handle GitLab project issues endpoint (/api/v4/projects/:id/issues or /projects/:id/issues)
  if (req.url.includes('/issues')) {
    const payload = JSON.stringify([ISSUE]);
    res.writeHead(200, {
      'content-type': 'application/json',
      'content-length': Buffer.byteLength(payload),
      'connection': 'close'
    });
    res.end(payload);
    return;
  }

  res.writeHead(404, {
    'content-type': 'application/json',
    'connection': 'close'
  });
  res.end(JSON.stringify({ error: 'not found', path: req.url }));
});

server.listen(PORT, '127.0.0.1', () => {
  log('fake GitLab forge API server listening on 127.0.0.1:' + PORT);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
