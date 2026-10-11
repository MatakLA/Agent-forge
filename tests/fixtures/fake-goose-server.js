/**
 * Fake Goose model API HTTP server for tests/test_runtime_goose.sh (no key, no network, no cost).
 * Public interface: node tests/fixtures/fake-goose-server.js [PORT]
 * Important invariants:
 * - Modeled directly on tests/fixtures/fake-model-api.js with zero external npm dependencies.
 * - Fakes OpenAI/Anthropic-compatible endpoint shapes (/v1/chat/completions, /v1/messages, /responses).
 * - Takes port from argv or PORT/GOOSE_SERVER_PORT environment variables.
 * - Logs the first line and Host header of every request so tests can assert worker reached expected host:port.
 */
const http = require('http');
const fs = require('fs');

const PORT = process.env.GOOSE_SERVER_PORT
  ? parseInt(process.env.GOOSE_SERVER_PORT, 10)
  : (process.env.PORT ? parseInt(process.env.PORT, 10) : (process.argv[2] ? parseInt(process.argv[2], 10) : 8099));

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

const sse = (res, events) => {
  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    'connection': 'close'
  });
  for (const [ev, data] of events) {
    res.write((ev ? 'event: ' + ev + '\n' : '') + 'data: ' + JSON.stringify(data) + '\n\n');
  }
  res.end();
};

const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', c => { body += c; });
  req.on('end', () => {
    let j = {};
    try { j = JSON.parse(body || '{}'); } catch (e) {}

    const reqLine = `${req.method} ${req.url} HTTP/${req.httpVersion}`;
    const host = req.headers.host || '';
    log(reqLine, 'Host: ' + host, 'model=' + (j.model || ''), 'bytes=' + body.length);

    const logFile = process.env.PROXY_LOG || process.env.LOG_FILE;
    if (logFile) {
      try {
        fs.appendFileSync(logFile, `${reqLine} Host: ${host}\n`);
      } catch (err) {}
    }

    // Health check endpoint
    if (req.url === '/health' || req.url === '/healthz') {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok' }));
      return;
    }

    // Explicit error simulation
    if (req.url.includes('/error') || j.error) {
      res.writeHead(400, { 'content-type': 'application/json' });
      res.end(JSON.stringify({
        error: {
          message: j.error || 'simulated goose endpoint error',
          type: 'invalid_request_error',
          code: 'simulated_error'
        }
      }));
      return;
    }

    // 1. Anthropic Messages API format (/v1/messages)
    if (req.url.includes('/messages')) {
      const usage = { input_tokens: 100, output_tokens: 30 };
      if (!j.stream) {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({
          id: 'msg_' + Date.now(),
          type: 'message',
          role: 'assistant',
          model: j.model || 'claude-3-5-sonnet',
          content: [
            { type: 'tool_use', id: 'toolu_01', name: 'bash', input: { command: 'git status' } },
            { type: 'text', text: 'Task completed successfully.' }
          ],
          stop_reason: 'end_turn',
          usage
        }));
        return;
      }
      const ev = [
        ['message_start', { type: 'message_start', message: { id: 'msg_' + Date.now(), role: 'assistant', model: j.model, usage } }],
        ['content_block_start', { type: 'content_block_start', index: 0, content_block: { type: 'tool_use', id: 'toolu_01', name: 'bash', input: {} } }],
        ['content_block_delta', { type: 'content_block_delta', index: 0, delta: { type: 'input_json_delta', partial_json: '{"command":"git status"}' } }],
        ['content_block_stop', { type: 'content_block_stop', index: 0 }],
        ['content_block_start', { type: 'content_block_start', index: 1, content_block: { type: 'text', text: '' } }],
        ['content_block_delta', { type: 'content_block_delta', index: 1, delta: { type: 'text_delta', text: 'Task completed successfully.' } }],
        ['content_block_stop', { type: 'content_block_stop', index: 1 }],
        ['message_delta', { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 30 } }],
        ['message_stop', { type: 'message_stop' }]
      ];
      return sse(res, ev);
    }

    // 2. OpenAI Responses API format (/responses)
    if (req.url.includes('/responses')) {
      const id = 'resp_' + Date.now();
      const usage = { input_tokens: 150, output_tokens: 40, total_tokens: 190 };
      const toolItem = {
        type: 'function_call',
        id: 'fc_1',
        call_id: 'call_1',
        name: 'shell',
        arguments: JSON.stringify({ command: 'git status' })
      };
      const textItem = {
        type: 'message',
        id: 'msg_1',
        role: 'assistant',
        status: 'completed',
        content: [{ type: 'output_text', text: 'Task completed successfully.' }]
      };
      if (j.stream) {
        return sse(res, [
          ['response.created', { type: 'response.created', response: { id, status: 'in_progress' } }],
          ['response.output_item.added', { type: 'response.output_item.added', output_index: 0, item: toolItem }],
          ['response.output_item.done', { type: 'response.output_item.done', output_index: 0, item: toolItem }],
          ['response.output_item.added', { type: 'response.output_item.added', output_index: 1, item: textItem }],
          ['response.output_item.done', { type: 'response.output_item.done', output_index: 1, item: textItem }],
          ['response.completed', { type: 'response.completed', response: { id, status: 'completed', output: [toolItem, textItem], usage } }]
        ]);
      }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ id, status: 'completed', output: [toolItem, textItem], usage }));
      return;
    }

    // 3. OpenAI Chat Completions API format (/v1/chat/completions, /chat/completions) & default
    if (j.stream) {
      return sse(res, [
        [null, {
          id: 'chatcmpl-' + Date.now(),
          choices: [{
            index: 0,
            delta: {
              role: 'assistant',
              tool_calls: [{
                index: 0,
                id: 'call_1',
                type: 'function',
                function: { name: 'bash', arguments: '{"command":"git status"}' }
              }]
            }
          }]
        }],
        [null, {
          id: 'chatcmpl-' + Date.now(),
          choices: [{
            index: 0,
            delta: { content: 'Task completed successfully.' },
            finish_reason: 'stop'
          }],
          usage: { prompt_tokens: 120, completion_tokens: 35, total_tokens: 155 }
        }]
      ]);
    }

    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({
      id: 'chatcmpl-' + Date.now(),
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: j.model || 'gpt-4o',
      choices: [{
        index: 0,
        message: {
          role: 'assistant',
          content: 'Task completed successfully.',
          tool_calls: [{
            id: 'call_1',
            type: 'function',
            function: { name: 'bash', arguments: '{"command":"git status"}' }
          }]
        },
        finish_reason: 'stop'
      }],
      usage: { prompt_tokens: 120, completion_tokens: 35, total_tokens: 155 }
    }));
  });
});

server.listen(PORT, '127.0.0.1', () => {
  log('fake Goose server listening on 127.0.0.1:' + PORT);
});

const stop = () => {
  server.close(() => {
    process.exit(0);
  });
};
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
