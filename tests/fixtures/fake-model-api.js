// Fake model APIs for tests/test_runtimes.sh (no key, no network, no cost): Anthropic Messages, OpenAI Responses and Gemini
// streamGenerateContent, all streaming (SSE). The first agentic call answers with one shell tool call that writes hello.txt, the
// call after the tool result gets the final text. Every request is logged (path + model, no headers). Port 8099.
const http = require('http');
// what the "agent" does: like a real worker, work on the task branch, then write the file (the supervisor commits + pushes it)
const CMD = 'git checkout -q feat/0001-x 2>/dev/null || git checkout -q -b feat/0001-x; echo hello > hello.txt';
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sse = (res, events) => { res.writeHead(200, {'content-type': 'text/event-stream', 'cache-control': 'no-cache'});
  for (const [ev, data] of events) res.write((ev ? 'event: ' + ev + '\n' : '') + 'data: ' + JSON.stringify(data) + '\n\n'); res.end(); };
http.createServer((req, res) => {
  let body = ''; req.on('data', c => body += c); req.on('end', () => {
    let j = {}; try { j = JSON.parse(body || '{}'); } catch (e) {}
    log(req.method, req.url, 'model=' + (j.model || ''), 'tools=' + ((j.tools || []).length), 'stream=' + j.stream, 'bytes=' + body.length);
    // ---- Anthropic
    if (req.url.includes('/v1/messages')) {
      const msgs = j.messages || [];
      // any tool result in the conversation means the tool already ran (Claude Code may append a reminder message after it)
      const lastHasResult = msgs.some(m => Array.isArray(m.content) && m.content.some(c => c.type === 'tool_result'));
      const hasBash = (j.tools || []).some(t => t.name === 'Bash');
      const usage = {input_tokens: 1000, output_tokens: 1, cache_creation_input_tokens: 200, cache_read_input_tokens: 300};
      let blocks, stop;
      if (hasBash && !lastHasResult) { blocks = [['tool_use', {type: 'tool_use', id: 'toolu_01', name: 'Bash', input: {}}, '{"command": "' + CMD + '", "description": "write file"}']]; stop = 'tool_use'; }
      else blocks = [['text', {type: 'text', text: ''}, hasBash ? 'Done: wrote hello.txt.' : 'ok']], stop = 'end_turn';
      if (!j.stream) { res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify({id: 'msg_x', type: 'message', role: 'assistant', model: j.model, content: [{type: 'text', text: 'ok'}], stop_reason: 'end_turn', usage})); return; }
      const ev = [['message_start', {type: 'message_start', message: {id: 'msg_' + Date.now(), type: 'message', role: 'assistant', model: j.model, content: [], stop_reason: null, stop_sequence: null, usage}}]];
      blocks.forEach(([kind, start, payload], i) => {
        ev.push(['content_block_start', {type: 'content_block_start', index: i, content_block: start}]);
        ev.push(['content_block_delta', {type: 'content_block_delta', index: i, delta: kind === 'text' ? {type: 'text_delta', text: payload} : {type: 'input_json_delta', partial_json: payload}}]);
        ev.push(['content_block_stop', {type: 'content_block_stop', index: i}]);
      });
      ev.push(['message_delta', {type: 'message_delta', delta: {stop_reason: stop, stop_sequence: null}, usage: {output_tokens: 42}}], ['message_stop', {type: 'message_stop'}]);
      return sse(res, ev);
    }
    // ---- OpenAI Responses (codex)
    if (req.url.includes('/responses')) {
      const input = j.input || [], hasOut = input.some(x => x.type === 'function_call_output' || x.type === 'custom_tool_call_output');
      const usage = {input_tokens: 1500, input_tokens_details: {cached_tokens: 500}, output_tokens: 60, output_tokens_details: {reasoning_tokens: 10}, total_tokens: 1560};
      const tools = (j.tools || []).map(t => t.name || t.type); log('  codex tools:', tools.join(','));
      const id = 'resp_' + Date.now();
      let item;
      if (!hasOut && tools.includes('shell')) item = {type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'shell', arguments: JSON.stringify({command: ['bash', '-lc', CMD]})};
      else if (!hasOut && tools.includes('exec_command')) item = {type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'exec_command', arguments: JSON.stringify({cmd: CMD})};
      else if (!hasOut && tools.includes('shell_command')) item = {type: 'function_call', id: 'fc_1', call_id: 'call_1', name: 'shell_command', arguments: JSON.stringify({command: CMD})};
      else item = {type: 'message', id: 'msg_1', role: 'assistant', status: 'completed', content: [{type: 'output_text', text: 'Done: wrote hello.txt.', annotations: []}]};
      return sse(res, [['response.created', {type: 'response.created', response: {id, status: 'in_progress'}}],
                       ['response.output_item.added', {type: 'response.output_item.added', output_index: 0, item}],
                       ['response.output_item.done', {type: 'response.output_item.done', output_index: 0, item}],
                       ['response.completed', {type: 'response.completed', response: {id, status: 'completed', output: [item], usage}}]]);
    }
    // ---- Gemini
    if (req.url.includes(':streamGenerateContent') || req.url.includes(':generateContent')) {
      const contents = j.contents || [];
      const hasResp = contents.some(c => (c.parts || []).some(p => p.functionResponse));
      const fns = ((j.tools || [])[0] || {}).functionDeclarations || [];
      const shellName = (fns.find(f => /shell/i.test(f.name)) || {}).name;
      log('  gemini fns:', fns.map(f => f.name).join(','));
      const usageMetadata = {promptTokenCount: 1200, candidatesTokenCount: 30, totalTokenCount: 1230, cachedContentTokenCount: 0};
      const parts = (!hasResp && shellName) ? [{functionCall: {name: shellName, args: {command: CMD, description: 'write'}}}] : [{text: shellName ? 'Done: wrote hello.txt.' : 'ok'}];
      const chunk = {candidates: [{content: {role: 'model', parts}, finishReason: 'STOP', index: 0}], usageMetadata, modelVersion: 'gemini-fake'};
      if (req.url.includes(':generateContent')) { res.writeHead(200, {'content-type': 'application/json'}); res.end(JSON.stringify(chunk)); return; }
      return sse(res, [[null, chunk]]);
    }
    res.writeHead(404, {'content-type': 'application/json'}); res.end('{"error":"not faked"}');
  });
}).listen(8099, () => log('fake APIs on :8099'));
