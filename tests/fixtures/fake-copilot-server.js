// Fake Copilot HTTP server for tests/test_runtime_copilot.sh (no key, no network, no cost).
// Serves newline-delimited JSON events mimicking Copilot CLI output stream:
// start, message, tool, and result events with sessionID and non-zero tool count.
// Starts on configured port (process.env.PORT or argv or 8098), logs every request, stops cleanly.
const http = require('http');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8098);

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
        { type: 'start', sessionID: sid },
        { type: 'result', status: 'error', error: j.error || 'simulated rate limit', sessionID: sid, part: { reason: 'error', error: j.error || 'simulated rate limit' } }
      ];
      return ndjson(res, errEvents);
    }

    // Default response: non-interactive Copilot run event stream
    // Events parsed by bin/lib/runtimes.py's scan('copilot', ...):
    // 1. start with sessionID
    // 2. message with text content
    // 3. tool with tool type / name
    // 4. result with reason="stop" and status SUCCESS
    const events = [
      {
        type: 'start',
        sessionID: sid,
        part: { id: 'part_start_01' }
      },
      {
        type: 'message',
        sessionID: sid,
        part: { text: 'Done: wrote hello.txt.' }
      },
      {
        type: 'tool',
        name: 'bash',
        sessionID: sid,
        part: {
          type: 'tool',
          tool: 'bash',
          command: 'echo "hello from copilot" > hello.txt'
        }
      },
      {
        type: 'result',
        status: 'success',
        sessionID: sid,
        part: { reason: 'stop' }
      }
    ];

    return ndjson(res, events);
  });
});

server.listen(PORT, '127.0.0.1', () => {
  log('fake Copilot server listening on 127.0.0.1:' + PORT);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
