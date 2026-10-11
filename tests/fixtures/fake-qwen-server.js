// Fake Qwen Code / DashScope HTTP server for tests/test_runtime_qwen.sh (no key, no network, no cost).
// Public interface: node tests/fixtures/fake-qwen-server.js [PORT]
// Modeled on tests/fixtures/fake-model-api.js: logs every request, serves canned DashScope API
// responses and Qwen Code CLI stream-json events (init, tool_use, message, result, error, turn.failed)
// matching what bin/lib/runtimes.py's scan('qwen-code', ...) parses. No external npm dependencies.

const http = require('http');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8097);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

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

const sse = (res, events) => {
  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    'connection': 'close'
  });
  for (const ev of events) {
    res.write('data: ' + JSON.stringify(ev) + '\n\n');
  }
  res.end();
};

const defaultEvents = (sid) => [
  { type: 'init', session_id: sid },
  { type: 'tool_use', name: 'shell', command: 'git status' },
  { type: 'message', role: 'assistant', content: 'Completed task successfully.' },
  {
    type: 'result',
    status: 'success',
    stats: {
      total_tokens: 1500,
      input_tokens: 1200,
      output_tokens: 300,
      cached: 200
    }
  }
];

const errorEvents = (sid, errMsg) => [
  { type: 'init', session_id: sid },
  { type: 'error', message: errMsg || 'DashScope API error: invalid API key' },
  {
    type: 'result',
    status: 'error',
    error: errMsg || 'DashScope API error: invalid API key',
    stats: { total_tokens: 100, input_tokens: 100, output_tokens: 0, cached: 0 }
  }
];

const failedEvents = (sid, errMsg) => [
  { type: 'init', session_id: sid },
  {
    type: 'turn.failed',
    error: { message: errMsg || 'DashScope task execution failed' }
  }
];

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    let j = {};
    try { j = JSON.parse(body || '{}'); } catch (e) {}
    const sid = j.session_id || j.sessionId || j.thread_id || j.conversation_id || j.sessionID || 'ses_qwen_test_01';
    log(req.method, req.url, 'model=' + (j.model || ''), 'session=' + sid, 'bytes=' + body.length);

    if (req.url === '/health' || req.url === '/healthz') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok' }));
      return;
    }

    if (req.url.includes('/error') || j.error) {
      return ndjson(res, errorEvents(sid, j.error));
    }

    if (req.url.includes('/failed') || req.url.includes('/turn-failed') || j.turn_failed) {
      return ndjson(res, failedEvents(sid, j.turn_failed));
    }

    // OpenAI/DashScope compatible chat completions endpoint
    if (req.url.includes('/chat/completions')) {
      if (j.stream) {
        return sse(res, [
          { id: 'chatcmpl-' + Date.now(), choices: [{ delta: { role: 'assistant', content: 'Completed task successfully.' } }] },
          { id: 'chatcmpl-' + Date.now(), choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } }
        ]);
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        id: 'chatcmpl-' + Date.now(),
        choices: [{ message: { role: 'assistant', content: 'Completed task successfully.' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 }
      }));
      return;
    }

    // Native DashScope text generation API endpoint
    if (req.url.includes('/services/aigc/text-generation')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        request_id: 'req_' + Date.now(),
        output: { text: 'Completed task successfully.', finish_reason: 'stop', session_id: sid },
        usage: { input_tokens: 1200, output_tokens: 300, total_tokens: 1500 }
      }));
      return;
    }

    // Default: stream-json events for Qwen Code CLI
    return ndjson(res, defaultEvents(sid));
  });
});

server.listen(PORT, '127.0.0.1', () => {
  log('fake Qwen Code server listening on 127.0.0.1:' + PORT);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
