/**
 * Fake local OpenAI-compatible HTTP server fixture for local model runtimes (Ollama, vLLM, LM Studio, llama.cpp).
 * Public interface: PORT=<port> node fake-local-compat-server.js or node fake-local-compat-server.js <port>
 * Important invariants:
 * - Listens on 127.0.0.1 on the port provided via argv or PORT env var (no hardcoded single port).
 * - Responds to POST /v1/chat/completions and /chat/completions with deterministic OpenAI completion JSON.
 * - Response includes model name, usage token counts, and assistant message; zero external network calls.
 * - Shuts down cleanly on SIGTERM and SIGINT with exit code 0.
 */

const http = require('http');
const fs = require('fs');

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function resolvePort(argv = process.argv, env = process.env) {
  // Check CLI flags --port 1234 or --port=1234
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--port=')) {
      const p = parseInt(a.slice(7), 10);
      if (Number.isInteger(p) && p >= 0 && p <= 65535) return p;
    }
    if ((a === '--port' || a === '-p') && i + 1 < argv.length) {
      const p = parseInt(argv[i + 1], 10);
      if (Number.isInteger(p) && p >= 0 && p <= 65535) return p;
    }
  }

  // Check positional argument that looks like a port (e.g. node server.js 11434)
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('-')) {
      const p = parseInt(a, 10);
      if (Number.isInteger(p) && p >= 0 && p <= 65535) return p;
    }
  }

  // Check PORT environment variable (e.g. PORT=11434)
  if (env.PORT !== undefined && env.PORT !== '') {
    const p = parseInt(env.PORT, 10);
    if (Number.isInteger(p) && p >= 0 && p <= 65535) return p;
  }

  return 8000;
}

function defaultCompletion(j = {}) {
  const msgs = j.messages || [];
  const sys = msgs.filter(m => m.role === 'system').map(m => String(m.content || '')).join('\n');
  const allText = msgs.map(m => String(m.content || '')).join('\n');
  const diff = /SEARCH/.test(sys) && /REPLACE/.test(sys);
  const fence = '```';

  let content = 'Task completed successfully.';
  if (diff) {
    content = 'hello.txt\n' + fence + '\n<<<<<<< SEARCH\n=======\nhello\n>>>>>>> REPLACE\n' + fence + '\n';
  } else if (/hello\.txt/.test(allText)) {
    content = 'hello.txt\n' + fence + '\nhello\n' + fence + '\n';
  }

  return {
    id: 'chatcmpl-fake-local-001',
    object: 'chat.completion',
    created: 1700000000,
    model: j.model || 'local-compat-model',
    choices: [
      {
        index: 0,
        message: {
          role: 'assistant',
          content
        },
        finish_reason: 'stop'
      }
    ],
    usage: {
      prompt_tokens: 1200,
      completion_tokens: 40,
      total_tokens: 1240
    }
  };
}

function createServer(options = {}) {
  return http.createServer((req, res) => {
    let body = '';
    req.on('data', c => { body += c; });
    req.on('end', () => {
      let j = {};
      try { j = JSON.parse(body || '{}'); } catch (e) {}

      if (!options.silent) {
        log(req.method, req.url, 'model=' + (j.model || ''), 'stream=' + Boolean(j.stream), 'bytes=' + body.length);
      }

      const logFile = process.env.PROXY_LOG || process.env.LOG_FILE;
      if (logFile) {
        try {
          fs.appendFileSync(logFile, `${req.method} ${req.url}\n`);
        } catch (err) {}
      }

      if (req.url === '/health' || req.url === '/healthz') {
        res.writeHead(200, { 'content-type': 'application/json', 'connection': 'close' });
        res.end(JSON.stringify({ status: 'ok' }));
        return;
      }

      if (req.url.includes('/models')) {
        res.writeHead(200, { 'content-type': 'application/json', 'connection': 'close' });
        res.end(JSON.stringify({
          object: 'list',
          data: [{ id: j.model || 'local-compat-model', object: 'model', created: 1700000000, owned_by: 'local' }]
        }));
        return;
      }

      if (req.url.includes('/error') || j.error) {
        res.writeHead(400, { 'content-type': 'application/json', 'connection': 'close' });
        res.end(JSON.stringify({
          error: {
            message: j.error || 'simulated local compat server error',
            type: 'invalid_request_error',
            code: 'simulated_error'
          }
        }));
        return;
      }

      if (req.method === 'POST' && req.url.includes('/chat/completions')) {
        const completion = defaultCompletion(j);

        if (j.stream) {
          res.writeHead(200, {
            'content-type': 'text/event-stream',
            'cache-control': 'no-cache',
            'connection': 'close'
          });
          const chunk1 = {
            id: completion.id,
            object: 'chat.completion.chunk',
            created: completion.created,
            model: completion.model,
            choices: [{
              index: 0,
              delta: { role: 'assistant', content: completion.choices[0].message.content },
              finish_reason: null
            }]
          };
          const chunk2 = {
            id: completion.id,
            object: 'chat.completion.chunk',
            created: completion.created,
            model: completion.model,
            choices: [{
              index: 0,
              delta: {},
              finish_reason: 'stop'
            }],
            usage: completion.usage
          };
          res.write('data: ' + JSON.stringify(chunk1) + '\n\n');
          res.write('data: ' + JSON.stringify(chunk2) + '\n\n');
          res.write('data: [DONE]\n\n');
          res.end();
          return;
        }

        res.writeHead(200, {
          'content-type': 'application/json',
          'connection': 'close'
        });
        res.end(JSON.stringify(completion));
        return;
      }

      res.writeHead(404, { 'content-type': 'application/json', 'connection': 'close' });
      res.end(JSON.stringify({ error: 'not faked', path: req.url }));
    });
  });
}

function startServer(port = resolvePort()) {
  const server = createServer();
  server.listen(port, '127.0.0.1', () => {
    const bound = server.address().port;
    log('fake local compat server listening on 127.0.0.1:' + bound);
  });

  const stop = () => {
    server.close(() => {
      process.exit(0);
    });
    setTimeout(() => process.exit(0), 2000).unref();
  };

  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);

  return server;
}

async function runTests() {
  const assert = require('assert');
  console.log('Running fake-local-compat-server test suite...');

  // 1. Port resolution tests
  assert.strictEqual(resolvePort(['node', 'script', '11434'], {}), 11434, 'test_port_from_argv_ollama_11434');
  console.log('ok   test_port_from_argv_ollama_11434');

  assert.strictEqual(resolvePort(['node', 'script', '8000'], {}), 8000, 'test_port_from_argv_vllm_8000');
  console.log('ok   test_port_from_argv_vllm_8000');

  assert.strictEqual(resolvePort(['node', 'script', '1234'], {}), 1234, 'test_port_from_argv_lmstudio_1234');
  console.log('ok   test_port_from_argv_lmstudio_1234');

  assert.strictEqual(resolvePort(['node', 'script', '8080'], {}), 8080, 'test_port_from_argv_llamacpp_8080');
  console.log('ok   test_port_from_argv_llamacpp_8080');

  assert.strictEqual(resolvePort(['node', 'script'], { PORT: '11434' }), 11434, 'test_port_from_env_ollama_11434');
  console.log('ok   test_port_from_env_ollama_11434');

  assert.strictEqual(resolvePort(['node', 'script'], { PORT: '8000' }), 8000, 'test_port_from_env_vllm_8000');
  console.log('ok   test_port_from_env_vllm_8000');

  assert.strictEqual(resolvePort(['node', 'script'], { PORT: '1234' }), 1234, 'test_port_from_env_lmstudio_1234');
  console.log('ok   test_port_from_env_lmstudio_1234');

  assert.strictEqual(resolvePort(['node', 'script'], { PORT: '8080' }), 8080, 'test_port_from_env_llamacpp_8080');
  console.log('ok   test_port_from_env_llamacpp_8080');

  assert.strictEqual(resolvePort(['node', 'script', '1234'], { PORT: '8080' }), 1234, 'test_argv_overrides_env_port');
  console.log('ok   test_argv_overrides_env_port');

  assert.strictEqual(resolvePort(['node', 'script'], {}), 8000, 'test_port_fallback_default_when_unset');
  console.log('ok   test_port_fallback_default_when_unset');

  assert.strictEqual(resolvePort(['node', 'script', 'not-a-number'], { PORT: 'bad' }), 8000, 'test_port_fallback_when_invalid');
  console.log('ok   test_port_fallback_when_invalid');

  assert.strictEqual(resolvePort(['node', 'script', '0'], {}), 0, 'test_port_zero_ephemeral');
  console.log('ok   test_port_zero_ephemeral');

  assert.strictEqual(resolvePort(['node', 'script', '--port=9090'], {}), 9090, 'test_port_flag_long_syntax');
  console.log('ok   test_port_flag_long_syntax');

  assert.strictEqual(resolvePort(['node', 'script', '-p', '9091'], {}), 9091, 'test_port_flag_short_syntax');
  console.log('ok   test_port_flag_short_syntax');

  // Start temporary server on ephemeral port (0) for wire tests
  const testServer = createServer({ silent: true });
  await new Promise(resolve => testServer.listen(0, '127.0.0.1', resolve));
  const testPort = testServer.address().port;

  const request = (path, method = 'POST', data = null, headers = {}) => {
    return new Promise((resolve, reject) => {
      const payload = data ? (typeof data === 'string' ? data : JSON.stringify(data)) : null;
      const reqHeaders = { ...headers };
      if (payload && !reqHeaders['Content-Type']) {
        reqHeaders['Content-Type'] = 'application/json';
      }
      if (payload) {
        reqHeaders['Content-Length'] = Buffer.byteLength(payload);
      }
      const req = http.request({
        hostname: '127.0.0.1',
        port: testPort,
        path,
        method,
        headers: reqHeaders
      }, res => {
        let resBody = '';
        res.on('data', d => { resBody += d; });
        res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: resBody }));
      });
      req.on('error', reject);
      if (payload) req.write(payload);
      req.end();
    });
  };

  try {
    // 2. Chat completions POST standard format
    const r1 = await request('/v1/chat/completions', 'POST', {
      model: 'mistral-local',
      messages: [{ role: 'user', content: 'Say hello' }]
    });
    assert.strictEqual(r1.status, 200, 'test_chat_completions_status_200');
    assert.strictEqual(r1.headers['content-type'], 'application/json');
    const b1 = JSON.parse(r1.body);
    assert.strictEqual(b1.id, 'chatcmpl-fake-local-001');
    assert.strictEqual(b1.object, 'chat.completion');
    assert.strictEqual(b1.created, 1700000000);
    assert.strictEqual(b1.model, 'mistral-local');
    assert.strictEqual(b1.choices.length, 1);
    assert.strictEqual(b1.choices[0].index, 0);
    assert.strictEqual(b1.choices[0].message.role, 'assistant');
    assert.strictEqual(b1.choices[0].message.content, 'Task completed successfully.');
    assert.strictEqual(b1.choices[0].finish_reason, 'stop');
    assert.strictEqual(b1.usage.prompt_tokens, 1200);
    assert.strictEqual(b1.usage.completion_tokens, 40);
    assert.strictEqual(b1.usage.total_tokens, 1240);
    console.log('ok   test_chat_completions_post_returns_deterministic_openai_json');

    // 3. Unprefixed path /chat/completions
    const r2 = await request('/chat/completions', 'POST', { model: 'llama-3' });
    assert.strictEqual(r2.status, 200);
    const b2 = JSON.parse(r2.body);
    assert.strictEqual(b2.model, 'llama-3');
    assert.strictEqual(b2.object, 'chat.completion');
    console.log('ok   test_chat_completions_unprefixed_path');

    // 4. Default model when omitted
    const r3 = await request('/v1/chat/completions', 'POST', {});
    assert.strictEqual(r3.status, 200);
    const b3 = JSON.parse(r3.body);
    assert.strictEqual(b3.model, 'local-compat-model');
    console.log('ok   test_chat_completions_default_model_when_omitted');

    // 5. Query parameters handling
    const r4 = await request('/v1/chat/completions?stream=false&preset=ollama', 'POST', { model: 'ollama-model' });
    assert.strictEqual(r4.status, 200);
    const b4 = JSON.parse(r4.body);
    assert.strictEqual(b4.model, 'ollama-model');
    console.log('ok   test_chat_completions_with_query_params');

    // 6. Streaming SSE response
    const r5 = await request('/v1/chat/completions', 'POST', { model: 'vllm-stream', stream: true });
    assert.strictEqual(r5.status, 200);
    assert.ok(r5.headers['content-type'].includes('text/event-stream'));
    assert.ok(r5.body.includes('data: {"id":"chatcmpl-fake-local-001"'));
    assert.ok(r5.body.includes('data: [DONE]'));
    console.log('ok   test_chat_completions_streaming_sse');

    // 7. Empty body handling
    const r6 = await request('/v1/chat/completions', 'POST', '');
    assert.strictEqual(r6.status, 200);
    const b6 = JSON.parse(r6.body);
    assert.strictEqual(b6.model, 'local-compat-model');
    console.log('ok   test_chat_completions_empty_body');

    // 8. Malformed JSON body handling
    const r7 = await request('/v1/chat/completions', 'POST', '{not-valid-json');
    assert.strictEqual(r7.status, 200);
    const b7 = JSON.parse(r7.body);
    assert.strictEqual(b7.model, 'local-compat-model');
    console.log('ok   test_chat_completions_malformed_json_body');

    // 9. Unicode and large payload handling
    const unicodeText = '🚀🔥 Unicode prompt with accents éàç and Chinese characters 计算机';
    const largePrompt = 'A'.repeat(50000) + ' ' + unicodeText;
    const r8 = await request('/v1/chat/completions', 'POST', {
      model: 'unicode-model',
      messages: [{ role: 'user', content: largePrompt }]
    });
    assert.strictEqual(r8.status, 200);
    const b8 = JSON.parse(r8.body);
    assert.strictEqual(b8.model, 'unicode-model');
    assert.strictEqual(b8.choices[0].message.content, 'Task completed successfully.');
    console.log('ok   test_chat_completions_unicode_and_large_payload');

    // 10. Aider SEARCH/REPLACE diff format
    const r9 = await request('/v1/chat/completions', 'POST', {
      model: 'aider-model',
      messages: [
        { role: 'system', content: 'Use SEARCH/REPLACE blocks' },
        { role: 'user', content: 'create hello.txt' }
      ]
    });
    assert.strictEqual(r9.status, 200);
    const b9 = JSON.parse(r9.body);
    assert.ok(b9.choices[0].message.content.includes('<<<<<<< SEARCH'));
    assert.ok(b9.choices[0].message.content.includes('hello.txt'));
    console.log('ok   test_chat_completions_aider_diff_format');

    // 11. Health and healthz endpoints
    const r10 = await request('/health', 'GET');
    assert.strictEqual(r10.status, 200);
    assert.strictEqual(JSON.parse(r10.body).status, 'ok');
    const r11 = await request('/healthz', 'GET');
    assert.strictEqual(r11.status, 200);
    assert.strictEqual(JSON.parse(r11.body).status, 'ok');
    console.log('ok   test_health_and_healthz_routes');

    // 12. Models endpoint
    const r12 = await request('/v1/models', 'GET');
    assert.strictEqual(r12.status, 200);
    const b12 = JSON.parse(r12.body);
    assert.strictEqual(b12.object, 'list');
    console.log('ok   test_models_route');

    // 13. Simulated error handling
    const r13 = await request('/v1/chat/completions', 'POST', { error: 'simulated rate limit' });
    assert.strictEqual(r13.status, 400);
    const b13 = JSON.parse(r13.body);
    assert.ok(b13.error);
    assert.strictEqual(b13.error.message, 'simulated rate limit');
    console.log('ok   test_error_endpoint_simulation');

    // 14. 404 for unknown path
    const r14 = await request('/unknown/path', 'GET');
    assert.strictEqual(r14.status, 404);
    assert.strictEqual(JSON.parse(r14.body).error, 'not faked');
    console.log('ok   test_unknown_path_returns_404');

    // 15. Concurrency of repeated calls
    const concurrentRequests = Array.from({ length: 10 }, (_, i) =>
      request('/v1/chat/completions', 'POST', { model: `concurrent-${i}` })
    );
    const results = await Promise.all(concurrentRequests);
    for (let i = 0; i < results.length; i++) {
      assert.strictEqual(results[i].status, 200);
      assert.strictEqual(JSON.parse(results[i].body).model, `concurrent-${i}`);
    }
    console.log('ok   test_concurrency_repeated_calls');

  } finally {
    await new Promise(resolve => testServer.close(resolve));
    console.log('ok   test_server_close_exits_cleanly');
  }

  console.log('ALL TESTS PASSED');
}

if (require.main === module) {
  if (process.argv.includes('--test')) {
    runTests().catch(err => {
      console.error('Test failure:', err);
      process.exit(1);
    });
  } else {
    startServer();
  }
}

module.exports = {
  resolvePort,
  defaultCompletion,
  createServer,
  startServer,
  runTests
};
