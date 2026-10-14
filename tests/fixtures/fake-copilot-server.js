/**
 * Fake Copilot HTTP server fixture for tests/test_runtime_copilot.sh (no key, no network, no cost).
 * Public interface: node tests/fixtures/fake-copilot-server.js [PORT]
 * Important invariants:
 * - Listens on 127.0.0.1 on the port provided via argv or PORT env var (defaults to 8098).
 * - Serves newline-delimited JSON (NDJSON) events identical in structure to fake-opencode-server.js:
 *   step_start, tool_use, text, and step_finish events with sessionID and non-zero tool count.
 * - Deterministic, fixed canned events, no randomness, no real network calls outside localhost, no API key.
 * - Logs each request and shuts down cleanly on SIGTERM and SIGINT via stop() helper.
 */
const http = require('http');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const PORT = process.env.COPILOT_SERVER_PORT
  ? parseInt(process.env.COPILOT_SERVER_PORT, 10)
  : (process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8098));

const ndjson = (res, events) => {
  res.writeHead(200, {
    'content-type': 'application/x-ndjson',
    'cache-control': 'no-cache',
    'connection': 'close'
  });
  for (const ev of events) {
    res.write(JSON.stringify(ev) + '\n');
  }
  res.end();
};

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    let j = {};
    try { j = JSON.parse(body || '{}'); } catch (e) {}
    const sid = j.sessionID || j.session_id || j.sessionId || j.session || 'ses_copilot_test_01';
    log(req.method, req.url, 'session=' + sid, 'bytes=' + body.length);

    if (req.url === '/health' || req.url === '/healthz') {
      res.writeHead(200, {'content-type': 'application/json'});
      res.end(JSON.stringify({status: 'ok'}));
      return;
    }

    if (req.url.includes('/error') || j.error) {
      const errEvents = [
        { type: 'step_start', sessionID: sid },
        { type: 'step_finish', sessionID: sid, part: { reason: 'error', error: j.error || 'simulated rate limit' } }
      ];
      return ndjson(res, errEvents);
    }

    // Default response: non-interactive Copilot run event stream
    // Events parsed by bin/lib/runtimes.py's scan('copilot', ...):
    // 1. step_start with sessionID
    // 2. tool_use with tool type / name
    // 3. text / message with text content
    // 4. step_finish with reason="stop" and status SUCCESS
    const events = [
      {
        type: 'step_start',
        sessionID: sid,
        part: { id: 'part_start_01' }
      },
      {
        type: 'tool_use',
        name: 'bash',
        sessionID: sid,
        part: {
          type: 'tool',
          tool: 'bash',
          command: 'echo "hello from copilot" > hello.txt'
        }
      },
      {
        type: 'text',
        sessionID: sid,
        part: { text: 'Done: wrote hello.txt.' }
      },
      {
        type: 'step_finish',
        sessionID: sid,
        part: { reason: 'stop' }
      }
    ];

    return ndjson(res, events);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  const boundPort = (server.address() && server.address().port) || PORT;
  log('fake Copilot server listening on 127.0.0.1:' + boundPort);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { server, stop };
}
