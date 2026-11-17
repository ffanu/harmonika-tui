'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createServer } = require('../tools/mcp-harmonika-tools.js');

process.env.HARMONIKA_API_KEY = 'test-key-123';
delete process.env.HARMONIKA_VISION_URL;

const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const TMP_DIRS = [];
const never = async () => {
  throw new Error('network must not be called');
};

function tmpDir(label) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-harmonika-' + label + '-'));
  TMP_DIRS.push(dir);
  return dir;
}

function pngBytes(extra = 128) {
  return Buffer.concat([
    PNG_SIG,
    Buffer.from([0, 0, 0, 13]),
    Buffer.from('IHDR'),
    Buffer.alloc(13, 7),
    Buffer.alloc(4, 0),
    Buffer.alloc(extra, 0x5a),
  ]);
}

function rpc(server, msg) {
  return server.handleLine(typeof msg === 'string' ? msg : JSON.stringify(msg));
}

function call(server, id, name, args) {
  return rpc(server, {
    jsonrpc: '2.0',
    id,
    method: 'tools/call',
    params: { name, arguments: args },
  });
}

function parseResult(res) {
  assert.ok(res && Array.isArray(res.content) && res.content.length === 1, 'one content part');
  assert.equal(res.content[0].type, 'text');
  return JSON.parse(res.content[0].text);
}

function parseTool(envelope) {
  assert.ok(envelope && envelope.result, 'tool response has result');
  return parseResult(envelope.result);
}

test.after(() => {
  for (const dir of TMP_DIRS) fs.rmSync(dir, { recursive: true, force: true });
});

test('module import and createServer never attach stdin', () => {
  assert.equal(process.stdin.listenerCount('data'), 0);
  assert.equal(process.stdin.listenerCount('line'), 0);
  createServer({ request: never });
  assert.equal(process.stdin.listenerCount('data'), 0);
  assert.equal(process.stdin.listenerCount('line'), 0);
});

test('initialize, ping, tools/list keep the contract and preserve the tool set', async () => {
  const s = createServer({ request: never });
  const init = await rpc(s, { jsonrpc: '2.0', id: 1, method: 'initialize', params: {} });
  assert.equal(init.id, 1);
  assert.equal(init.result.serverInfo.name, 'harmonika-tools');
  assert.ok(init.result.capabilities.tools);
  assert.equal(typeof init.result.protocolVersion, 'string');

  const ping = await rpc(s, { jsonrpc: '2.0', id: 2, method: 'ping' });
  assert.deepEqual(ping.result, {});

  const listed = await rpc(s, { jsonrpc: '2.0', id: 3, method: 'tools/list' });
  const names = listed.result.tools.map((t) => t.name);
  for (const n of [
    'web_search', 'web_read', 'kb_store', 'kb_search', 'file_parse',
    'transcribe', 'image_generate', 'video_generate', 'vision', 'repo_read',
    'safety_check', 'tokenize', 'similarity', 'rerank', 'video_status',
  ]) {
    assert.ok(names.includes(n), 'missing tool ' + n);
  }
  assert.equal(names.length, 19);
  const byName = Object.fromEntries(listed.result.tools.map((t) => [t.name, t]));
  assert.deepEqual(byName.image_generate.inputSchema.required, ['prompt']);
  assert.deepEqual(Object.keys(byName.image_generate.inputSchema.properties).sort(), ['prompt', 'size', 'steps']);
  assert.deepEqual(Object.keys(byName.vision.inputSchema.properties).sort(), ['image_b64', 'question']);
  assert.deepEqual(byName.video_generate.inputSchema.required, ['prompt']);
  assert.equal(byName.video_status.annotations.readOnlyHint, true);
  assert.deepEqual(byName.video_status.inputSchema.required, ['job']);
});

test('malformed JSON gets -32700 and blank lines stay silent', async () => {
  const s = createServer({ request: never });
  const r = await s.handleLine('{ this is not json');
  assert.equal(r.error.code, -32700);
  assert.equal(r.id, null);
  assert.equal(await s.handleLine(''), null);
  assert.equal(await s.handleLine('   '), null);
  assert.equal(await s.handleLine('\t'), null);
});

test('invalid envelopes map to -32600 without crashing', async () => {
  const s = createServer({ request: never });
  for (const line of [
    '[]',
    'null',
    '"str"',
    '3',
    'true',
    '{"id":1}',
    '{"id":1,"method":""}',
    '{"id":1,"method":123}',
    '{"id":1,"method":"ping","jsonrpc":"1.0"}',
  ]) {
    const r = await s.handleLine(line);
    assert.equal(r.error.code, -32600, line);
  }
  const badId = await s.handleLine('{"id":[],"method":"ping"}');
  assert.equal(badId.error.code, -32600);
  assert.equal(badId.id, null);
  const echoed = await s.handleLine('{"id":7,"method":123}');
  assert.equal(echoed.id, 7);
});

test('no-id notifications never receive replies', async () => {
  const s = createServer({ request: never });
  for (const line of [
    '{"jsonrpc":"2.0","method":"notifications/initialized"}',
    '{"jsonrpc":"2.0","method":"some/unknown"}',
    '{"jsonrpc":"2.0","method":"tools/list"}',
    '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"web_search","arguments":{"query":"x"}}}',
    '{"method":123}',
  ]) {
    assert.equal(await s.handleLine(line), null, line);
  }
});

test('unknown method with id gets -32601', async () => {
  const s = createServer({ request: never });
  const r = await rpc(s, { jsonrpc: '2.0', id: 9, method: 'resources/list' });
  assert.equal(r.error.code, -32601);
  assert.equal(r.id, 9);
});

test('invalid tools/call and initialize params get -32602', async () => {
  const s = createServer({ request: never });
  for (const params of [
    undefined,
    null,
    [],
    'x',
    5,
    {},
    { name: 7 },
    { name: '' },
    { name: 'web_search', arguments: [] },
    { name: 'web_search', arguments: 'x' },
    { name: 'web_search', arguments: 5 },
    { name: 'web_search', arguments: null, extra: 1 },
  ]) {
    const msg = { jsonrpc: '2.0', id: 4, method: 'tools/call' };
    if (params !== undefined) msg.params = params;
    const r = await rpc(s, msg);
    assert.equal(r.error.code, -32602, JSON.stringify(params));
    assert.equal(r.id, 4);
  }
  const badInit = await rpc(s, { jsonrpc: '2.0', id: 5, method: 'initialize', params: [] });
  assert.equal(badInit.error.code, -32602);
  const badList = await rpc(s, { jsonrpc: '2.0', id: 6, method: 'tools/list', params: 'x' });
  assert.equal(badList.error.code, -32602);
});

test('synchronous request throws become structured isError and never leak', async () => {
  const s = createServer({
    request: (url, body, opts) => {
      throw new Error('SYNC BOOM ' + url + ' ' + JSON.stringify(opts));
    },
  });
  const r = await call(s, 11, 'web_search', { query: 'x' });
  assert.equal(r.result.isError, true);
  assert.equal(parseTool(r).error, 'gateway_transport');
  assert.ok(!JSON.stringify(r).includes('SYNC BOOM'));
  assert.ok(!JSON.stringify(r).includes('test-key-123'));
});

test('bad tool arguments return isError and never reach the gateway', async () => {
  const calls = [];
  const s = createServer({
    request: async (url) => {
      calls.push(url);
      return { status: 200, value: { ok: true } };
    },
  });
  const bad = [
    ['web_search', [{}], 'missing_argument:query'],
    ['web_search', [{ query: 5 }], 'invalid_argument_type:query'],
    ['web_search', [{ query: 'x', cwd: '/etc' }], 'unknown_argument:cwd'],
    ['tokenize', [{ texts: 'nope' }], 'invalid_argument_type:texts'],
    ['tokenize', [{ texts: ['ok', 42] }], 'invalid_argument_type:texts[1]'],
    ['tokenize', [{ texts: Array.from({ length: 65 }, () => 'x') }], 'argument_too_many_items:texts'],
    ['similarity', [{ query: 'q' }], 'missing_argument:texts'],
    ['image_generate', [{ prompt: 'p', steps: -5 }], 'invalid_argument_type:steps'],
    ['vision', [{ question: 'q' }], 'missing_argument:image_b64'],
    ['video_generate', [{ prompt: 7 }], 'invalid_argument_type:prompt'],
    ['web_read', [{ url: 'ftp://x/y' }], 'invalid_argument_format:url'],
    ['repo_read', [{ url: 'not-a-url' }], 'invalid_argument_format:url'],
  ];
  for (const [name, [args], code] of bad) {
    const r = await call(s, 21, name, args);
    assert.equal(r.result.isError, true, name);
    assert.equal(parseTool(r).error, code, name + ' ' + JSON.stringify(args));
  }
  assert.equal(calls.length, 0);
  assert.equal(parseResult(await s.callTool('web_search', 'x')).error, 'arguments_must_be_object');
  assert.equal(parseResult(await s.callTool('nope', {})).error, 'unknown_tool:nope');
  const unknown = await call(s, 22, 'definitely_not', {});
  assert.equal(unknown.result.isError, true);
  assert.equal(parseTool(unknown).error, 'unknown_tool:definitely_not');
});

test('successful HTTP responses containing private backend errors are sanitized', async () => {
  const s=createServer({request:async()=>({status:200,value:{ok:false,error:'SYNTHETIC_PRIVATE_BACKEND_MESSAGE'}})});
  const r=await call(s,80,'web_search',{query:'synthetic'});
  assert.equal(r.result.isError,true);
  assert.equal(parseTool(r).error,'tool_failed');
  assert.ok(!JSON.stringify(r).includes('SYNTHETIC_PRIVATE_BACKEND_MESSAGE'));
});

test('serialized output limits count UTF-8 bytes', async () => {
  const s=createServer({request:async()=>({status:200,value:{text:'字'.repeat(80000)}})});
  const r=await call(s,81,'web_search',{query:'synthetic'});
  assert.equal(r.result.isError,true);
  assert.equal(parseTool(r).error,'output_limit');
});

test('vision routes through the authenticated gateway, never localhost', async () => {
  const calls = [];
  const s = createServer({
    request: async (url, body, opts) => {
      calls.push({ url, body, opts });
      return {
        status: 200,
        value: { answer: 'A red container with the text HARMONIKA', model: 'qwen3-vl-8b' },
      };
    },
  });
  process.env.HARMONIKA_VISION_URL = 'http://127.0.0.1:11435/api/generate';
  try {
    const img = pngBytes().toString('base64');
    const r = await call(s, 31, 'vision', { image_b64: img, question: 'what is it?' });
    assert.equal(r.result.isError, undefined);
    const data = parseTool(r);
    assert.equal(data.answer, 'A red container with the text HARMONIKA');
    assert.equal(data.model, 'qwen3-vl-8b');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://chat.harmonika.id/api/tools/vlm/generate');
    assert.equal(calls[0].opts.method, 'POST');
    assert.equal(calls[0].opts.key, 'test-key-123');
    assert.ok(Number.isFinite(calls[0].opts.timeout));
    assert.ok(Number.isFinite(calls[0].opts.maxResponseBytes));
    assert.equal(calls[0].body.image_b64, img);
    assert.equal(calls[0].body.question, 'what is it?');
    const plain = await call(s, 32, 'vision', { image_b64: img });
    assert.equal(calls[1].body.question, 'Describe this image and transcribe any visible text.');
  } finally {
    delete process.env.HARMONIKA_VISION_URL;
  }
});

test('vision extracts a readable answer from nested fields or fails fixed', async () => {
  const nested = createServer({
    request: async () => ({ status: 200, value: { result: { answer: 'nested answer' } } }),
  });
  assert.equal(parseTool(await call(nested, 1, 'vision', { image_b64: 'aGlzdGVyZQ==' })).answer, 'nested answer');

  const s2 = createServer({ request: async () => ({ status: 200, value: { foo: 1 } }) });
  const r2 = await call(s2, 2, 'vision', { image_b64: 'aGlzdGVyZQ==' });
  assert.equal(r2.result.isError, true);
  assert.equal(parseTool(r2).error, 'invalid_response');
});

test('oversized PNG media is saved intact to a private exclusive file, reply stays small', async () => {
  const root = tmpDir('img');
  const dir = path.join(root, 'out');
  const big = pngBytes(400 * 1024);
  const s = createServer({
    request: async () => ({
      status: 200,
      value: { ok: true, png_b64: big.toString('base64'), took_seconds: 1.5 },
    }),
    outputDir: dir,
  });
  const r = await call(s, 41, 'image_generate', { prompt: 'a big test image' });
  assert.equal(r.result.isError, undefined);
  const data = parseTool(r);
  assert.ok(!('png_b64' in data), 'no base64 in reply');
  assert.ok(!('image_b64' in data), 'no image_b64 in reply');
  assert.equal(data.bytes, big.length);
  assert.equal(data.format, 'png');
  assert.equal(data.took_seconds, 1.5);
  assert.ok(data.path.startsWith(dir));
  assert.equal(data.uri, pathToFileURL(data.path).href);
  assert.deepEqual(fs.readFileSync(data.path), big);
  assert.equal(fs.statSync(data.path).mode & 0o777, 0o600);
  assert.equal(fs.statSync(dir).mode & 0o777, 0o700);
  assert.ok(r.result.content[0].text.length < 5000, 'reply must stay far below the 180000 limit');

  const r2 = await call(s, 42, 'image_generate', { prompt: 'second' });
  const data2 = parseTool(r2);
  assert.notEqual(data.path, data2.path, 'each image gets an exclusive unique file');
  assert.deepEqual(fs.readFileSync(data2.path), big);
});

test('HARMONIKA_OUTPUT_DIR is respected for image output', async () => {
  const dir = tmpDir('env-out');
  process.env.HARMONIKA_OUTPUT_DIR = dir;
  try {
    const s = createServer({
      request: async () => ({ status: 200, value: { png_b64: pngBytes(64).toString('base64') } }),
    });
    const data = parseResult(await s.callTool('image_generate', { prompt: 'x' }));
    assert.ok(data.path.startsWith(dir));
    assert.ok(fs.existsSync(data.path));
  } finally {
    delete process.env.HARMONIKA_OUTPUT_DIR;
  }
});

test('image_generate rejects invalid or oversized PNG payloads without writing files', async () => {
  const dir = tmpDir('img-bad');
  const cases = [
    [Buffer.concat([Buffer.from([0xff, 0xd8, 0xff]), Buffer.alloc(64, 1)]), 'invalid_image_data'],
    [Buffer.concat([PNG_SIG, Buffer.alloc(16, 1)]), 'invalid_image_data'],
    [Buffer.concat([PNG_SIG, Buffer.from([0, 0, 0, 99]), Buffer.from('IHDR'), Buffer.alloc(13, 1), Buffer.alloc(4)]), 'invalid_image_data'],
    [Buffer.concat([PNG_SIG, Buffer.from([0, 0, 0, 13]), Buffer.from('XXXX'), Buffer.alloc(17, 1)]), 'invalid_image_data'],
    [Buffer.alloc(0), 'invalid_image_data'],
    [Buffer.concat([pngBytes(0), Buffer.alloc(24 * 1024 * 1024 + 1, 1)]), 'image_too_large'],
  ];
  for (const [buf, code] of cases) {
    const s = createServer({
      request: async () => ({ status: 200, value: { png_b64: buf.toString('base64') } }),
      outputDir: dir,
    });
    const res = await s.callTool('image_generate', { prompt: 'x' });
    assert.equal(res.isError, true, code);
    assert.equal(parseResult(res).error, code);
    assert.deepEqual(fs.readdirSync(dir), [], 'no file may be written for bad payloads');
  }
  const empty = createServer({
    request: async () => ({ status: 200, value: { ok: true } }),
    outputDir: dir,
  });
  assert.equal(parseResult(await empty.callTool('image_generate', { prompt: 'x' })).error, 'invalid_image_data');
});

test('oversized JSON results become explicit output_limit errors, never sliced invalid JSON', async () => {
  const s = createServer({
    request: async () => ({ status: 200, value: { ok: true, results: { blob: 'x'.repeat(250000) } } }),
  });
  const r = await call(s, 51, 'kb_search', { query: 'big' });
  assert.equal(r.result.isError, true);
  const text = r.result.content[0].text;
  assert.ok(text.length <= 180000);
  const data = JSON.parse(text);
  assert.equal(data.error, 'output_limit');
  assert.equal(data.limit, 180000);
  assert.ok(data.bytes > 180000);
  assert.ok(!text.includes('xxxxxxxxxx'), 'payload content must not leak into the error');
});

test('video_generate polls read-only status and preserves the original job id', async () => {
  const calls = [];
  let polls = 0;
  const s = createServer({
    pollMs: 1,
    videoTimeoutMs: 5000,
    request: async (url, body, opts) => {
      calls.push({ url, opts });
      if (url.endsWith('/video/generate')) return { status: 200, value: { job: 'vj_abc123' } };
      polls += 1;
      return { status: 200, value: { state: polls < 3 ? 'running' : 'done', size: 4242 } };
    },
  });
  const r = await call(s, 61, 'video_generate', { prompt: 'a wave' });
  assert.equal(r.result.isError, undefined);
  const data = parseTool(r);
  assert.equal(data.job, 'vj_abc123');
  assert.equal(data.state, 'done');
  assert.equal(data.size, 4242);
  assert.equal(data.fetch, '/api/tools/video/file/vj_abc123');
  const submits = calls.filter((c) => c.url.endsWith('/video/generate'));
  const statuses = calls.filter((c) => c.url.includes('/video/status/'));
  assert.equal(submits.length, 1);
  assert.equal(submits[0].opts.method, 'POST');
  assert.equal(statuses[0].opts.method, 'GET');
  assert.ok(statuses.length >= 3);
});

test('video timeout returns structured isError with the job id and never re-submits', async () => {
  const calls = [];
  const s = createServer({
    pollMs: 1,
    videoTimeoutMs: 60,
    request: async (url) => {
      calls.push(url);
      if (url.endsWith('/video/generate')) return { status: 200, value: { job: 'vj_to1' } };
      return { status: 200, value: { state: 'running' } };
    },
  });
  const r = await call(s, 62, 'video_generate', { prompt: 'x' });
  assert.equal(r.result.isError, true);
  const data = parseTool(r);
  assert.equal(data.error, 'video_timeout');
  assert.equal(data.job, 'vj_to1');
  assert.ok(typeof data.fetch === 'string');
  assert.equal(calls.filter((u) => u.endsWith('/video/generate')).length, 1);
});

test('video failure is structured isError preserving the job id without raw backend errors', async () => {
  const s = createServer({
    pollMs: 1,
    request: async (url) =>
      url.endsWith('/video/generate')
        ? { status: 200, value: { job: 'vj_f1' } }
        : { status: 200, value: { state: 'failed', error: 'PRIVATE BACKEND DETAIL' } },
  });
  const r = await call(s, 63, 'video_generate', { prompt: 'x' });
  assert.equal(r.result.isError, true);
  const data = parseTool(r);
  assert.equal(data.error, 'video_failed');
  assert.equal(data.job, 'vj_f1');
  assert.ok(!JSON.stringify(r).includes('PRIVATE BACKEND DETAIL'));
});

test('video status outage stops with video_status_failed and never re-submits', async () => {
  const calls = [];
  const s = createServer({
    pollMs: 1,
    videoTimeoutMs: 5000,
    request: async (url) => {
      calls.push(url);
      if (url.endsWith('/video/generate')) return { status: 200, value: { job: 'vj_st' } };
      throw new Error('GET failed');
    },
  });
  const r = await call(s, 64, 'video_generate', { prompt: 'x' });
  assert.equal(r.result.isError, true);
  assert.equal(parseTool(r).error, 'video_status_failed');
  assert.ok(!JSON.stringify(r).includes('GET failed'));
  assert.equal(calls.filter((u) => u.endsWith('/video/generate')).length, 1);
});

test('video_status is read-only, routed by job id, and validates the id first', async () => {
  const calls = [];
  const s = createServer({
    request: async (url, body, opts) => {
      calls.push({ url, opts });
      return { status: 200, value: { state: 'done', size: 9 } };
    },
  });
  const r = await call(s, 65, 'video_status', { job: 'vj_abc123' });
  assert.equal(r.result.isError, undefined);
  const data = parseTool(r);
  assert.equal(data.state, 'done');
  assert.equal(data.job, 'vj_abc123');
  assert.equal(data.size, 9);
  assert.deepEqual(calls.map((c) => c.url), ['https://chat.harmonika.id/api/tools/video/status/vj_abc123']);
  assert.equal(calls[0].opts.method, 'GET');

  const bad = await call(s, 66, 'video_status', { job: '../../etc' });
  assert.equal(bad.result.isError, true);
  assert.equal(parseTool(bad).error, 'invalid_argument_format:job');
  assert.equal(calls.length, 1);
});

test('gateway statuses map to fixed enums without leaking backend text', async () => {
  for (const [status, code] of [
    [401, 'gateway_access_denied'],
    [403, 'gateway_access_denied'],
    [429, 'gateway_busy'],
    [500, 'gateway_unavailable'],
    [503, 'gateway_unavailable'],
    [404, 'gateway_rejected'],
  ]) {
    const s = createServer({
      request: async () => ({ status, value: { detail: 'SECRET backend message' } }),
    });
    const r = await call(s, 70, 'web_search', { query: 'x' });
    assert.equal(r.result.isError, true, String(status));
    assert.equal(parseTool(r).error, code);
    assert.ok(!JSON.stringify(r).includes('SECRET'));
  }
});

test('web_search issues an authenticated bounded GET with encoded query', async () => {
  const calls = [];
  const s = createServer({
    request: async (url, body, opts) => {
      calls.push({ url, body, opts });
      return { status: 200, value: { results: [] } };
    },
  });
  const r = await call(s, 71, 'web_search', { query: 'a b&c=d' });
  assert.equal(r.result.isError, undefined);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://chat.harmonika.id/api/tools/webreader/search?q=a%20b%26c%3Dd');
  assert.equal(calls[0].opts.method, 'GET');
  assert.equal(calls[0].body, null);
  assert.equal(calls[0].opts.key, 'test-key-123');
  assert.ok(Number.isFinite(calls[0].opts.timeout) && calls[0].opts.timeout > 0);
  assert.ok(Number.isFinite(calls[0].opts.maxResponseBytes) && calls[0].opts.maxResponseBytes > 0);
});

test('nested and hostile argument objects are rejected before any effect', async () => {
  const calls = [];
  const s = createServer({
    request: async (url) => {
      calls.push(url);
      return { status: 200, value: {} };
    },
  });
  const nested = { query: 'q', tags: { deep: { deeper: [1] } } };
  const r = await call(s, 81, 'web_search', nested);
  assert.equal(r.result.isError, true);
  assert.equal(parseTool(r).error, 'unknown_argument:tags');
  const longQuery = 'x'.repeat(1000001);
  const r2 = await call(s, 82, 'web_search', { query: longQuery });
  assert.equal(parseTool(r2).error, 'argument_too_long:query');
  assert.equal(calls.length, 0);
});
