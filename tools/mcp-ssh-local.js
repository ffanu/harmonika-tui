#!/usr/bin/env node
'use strict';
// MCP SSH Server — tool lokal utk eksekusi SSH ke target terdaftar.
// Agent panggil via MCP: ssh_run(alias, command) → output
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const readline = require('readline');

const TARGETS_FILE = path.join(process.env.HOME, '.config/harmonika/ssh-targets.json');

function loadTargets() {
  try {
    return JSON.parse(fs.readFileSync(TARGETS_FILE, 'utf8')).targets || {};
  } catch { return {}; }
}

function sshExec(alias, command) {
  return new Promise((resolve) => {
    const targets = loadTargets();
    const t = targets[alias];
    if (!t) { resolve(JSON.stringify({ error: `target "${alias}" not found`, available: Object.keys(targets) })); return; }
    const args = ['-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no'];
    if (t.identity_file) args.push('-i', t.identity_file);
    if (t.port) args.push('-p', String(t.port));
    args.push(`${t.username || 'root'}@${t.host}`, command);
    const proc = spawn('ssh', args, { timeout: 60000 });
    let stdout = '', stderr = '';
    proc.stdout.on('data', d => { stdout += d; if (stdout.length > 100000) stdout = stdout.slice(0, 100000); });
    proc.stderr.on('data', d => { stderr += d; });
    proc.on('close', (code) => {
      resolve(JSON.stringify({ code, output: stdout.slice(0, 50000), error: stderr.slice(0, 2000) || null }));
    });
    proc.on('error', (err) => resolve(JSON.stringify({ code: -1, output: '', error: err.message })));
  });
}

// MCP protocol (stdio)
const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
let buffer = '';
rl.on('line', (line) => {
  try {
    const msg = JSON.parse(line);
    if (msg.method === 'initialize') {
      respond(msg.id, { protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'harmonika-ssh-local', version: '1.0.0' } });
    } else if (msg.method === 'tools/list') {
      respond(msg.id, { tools: [
        { name: 'ssh_run', description: 'Execute SSH command on a registered target', inputSchema: { type: 'object', properties: { alias: { type: 'string', description: 'Target alias from ssh-targets.json' }, command: { type: 'string', description: 'Shell command to execute' } }, required: ['alias', 'command'] } },
        { name: 'ssh_targets', description: 'List registered SSH targets', inputSchema: { type: 'object', properties: {} } }
      ]});
    } else if (msg.method === 'tools/call') {
      const tool = msg.params.name;
      const args = msg.params.arguments || {};
      if (tool === 'ssh_run') {
        Promise.resolve(sshExec(args.alias, args.command)).then(result => {
          respond(msg.id, { content: [{ type: 'text', text: result }] });
        });
      } else if (tool === 'ssh_targets') {
        const targets = loadTargets();
        const list = Object.entries(targets).map(([k, v]) => `${k}: ${v.username}@${v.host}:${v.port || 22}`).join('\n');
        respond(msg.id, { content: [{ type: 'text', text: list || 'no targets' }] });
      }
    } else if (msg.id !== undefined && !msg.method) {
      respond(msg.id, {});
    }
  } catch (e) { /* ignore parse errors */ }
});

function respond(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n');
}
