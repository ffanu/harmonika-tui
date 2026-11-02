'use strict';
// Guard-rail tests for runtime/tools/qa_matrix.cjs. No browser is launched: every case
// fails during argument/target/output validation, before Playwright is loaded.
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const SCRIPT = path.join(__dirname, '..', 'runtime', 'tools', 'qa_matrix.cjs');

function run(args) {
  const result = spawnSync(process.execPath, [SCRIPT, ...args], { encoding: 'utf8' });
  return { code: result.status, stderr: result.stderr || '', stdout: result.stdout || '' };
}

test('requires both --url and --out', () => {
  const { code, stderr } = run(['--url', 'http://example.com/']);
  assert.notStrictEqual(code, 0);
  assert.match(stderr, /(Usage: qa_matrix\.cjs|--url and --out are required)/);
});

test('rejects a non-absolute output directory', () => {
  const { code, stderr } = run(['--url', 'http://example.com/', '--out', 'relative/dir']);
  assert.notStrictEqual(code, 0);
  assert.match(stderr, /--out must be an absolute path/);
});

test('rejects dot-directories and the home directory as output', () => {
  const dot = run(['--url', 'http://example.com/', '--out', path.join(require('node:os').homedir(), '.hidden', 'qa')]);
  assert.notStrictEqual(dot.code, 0);
  assert.match(dot.stderr, /Protected dot-directory blocked/);
  const home = run(['--url', 'http://example.com/', '--out', require('node:os').homedir()]);
  assert.notStrictEqual(home.code, 0);
  assert.match(home.stderr, /dedicated output subdirectory/);
});

test('blocks private network targets', () => {
  const { code, stderr } = run(['--url', 'http://10.0.0.5/', '--out', path.join(require('node:os').homedir(), 'qa-matrix-guard-test')]);
  assert.notStrictEqual(code, 0);
  assert.match(stderr, /Private network address blocked/);
});

test('rejects non-http protocols and embedded credentials', () => {
  const ftp = run(['--url', 'ftp://example.com/file', '--out', '/tmp/qa-matrix-guard']);
  assert.notStrictEqual(ftp.code, 0);
  const creds = run(['--url', 'https://user:pass@example.com/', '--out', '/tmp/qa-matrix-guard']);
  assert.notStrictEqual(creds.code, 0);
  assert.match(creds.stderr, /without embedded credentials/);
});
