#!/usr/bin/env node
'use strict';
/**
 * Harmonika Agent Client v2 — dengan PLAN → BUILD → VERIFY cycle
 * - Phase 1: PLAN (agent buat rencana sebelum eksekusi)
 * - Phase 2: BUILD (eksekusi tool calls)
 * - Phase 3: VERIFY (verifikasi hasil)
 * - Resource monitoring (disk/RAM)
 */
const { execSync } = require('child_process');
const https = require('https');
const fs = require('fs');
const path = require('path');

const GATEWAY = 'https://chat.harmonika.id';
const API_KEY = (() => {
  const raw = fs.readFileSync(path.join(process.env.HOME, 'Projects/harmonika-engine/config/opencode.jsonc'), 'utf8');
  const m = raw.match(/"apiKey":\s*"([^"]+)"/);
  return m ? m[1] : '';
})();

const SSH_CMD = 'ssh -i ~/.ssh/harmonika_wg_target -o ConnectTimeout=10 -o BatchMode=yes root@38.47.176.62';

const TOOLS = [
  {
    type: 'function', function: {
      name: 'bash',
      description: 'Execute a bash command locally or via SSH. For remote: ' + SSH_CMD + ' "command"',
      parameters: { type: 'object', properties: { command: { type: 'string', description: 'Bash command to execute' }, timeout: { type: 'integer', description: 'Timeout in seconds' } }, required: ['command'] },
    },
  },
  {
    type: 'function', function: {
      name: 'read_file',
      description: 'Read a local file',
      parameters: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
    },
  },
  {
    type: 'function', function: {
      name: 'write_file',
      description: 'Write content to a local file',
      parameters: { type: 'object', properties: { path: { type: 'string' }, content: { type: 'string' } }, required: ['path', 'content'] },
    },
  },
];

function callModel(messages) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ model: 'harmonika-ai', messages, tools: TOOLS, stream: false });
    const url = new URL(GATEWAY + '/v1/chat/completions');
    const req = https.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${API_KEY}`, 'Content-Length': Buffer.byteLength(body) },
      timeout: 120000,
    }, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch (e) { reject(new Error(`parse: ${e.message} | ${data.slice(0, 200)}`)); } });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
    req.write(body); req.end();
  });
}

function execBash(command, timeoutSec = 90) {
  return new Promise((resolve) => {
    try {
      const result = execSync(command, { timeout: timeoutSec * 1000, encoding: 'utf8', maxBuffer: 2 * 1024 * 1024, cwd: '/tmp' });
      resolve({ output: (result || '').slice(0, 40000), error: null, code: 0 });
    } catch (err) {
      resolve({ output: (err.stdout || '').slice(0, 20000), error: (err.stderr || err.message || '').slice(0, 3000), code: err.status || -1 });
    }
  });
}

function checkResources() {
  return execSync(`${SSH_CMD} "df -h / | tail -1 | awk '{print \$5}' ; free -m | awk '/Mem:/ {print int(\$3/\$2*100)}' ; docker ps -q | wc -l"`, { encoding: 'utf8', timeout: 15000 }).trim().split('\n');
}

async function run(task, maxTurns = 25) {
  console.log('╔══════════════════════════════════════════════╗');
  console.log('  🤖 HARMONIKA AGENT v2 (PLAN → BUILD → VERIFY)');
  console.log('╚══════════════════════════════════════════════╝');
  console.log(`📋 Task: ${task.slice(0, 80)}...\n`);

  // Resource check
  try {
    const [disk, mem, containers] = checkResources();
    console.log(`📊 Resources: disk=${disk} RAM=${mem}% containers=${containers}`);
    if (parseInt(disk) > 85) {
      console.log('⚠️  Disk > 85%! Cleaning old Docker images...');
      await execBash(`${SSH_CMD} "docker system prune -af --volumes 2>/dev/null | tail -1"`);
      console.log('   Cleaned.');
    }
  } catch (e) { console.log('   (resource check skipped)'); }
  console.log('');

  // PHASE 1: PLAN
  console.log('━━━ 📝 PHASE 1: PLANNING ━━━');
  const planMessages = [
    { role: 'system', content: 'Anda adalah Harmonika AI agent. Tugas Anda adalah membuat PLAN terstruktur sebelum eksekusi. Buat plan dengan langkah-langkah bernomor, estimasi resource, dan potensi risiko. JANGAN eksekusi apapun di phase ini — hanya planning. Format:\n\nPLAN:\n1. [langkah]\n2. [langkah]\n...\nRISIKO: [potensi masalah]\nRESOURCE: [disk/RAM/port yang dibutuhkan]' },
    { role: 'user', content: task },
  ];

  try {
    const planResp = await callModel(planMessages);
    const planText = planResp.choices?.[0]?.message?.content || '';
    console.log(planText.slice(0, 500));
  } catch (e) {
    console.log(`   (planning skipped: ${e.message})`);
  }

  console.log('\n━━━ 🔨 PHASE 2: BUILDING ━━━');

  // PHASE 2: BUILD (with tools)
  const buildMessages = [
    { role: 'system', content: 'Anda adalah Harmonika AI agent executor. Kerjakan tugas dengan bash tool. Jalankan setiap langkah, verifikasi output, lanjut ke berikutnya. Jangan tanya izin — langsung eksekusi. Jika ada error, debug dan coba lagi. Jawab dalam bahasa Indonesia. Setelah selesai, verifikasi semua berfungsi dan laporkan hasil.' },
    { role: 'user', content: task },
  ];

  let toolCallCount = 0;
  let finalReport = '';

  for (let turn = 1; turn <= maxTurns; turn++) {
    let response;
    try {
      response = await callModel(buildMessages);
    } catch (e) {
      console.error(`   ❌ Gateway: ${e.message}`);
      await new Promise(r => setTimeout(r, 5000)); // wait before retry
      if (turn < 3) { turn--; continue; } // retry
      break;
    }

    const choice = response.choices?.[0];
    if (!choice) { console.error('   ❌ No response'); break; }
    const msg = choice.message;

    if (msg.tool_calls && msg.tool_calls.length > 0) {
      buildMessages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls });

      for (const tc of msg.tool_calls) {
        const name = tc.function.name;
        let args;
        try { args = JSON.parse(tc.function.arguments); } catch { args = {}; }

        toolCallCount++;
        const cmdPreview = (args.command || JSON.stringify(args)).slice(0, 120);
        console.log(`   🔧 [${toolCallCount}] ${name}: ${cmdPreview}`);

        let result;
        if (name === 'bash') {
          result = await execBash(args.command, args.timeout || 90);
        } else if (name === 'read_file') {
          try { result = { output: fs.readFileSync(path.join(process.env.HOME, args.path.replace('~/', '')), 'utf8').slice(0, 30000), code: 0 }; }
          catch (e) { result = { output: '', error: e.message, code: -1 }; }
        } else if (name === 'write_file') {
          try {
            const fp = args.path.startsWith('~') ? path.join(process.env.HOME, args.path.slice(2)) : args.path;
            fs.writeFileSync(fp, args.content);
            result = { output: 'written', code: 0 };
          } catch (e) { result = { output: '', error: e.message, code: -1 }; }
        } else {
          result = { output: '', error: `unknown tool: ${name}`, code: -1 };
        }

        const icon = result.code === 0 ? '✅' : '❌';
        const output = (result.output || result.error || '(empty)').slice(0, 150);
        console.log(`      ${icon} → ${output}`);
        buildMessages.push({ role: 'tool', tool_call_id: tc.id, content: JSON.stringify({ output: (result.output || result.error || '').slice(0, 20000), code: result.code }) });
      }
      continue;
    }

    // Text response = done or planning
    const text = msg.content || '';
    if (text.trim()) {
      finalReport = text;
      console.log(`   💬 ${text.slice(0, 200)}`);
    }
    if (choice.finish_reason === 'stop') { break; }
  }

  // PHASE 3: VERIFY
  console.log('\n━━━ ✅ PHASE 3: VERIFYING ━━━');
  try {
    const [disk, mem, containers] = checkResources();
    console.log(`   Disk: ${disk} | RAM: ${mem}% | Containers: ${containers}`);
  } catch { console.log('   (verify skipped)'); }
  if (finalReport) {
    console.log(`   Report: ${finalReport.slice(0, 300)}`);
  }
  console.log(`   Tool calls: ${toolCallCount}`);
  console.log('\n═══ AGENT SELESAI ═══\n');

  return { toolCallCount, report: finalReport };
}

// CLI
const task = process.argv.slice(2).join(' ');
if (!task) { console.log('Usage: node agent-v2.js "task"'); process.exit(1); }
run(task).catch(e => { console.error('FATAL:', e); process.exit(1); });
