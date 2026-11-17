"use strict";
const test = require("node:test"), assert = require("node:assert/strict"),
  fs = require("node:fs"), path = require("node:path"), os = require("node:os"),
  cp = require("node:child_process"), crypto = require("node:crypto");
const platform = require("../tools/agent-platform.cjs");
const { StateStore, atomic } = require("../tools/agent-state.cjs");
const { Agent, defaultVerification, execute, key } = require("../tools/agent-v32.js");
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent portable é "));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("portable private checkpoints survive another real process and reject tampering", (t) => {
  const root = path.join(fixture(t), "state");
  const state = new StateStore(root), id = crypto.randomUUID();
  state.save({ id, status: "running", workspace: state.digest("synthetic-workspace"), task: state.digest("synthetic-task"), steps: [] });
  const child = `const {StateStore}=require(process.argv[1]);const store=new StateStore(process.argv[2]);const value=store.load(process.argv[3]);store.lock(value.id);value.status='verified';store.save(value);store.unlock();process.stdout.write(value.status);`;
  const run = cp.spawnSync(process.execPath, ["-e", child, path.resolve(__dirname, "../tools/agent-state.cjs"), root, id], { encoding: "utf8", timeout: 30000, env: process.env });
  assert.equal(run.status, 0, run.stderr); assert.equal(run.stdout, "verified");
  assert.equal(state.load(id).status, "verified");
  const file = state.file(id), value = JSON.parse(fs.readFileSync(file));
  value.payload = value.payload.replace('verified', 'unverified');
  fs.writeFileSync(file, JSON.stringify(value));
  assert.throws(() => state.load(id), /checkpoint_integrity/);
});

test("a live process owns its lock; another process cannot steal it", (t) => {
  const root = path.join(fixture(t), "state"), id = crypto.randomUUID();
  const state = new StateStore(root); state.lock(id); t.after(() => state.unlock());
  const child = `const {StateStore}=require(process.argv[1]);try {new StateStore(process.argv[2]).lock(process.argv[3]);process.exitCode=2;}catch(e){if(e.message!=='session_busy')throw e;process.stdout.write('busy');}`;
  const run = cp.spawnSync(process.execPath, ["-e", child, path.resolve(__dirname, "../tools/agent-state.cjs"), root, id], { encoding: "utf8", timeout: 30000, env: process.env });
  assert.equal(run.status, 0, run.stderr); assert.equal(run.stdout, "busy");
});

test("an exited owner's lock is reconciled by identity without process signals", (t) => {
  const root = path.join(fixture(t), "state"), id = crypto.randomUUID();
  const child = `const {StateStore}=require(process.argv[1]);new StateStore(process.argv[2]).lock(process.argv[3]);`;
  const run = cp.spawnSync(process.execPath, ["-e", child, path.resolve(__dirname, "../tools/agent-state.cjs"), root, id], { encoding: "utf8", timeout: 30000, env: process.env });
  assert.equal(run.status, 0, run.stderr);
  const store = new StateStore(root); store.lock(id); store.unlock();
  assert.equal(fs.existsSync(store.file(id) + ".lock"), false);
});

test("atomic state replaces an existing file on the actual host", (t) => {
  const dir = fixture(t), file = path.join(dir, 'mechanics.json');
  atomic(file, { completed: 1 }); atomic(file, { completed: 2 });
  assert.deepEqual(JSON.parse(fs.readFileSync(file)), { completed: 2 });
  if (process.platform !== 'win32') assert.equal(fs.statSync(file).mode & 0o777, 0o600);
});

test("automatic test discovery executes actual assertions in paths with spaces and apostrophes", async (t) => {
  const root = fixture(t), cwd = path.join(root, "work apostrophe's é"); fs.mkdirSync(cwd);
  fs.writeFileSync(path.join(cwd, "test calculator's.cjs"), "const t=require('node:test'),a=require('node:assert/strict');t('original assertion',()=>a.equal(2+3,5));");
  const command = defaultVerification(cwd);
  const result = await execute(command, { cwd, timeout: 30000 });
  assert.equal(result.status, "completed", result.output);
  assert.equal(result.exit_code, 0); assert.match(result.output, /# pass 1/);
  assert.match(command, /--test/);
});

test("installer-selected Python path and config root are respected", () => {
  assert.equal(platform.pythonCommand({ HARMONIKA_PYTHON: 'C:\\Python 3\\python.exe' }, 'win32'), 'C:\\Python 3\\python.exe');
  assert.equal(platform.pythonCommand({}, 'win32'), 'python');
  assert.equal(platform.configDirectory({ HARMONIKA_GLOBAL_CONFIG: path.join(os.tmpdir(), 'profile space') }), path.resolve(os.tmpdir(), 'profile space'));
});

test("Windows shell quoting treats spaces/apostrophes as data and refuses expansion", () => {
  assert.equal(platform.shellQuote("folder space/test's.cjs", 'win32'), '"folder space/test\'s.cjs"');
  for (const v of ['%USERPROFILE%', '!PATH!', 'bad"arg', 'line\nnext'])
    assert.throws(() => platform.shellQuote(v, 'win32'), /unsupported_windows_shell_argument/);
});

test("key loader uses private configured key and never falls back to a Mac project", (t) => {
  const dir = fixture(t), saved = { key: process.env.HARMONIKA_API_KEY, cfg: process.env.HARMONIKA_GLOBAL_CONFIG };
  t.after(() => { for (const [name, value] of [['HARMONIKA_API_KEY', saved.key], ['HARMONIKA_GLOBAL_CONFIG', saved.cfg]]) { if (value === undefined) delete process.env[name]; else process.env[name] = value; } });
  delete process.env.HARMONIKA_API_KEY; process.env.HARMONIKA_GLOBAL_CONFIG = dir;
  assert.throws(() => key(), /license_key_not_configured/);
  fs.writeFileSync(path.join(dir, 'harmonika-api-key'), 'hai_fixture_' + 'x'.repeat(32));
  assert.equal(key(), 'hai_fixture_' + 'x'.repeat(32));
});

test("macOS-only sandbox is explicit and fails closed on other platforms", (t) => {
  const root = fixture(t), create = () => new Agent({ cwd: root, stateDir: path.join(root, 'state'), sandbox: true });
  if (process.platform === 'darwin') assert.doesNotThrow(create);
  else assert.throws(create, /sandbox_platform_unsupported/);
});
