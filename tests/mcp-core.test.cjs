'use strict';

/*
 * Tests for mcp-core.cjs (mocked rpc/asr/ffprobe/ffmpeg + tmp fixtures)
 * and protocol-level integration for mcp-server.cjs (initialize/tools/list
 * only; no real backend, provider, or model is ever contacted).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');

const { createCore, LIMITS, detectImageMime } = require('../runtime/tools/mcp-core.cjs');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-core-test-'));

test.after(() => {
  fs.rmSync(ROOT, { recursive: true, force: true });
});

/* ---------------- helpers ---------------- */

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const JPEG_MAGIC = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]);

function pngBuffer() {
  return Buffer.concat([PNG_MAGIC, Buffer.alloc(64, 0x07)]);
}

function mkProject(name) {
  const dir = path.join(ROOT, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'package.json'), '{"name":"fixture-project"}\n');
  return dir;
}

function parseResult(res) {
  assert.ok(Array.isArray(res.content) && res.content.length === 1);
  assert.equal(res.content[0].type, 'text');
  assert.ok(res.content[0].text.length <= LIMITS.RESULT_MAX_CHARS, 'result text must stay bounded');
  return JSON.parse(res.content[0].text);
}

function makeMockRpc(responses, calls) {
  return async (payload) => {
    if (calls) calls.push(payload);
    const r = responses[payload.operation];
    if (r === undefined) return { ok: false, status: 'error', error: 'no_mock_for_operation' };
    return typeof r === 'function' ? r(payload) : r;
  };
}

const neverRpc = async () => {
  throw new Error('rpc must not be called');
};

test('failed image write removes only its own partial output', async () => {
  const project=mkProject('image-disk-failure'),output=path.join(project,'failed.png');
  const core=createCore({callerCwd:project,rpc:async()=>({ok:true,status:'ok',mime_type:'image/png',image_base64:pngBuffer().toString('base64')})});
  const write=fs.writeFileSync;
  fs.writeFileSync=(target,data,...rest)=>{
    if(typeof target==='number') {write(target,data.subarray(0,8));const error=new Error('simulated full disk');error.code='ENOSPC';throw error;}
    return write(target,data,...rest);
  };
  try {const result=await core.callTool('generate_image',{prompt:'test',output_file:output});assert.equal(result.isError,true);assert.equal(fs.existsSync(output),false);}
  finally {fs.writeFileSync=write;}
});

test('production response fields survive adapter shaping without binary context', async () => {
  const project = mkProject('production-contract');
  fs.writeFileSync(path.join(project, 'image.png'), pngBuffer());
  fs.writeFileSync(path.join(project, 'doc.pdf'), '%PDF-1.4 fixture');
  const core = createCore({callerCwd: project, rpc: makeMockRpc({
    capabilities: {ok:true,status:'ok',features:{web_search:true},limits:{pdf_pages:20}},
    analyze_image: {ok:true,status:'ok',page_state:'blue',text_detected:'427',ui_elements:[],warnings:[]},
    read_pdf: {ok:true,status:'ok',text:'VERIFY 427',content_is_untrusted:true,truncated:false},
    web_search: {ok:true,status:'ok',answer:'official docs',sources:['https://www.typescriptlang.org/docs/']}
  })});
  assert.equal(parseResult(await core.callTool('capabilities',{})).backend.capabilities.web_search,true);
  assert.equal(parseResult(await core.callTool('analyze_image',{file_path:'image.png',prompt:'read'})).page_state,'blue');
  assert.equal(parseResult(await core.callTool('read_pdf',{file_path:'doc.pdf',prompt:'read'})).text,'VERIFY 427');
  assert.equal(parseResult(await core.callTool('web_search',{query:'docs'})).sources.length,1);
});

test('structured provider error remains explicit and degraded', async () => {
  const core = createCore({rpc:async()=>({ok:false,status:'degraded',error:{code:'vision_unavailable'}})});
  const result = await core.callTool('web_search',{query:'test'});
  assert.equal(result.isError,true);
  assert.equal(parseResult(result).error,'vision_unavailable');
});

/* ---------------- tool registry ---------------- */

test('listTools exposes 8 tools including project creation with closed schemas', () => {
  const core = createCore({ callerCwd: mkProject('registry') });
  const tools = core.listTools();
  assert.deepEqual(
    tools.map((t) => t.name),
    [
      'create_directory',
      'capabilities',
      'web_search',
      'analyze_image',
      'read_pdf',
      'analyze_video',
      'transcribe_audio',
      'generate_image',
    ]
  );
  for (const t of tools) {
    assert.equal(t.inputSchema.type, 'object');
    assert.equal(t.inputSchema.additionalProperties, false, 'schemas must reject unknown fields');
    assert.ok(typeof t.description === 'string' && t.description.length > 0);
  }
  const byName = Object.fromEntries(tools.map((t) => [t.name, t]));
  for (const readOnly of ['capabilities', 'web_search', 'analyze_image', 'read_pdf', 'analyze_video', 'transcribe_audio']) {
    assert.equal(byName[readOnly].annotations.readOnlyHint, true, `${readOnly} must be readOnlyHint`);
  }
  assert.equal(byName.generate_image.annotations.readOnlyHint, false, 'generate_image writes a file');
  assert.equal(byName.create_directory.annotations.readOnlyHint, false);
  assert.deepEqual(byName.capabilities.inputSchema.required, []);
  assert.deepEqual(byName.web_search.inputSchema.required, ['query']);
  assert.deepEqual(byName.analyze_image.inputSchema.required, ['file_path', 'prompt']);
  assert.deepEqual(byName.transcribe_audio.inputSchema.required, ['file_path']);
  assert.deepEqual(byName.generate_image.inputSchema.required, ['prompt', 'output_file']);
});

/* ---------------- capabilities ---------------- */

test('capabilities merges remote RPC capabilities with local ASR availability', async () => {
  const proj = mkProject('cap-ok');
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ capabilities: { ok: true, status: 'ok', capabilities: { web_search: true, vision: true } } }, calls),
    exists: () => true,
  });
  const res = await core.callTool('capabilities', {});
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.ok, true);
  assert.equal(data.backend.available, true);
  assert.equal(data.backend.capabilities.web_search, true);
  assert.equal(data.local_asr.available, true);
  assert.deepEqual(calls, [{ operation: 'capabilities' }]);
});

test('capabilities reports local ASR unavailable without statically labelling active', async () => {
  const proj = mkProject('cap-noasr');
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ capabilities: { ok: true, status: 'ok', capabilities: {} } }),
    exists: () => false,
  });
  const data = parseResult(await core.callTool('capabilities', {}));
  assert.equal(data.ok, true);
  assert.equal(data.local_asr.available, false);
});

test('capabilities marks degraded (isError) when the backend RPC fails', async () => {
  const proj = mkProject('cap-down');
  const core = createCore({
    callerCwd: proj,
    rpc: async () => {
      throw new Error('backend_unavailable');
    },
    exists: () => true,
  });
  const res = await core.callTool('capabilities', {});
  const data = parseResult(res);
  assert.equal(res.isError, true, 'degraded must not be reported as success');
  assert.equal(data.status, 'degraded');
  assert.equal(data.backend.available, false);
  assert.equal(data.local_asr.available, true);
});

/* ---------------- web_search ---------------- */

test('web_search passes query to backend and returns safe JSON text', async () => {
  const proj = mkProject('ws-ok');
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ web_search: { ok: true, status: 'ok', results: ['a', 'b'], count: 2 } }, calls),
  });
  const res = await core.callTool('web_search', { query: 'harmonika mcp' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.deepEqual(data.results, ['a', 'b']);
  assert.deepEqual(calls, [{ operation: 'web_search', query: 'harmonika mcp' }]);
});

test('web_search rejects unknown fields (including a user-supplied cwd), long query, wrong type, missing arg', async () => {
  const proj = mkProject('ws-bad');
  const core = createCore({ callerCwd: proj, rpc: neverRpc });
  for (const [args, code] of [
    [{ query: 'a', cwd: '/etc' }, 'unknown_argument:cwd'],
    [{ query: 'x'.repeat(LIMITS.QUERY_MAX_CHARS + 1) }, 'argument_too_long:query'],
    [{ query: 42 }, 'invalid_argument_type:query'],
    [{}, 'missing_argument:query'],
    ['nope', 'arguments_must_be_object'],
  ]) {
    const data = parseResult(await core.callTool('web_search', args));
    assert.equal(data.ok, false);
    assert.equal(data.error, code);
  }
});

test('web_search bounds oversized backend output to <=40000 chars as valid JSON', async () => {
  const proj = mkProject('ws-big');
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ web_search: { ok: true, status: 'ok', results: { blob: 'x'.repeat(200000) } } }),
  });
  const res = await core.callTool('web_search', { query: 'big' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.truncated, true);
  assert.ok(typeof data.preview === 'string');
});

test('web_search surfaces backend failure as isError without claiming success', async () => {
  const proj = mkProject('ws-fail');
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ web_search: { ok: false, status: 'error', error: 'quota_exceeded' } }),
  });
  const res = await core.callTool('web_search', { query: 'q' });
  const data = parseResult(res);
  assert.equal(res.isError, true);
  assert.equal(data.ok, false);
  assert.equal(data.error, 'quota_exceeded');
});

test('web_search marks degraded status as isError', async () => {
  const proj = mkProject('ws-degraded');
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ web_search: { ok: true, status: 'degraded', results: [] } }),
  });
  const res = await core.callTool('web_search', { query: 'q' });
  assert.equal(res.isError, true);
  assert.equal(parseResult(res).status, 'degraded');
});

/* ---------------- analyze_image ---------------- */

test('analyze_image validates PNG signature and sends base64 + prompt to backend', async () => {
  const proj = mkProject('img-ok');
  const imgPath = path.join(proj, 'shot.png');
  const buf = pngBuffer();
  fs.writeFileSync(imgPath, buf);
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ analyze_image: { ok: true, status: 'ok', description: 'a red test pattern' } }, calls),
  });
  const res = await core.callTool('analyze_image', { file_path: 'shot.png', prompt: 'describe this' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.description, 'a red test pattern');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, 'analyze_image');
  assert.equal(calls[0].prompt, 'describe this');
  assert.equal(calls[0].image_base64, buf.toString('base64'));
});

test('analyze_image rejects symlink, private files, bad signatures, oversize, and outside-root paths', async () => {
  const proj = mkProject('img-bad');
  const outside = path.join(ROOT, 'img-outside');
  fs.mkdirSync(outside, { recursive: true });

  fs.writeFileSync(path.join(proj, 'real.png'), pngBuffer());
  fs.symlinkSync(path.join(proj, 'real.png'), path.join(proj, 'link.png'));
  fs.writeFileSync(path.join(proj, '.env'), pngBuffer());
  fs.mkdirSync(path.join(proj, 'creds'), { recursive: true });
  fs.writeFileSync(path.join(proj, 'creds', 'credentials.png'), pngBuffer());
  fs.writeFileSync(path.join(proj, 'fake.png'), JPEG_MAGIC);
  fs.writeFileSync(path.join(proj, 'junk.png'), Buffer.from('not an image at all'));
  fs.writeFileSync(path.join(proj, 'photo.bmp'), pngBuffer());
  fs.writeFileSync(path.join(outside, 'leak.png'), pngBuffer());
  const bigPath = path.join(proj, 'big.png');
  fs.writeFileSync(bigPath, pngBuffer());
  fs.truncateSync(bigPath, LIMITS.IMAGE_PDF_MAX_BYTES + 1);

  const core = createCore({ callerCwd: proj, rpc: neverRpc });
  for (const [file, code] of [
    ['link.png', 'symlink_not_allowed'],
    ['.env', 'private_file_not_allowed'],
    [path.join('creds', 'credentials.png'), 'private_file_not_allowed'],
    ['fake.png', 'content_type_mismatch'],
    ['junk.png', 'invalid_image_signature'],
    ['photo.bmp', 'unsupported_file_extension'],
    ['big.png', 'file_too_large'],
    ['missing.png', 'file_not_found'],
    [path.join(outside, 'leak.png'), 'path_outside_allowed_roots'],
  ]) {
    const data = parseResult(await core.callTool('analyze_image', { file_path: file, prompt: 'p' }));
    assert.equal(data.error, code, `${file}: expected ${code}, got ${data.error}`);
  }
});

test('analyze_image rejects prompt over 3000 chars and non-regular file', async () => {
  const proj = mkProject('img-bad2');
  fs.mkdirSync(path.join(proj, 'dir.png'), { recursive: true });
  const core = createCore({ callerCwd: proj, rpc: neverRpc });
  const long = parseResult(
    await core.callTool('analyze_image', { file_path: 'a.png', prompt: 'x'.repeat(LIMITS.PROMPT_MAX_CHARS + 1) })
  );
  assert.equal(long.error, 'argument_too_long:prompt');
  const dir = parseResult(await core.callTool('analyze_image', { file_path: 'dir.png', prompt: 'p' }));
  assert.equal(dir.error, 'not_a_regular_file');
});

/* ---------------- read_pdf ---------------- */

test('read_pdf validates %PDF signature and sends pdf_base64', async () => {
  const proj = mkProject('pdf-ok');
  const buf = Buffer.from('%PDF-1.4\n%test-fixture\n');
  fs.writeFileSync(path.join(proj, 'doc.pdf'), buf);
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ read_pdf: { ok: true, status: 'ok', description: 'invoice total 42' } }, calls),
  });
  const res = await core.callTool('read_pdf', { file_path: 'doc.pdf', prompt: 'total?' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.description, 'invoice total 42');
  assert.equal(calls[0].operation, 'read_pdf');
  assert.equal(calls[0].pdf_base64, buf.toString('base64'));
  assert.equal(calls[0].prompt, 'total?');
});

test('read_pdf rejects non-PDF content and oversize pdf', async () => {
  const proj = mkProject('pdf-bad');
  fs.writeFileSync(path.join(proj, 'fake.pdf'), 'NOT A PDF');
  const big = path.join(proj, 'big.pdf');
  fs.writeFileSync(big, Buffer.from('%PDF-1.4\n'));
  fs.truncateSync(big, LIMITS.IMAGE_PDF_MAX_BYTES + 1);
  const core = createCore({ callerCwd: proj, rpc: neverRpc });
  const fake = parseResult(await core.callTool('read_pdf', { file_path: 'fake.pdf', prompt: 'p' }));
  assert.equal(fake.error, 'invalid_pdf_signature');
  const oversize = parseResult(await core.callTool('read_pdf', { file_path: 'big.pdf', prompt: 'p' }));
  assert.equal(oversize.error, 'file_too_large');
});

/* ---------------- analyze_video ---------------- */

test('analyze_video samples 6 frames via DI ffmpeg, captions timestamps, cleans tmp', async () => {
  const proj = mkProject('vid-ok');
  const videoPath = path.join(proj, 'clip.mp4');
  fs.writeFileSync(videoPath, Buffer.alloc(2048, 0x01));
  const calls = [];
  let extractDir = null;
  let extractTimes = null;
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ analyze_video: { ok: true, status: 'ok', description: 'someone dancing' } }, calls),
    ffprobe: async () => ({ duration: 120, hasVideo: true, hasAudio: true }),
    extractFrames: async (file, times, dir) => {
      extractDir = dir;
      extractTimes = times;
      assert.equal(file, fs.realpathSync(videoPath));
      return times.map((t) => ({ t, buffer: pngBuffer() }));
    },
  });
  const res = await core.callTool('analyze_video', { file_path: 'clip.mp4', prompt: 'what happens?' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.description, 'someone dancing');
  assert.equal(data.frame_count, 6);
  assert.deepEqual(data.timestamps, [10, 30, 50, 70, 90, 110]);
  assert.equal(data.analysis_scope, 'sampled_frames_only');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].operation, 'analyze_video');
  assert.equal(calls[0].prompt, 'what happens?');
  assert.equal(calls[0].frames_base64.length, 6);
  assert.ok(calls[0].frames_base64.every((b) => detectImageMime(Buffer.from(b, 'base64')) === 'image/png'));
  assert.match(calls[0].caption, /sample timestamps 10s, 30s, 50s, 70s, 90s, 110s of 120s/);
  assert.match(calls[0].caption, /sampled frames only/);
  assert.equal(extractTimes.length, 6);
  assert.ok(!fs.existsSync(extractDir), 'private tmp frame dir must be cleaned up');
});

test('analyze_video rejects videos longer than 10 minutes and files without video stream', async () => {
  const proj = mkProject('vid-bad');
  fs.writeFileSync(path.join(proj, 'long.mp4'), Buffer.alloc(16, 1));
  fs.writeFileSync(path.join(proj, 'audio-only.mp4'), Buffer.alloc(16, 1));
  const core = createCore({
    callerCwd: proj,
    rpc: neverRpc,
    ffprobe: async (file) =>
      path.basename(file) === 'long.mp4'
        ? { duration: 601, hasVideo: true, hasAudio: false }
        : { duration: 5, hasVideo: false, hasAudio: true },
  });
  const tooLong = parseResult(await core.callTool('analyze_video', { file_path: 'long.mp4', prompt: 'p' }));
  assert.equal(tooLong.error, 'video_too_long');
  const noVideo = parseResult(await core.callTool('analyze_video', { file_path: 'audio-only.mp4', prompt: 'p' }));
  assert.equal(noVideo.error, 'no_video_stream');
});

/* ---------------- transcribe_audio ---------------- */

test('transcribe_audio runs DI ASR with optional language and returns contract JSON', async () => {
  const proj = mkProject('asr-ok');
  fs.writeFileSync(path.join(proj, 'note.m4a'), Buffer.alloc(512, 0x02));
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: neverRpc,
    ffprobe: async () => ({ duration: 9.5, hasVideo: false, hasAudio: true }),
    asr: async (file, language) => {
      calls.push({ file, language });
      return { ok: true, status: 'ok', transcript: 'hello world', language: language || 'en', confidence: 0.93, seconds: 9.5 };
    },
  });
  const res = await core.callTool('transcribe_audio', { file_path: 'note.m4a', language: 'id' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  assert.equal(data.transcript, 'hello world');
  assert.equal(data.language, 'id');
  assert.equal(data.confidence, 0.93);
  assert.equal(data.transcription_scope, 'local_asr_audio_only');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].language, 'id');

  const res2 = await core.callTool('transcribe_audio', { file_path: 'note.m4a' });
  const data2 = parseResult(res2);
  assert.equal(res2.isError, false);
  assert.equal(calls[1].language, undefined);
  assert.equal(data2.language, 'en');
});

test('transcribe_audio rejects bad language format, non-audio files, and ASR failures', async () => {
  const proj = mkProject('asr-bad');
  fs.writeFileSync(path.join(proj, 'note.mp3'), Buffer.alloc(64, 0x02));
  const core = createCore({
    callerCwd: proj,
    rpc: neverRpc,
    ffprobe: async () => ({ duration: 2, hasVideo: false, hasAudio: true }),
    asr: async () => ({ ok: false, status: 'error', error: 'model_missing' }),
  });
  const badLang = parseResult(await core.callTool('transcribe_audio', { file_path: 'note.mp3', language: '1nvalid!' }));
  assert.equal(badLang.error, 'invalid_argument_format:language');
  const fail = parseResult(await core.callTool('transcribe_audio', { file_path: 'note.mp3' }));
  assert.equal(fail.ok, false);
  assert.equal(fail.error, 'model_missing');
  const noAudio = createCore({
    callerCwd: proj,
    rpc: neverRpc,
    ffprobe: async () => ({ duration: 2, hasVideo: true, hasAudio: false }),
  });
  const silent = parseResult(await noAudio.callTool('transcribe_audio', { file_path: 'note.mp3' }));
  assert.equal(silent.error, 'no_audio_stream');
});

/* ---------------- generate_image ---------------- */

test('generate_image writes PNG exclusively (mode 0600), never overwrites, returns metadata only', async () => {
  const proj = mkProject('gen-ok');
  fs.mkdirSync(path.join(proj, 'out'), { recursive: true });
  const png = pngBuffer();
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc(
      { generate_image: { ok: true, status: 'ok', mime_type: 'image/png', width: 64, height: 64, provider: 'fixture', image_base64: png.toString('base64') } },
      calls
    ),
  });
  const res = await core.callTool('generate_image', { prompt: 'a tiny square', output_file: 'out/img.png' });
  const data = parseResult(res);
  assert.equal(res.isError, false);
  const outPath = path.join(proj, 'out', 'img.png');
  assert.equal(data.output_file, outPath);
  assert.equal(data.mime_type, 'image/png');
  assert.equal(data.bytes, png.length);
  assert.equal(data.width, 64);
  assert.equal(data.provider, 'Harmonika Ai');
  assert.ok(!res.content[0].text.includes('image_base64'), 'base64 must never appear in MCP content');
  assert.deepEqual(calls, [{ operation: 'generate_image', prompt: 'a tiny square' }]);
  const written = fs.readFileSync(outPath);
  assert.ok(written.equals(png));
  assert.equal(fs.statSync(outPath).mode & 0o777, 0o600);

  const again = parseResult(
    await core.callTool('generate_image', { prompt: 'again', output_file: 'out/img.png' })
  );
  assert.equal(again.error, 'output_exists');
});

test('generate_image rejects non-png output, outside-project output, and bad backend payloads', async () => {
  const proj = mkProject('gen-bad');
  fs.mkdirSync(path.join(proj, 'out'), { recursive: true });
  fs.mkdirSync(path.join(ROOT, 'gen-outside'), { recursive: true });
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({
      generate_image: { ok: true, status: 'ok', mime_type: 'image/jpeg', image_base64: JPEG_MAGIC.toString('base64') },
    }),
  });
  const ext = parseResult(await core.callTool('generate_image', { prompt: 'p', output_file: 'out/img.jpg' }));
  assert.equal(ext.error, 'output_must_be_png');
  const outside = parseResult(
    await core.callTool('generate_image', { prompt: 'p', output_file: path.join(ROOT, 'gen-outside', 'x.png') })
  );
  assert.equal(outside.error, 'output_outside_project');
  const missingDir = parseResult(await core.callTool('generate_image', { prompt: 'p', output_file: 'nope/x.png' }));
  assert.equal(missingDir.error, 'output_dir_missing');
  const badMime = parseResult(await core.callTool('generate_image', { prompt: 'p', output_file: 'out/ok.png' }));
  assert.equal(badMime.ok, false);
  assert.equal(badMime.error, 'invalid_backend_image');
});

test('generate_image treats non-ok backend as failure and does not write files', async () => {
  const proj = mkProject('gen-fail');
  fs.mkdirSync(path.join(proj, 'out'), { recursive: true });
  const core = createCore({
    callerCwd: proj,
    rpc: makeMockRpc({ generate_image: { ok: false, status: 'error', error: 'provider_down' } }),
  });
  const res = await core.callTool('generate_image', { prompt: 'p', output_file: 'out/fail.png' });
  const data = parseResult(res);
  assert.equal(res.isError, true);
  assert.equal(data.error, 'provider_down');
  assert.ok(!fs.existsSync(path.join(proj, 'out', 'fail.png')));
});

/* ---------------- project-root / cwd policy ---------------- */

test('rejects home-root cwd and generic cwd without project markers', async () => {
  const plain = path.join(ROOT, 'plain-cwd');
  fs.mkdirSync(plain, { recursive: true });
  fs.writeFileSync(path.join(plain, 'shot.png'), pngBuffer());
  const noMarker = parseResult(
    await createCore({ callerCwd: plain, rpc: neverRpc }).callTool('analyze_image', {
      file_path: 'shot.png',
      prompt: 'p',
    })
  );
  assert.equal(noMarker.error, 'cwd_not_a_project');

  const home = parseResult(
    await createCore({ callerCwd: os.homedir(), rpc: neverRpc }).callTool('analyze_image', {
      file_path: path.join(plain, 'shot.png'),
      prompt: 'p',
    })
  );
  assert.equal(home.error, 'cwd_home_root_not_allowed');

  const markerFile = parseResult(
    await createCore({ callerCwd: plain, rpc: neverRpc }).callTool('analyze_image', {
      file_path: path.join(plain, 'AGENTS.md'),
      prompt: 'p',
    })
  );
  assert.equal(markerFile.error, 'file_not_found');
});

test('allows project via marker file and media files from injected ~/Downloads', async () => {
  const proj = mkProject('marker-ok');
  fs.unlinkSync(path.join(proj, 'package.json'));
  fs.writeFileSync(path.join(proj, 'pyproject.toml'), '[project]\nname="x"\n');
  fs.writeFileSync(path.join(proj, 'shot.png'), pngBuffer());
  const downloads = path.join(ROOT, 'Downloads');
  fs.mkdirSync(downloads, { recursive: true });
  fs.writeFileSync(path.join(downloads, 'voice.mp3'), Buffer.alloc(128, 0x03));
  const calls = [];
  const core = createCore({
    callerCwd: proj,
    rpc: neverRpc,
    downloadsDir: downloads,
    ffprobe: async () => ({ duration: 1, hasVideo: false, hasAudio: true }),
    asr: async (file) => {
      calls.push(file);
      return { ok: true, status: 'ok', transcript: 'hi', language: 'en', confidence: 1, seconds: 1 };
    },
  });
  const img = parseResult(await core.callTool('analyze_image', { file_path: 'shot.png', prompt: 'p' }));
  assert.equal(img.ok, false, 'marker-project validation passed; failure comes from stub rpc');
  assert.equal(img.error, 'internal_error');
  const asr = parseResult(await core.callTool('transcribe_audio', { file_path: path.join(downloads, 'voice.mp3') }));
  assert.equal(asr.ok, true);
  assert.equal(calls.length, 1);
});

/* ---------------- dispatch edge cases ---------------- */

test('unknown tool and invalid arguments are reported without touching backend', async () => {
  const proj = mkProject('edge');
  const core = createCore({ callerCwd: proj, rpc: neverRpc });
  const unknown = parseResult(await core.callTool('definitely_not_a_tool', {}));
  assert.equal(unknown.error, 'unknown_tool:definitely_not_a_tool');
  assert.equal(unknown.ok, false);
  const noArgs = parseResult(await core.callTool('web_search', { query: new Date() }));
  assert.equal(noArgs.error, 'invalid_argument_type:query');
});

/* ---------------- protocol integration (no real tool execution) ---------------- */

test('mcp-server.cjs speaks MCP: initialize + tools/list over stdio, stdout protocol-only', async () => {
  const child = spawn(process.execPath, [path.join(__dirname, '../runtime/tools/mcp-server.cjs')], {
    cwd: ROOT,
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let buf = '';
  let foreignLines = 0;
  const pending = new Map();
  let nextId = 1;
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', (chunk) => {
    buf += chunk;
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        foreignLines += 1;
        continue;
      }
      const waiter = pending.get(msg.id);
      if (waiter) {
        pending.delete(msg.id);
        if (msg.error) waiter.reject(new Error(JSON.stringify(msg.error)));
        else waiter.resolve(msg.result);
      }
    }
  });
  child.stderr.on('data', () => {});
  const request = (method, params) =>
    new Promise((resolve, reject) => {
      const id = nextId++;
      pending.set(id, { resolve, reject });
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
      setTimeout(() => {
        if (pending.has(id)) {
          pending.delete(id);
          reject(new Error(`timeout waiting for ${method}`));
        }
      }, 15000);
    });

  try {
    const init = await request('initialize', {
      protocolVersion: '2025-06-18',
      capabilities: {},
      clientInfo: { name: 'protocol-test', version: '0.0.0' },
    });
    assert.equal(init.serverInfo.name, 'harmonika-crush-tools');
    assert.ok(typeof init.protocolVersion === 'string' && init.protocolVersion.length > 0);
    assert.ok(init.capabilities && init.capabilities.tools);
    child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');

    const ping = await request('ping', {});
    assert.deepEqual(ping, {});

    const listed = await request('tools/list', {});
    assert.equal(listed.tools.length, 8);
    assert.deepEqual(
      listed.tools.map((t) => t.name),
      [
        'create_directory',
        'capabilities',
        'web_search',
        'analyze_image',
        'read_pdf',
        'analyze_video',
        'transcribe_audio',
        'generate_image',
      ]
    );
    assert.equal(foreignLines, 0, 'stdout must carry MCP protocol frames only');
  } finally {
    child.stdin.end();
    await new Promise((resolve) => {
      const t = setTimeout(() => {
        try {
          child.kill('SIGTERM');
        } catch {}
        resolve();
      }, 5000);
      child.on('close', () => {
        clearTimeout(t);
        resolve();
      });
    });
  }
});
