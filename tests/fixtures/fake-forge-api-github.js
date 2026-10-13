// Fake GitHub forge API HTTP server for tests/test_issues_intake_parsing.sh (no key, no network, no cost).
// Public interface: node tests/fixtures/fake-forge-api-github.js [PORT]
// Invariants:
// - Self-contained Node http server using only Node.js stdlib (no external dependencies).
// - Serves GitHub-style /repos/:owner/:repo/issues endpoint returning repeatable issue payloads.
// - Covers at least 4 issues with real triage labels (bug, docs, security, feature) plus 1 non-matching label.
// - Starts on configured port (process.env.PORT or argv[2] or 0 for ephemeral) and logs all requests.
// - Shuts down cleanly on SIGTERM/SIGINT.

const http = require('http');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 0);

const ISSUES = [
  {
    id: 1001,
    number: 1,
    title: 'Fix session cookie expiration bug',
    body: 'Session cookie expires prematurely on dashboard refresh.<br>Expected: <b>preserve session</b> until expiry.',
    state: 'open',
    labels: [
      { id: 11, name: 'agent-forge' },
      { id: 12, name: 'bug' }
    ],
    html_url: 'https://github.com/agent-forge/agent-forge/issues/1',
    user: { id: 1, login: 'qa-tester' },
    created_at: '2026-10-13T09:00:00Z',
    updated_at: '2026-10-13T09:00:00Z'
  },
  {
    id: 1002,
    number: 2,
    title: 'Update documentation for custom providers',
    body: 'Add guide for custom OpenAI-compatible providers in docs/PROVIDERS.md.',
    state: 'open',
    labels: [
      { id: 11, name: 'agent-forge' },
      { id: 13, name: 'docs' }
    ],
    html_url: 'https://github.com/agent-forge/agent-forge/issues/2',
    user: { id: 2, login: 'tech-writer' },
    created_at: '2026-10-13T09:05:00Z',
    updated_at: '2026-10-13T09:05:00Z'
  },
  {
    id: 1003,
    number: 3,
    title: 'Perform security audit on egress firewall',
    body: 'Verify sandbox container iptables rules block direct host access.<p>Audit drop policies and port isolation.</p>',
    state: 'open',
    labels: [
      { id: 11, name: 'agent-forge' },
      { id: 14, name: 'security' }
    ],
    html_url: 'https://github.com/agent-forge/agent-forge/issues/3',
    user: { id: 3, login: 'sec-auditor' },
    created_at: '2026-10-13T09:10:00Z',
    updated_at: '2026-10-13T09:10:00Z'
  },
  {
    id: 1004,
    number: 4,
    title: 'Add token count metric in status',
    body: 'Expose token count metrics in status command and dashboard overview.',
    state: 'open',
    labels: [
      { id: 11, name: 'agent-forge' },
      { id: 15, name: 'feature' }
    ],
    html_url: 'https://github.com/agent-forge/agent-forge/issues/4',
    user: { id: 4, login: 'dev-lead' },
    created_at: '2026-10-13T09:15:00Z',
    updated_at: '2026-10-13T09:15:00Z'
  },
  {
    id: 1005,
    number: 5,
    title: 'Triage incoming community feedback notes',
    body: 'Community discussion notes that require triage before assignment.',
    state: 'open',
    labels: [
      { id: 11, name: 'agent-forge' },
      { id: 16, name: 'triage-needed' }
    ],
    html_url: 'https://github.com/agent-forge/agent-forge/issues/5',
    user: { id: 5, login: 'community-user' },
    created_at: '2026-10-13T09:20:00Z',
    updated_at: '2026-10-13T09:20:00Z'
  }
];

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

  // Handle GitHub repo issues endpoint (/repos/:owner/:repo/issues)
  if (req.url.includes('/issues')) {
    const payload = JSON.stringify(ISSUES);
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
  const boundPort = server.address().port;
  log('fake GitHub forge API server listening on 127.0.0.1:' + boundPort);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
