'use strict';

/*
 * Harmonika Terminal Tools - MCP core (framework-agnostic).
 * Dependency-injected: rpc (backend HTTPS/SSH), asr (local transcribe.py),
 * ffprobe, extractFrames. Callers/tests replace any of them.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const projectAccess = require('./project-access.cjs');
const runtimePaths = require('./paths.cjs');

const LIMITS = Object.freeze({
  PROMPT_MAX_CHARS: 3000,
  QUERY_MAX_CHARS: 1000,
  LANGUAGE_MAX_CHARS: 32,
  IMAGE_PDF_MAX_BYTES: 10 * 1024 * 1024,
  AV_MAX_BYTES: 50 * 1024 * 1024,
  RPC_STDIN_MAX_BYTES: 20 * 1024 * 1024,
  RPC_OUTPUT_MAX_BYTES: 24 * 1024 * 1024,
  RPC_TIMEOUT_MS: 240000,
  ASR_TIMEOUT_MS: 180000,
  ASR_OUTPUT_MAX_BYTES: 128 * 1024,
  PROBE_TIMEOUT_MS: 30000,
  PROBE_OUTPUT_MAX_BYTES: 1024 * 1024,
  FFMPEG_TIMEOUT_MS: 60000,
  FRAME_COUNT: 6,
  FRAME_MAX_PX: 1280,
  VIDEO_MAX_DURATION_S: 600,
  RESULT_MAX_CHARS: 40000,
});

const BIN = Object.freeze({
  ssh: '/usr/bin/ssh',
  ffprobe: process.env.HARMONIKA_FFPROBE||path.join(os.homedir(),'.local/bin/ffprobe'),
  ffmpeg: process.env.HARMONIKA_FFMPEG||path.join(os.homedir(),'.local/bin/ffmpeg'),
  asrPython: path.join(runtimePaths.config,'media-runtime/bin/python'),
  asrScript: path.join(__dirname,'transcribe.py'),
});

const RPC_SSH_ARGS = Object.freeze([
  '-F', '/dev/null',
  '-T',
  '-p', process.env.HARMONIKA_OWNER_SSH_PORT||'22',
  '-oBatchMode=yes',
  '-oIdentitiesOnly=yes',
  '-oStrictHostKeyChecking=yes',
  '-i', process.env.HARMONIKA_OWNER_SSH_KEY||'',
  process.env.HARMONIKA_OWNER_SSH_TARGET||'',
  'harmonika-terminal-tools',
]);

const SERVER_INFO = Object.freeze({ name: 'harmonika-terminal-tools', version: '1.0.0' });
const DISPLAY_PROVIDER = 'Harmonika Ai';
const CONTENT_FIELDS = new Set(['text','answer','analysis','description','summary','page_state','text_detected','ui_elements','topics','brand_product_mentions','safety_notes','sources','results','query','prompt','transcript','source_prompt']);
function publicMetadata(value) {
  if(Array.isArray(value))return value.map(publicMetadata);
  if(!value||typeof value!=='object')return value;
  return Object.fromEntries(Object.entries(value).map(([key,item])=>{
    if(CONTENT_FIELDS.has(key))return [key,item];
    if(/^(?:provider|model|engine|provider_name|model_name|engine_name|backend_provider|backend_model|source_provider|fallback_provider|selected_provider)$/i.test(key))return [key,DISPLAY_PROVIDER];
    if(key==='providers'&&Array.isArray(item))return [key,[DISPLAY_PROVIDER]];
    if(key==='error'&&typeof item==='string')return [key,item.replace(/\b(?:rtx|codex|opencode|claude|gemini|openai|google|whisper)(?=[_-])/gi,'harmonika')];
    return [key,publicMetadata(item)];
  }));
}

const PROJECT_HOME_PREFIX = path.join(os.homedir(),'Projects')+path.sep;
const PROJECT_MARKER_FILES = Object.freeze([
  'package.json', 'go.mod', 'composer.json', 'pyproject.toml', 'AGENTS.md',
  'Cargo.toml', 'settings.gradle', 'settings.gradle.kts', 'build.gradle', 'build.gradle.kts', '.git',
]);

const EXT_BY_KIND = Object.freeze({
  image: Object.freeze(['.png', '.jpg', '.jpeg', '.webp']),
  pdf: Object.freeze(['.pdf']),
  video: Object.freeze(['.mp4', '.mov', '.webm', '.mkv', '.m4v', '.avi']),
  audio: Object.freeze(['.mp3', '.wav', '.m4a', '.ogg', '.oga', '.flac', '.aac', '.opus']),
});

const IMAGE_MIME_BY_EXT = Object.freeze({
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
});

const PRIVATE_BASENAME_RE = Object.freeze([
  /^\.env(\.|$)/i,
  /credential/i,
  /^id_(rsa|ed25519|ecdsa|dsa)/i,
  /^private_key/i,
  /\.pem$/i,
  /\.key$/i,
  /^known_hosts/i,
  /^authorized_keys/i,
  /^\.netrc$/i,
  /^\.git-credentials$/i,
  /^\.npmrc$/i,
]);
const PRIVATE_SEGMENTS = Object.freeze(new Set(['.ssh', '.aws', '.gnupg', '.gcloud', '.kube', '.config', '.codex', '.claude', '.harmonika', '.git', 'secrets', 'credentials']));

class ToolError extends Error {
  constructor(code) {
    super(String(code));
    this.name = 'ToolError';
    this.code = String(code);
  }
}

function isUnderOrEqual(parent, child) {
  const rel = path.relative(parent, child);
  return rel === '' || (!rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel));
}

function isUnder(parent, child) {
  const rel = path.relative(parent, child);
  return rel !== '' && !rel.startsWith('..' + path.sep) && rel !== '..' && !path.isAbsolute(rel);
}

function isPrivatePath(p) {
  const segments = p.split(path.sep).filter(Boolean);
  const sourceFolders=new Set(['.github','.vscode','.cursor','.agents']);
  if(segments.some((part,index)=>part.startsWith('.')&&!sourceFolders.has(part)&&(index<segments.length-1||(fs.existsSync(p)&&fs.statSync(p).isDirectory()))))return true;
  for (const seg of segments) {
    if (PRIVATE_SEGMENTS.has(seg.toLowerCase())) return true;
  }
  const base = path.basename(p);
  return PRIVATE_BASENAME_RE.some((re) => re.test(base));
}

function readMagic(canonical, bytes = 16) {
  let fd;
  try {
    fd = fs.openSync(canonical, 'r');
    const buf = Buffer.alloc(bytes);
    const n = fs.readSync(fd, buf, 0, bytes, 0);
    return buf.subarray(0, n);
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

function detectImageMime(header) {
  if (header.length >= 8 && header.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return 'image/png';
  }
  if (header.length >= 3 && header[0] === 0xff && header[1] === 0xd8 && header[2] === 0xff) {
    return 'image/jpeg';
  }
  if (header.length >= 12 && header.subarray(0, 4).toString('latin1') === 'RIFF' && header.subarray(8, 12).toString('latin1') === 'WEBP') {
    return 'image/webp';
  }
  return null;
}

function validateProjectCwd(callerCwd, homedir, target) {
  const selected=projectAccess.findRoot(target||callerCwd);
  if(selected)return selected;
  if (callerCwd === homedir || callerCwd === path.join(homedir, '/')) {
    throw new ToolError('cwd_home_root_not_allowed');
  }
  if (path.parse(callerCwd).root === callerCwd) {
    throw new ToolError('cwd_generic_not_allowed');
  }
  let st;
  try {
    st = fs.statSync(callerCwd);
  } catch {
    throw new ToolError('cwd_missing');
  }
  if (!st.isDirectory()) throw new ToolError('cwd_not_directory');
  let real;
  try {
    real = fs.realpathSync(callerCwd);
  } catch {
    throw new ToolError('cwd_missing');
  }
  if (real === homedir) throw new ToolError('cwd_home_root_not_allowed');
  if (real.startsWith(PROJECT_HOME_PREFIX) && path.relative(PROJECT_HOME_PREFIX, real).split(path.sep)[0] !== '') {
    return real;
  }
  for (const marker of PROJECT_MARKER_FILES) {
    try {
      const markerStat = fs.statSync(path.join(real, marker));
      if (markerStat.isFile() || (marker === '.git' && markerStat.isDirectory())) return real;
    } catch {
      /* keep scanning */
    }
  }
  throw new ToolError('cwd_not_a_project');
}

function resolveInputFile(rawPath, kind, callerCwd, opts) {
  const { homedir, downloadsDir } = opts;
  if (typeof rawPath !== 'string' || rawPath.trim() === '') throw new ToolError('invalid_file_path');
  const abs = path.resolve(callerCwd, rawPath);

  let lst;
  try {
    lst = fs.lstatSync(abs);
  } catch {
    throw new ToolError('file_not_found');
  }
  if (lst.isSymbolicLink()) throw new ToolError('symlink_not_allowed');

  let canonical;
  try {
    canonical = fs.realpathSync(abs);
  } catch {
    throw new ToolError('file_not_found');
  }
  if (isPrivatePath(canonical) || isPrivatePath(abs)) throw new ToolError('private_file_not_allowed');

  const st = fs.statSync(canonical);
  if (!st.isFile()) throw new ToolError('not_a_regular_file');

  const projectRoot = validateProjectCwd(callerCwd, homedir, canonical);
  const inProject = isUnderOrEqual(projectRoot, canonical);
  let downloadsReal = null;
  if (downloadsDir) {
    try {
      downloadsReal = fs.realpathSync(downloadsDir);
    } catch {
      downloadsReal = null;
    }
  }
  const inDownloads = downloadsReal ? isUnder(downloadsReal, canonical) : false;
  if (!inProject && !inDownloads) throw new ToolError('path_outside_allowed_roots');

  const maxBytes = kind === 'video' || kind === 'audio' ? LIMITS.AV_MAX_BYTES : LIMITS.IMAGE_PDF_MAX_BYTES;
  if (st.size === 0) throw new ToolError('empty_file');
  if (st.size > maxBytes) throw new ToolError('file_too_large');

  const ext = path.extname(canonical).toLowerCase();
  if (!EXT_BY_KIND[kind].includes(ext)) throw new ToolError('unsupported_file_extension');

  return { canonical, ext, size: st.size, projectRoot, inDownloads };
}

function validateImageHeader(canonical, ext) {
  const mime = detectImageMime(readMagic(canonical));
  if (!mime) throw new ToolError('invalid_image_signature');
  if (mime !== IMAGE_MIME_BY_EXT[ext]) throw new ToolError('content_type_mismatch');
  return mime;
}

function validatePdfHeader(canonical) {
  const header = readMagic(canonical, 5).toString('latin1');
  if (!header.startsWith('%PDF-')) throw new ToolError('invalid_pdf_signature');
}

/* ---------------- default backend RPC (fixed SSH spawn) ---------------- */

function readLicensedKey() {
  let fd;
  try {
    const keyFile = path.join(os.homedir(), '.config/harmonika/harmonika-api-key');
    fd = fs.openSync(keyFile, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0));
    const st = fs.fstatSync(fd);
    if (!st.isFile() || (process.platform !== 'win32' && ((st.mode & 0o077) || (typeof process.getuid === 'function' && st.uid !== process.getuid())))) {
      return null;
    }
    const key = fs.readFileSync(fd, 'utf8').trim();
    return /^hai_[A-Za-z0-9_]{16,240}$/.test(key) ? key : null;
  } catch {
    return null;
  } finally {
    if (fd !== undefined) {
      try { fs.closeSync(fd); } catch {}
    }
  }
}

function readDeviceId() {
  if (process.env.HARMONIKA_DEVICE_ID && /^dev_[a-f0-9]{32}$/.test(process.env.HARMONIKA_DEVICE_ID)) {
    return process.env.HARMONIKA_DEVICE_ID;
  }
  try {
    const devFile = path.join(os.homedir(), '.config/harmonika/device-id');
    const val = fs.readFileSync(devFile, 'utf8').trim();
    if (/^dev_[a-f0-9]{32}$/.test(val)) return val;
  } catch {}
  return '';
}

async function httpsRpc(payload, apiKey) {
  const input = Buffer.from(JSON.stringify(payload), 'utf8');
  if (input.length > LIMITS.RPC_STDIN_MAX_BYTES) {
    throw new ToolError('payload_too_large');
  }
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), LIMITS.RPC_TIMEOUT_MS);
  if (timer.unref) timer.unref();
  const devId = readDeviceId();
  const reqHeaders = {
    Authorization: 'Bearer ' + apiKey,
    'Content-Type': 'application/json',
  };
  if (devId) {
    reqHeaders['X-Harmonika-Device-ID'] = devId;
    reqHeaders['X-Harmonika-Device-Name'] = (process.env.HARMONIKA_DEVICE_NAME || os.hostname() || 'terminal').slice(0, 72);
  }
  try {
    const res = await fetch('https://chat.harmonika.id/v1/terminal/rpc', {
      method: 'POST',
      redirect: 'error',
      signal: control.signal,
      headers: reqHeaders,
      body: input,
    });
    const parts = [];
    let outLen = 0;
    for await (const chunk of res.body) {
      outLen += chunk.byteLength;
      if (outLen > LIMITS.RPC_OUTPUT_MAX_BYTES) {
        control.abort();
        throw new ToolError('backend_output_too_large');
      }
      parts.push(Buffer.from(chunk));
    }
    const text = Buffer.concat(parts).toString('utf8');
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new ToolError(res.ok ? 'backend_invalid_response' : 'backend_unavailable');
    }
    if (!res.ok && parsed && parsed.error && parsed.error.code) {
      throw new ToolError(String(parsed.error.code));
    }
    return parsed;
  } catch (err) {
    if (err instanceof ToolError) throw err;
    throw new ToolError(err && err.name === 'AbortError' ? 'backend_timeout' : 'backend_unavailable');
  } finally {
    clearTimeout(timer);
  }
}

function defaultRpc(payload, deps) {
  if (process.env.HARMONIKA_TRANSPORT !== 'owner-ssh') {
    if (process.env.HARMONIKA_MEDIA_BRIDGE === '0') {
      return Promise.reject(new ToolError('licensed_media_transport_unavailable'));
    }
    const apiKey = readLicensedKey();
    if (!apiKey) {
      return Promise.reject(new ToolError('licensed_media_transport_unavailable'));
    }
    return httpsRpc(payload, apiKey);
  }
  if(!/^\d{1,5}$/.test(process.env.HARMONIKA_OWNER_SSH_PORT||'22')||
    !/^[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+$/.test(process.env.HARMONIKA_OWNER_SSH_TARGET||'')||
    !path.isAbsolute(process.env.HARMONIKA_OWNER_SSH_KEY||''))return Promise.reject(new ToolError('licensed_media_transport_unavailable'));
  const input = Buffer.from(JSON.stringify(payload), 'utf8');
  if (input.length > LIMITS.RPC_STDIN_MAX_BYTES) {
    return Promise.reject(new ToolError('payload_too_large'));
  }
  return new Promise((resolve, reject) => {
    const child = spawn(BIN.ssh, RPC_SSH_ARGS, {
      shell: false,
      stdio: ['pipe', 'pipe', 'ignore'],
      windowsHide: true,
    });
    deps._activeChildren.add(child);
    const chunks = [];
    let outLen = 0;
    let settled = false;
    let timer = null;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      deps._activeChildren.delete(child);
      fn(value);
    };
    timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      finish(reject, new ToolError('backend_timeout'));
    }, LIMITS.RPC_TIMEOUT_MS);
    child.stdout.on('data', (b) => {
      outLen += b.length;
      if (outLen > LIMITS.RPC_OUTPUT_MAX_BYTES) {
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        finish(reject, new ToolError('backend_output_too_large'));
        return;
      }
      chunks.push(b);
    });
    child.on('error', () => finish(reject, new ToolError('backend_unavailable')));
    child.on('close', (code) => {
      const text = Buffer.concat(chunks).toString('utf8');
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch {
        return finish(reject, new ToolError(code === 0 ? 'backend_invalid_response' : 'backend_unavailable'));
      }
      finish(resolve, parsed);
    });
    child.stdin.on('error', () => { /* surfaced via close/error */ });
    child.stdin.end(input);
  });
}

/* ---------------- default local media helpers ---------------- */

function defaultFfprobe(file) {
  return new Promise((resolve, reject) => {
    const child = spawn(BIN.ffprobe, ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov,mp3,wav,ogg,matroska,webm,aac,flac,avi', '-print_format', 'json', '-show_format', '-show_streams', file], {
      shell: false,
      stdio: ['ignore', 'pipe', 'ignore'],
      windowsHide: true,
    });
    const chunks = [];
    let outLen = 0;
    let settled = false;
    let timer = null;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      fn(value);
    };
    timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      finish(reject, new ToolError('probe_timeout'));
    }, LIMITS.PROBE_TIMEOUT_MS);
    child.stdout.on('data', (b) => {
      outLen += b.length;
      if (outLen > LIMITS.PROBE_OUTPUT_MAX_BYTES) {
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        finish(reject, new ToolError('probe_output_too_large'));
        return;
      }
      chunks.push(b);
    });
    child.on('error', () => finish(reject, new ToolError('probe_unavailable')));
    child.on('close', () => {
      let info;
      try {
        info = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        return finish(reject, new ToolError('probe_failed'));
      }
      const duration = Number(info && info.format && info.format.duration);
      const streams = Array.isArray(info && info.streams) ? info.streams : [];
      if (!Number.isFinite(duration) || duration <= 0) return finish(reject, new ToolError('probe_failed'));
      finish(resolve, {
        duration,
        hasVideo: streams.some((s) => s && s.codec_type === 'video'),
        hasAudio: streams.some((s) => s && s.codec_type === 'audio'),
      });
    });
  });
}

function defaultExtractFrames(file, times, dir) {
  const vf = `scale=${LIMITS.FRAME_MAX_PX}:${LIMITS.FRAME_MAX_PX}:force_original_aspect_ratio=decrease:force_divisible_by=2`;
  const one = (t, i) =>
    new Promise((resolve, reject) => {
      const framePath = path.join(dir, `frame-${i}.png`);
      const args = [
        '-protocol_whitelist', 'file,pipe',
        '-format_whitelist', 'mov,matroska,webm,avi',
        '-hide_banner', '-loglevel', 'error',
        '-ss', String(t),
        '-i', file,
        '-frames:v', '1',
        '-vf', vf,
        '-f', 'image2',
        '-c:v', 'png',
        '-y', framePath,
      ];
      const child = spawn(BIN.ffmpeg, args, { shell: false, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
      const errChunks = [];
      let errLen = 0;
      let settled = false;
      let timer = null;
      const finish = (fn, value) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        fn(value);
      };
      timer = setTimeout(() => {
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        finish(reject, new ToolError('frame_extraction_timeout'));
      }, LIMITS.FFMPEG_TIMEOUT_MS);
      child.stderr.on('data', (b) => {
        errLen += b.length;
        if (errLen <= LIMITS.PROBE_OUTPUT_MAX_BYTES) errChunks.push(b);
      });
      child.on('error', () => finish(reject, new ToolError('frame_extraction_failed')));
      child.on('close', (code) => {
        let buf;
        try {
          buf = fs.readFileSync(framePath);
        } catch {
          return finish(reject, new ToolError('frame_extraction_failed'));
        }
        if (code !== 0 || buf.length === 0 || detectImageMime(buf) !== 'image/png') {
          return finish(reject, new ToolError('frame_extraction_failed'));
        }
        finish(resolve, { t, buffer: buf });
      });
    });
  return Promise.all(times.map(one));
}

function defaultAsr(file, language) {
  return new Promise((resolve, reject) => {
    const args = [BIN.asrScript, file];
    if (language) args.push(String(language));
    const child = spawn(BIN.asrPython, args, {
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    const chunks = [];
    let outLen = 0;
    let settled = false;
    let timer = null;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      fn(value);
    };
    timer = setTimeout(() => {
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      finish(reject, new ToolError('asr_timeout'));
    }, LIMITS.ASR_TIMEOUT_MS);
    const onData = (b) => {
      outLen += b.length;
      if (outLen > LIMITS.ASR_OUTPUT_MAX_BYTES) {
        try { child.kill('SIGKILL'); } catch { /* already gone */ }
        finish(reject, new ToolError('asr_output_too_large'));
        return;
      }
      chunks.push(b);
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('error', () => finish(reject, new ToolError('asr_unavailable')));
    child.on('close', (code) => {
      const text = Buffer.concat(chunks).toString('utf8');
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch {
        return finish(reject, new ToolError('asr_invalid_output'));
      }
      if (code !== 0 && parsed && parsed.ok !== true) {
        return finish(reject, new ToolError('asr_failed'));
      }
      finish(resolve, parsed);
    });
  });
}

/* ---------------- argument validation ---------------- */

const ARG_SPECS = Object.freeze({
  create_directory: Object.freeze({directory: Object.freeze({required:true,max:4096})}),
  capabilities: Object.freeze({}),
  web_search: Object.freeze({
    query: Object.freeze({ required: true, max: LIMITS.QUERY_MAX_CHARS }),
  }),
  analyze_image: Object.freeze({
    file_path: Object.freeze({ required: true, max: 4096 }),
    prompt: Object.freeze({ required: true, max: LIMITS.PROMPT_MAX_CHARS }),
  }),
  read_pdf: Object.freeze({
    file_path: Object.freeze({ required: true, max: 4096 }),
    prompt: Object.freeze({ required: true, max: LIMITS.PROMPT_MAX_CHARS }),
  }),
  analyze_video: Object.freeze({
    file_path: Object.freeze({ required: true, max: 4096 }),
    prompt: Object.freeze({ required: true, max: LIMITS.PROMPT_MAX_CHARS }),
  }),
  transcribe_audio: Object.freeze({
    file_path: Object.freeze({ required: true, max: 4096 }),
    language: Object.freeze({ required: false, max: LIMITS.LANGUAGE_MAX_CHARS, pattern: /^[A-Za-z][A-Za-z0-9_-]{0,31}$/ }),
  }),
  generate_image: Object.freeze({
    prompt: Object.freeze({ required: true, max: LIMITS.PROMPT_MAX_CHARS }),
    output_file: Object.freeze({ required: true, max: 4096 }),
  }),
});

function validateArgs(toolName, args) {
  const spec = ARG_SPECS[toolName];
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new ToolError('arguments_must_be_object');
  const out = {};
  for (const key of Object.keys(args)) {
    if (!Object.prototype.hasOwnProperty.call(spec, key)) throw new ToolError(`unknown_argument:${key}`);
  }
  for (const [key, rule] of Object.entries(spec)) {
    const v = args[key];
    if (v === undefined || v === null) {
      if (rule.required) throw new ToolError(`missing_argument:${key}`);
      continue;
    }
    if (typeof v !== 'string') throw new ToolError(`invalid_argument_type:${key}`);
    if (rule.required && v.trim() === '') throw new ToolError(`empty_argument:${key}`);
    if (v.length > rule.max) throw new ToolError(`argument_too_long:${key}`);
    if (rule.pattern && !rule.pattern.test(v)) throw new ToolError(`invalid_argument_format:${key}`);
    out[key] = v;
  }
  return out;
}

/* ---------------- backend response shaping ---------------- */

const BACKEND_SAFE_KEYS = Object.freeze([
  'status', 'results', 'answer', 'query', 'error', 'provider', 'model',
  'capabilities', 'version', 'count', 'elapsed_ms', 'description', 'analysis',
  'frame_count', 'timestamps', 'width', 'height', 'mime_type',
]);

function pickSafe(src, keys) {
  const out = {};
  for (const k of keys) {
    if (src && Object.prototype.hasOwnProperty.call(src, k) && src[k] !== undefined) out[k] = src[k];
  }
  return out;
}

function backendResult(res, extraKeys) {
  if (!res || typeof res !== 'object' || typeof res.ok !== 'boolean') {
    return { ok: false, status: 'error', error: 'invalid_backend_response' };
  }
  const safeError = res.ok ? undefined : typeof res.error === 'string' ? res.error : typeof res.error?.code === 'string' ? res.error.code : 'backend_error';
  const shaped = {
    ok: res.ok,
    status: typeof res.status === 'string' ? res.status : res.ok ? 'ok' : 'error',
    ...pickSafe(res, extraKeys || BACKEND_SAFE_KEYS),
  };
  if (safeError !== undefined) shaped.error = safeError;
  return shaped;
}

/* ---------------- tool metadata ---------------- */

const objSchema = (properties, required) => ({
  type: 'object',
  properties,
  required: required || [],
  additionalProperties: false,
});

const TOOL_DEFS = Object.freeze([
  {
    name: 'create_directory',
    description: 'Create/select a project folder using an absolute directory inside your Downloads or Projects. Works from Home. Then use returned absolute paths with file tools and working_dir with bash; no manual mkdir needed.',
    inputSchema: objSchema({directory:{type:'string',maxLength:4096}},['directory']),
    annotations: {readOnlyHint:false,destructiveHint:false},
  },
  {
    name: 'capabilities',
    description: 'Report backend (remote RPC) capabilities combined with local ASR availability.',
    inputSchema: objSchema({}),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'web_search',
    description: 'Search the web via the harmonika backend.',
    inputSchema: objSchema(
      { query: { type: 'string', description: 'Search query', maxLength: LIMITS.QUERY_MAX_CHARS } },
      ['query']
    ),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'analyze_image',
    description: 'Analyze a local image (PNG/JPEG/WebP, <=10MB) with a prompt via the backend vision model.',
    inputSchema: objSchema(
      {
        file_path: { type: 'string', description: 'Image path inside the project (or a media file in ~/Downloads)' },
        prompt: { type: 'string', maxLength: LIMITS.PROMPT_MAX_CHARS },
      },
      ['file_path', 'prompt']
    ),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'read_pdf',
    description: 'Read/analyze a local PDF (<=10MB) with a prompt via the backend document model.',
    inputSchema: objSchema(
      {
        file_path: { type: 'string', description: 'PDF path inside the project (or a media file in ~/Downloads)' },
        prompt: { type: 'string', maxLength: LIMITS.PROMPT_MAX_CHARS },
      },
      ['file_path', 'prompt']
    ),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'analyze_video',
    description: 'Analyze a local video by sampling up to 6 frames (<=10min duration); the full video is never uploaded, only the sampled frames.',
    inputSchema: objSchema(
      {
        file_path: { type: 'string', description: 'Video path inside the project (or a media file in ~/Downloads)' },
        prompt: { type: 'string', maxLength: LIMITS.PROMPT_MAX_CHARS },
      },
      ['file_path', 'prompt']
    ),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'transcribe_audio',
    description: 'Transcribe a local audio file with the local ASR runtime (no audio is uploaded).',
    inputSchema: objSchema(
      {
        file_path: { type: 'string', description: 'Audio path inside the project (or a media file in ~/Downloads)' },
        language: { type: 'string', description: 'Optional language hint', maxLength: LIMITS.LANGUAGE_MAX_CHARS },
      },
      ['file_path']
    ),
    annotations: { readOnlyHint: true },
  },
  {
    name: 'generate_image',
    description: 'Generate a PNG image from a prompt; the file is created exclusively (mode 0600, never overwritten) inside the project.',
    inputSchema: objSchema(
      {
        prompt: { type: 'string', maxLength: LIMITS.PROMPT_MAX_CHARS },
        output_file: { type: 'string', description: 'Output .png path inside the project; must not exist yet' },
      },
      ['prompt', 'output_file']
    ),
    annotations: { readOnlyHint: false },
  },
]);

/* ---------------- core factory ---------------- */

function createCore(overrides) {
  const deps = {
    callerCwd: process.cwd(),
    homedir: os.homedir(),
    downloadsDir: path.join(os.homedir(), 'Downloads'),
    tmpDir: os.tmpdir(),
    rpc: (payload) => defaultRpc(payload, internalDeps),
    ffprobe: defaultFfprobe,
    extractFrames: defaultExtractFrames,
    asr: defaultAsr,
    exists: (p) => fs.existsSync(p),
    ...(overrides || {}),
  };
  const internalDeps = { _activeChildren: new Set() };

  function asrAvailability() {
    const runtimePresent = !!deps.exists(BIN.asrPython);
    const scriptPresent = !!deps.exists(BIN.asrScript);
    return {
      available: runtimePresent && scriptPresent,
      runtime_present: runtimePresent,
      script_present: scriptPresent,
    };
  }

  function resolveOutputFile(rawPath) {
    if (typeof rawPath !== 'string' || rawPath.trim() === '') throw new ToolError('invalid_output_path');
    let abs = path.resolve(deps.callerCwd, rawPath);
    const autoGambarDir = path.join(deps.downloadsDir || path.join(deps.homedir, 'Downloads'), 'harmonika-ai', 'gambar');
    const isBroadCwd = [deps.homedir, path.join(deps.homedir, 'Downloads'), path.join(deps.homedir, 'Projects')].includes(path.resolve(deps.callerCwd));
    if (!path.isAbsolute(rawPath) && isBroadCwd && !rawPath.split(/[\\/]/).includes('..')) {
      fs.mkdirSync(autoGambarDir, { recursive: true, mode: 0o755 });
      abs = path.join(autoGambarDir, path.basename(rawPath));
    }
    const autoRoot = path.join(deps.downloadsDir || path.join(deps.homedir, 'Downloads'), 'harmonika-ai');
    if (isUnderOrEqual(autoRoot, abs)) {
      fs.mkdirSync(path.dirname(abs), { recursive: true, mode: 0o755 });
    }
    const projectRoot = isUnderOrEqual(autoRoot, abs)
      ? fs.realpathSync(autoRoot)
      : validateProjectCwd(deps.callerCwd, deps.homedir, abs);
    if (path.extname(abs).toLowerCase() !== '.png') throw new ToolError('output_must_be_png');
    if (isPrivatePath(abs)) throw new ToolError('private_file_not_allowed');
    let parentReal;
    try {
      parentReal = fs.realpathSync(path.dirname(abs));
      if (!fs.statSync(parentReal).isDirectory()) throw new ToolError('output_dir_missing');
    } catch (e) {
      if (e instanceof ToolError) throw e;
      throw new ToolError('output_dir_missing');
    }
    if (!isUnderOrEqual(projectRoot, parentReal)) throw new ToolError('output_outside_project');
    if (fs.existsSync(abs)) throw new ToolError('output_exists');
    return abs;
  }

  const ops = {
    async create_directory(args) {return projectAccess.createDirectory(args.directory);},
    async capabilities() {
      let remote;
      try {
        // Status must probe the same authenticated transport used by image tools.
        const res = deps.mediaBridge
          ? await deps.mediaBridge.capabilities()
          : await deps.rpc({ operation: 'capabilities' });
        if (res && typeof res === 'object' && res.ok === true) {
          remote = {
            available: true,
            status: typeof res.status === 'string' ? res.status : 'ok',
            capabilities: res.features ?? res.capabilities ?? null,
            limits: res.limits ?? null,
          };
        } else {
          remote = { available: false, status: 'error', error: 'backend_error' };
        }
      } catch {
        remote = { available: false, status: 'unavailable', error: 'backend_unavailable' };
      }
      const degraded = !remote.available;
      return {
        ok: !degraded,
        status: degraded ? 'degraded' : 'ok',
        backend: remote,
        local_asr: asrAvailability(),
      };
    },

    async web_search(args) {
      const res = await deps.rpc({ operation: 'web_search', query: args.query });
      return backendResult(res, ['results', 'answer', 'sources', 'query', 'count', 'provider', 'elapsed_ms']);
    },

    async analyze_image(args) {
      const f = resolveInputFile(args.file_path, 'image', deps.callerCwd, deps);
      const mime = validateImageHeader(f.canonical, f.ext);
      const image_base64 = fs.readFileSync(f.canonical).toString('base64');
      let res;
      if(deps.mediaBridge){
        res=await deps.mediaBridge.analyze(image_base64,args.prompt);
        if(res?.handled!==true){if(res?.not_dispatched!==true)throw new ToolError('media_dispatch_uncertain');res=undefined;}
      }
      if(!res)res = await deps.rpc({ operation: 'analyze_image', image_base64, prompt: args.prompt });
      return backendResult(res, ['text_detected', 'ui_elements', 'page_state', 'warnings', 'recommended_action', 'description', 'analysis', 'model', 'provider', 'mime_type', 'processing_mode', 'analysis_performed', 'content_is_untrusted']);
    },

    async read_pdf(args) {
      const f = resolveInputFile(args.file_path, 'pdf', deps.callerCwd, deps);
      validatePdfHeader(f.canonical);
      const pdf_base64 = fs.readFileSync(f.canonical).toString('base64');
      const res = await deps.rpc({ operation: 'read_pdf', pdf_base64, prompt: args.prompt });
      return backendResult(res, ['text', 'truncated', 'max_pages_processed', 'content_is_untrusted', 'warnings', 'description', 'analysis', 'model', 'provider']);
    },

    async analyze_video(args) {
      const f = resolveInputFile(args.file_path, 'video', deps.callerCwd, deps);
      const probe = await deps.ffprobe(f.canonical);
      if (!probe.hasVideo) throw new ToolError('no_video_stream');
      if (!(probe.duration > 0)) throw new ToolError('probe_failed');
      if (probe.duration > LIMITS.VIDEO_MAX_DURATION_S) throw new ToolError('video_too_long');
      const count = LIMITS.FRAME_COUNT;
      const times = Array.from({ length: count }, (_, i) =>
        Math.round(((i + 0.5) * probe.duration) / count * 100) / 100
      );
      const dir = fs.mkdtempSync(path.join(deps.tmpDir, 'harmonika-vid-'));
      try {
        const frames = await deps.extractFrames(f.canonical, times, dir);
        const frames_base64 = frames.map((fr) => fr.buffer.toString('base64'));
        const caption =
          `sample timestamps ${times.map((t) => `${t}s`).join(', ')} of ${probe.duration}s; ` +
          `visual analysis derived from ${frames_base64.length} sampled frames only, not the full video`;
        const res = await deps.rpc({ operation: 'analyze_video', frames_base64, caption, prompt: args.prompt });
        const shaped = backendResult(res, ['summary', 'topics', 'brand_product_mentions', 'relevance_score', 'safety_notes', 'description', 'analysis', 'model', 'provider']);
        shaped.frame_count = frames_base64.length;
        shaped.timestamps = times;
        shaped.analysis_scope = 'sampled_frames_only';
        return shaped;
      } finally {
        fs.rmSync(dir, { recursive: true, force: true });
      }
    },

    async transcribe_audio(args) {
      const f = resolveInputFile(args.file_path, 'audio', deps.callerCwd, deps);
      const probe = await deps.ffprobe(f.canonical);
      if (!probe.hasAudio) throw new ToolError('no_audio_stream');
      const res = await deps.asr(f.canonical, args.language);
      const shaped = backendResult(res, ['transcript', 'language', 'confidence', 'language_probability', 'seconds', 'truncated', 'warnings']);
      shaped.audio_path = path.basename(f.canonical);
      shaped.transcription_scope = 'local_asr_audio_only';
      return shaped;
    },

    async generate_image(args) {
      const outPath = resolveOutputFile(args.output_file);
      let res;
      if(deps.mediaBridge){
        res=await deps.mediaBridge.generate(args.prompt,outPath);
        if(res?.handled!==true){
          if(res?.not_dispatched!==true)throw new ToolError('media_dispatch_uncertain');
          if(deps.allowMediaFallback!==true)throw new ToolError('image_engine_offline');
          res=undefined;
        }
      }
      if(!res)res = await deps.rpc({ operation: 'generate_image', prompt: args.prompt });
      if (!res || typeof res !== 'object' || res.ok !== true || res.status !== 'ok') {
        return backendResult(res);
      }
      if (res.mime_type !== 'image/png' || typeof res.image_base64 !== 'string' || res.image_base64 === '') {
        throw new ToolError('invalid_backend_image');
      }
      const png = Buffer.from(res.image_base64, 'base64');
      if (png.length === 0 || detectImageMime(png) !== 'image/png') throw new ToolError('invalid_backend_image');
      let fd;
      try {
        fd = fs.openSync(outPath, 'wx', 0o600);
        fs.writeFileSync(fd, png);
      } catch (error) {
        // Only remove a file created by this call, never an existing user's file.
        if (fd !== undefined) {
          fs.closeSync(fd); fd = undefined;
          try { fs.unlinkSync(outPath); } catch {}
        }
        throw error;
      } finally {
        if (fd !== undefined) fs.closeSync(fd);
      }
      const shaped = backendResult(res, ['provider']);
      shaped.status = 'ok';
      shaped.mime_type = 'image/png';
      shaped.width = Number.isFinite(res.width) ? res.width : null;
      shaped.height = Number.isFinite(res.height) ? res.height : null;
      shaped.output_file = outPath;
      shaped.bytes = png.length;
      return shaped;
    },
  };

  function listTools() {
    return TOOL_DEFS.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      annotations: { ...t.annotations },
    }));
  }

  async function callTool(name, args) {
    const def = TOOL_DEFS.find((t) => t.name === name);
    if (!def) {
      return textResult({ ok: false, status: 'error', error: `unknown_tool:${name}` });
    }
    let data;
    try {
      const normalizedArgs = args === undefined || args === null ? {} : args;
      const parsed = validateArgs(name, normalizedArgs);
      data = await ops[name](parsed);
    } catch (e) {
      const code = e && e.code ? e.code : 'internal_error';
      data = { ok: false, status: 'error', error: code, ...(e?.uncertain?{retry_safe:false,operation_state:'uncertain',message:'Status pekerjaan belum pasti. Jangan membuat ulang pekerjaan dengan identitas berbeda.'}:{}) };
      const mediaFailures={
        image_engine_offline:['Engine gambar lokal sedang offline. Silakan coba lagi setelah PC gambar terhubung.',true],
        image_worker_offline:['Engine gambar lokal sedang offline. Silakan coba lagi setelah PC gambar terhubung.',true],
        rtx_offline:['Engine gambar lokal sedang offline. Silakan coba lagi setelah PC gambar terhubung.',true],
        worker_busy:['Engine gambar sedang mengerjakan permintaan lain. Silakan coba lagi sebentar.',true],
        worker_vram_full:['Memori GPU sedang penuh. Permintaan gambar belum berhasil; silakan coba lagi setelah memori tersedia.',true],
        worker_warming:['Engine gambar sedang disiapkan. Silakan coba lagi sebentar.',true],
        gpu_job_recovery_required:['Status pekerjaan GPU perlu diperiksa sebelum membuat permintaan baru.',false],
        invalid_worker_response:['Hasil dari engine gambar tidak valid dan sudah ditolak.',false],
        invalid_image_result:['Hasil gambar tidak valid dan sudah ditolak.',false],
        worker_auth_failed:['Koneksi engine gambar memerlukan pemulihan akses.',false],
      };
      if(mediaFailures[code])data={...data,status:'degraded',message:e?.uncertain?data.message:mediaFailures[code][0],retryable:!e?.uncertain&&mediaFailures[code][1],retry_safe:!e?.uncertain,operation_state:e?.uncertain?'uncertain':code==='image_engine_offline'?'not_dispatched':'failed'};
      if(code==='local_vision_unavailable')data={ok:false,status:'degraded',error:code,analysis_performed:false,processing_mode:'local_only',message:'Analisis gambar lokal sedang tidak tersedia. Gambar belum dianalisis.'};
    }
    return textResult(data);
  }

  function textResult(data) {
    const isError = !data || data.ok !== true || data.status === 'degraded';
    // Branding only: do not replace user document text, URLs, prompts or code.
    data = {...publicMetadata(data), provider: DISPLAY_PROVIDER};
    let text = JSON.stringify(data);
    if (text.length > LIMITS.RESULT_MAX_CHARS) {
      text = JSON.stringify({
        ok: !!data.ok,
        status: typeof data.status === 'string' ? data.status : 'ok',
        truncated: true,
        provider: DISPLAY_PROVIDER,
        preview: text.slice(0, 20000),
      });
    }
    return { content: [{ type: 'text', text }], isError };
  }

  function shutdown() {
    if(deps.mediaBridge)deps.mediaBridge.close();
    for (const child of internalDeps._activeChildren) {
      try { child.kill('SIGTERM'); } catch { /* already gone */ }
    }
    internalDeps._activeChildren.clear();
  }

  return { listTools, callTool, shutdown, ops, asrAvailability, deps };
}

module.exports = {
  publicMetadata,
  createCore,
  ToolError,
  SERVER_INFO,
  LIMITS,
  BIN,
  RPC_SSH_ARGS,
  PROJECT_MARKER_FILES,
  EXT_BY_KIND,
  detectImageMime,
  isPrivatePath,
  isUnderOrEqual,
  validateProjectCwd,
};
