// Fake Mistral Vibe HTTP server for tests/test_runtime_mistral_vibe.sh (no key, no network, no cost).
// Public interface: node tests/fixtures/fake-mistral-vibe-server.js [PORT]
// Modeled on tests/fixtures/fake-qwen-server.js: logs every request, serves canned Mistral AI API
// responses and Mistral Vibe CLI stream-json events (session, tool_use, message, step_finish, result, error, turn.failed)
// matching what bin/lib/runtimes.py's scan('mistral-vibe', ...) parses. No external npm dependencies.

const http = require('http');

const PORT = process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8099);
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
  { type: 'session', conversation_id: sid },
  { type: 'tool_use', name: 'bash', part: { type: 'tool_use', command: 'git status' } },
  { type: 'message', role: 'assistant', text: 'Completed task with Mistral Vibe.' },
  {
    type: 'step_finish',
    part: { reason: 'stop' },
    usage: {
      total_tokens: 1500,
      input_tokens: 1200,
      output_tokens: 300,
      cached_tokens: 200
    }
  }
];

const resultEvents = (sid) => [
  { type: 'session', conversation_id: sid },
  { type: 'tool_call', name: 'bash', tool: 'bash' },
  { type: 'message', text: 'Completed task with Mistral Vibe.' },
  {
    type: 'result',
    status: 'success',
    response: 'Completed task with Mistral Vibe.',
    usage: {
      total_tokens: 1500,
      input_tokens: 1200,
      output_tokens: 300,
      cached_tokens: 200
    }
  }
];

const errorEvents = (sid, errMsg) => [
  { type: 'session', conversation_id: sid },
  { type: 'error', message: errMsg || 'Mistral API error: invalid API key' },
  {
    type: 'step_finish',
    part: {
      reason: 'error',
      error: errMsg || 'Mistral API error: invalid API key'
    },
    usage: { total_tokens: 100, input_tokens: 100, output_tokens: 0, cached_tokens: 0 }
  }
];

const failedEvents = (sid, errMsg) => [
  { type: 'session', conversation_id: sid },
  {
    type: 'turn.failed',
    error: { message: errMsg || 'Mistral Vibe task execution failed' }
  }
];

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    let j = {};
    try { j = JSON.parse(body || '{}'); } catch (e) {}
    const sid = j.conversation_id || j.session_id || j.sessionId || j.thread_id || j.session || 'ses_mistral_vibe_test_01';
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

    if (req.url.includes('/result') || j.use_result_event) {
      return ndjson(res, resultEvents(sid));
    }

    // Mistral / OpenAI compatible chat completions endpoint
    if (req.url.includes('/chat/completions')) {
      if (j.stream) {
        return sse(res, [
          { id: 'mistralcmpl-' + Date.now(), choices: [{ delta: { role: 'assistant', content: 'Completed task with Mistral Vibe.' } }] },
          { id: 'mistralcmpl-' + Date.now(), choices: [{ delta: {}, finish_reason: 'stop' }], usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 } }
        ]);
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        id: 'mistralcmpl-' + Date.now(),
        choices: [{ message: { role: 'assistant', content: 'Completed task with Mistral Vibe.' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 1200, completion_tokens: 300, total_tokens: 1500 }
      }));
      return;
    }

    // Native Mistral FIM / embeddings / models endpoints
    if (req.url.includes('/v1/models')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        object: 'list',
        data: [{ id: 'codestral-latest', object: 'model' }, { id: 'mistral-large-latest', object: 'model' }]
      }));
      return;
    }

    // Default: stream-json events for Mistral Vibe CLI
    return ndjson(res, defaultEvents(sid));
  });
});

server.listen(PORT, '127.0.0.1', () => {
  const boundPort = server.address().port;
  log('fake Mistral Vibe server listening on 127.0.0.1:' + boundPort);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
