#!/usr/bin/env node
'use strict';
/**
 * Harmonika Agent v3.1 — Target 100%
 * Improvements over v3:
 * - Handles ALL gateway errors gracefully (including invalid_tool_response)
 * - STREAMING progress (real-time output)
 * - AUTONOMOUS mode (detects and fixes issues without being asked)
 * - Enhanced memory (pre-loaded common patterns)
 * - CLEAN tool responses (strips secrets, limits size, validates format)
 */
const { execSync } = require('child_process');
const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const GATEWAY = 'https://chat.harmonika.id';
const HOME = process.env.HOME;
const STATE_DIR = path.join(HOME, '.config/harmonika/agent-state');
fs.mkdirSync(STATE_DIR, { recursive: true });

const API_KEY = (() => {
  try {
    const raw = fs.readFileSync(path.join(HOME, 'Projects/harmonika-engine/config/opencode.jsonc'), 'utf8');
    return raw.match(/"apiKey":\s*"([^"]+)"/)?.[1] || '';
  } catch { return ''; }
})();

const SSH = 'ssh -i ~/.ssh/harmonika_wg_target -o ConnectTimeout=10 -o BatchMode=yes root@38.47.176.62';

// ═══ CLEAN TOOL RESPONSE ═══
function cleanToolOutput(output) {
  let text = String(output || '');
  // Strip ANSI
  text = text.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '');
  // Strip potential secrets
  text = text.replace(/(?:api[_-]?key|token|password|secret)\s*[:=]\s*\S+/gi, '[REDACTED]');
  // Limit size
  if (text.length > 15000) text = text.slice(0, 15000) + '\n...[truncated]';
  return text;
}

// ═══ PRE-LOADED MEMORY PATTERNS ═══
const BUILTIN_PATTERNS = {
  'permission denied': 'Check SSH key permissions (chmod 600), verify correct user (root@), ensure key is added',
  'connection refused': 'Check if service is running (systemctl/docker), verify port is correct, check firewall',
  'no such file': 'Create the file or directory first, check path spelling',
  'docker build failed': 'Check Dockerfile syntax, verify base image exists, use --network host for npm/pip',
  'port already in use': 'Find process with ss -tlnp | grep PORT, stop it or use different port',
  'gateway timeout': 'Retry with exponential backoff, check gateway health',
  'rate limit': 'Wait before retrying, reduce request frequency',
  'ipv4 forwarding disabled': 'Run: sysctl -w net.ipv4.ip_forward=1',
  'dns resolution failed': 'Check /etc/resolv.conf, use --network host for Docker builds',
};

// ═══ SMART GATEWAY v2 ═══
class SmartGateway {
  constructor() {
    this.delay = 1500;
    this.maxRetries = 7;
    this.lastRequest = 0;
    this.consecutiveFailures = 0;
  }

  async call(messages, tools) {
    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      const elapsed = Date.now() - this.lastRequest;
      if (elapsed < this.delay) {
        await new Promise(r => setTimeout(r, this.delay - elapsed));
      }

      try {
        const result = await this._request(messages, tools);
        this.lastRequest = Date.now();
        this.consecutiveFailures = 0;
        if (this.delay > 1500) this.delay = Math.max(1500, this.delay - 500);
        return result;
      } catch (error) {
        this.lastRequest = Date.now();
        this.consecutiveFailures++;

        const retryable = ['timeout', 'socket hang up', 'parse error', 'ECONNRESET', 'No choice'];
        const isToolError = error.message.includes('invalid_tool');

        if (isToolError) {
          // Tool response was rejected — clean it and retry
          if (messages.length > 0) {
            const lastTool = messages[messages.length - 1];
            if (lastTool.role === 'tool') {
              lastTool.content = JSON.stringify({ output: cleanToolOutput(lastTool.content), code: 0 });
            }
          }
          if (attempt < this.maxRetries) continue;
        }

        if (!retryable.some(r => error.message.includes(r)) || attempt === this.maxRetries) {
          throw error;
        }

        this.delay = Math.min(this.delay * 1.3, 20000);
        process.stdout.write(`   🔄 ${attempt}/${this.maxRetries} (${Math.ceil(this.delay/1000)}s) `);
      }
    }
    throw new Error('max retries exceeded');
  }

  _request(messages, tools) {
    return new Promise((resolve, reject) => {
      // Clean all tool messages before sending
      const cleaned = messages.map(m => {
        if (m.role === 'tool') {
          return { ...m, content: typeof m.content === 'string' ? m.content.slice(0, 10000) : JSON.stringify({ output: cleanToolOutput(m.content) }) };
        }
        return m;
      });

      const body = JSON.stringify({ model: 'harmonika-ai', messages: cleaned, tools: tools || undefined, stream: false });
      const req = https.request(new URL(GATEWAY + '/v1/chat/completions'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}`, 'Content-Length': Buffer.byteLength(body) },
        timeout: 120000,
      }, (res) => {
        let data = '';
        res.on('data', c => data += c);
        res.on('end', () => {
          try {
            const p = JSON.parse(data);
            if (p.choices?.[0]) resolve(p);
            else if (p.error) reject(new Error(p.error.message || 'gateway error'));
            else reject(new Error('no choices'));
          } catch { reject(new Error('parse: ' + data.slice(0, 80))); }
        });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
      req.write(body); req.end();
    });
  }
}

// ═══ AGENT v3.1 ═══
const TOOLS = [
  { type: 'function', function: { name: 'bash', description: `Execute bash command. SSH: ${SSH} "cmd"`, parameters: { type: 'object', properties: { command: { type: 'string' }, timeout: { type: 'integer' } }, required: ['command'] } } },
  { type: 'function', function: { name: 'check', description: 'Check if service is running. Returns status.', parameters: { type: 'object', properties: { service: { type: 'string' }, port: { type: 'integer' } }, required: ['service'] } } },
  { type: 'function', function: { name: 'write_file', description: 'Write file locally', parameters: { type: 'object', properties: { path: { type: 'string' }, content: { type: 'string' } }, required: ['path', 'content'] } } },
];

async function run(task, maxTurns = 30) {
  console.log(`🤖 Agent v3.1 | Task: ${task.slice(0, 60)}...`);
  const gw = new SmartGateway();
  const start = Date.now();
  let toolCalls = 0;
  let report = '';

  // Pre-flight
  console.log('🔍 Pre-flight:');
  try {
    const [disk, ram, containers] = execSync(`${SSH} "df / | tail -1 | awk '{print int(\$5)}'; free | awk '/Mem:/ {print int(\$3*100/\$2)}'; docker ps -q | wc -l"`, { encoding: 'utf8', timeout: 15000 }).trim().split('\n');
    console.log(`   Disk:${disk}% RAM:${ram}% Containers:${containers}`);
    if (parseInt(disk) > 85) {
      execSync(`${SSH} "docker system prune -af 2>/dev/null | tail -1"`, { timeout: 60000 });
      console.log('   🔧 Cleaned disk');
    }
  } catch { console.log('   (pre-flight skipped)'); }

  // Messages
  const messages = [
    { role: 'system', content: `Anda adalah Harmonika AI agent executor PRO. Aturan:
1. SELALU gunakan bash tool untuk eksekusi nyata
2. VERIFIKASI dengan output — jangan asal klaim sukses
3. Jika error → diagnose dengan tool, gunakan pattern umum:
${Object.entries(BUILTIN_PATTERNS).map(([k,v]) => `   "${k}" → ${v}`).join('\n')}
4. Ambil inisiatif — fix masalah tanpa ditanya
5. Jawab dalam bahasa Indonesia
SSH: ${SSH}` },
    { role: 'user', content: task },
  ];

  for (let turn = 1; turn <= maxTurns; turn++) {
    let resp;
    try {
      resp = await gw.call(messages, TOOLS);
    } catch (e) {
      console.error(`   ❌ Gateway: ${e.message}`);
      break;
    }

    const choice = resp.choices[0];
    const msg = choice.message;

    if (msg.tool_calls?.length > 0) {
      messages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls });
      for (const tc of msg.tool_calls) {
        const name = tc.function.name;
        let args = {};
        try { args = JSON.parse(tc.function.arguments); } catch {}

        toolCalls++;
        process.stdout.write(`   [${toolCalls}] ${name}: ${(args.command || args.service || '').slice(0, 80)}`);

        let result;
        if (name === 'bash') {
          try {
            const out = execSync(args.command, { timeout: (args.timeout || 90) * 1000, encoding: 'utf8', maxBuffer: 1024*1024, cwd: '/tmp' });
            result = { output: cleanToolOutput(out), code: 0 };
          } catch (err) {
            result = { output: cleanToolOutput(err.stdout || ''), error: cleanToolOutput(err.stderr || err.message), code: err.status || -1 };
          }
        } else if (name === 'check') {
          try {
            const type = args.service.includes('-') && !args.service.includes('@') ? 'docker' : 'systemd';
            const cmd = type === 'docker' ? `docker inspect ${args.service} --format '{{.State.Status}}'` : `systemctl is-active ${args.service}`;
            const out = execSync(`${SSH} "${cmd}"`, { encoding: 'utf8', timeout: 15000 }).trim();
            const ok = out.includes('running') || out.includes('active');
            result = { output: `${args.service}: ${ok ? 'UP' : 'DOWN'} (${out})`, code: ok ? 0 : 1 };
          } catch (e) { result = { output: '', error: e.message, code: -1 }; }
        } else if (name === 'write_file') {
          try {
            const fp = args.path.startsWith('~') ? path.join(HOME, args.path.slice(2)) : args.path;
            fs.writeFileSync(fp, args.content);
            result = { output: 'written', code: 0 };
          } catch (e) { result = { output: '', error: e.message, code: -1 }; }
        } else {
          result = { output: '', error: `unknown: ${name}`, code: -1 };
        }

        console.log(result.code === 0 ? ' ✅' : ' ❌');
        if (result.output) console.log(`      ${result.output.slice(0, 100).replace(/\n/g, ' ')}`);

        // Clean the tool response before sending to gateway
        const cleanResult = JSON.stringify({
          output: cleanToolOutput(result.output || result.error || '(empty)'),
          code: result.code,
        });
        messages.push({ role: 'tool', tool_call_id: tc.id, content: cleanResult });
      }
      continue;
    }

    const text = msg.content || '';
    if (text.trim()) {
      report = text;
      console.log(`   💬 ${text.slice(0, 200)}`);
    }
    if (choice.finish_reason === 'stop') break;
  }

  const elapsed = Math.round((Date.now() - start) / 1000);
  console.log(`\n📊 ${toolCalls} calls | ${elapsed}s | ${report ? '✅' : '⚠️'}`);

  return { toolCalls, elapsed, report, success: toolCalls > 0 };
}

const task = process.argv.slice(2).join(' ');
if (!task) { console.log('Usage: node agent-v31.js "task"'); process.exit(1); }
run(task).then(r => { if (!r.success) process.exit(1); }).catch(e => { console.error('FATAL:', e); process.exit(1); });
