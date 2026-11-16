#!/usr/bin/env node
'use strict';
/**
 * Harmonika Agent Client — properly mengirim tool definitions ke gateway,
 * menerima tool_calls, eksekusi lokal, loop sampai task selesai.
 */
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const https = require('https');
const path = require('path');

const GATEWAY = 'https://chat.harmonika.id';
const API_KEY = (() => {
  const raw = fs.readFileSync(path.join(process.env.HOME, 'Projects/harmonika-engine/config/opencode.jsonc'), 'utf8');
  const m = raw.match(/"apiKey":\s*"([^"]+)"/);
  return m ? m[1] : '';
})();

const TOOLS = [
  {
    type: 'function',
    function: {
      name: 'bash',
      description: 'Execute a bash command locally or via SSH. Returns stdout/stderr. For SSH: ssh -i KEY_FILE user@host "command"',
      parameters: {
        type: 'object',
        properties: {
          command: { type: 'string', description: 'Bash command to execute' },
          timeout: { type: 'integer', description: 'Timeout in seconds (default 60)' },
        },
        required: ['command'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'read_file',
      description: 'Read contents of a local file',
      parameters: {
        type: 'object',
        properties: { path: { type: 'string', description: 'File path to read' } },
        required: ['path'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'write_file',
      description: 'Write content to a local file',
      parameters: {
        type: 'object',
        properties: {
          path: { type: 'string', description: 'File path to write' },
          content: { type: 'string', description: 'Content to write' },
        },
        required: ['path', 'content'],
      },
    },
  },
];

function callModel(messages, maxRetries = 2) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      model: 'harmonika-ai',
      messages,
      tools: TOOLS,
      stream: false,
    });

    const url = new URL(GATEWAY + '/v1/chat/completions');
    const req = https.request(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${API_KEY}`,
        'Content-Length': Buffer.byteLength(body),
      },
      timeout: 120000,
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          reject(new Error(`parse error: ${e.message} | raw: ${data.slice(0, 200)}`));
        }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('gateway timeout')); });
    req.write(body);
    req.end();
  });
}

function execBash(command, timeoutSec = 60) {
  return new Promise((resolve) => {
    try {
      const result = execSync(command, {
        timeout: timeoutSec * 1000,
        encoding: 'utf8',
        maxBuffer: 1024 * 1024,
        cwd: '/tmp',
        env: { ...process.env, HOME: process.env.HOME },
      });
      resolve({ output: result.slice(0, 50000), error: null, code: 0 });
    } catch (err) {
      resolve({
        output: (err.stdout || '').slice(0, 25000),
        error: (err.stderr || err.message || '').slice(0, 5000),
        code: err.status || -1,
      });
    }
  });
}

function executeTool(name, args) {
  switch (name) {
    case 'bash':
      return execBash(args.command, args.timeout || 60);
    case 'read_file':
      try {
        const content = fs.readFileSync(path.expand(args.path), 'utf8');
        return { output: content.slice(0, 50000), error: null, code: 0 };
      } catch (e) {
        return { output: '', error: e.message, code: -1 };
      }
    case 'write_file':
      try {
        fs.writeFileSync(path.expand(args.path), args.content);
        return { output: 'written', error: null, code: 0 };
      } catch (e) {
        return { output: '', error: e.message, code: -1 };
      }
    default:
      return { output: '', error: `unknown tool: ${name}`, code: -1 };
  }
}

async function run(task, maxTurns = 20) {
  console.log('🤖 Harmonika Agent — starting task...');
  console.log(`📋 Task: ${task.slice(0, 100)}...`);
  console.log('');

  const messages = [
    { role: 'system', content: 'Anda adalah Harmonika AI agent. Gunakan tool bash untuk eksekusi perintah. Jalankan setiap langkah, verifikasi output, lanjut ke berikutnya. Jangan tanya izin — langsung kerjakan. Jawab dalam bahasa Indonesia.' },
    { role: 'user', content: task },
  ];

  for (let turn = 1; turn <= maxTurns; turn++) {
    console.log(`\n━━━ Turn ${turn}/${maxTurns} ━━━`);

    let response;
    try {
      response = await callModel(messages);
    } catch (e) {
      console.error(`❌ Gateway error: ${e.message}`);
      break;
    }

    const choice = response.choices && response.choices[0];
    if (!choice) { console.error('❌ No choice in response'); break; }

    const msg = choice.message;

    // Check for tool calls
    if (msg.tool_calls && msg.tool_calls.length > 0) {
      // Add assistant message with tool_calls
      messages.push({ role: 'assistant', content: msg.content || '', tool_calls: msg.tool_calls });

      for (const tc of msg.tool_calls) {
        const toolName = tc.function.name;
        let toolArgs;
        try { toolArgs = JSON.parse(tc.function.arguments); } catch { toolArgs = {}; }

        console.log(`🔧 TOOL: ${toolName}`);
        console.log(`   args: ${JSON.stringify(toolArgs).slice(0, 150)}`);

        const result = await executeTool(toolName, toolArgs);
        const output = result.output || result.error || '(empty)';
        console.log(`   ${result.code === 0 ? '✅' : '❌'} (${result.code}) → ${output.slice(0, 120)}`);

        // Add tool result to conversation
        messages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: JSON.stringify({ output: output.slice(0, 30000), code: result.code }),
        });
      }
      continue; // next turn
    }

    // Regular text response (no tool calls)
    const text = msg.content || '';
    if (text.trim()) {
      console.log(`💬 ${text.slice(0, 300)}`);
    }

    if (choice.finish_reason === 'stop' || !msg.tool_calls) {
      console.log('\n✅ Task selesai.');
      break;
    }
  }

  console.log('\n━━━ Agent selesai ━━━');
}

// CLI
const task = process.argv.slice(2).join(' ');
if (!task) {
  console.log('Usage: node agent-client.js "task description"');
  process.exit(1);
}

path.expand = (p) => p.startsWith('~/') ? path.join(process.env.HOME, p.slice(2)) : p;

run(task).catch(e => { console.error('FATAL:', e); process.exit(1); });
