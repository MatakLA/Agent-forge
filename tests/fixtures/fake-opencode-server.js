// Fake OpenCode HTTP server for tests/test_runtime_opencode.sh (no key, no network, no cost).
// Serves newline-delimited JSON events mimicking SST's OpenCode CLI output stream:
// step_start, tool_use, text, and step_finish events with sessionID and non-zero tool count.
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
    const sid = j.sessionID || j.session_id || j.sessionId || j.session || 'ses_opencode_test_01';
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

    // Default response: non-interactive OpenCode run event stream
    // Events parsed by bin/lib/runtimes.py's scan('opencode', ...):
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
          command: 'echo "hello from opencode" > hello.txt'
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
  log('fake OpenCode server listening on 127.0.0.1:' + PORT);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
