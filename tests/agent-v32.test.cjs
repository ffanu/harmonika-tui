"use strict";
const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os");
const {
  Agent,
  execute,
  metrics,
  forbidden,
  clean,
} = require("../tools/agent-v32.js");
const { StateStore, safePath } = require("../tools/agent-state.cjs");
const { model, Delegation, request } = require("../tools/agent-http.cjs");
function fixture(t, options = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "v32-unit-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const cwd = path.join(dir, "work");
  fs.mkdirSync(cwd);
  const events = [];
  return {
    cwd,
    root: dir,
    events,
    agent: new Agent({
      cwd,
      stateDir: path.join(dir, "state"),
      emit: (e) => events.push(e),
      ...options,
    }),
  };
}
const call = (name, args) => ({
  message: {
    tool_calls: [
      {
        id: Math.random().toString(),
        type: "function",
        function: { name, arguments: JSON.stringify(args) },
      },
    ],
  },
});
const answer = (content) => ({ message: { content }, finish_reason: "stop" });
function sequence(values) {
  return async () => {
    if (!values.length) throw Error("fixture_exhausted");
    return values.shift();
  };
}
const pass = {
  status: "completed",
  exit_code: 0,
  output: "TAP version 13\n1..1\n# tests 1\n# pass 1\n# fail 0\n# skipped 0\n",
};
test("failed executor plus confident prose never becomes success or prints false report", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("bash", { command: "false" }),
      answer("Semua sudah sukses."),
      answer("Semua sudah sukses."),
      answer("Semua sudah sukses."),
    ]),
    execute: async () => ({
      status: "failed",
      exit_code: 7,
      output: "fixture failure",
    }),
  });
  const r = await f.agent.run("Fix this fixture");
  assert.equal(r.success, false);
  assert.notEqual(r.status, "completed");
  assert.ok(!f.events.some((e) => e.text?.includes("Semua sudah sukses")));
  assert.equal(f.agent.state.steps[0].exit_code, 7);
});
test("real write and original tests verify completion on current revision", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("write", {
        path: "sum.cjs",
        content: "exports.total=x=>x.reduce((a,b)=>a+b,0);",
      }),
      call("verify", {}),
      answer("Fixture fixed and tested."),
    ]),
  });
  fs.writeFileSync(
    path.join(f.cwd, "package.json"),
    JSON.stringify({ scripts: { test: "missing-fixture-runner" } }),
  );
  fs.writeFileSync(path.join(f.cwd, "sum.cjs"), "exports.total=()=>0;");
  fs.writeFileSync(
    path.join(f.cwd, "test.cjs"),
    "const t=require('node:test'),a=require('node:assert/strict');t('sum',()=>a.equal(require('./sum.cjs').total([2,3]),5));",
  );
  const r = await f.agent.run("Fix the sum fixture");
  assert.equal(r.success, true);
  assert.equal(r.evidence.count, 1);
  assert.equal(r.evidence.kind, "tests");
});
test("a later write invalidates a passing verification", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("verify", {}),
      call("write", { path: "app.cjs", content: "broken" }),
      answer("Done"),
    ]),
    execute: async () => pass,
  });
  const r = await f.agent.run("Fix fixture", { maxTurns: 3 });
  assert.equal(r.success, false);
  assert.equal(r.status, "step_limit");
});
test("zero, skipped or truncated tests are not positive verification", async (t) => {
  for (const result of [
    { ...pass, output: "# pass 0\n# fail 0\n" },
    { ...pass, output: "# pass 1\n# fail 0\n# skipped 1\n" },
    { ...pass, truncated: true },
  ]) {
    const f = fixture(t, {
      model: sequence([call("verify", {}), answer("Done")]),
      execute: async () => result,
    });
    const r = await f.agent.run("Test fixture", { maxTurns: 2 });
    assert.equal(r.success, false);
    assert.equal(f.agent.state.verification, null);
  }
});
test("original assertions cannot be changed to obtain green tests", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("write", { path: "test.cjs", content: "// removed assertions" }),
      call("verify", {}),
    ]),
    execute: async () => pass,
  });
  fs.writeFileSync(path.join(f.cwd, "test.cjs"), "throw Error('original')");
  const r = await f.agent.run("Fix fixture", { maxTurns: 2 });
  assert.equal(r.success, false);
  assert.equal(f.agent.state.steps.at(-1).error, "original_assertions_changed");
});
test("successful tool followed by step limit is not completion", async (t) => {
  const f = fixture(t, {
    model: sequence([call("bash", { command: "printf ok" })]),
    execute: async () => ({ status: "completed", exit_code: 0, output: "ok" }),
  });
  const r = await f.agent.run("Fix fixture", { maxTurns: 1 });
  assert.equal(r.success, false);
  assert.equal(r.status, "step_limit");
});
test("invalid arguments after green tests invalidate completion evidence", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("verify", {}),
      call("write", { path: "x" }),
      answer("Done"),
      answer("Done"),
      answer("Done"),
    ]),
    execute: async () => ({ ...pass }),
  });
  const r = await f.agent.run("Original task");
  assert.equal(r.success, false);
  assert.equal(r.verification, null);
  assert.ok(
    f.agent.state.steps.some(
      (s) => s.tool === "invalid_tool" && s.status === "failed",
    ),
  );
});
test("failed read cannot reuse earlier verification even in the same clock tick", async (t) => {
  const f = fixture(t, {
    model: sequence([
      call("verify", {}),
      call("read", { path: "absent.cjs" }),
      answer("Done"),
      answer("Done"),
      answer("Done"),
    ]),
    execute: async () => ({ ...pass }),
  });
  const realNow = Date.now;
  Date.now = () => 123456;
  try {
    const r = await f.agent.run("Original task");
    assert.equal(r.success, false);
    assert.equal(r.verification, null);
  } finally {
    Date.now = realNow;
  }
});
test("preflight does not execute anything and forbidden cleanup/signals stay blocked", async (t) => {
  let execs = 0;
  const f = fixture(t, {
    model: sequence([call("bash", { command: "docker system prune -af" })]),
    execute: async () => {
      execs++;
      return pass;
    },
  });
  await f.agent.run("Read disk status", { maxTurns: 1 });
  assert.equal(execs, 0);
  for (const c of [
    "ssh root@example true",
    "pkill codex",
    "kill -9 123",
    "docker volume prune -f",
  ])
    assert.equal(forbidden(c), true);
});
test("checkpoint and memory contain mechanics, not task/commands/outputs", async (t) => {
  const sentinel = "SYNTHETIC_CUSTOMER_PRIVATE_77";
  const f = fixture(t, {
    model: sequence([call("bash", { command: "printf " + sentinel })]),
    execute: async () => ({ ...pass, output: sentinel }),
  });
  const r = await f.agent.run("Investigate " + sentinel, { maxTurns: 1 });
  const raw = fs.readFileSync(f.agent.store.file(r.session_id), "utf8");
  assert.ok(!raw.includes(sentinel));
  assert.ok(!raw.includes("printf"));
  if (process.platform === 'win32') {
    const platform = require('../tools/agent-platform.cjs');
    const code = 'import importlib.util,json,sys;s=importlib.util.spec_from_file_location("p",sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(json.dumps([m.acl_is_private(m.windows_security(p)) for p in sys.argv[2:]]))';
    const checked = require('node:child_process').spawnSync(platform.pythonCommand(), ['-I','-c',code,
      path.resolve(__dirname,'../runtime/tools/private_files.py'),f.agent.store.root,f.agent.store.file(r.session_id)], {encoding:'utf8',timeout:10000});
    assert.equal(checked.status,0,checked.stderr);
    assert.deepEqual(JSON.parse(checked.stdout),[true,true]);
  } else {
    assert.equal(fs.statSync(f.agent.store.file(r.session_id)).mode & 0o777,0o600);
    assert.equal(fs.statSync(f.agent.store.root).mode & 0o777,0o700);
  }
});
test("checkpoint authentication rejects tampering", async (t) => {
  const f = fixture(t, { model: sequence([answer("not executed")]) });
  const r = await f.agent.run("Fix fixture", { maxTurns: 1 });
  const file = f.agent.store.file(r.session_id),
    v = JSON.parse(fs.readFileSync(file));
  v.payload = v.payload.replace("step_limit", "completed");
  fs.writeFileSync(file, JSON.stringify(v));
  assert.throws(() => f.agent.store.load(r.session_id), /checkpoint_integrity/);
});
test("resume rejects different task or workspace", async (t) => {
  const f = fixture(t, { model: sequence([answer("No verification")]) });
  const r = await f.agent.run("First task", { maxTurns: 1 });
  await assert.rejects(
    f.agent.run("Different task", { resume: r.session_id }),
    /resume_scope_mismatch/,
  );
});
test("restart does not replay successful mutation and verification must be refreshed", async (t) => {
  let writes = 0;
  const f = fixture(t, {
    model: sequence([call("bash", { command: "printf synthetic" })]),
    execute: async () => {
      writes++;
      return { ...pass, output: "ok" };
    },
  });
  const first = await f.agent.run("Original task", { maxTurns: 1 });
  const resumed = new Agent({
    ...f.agent.options,
    store: new StateStore(f.agent.store.root),
    model: sequence([
      call("bash", { command: "printf synthetic" }),
      call("verify", {}),
      answer("Verified after reconnect"),
    ]),
    execute: async () => {
      writes++;
      return pass;
    },
  });
  const r = await resumed.run("Original task", { resume: first.session_id });
  assert.equal(r.success, true);
  assert.equal(writes, 2);
});
test("durable pre-execution intent becomes unknown after interrupted client", async (t) => {
  const f = fixture(t, { model: sequence([answer("Not verified")]) });
  const r = await f.agent.run("Original task", { maxTurns: 1 });
  const state = f.agent.store.load(r.session_id);
  state.steps.push({
    id: "intent-1",
    signature: "test",
    tool: "bash",
    status: "intent",
    at: Date.now(),
  });
  f.agent.store.save(state);
  let execs = 0;
  const resumed = new Agent({
    ...f.agent.options,
    store: new StateStore(f.agent.store.root),
    model: sequence([
      call("bash", { command: "printf mutate" }),
      answer("Done"),
    ]),
    execute: async () => {
      execs++;
      return pass;
    },
  });
  const out = await resumed.run("Original task", {
    resume: r.session_id,
    maxTurns: 2,
  });
  assert.equal(execs, 0);
  assert.equal(out.success, false);
  assert.equal(out.pending[0].status, "outcome_unknown");
});
test("broken and ordinary symlink writes are denied", (t) => {
  const f = fixture(t);
  fs.symlinkSync(
    path.join(f.root, "outside-missing"),
    path.join(f.cwd, "link"),
  );
  assert.throws(() => safePath(f.cwd, "link"), /symlink/);
  assert.throws(() => safePath(f.cwd, "../escape"), /scope/);
  assert.throws(() => safePath(f.cwd, ".ssh/id_rsa"), /scope/);
});
test("artifact mode checks actual readback instead of counting writes", async (t) => {
  const f = fixture(t, {
    verification: "artifact",
    model: sequence([
      call("write", { path: "out.txt", content: "expected" }),
      call("bash", { command: "change" }),
      call("verify", {}),
    ]),
    execute: async () => {
      fs.writeFileSync(path.join(f.cwd, "out.txt"), "wrong");
      return pass;
    },
  });
  const r = await f.agent.run("Write file", { maxTurns: 3 });
  assert.equal(r.success, false);
  assert.equal(f.agent.state.steps.at(-1).error, "artifact_readback_mismatch");
});
test("gateway access/schema errors are terminal; transient retry preserves receipts", async () => {
  let calls = 0,
    waits = 0;
  const messages = [{ role: "tool", content: "failed receipt unchanged" }],
    saved = JSON.stringify(messages);
  await assert.rejects(
    model(messages, [], {
      url: "https://fixture",
      request: async () => {
        calls++;
        return { status: 401, value: {} };
      },
      wait: async () => {
        waits++;
      },
    }),
    /access_denied/,
  );
  assert.equal(calls, 1);
  assert.equal(waits, 0);
  calls = 0;
  const out = await model(messages, [], {
    url: "https://fixture",
    request: async () => {
      calls++;
      return calls === 1
        ? { status: 503, value: {} }
        : { status: 200, value: { choices: [answer("ok")] } };
    },
    wait: async () => {
      waits++;
    },
  });
  assert.equal(out.message.content, "ok");
  assert.equal(calls, 2);
  assert.equal(JSON.stringify(messages), saved);
});
test("delegation lost ACK reconciles same request after restart, never resubmits", async (t) => {
  let submits = 0,
    statuses = 0,
    requestId;
  const fake = {
    submit: async (task, id) => {
      submits++;
      requestId = id;
      throw Error("gateway_timeout");
    },
    status: async (job, id) => {
      statuses++;
      assert.equal(job, null);
      assert.equal(id, requestId);
      return {
        job_id: "dg_" + "a".repeat(32),
        request_id: id,
        status: "completed",
        output: "synthetic",
      };
    },
  };
  const f = fixture(t, {
    delegation: fake,
    model: sequence([call("delegate", { task: "Count 2 plus 2" })]),
  });
  const first = await f.agent.run("Original task");
  assert.equal(first.status, "outcome_unknown");
  const resumed = new Agent({
    ...f.agent.options,
    store: new StateStore(f.agent.store.root),
    delegation: fake,
    model: async () => call("delegate_status", { request_id: requestId }),
  });
  const result = await resumed.run("Original task", {
    resume: first.session_id,
    maxTurns: 1,
  });
  assert.equal(submits, 1);
  assert.equal(statuses, 1);
  assert.equal(result.pending.length, 0);
  assert.equal(result.success, false);
});
test("busy delegation retries reuse one request ID and completed jobs never replay", async (t) => {
  const ids = [];
  const f = fixture(t, {
    delegation: {
      submit: async (_task, id) => {
        ids.push(id);
        if (ids.length === 1) throw Error("delegate_busy");
        return {
          status: "completed",
          job_id: "dg_" + "a".repeat(32),
          request_id: id,
          output: "4",
        };
      },
    },
    model: sequence([
      call("delegate", { task: "Count 2 plus 2" }),
      call("delegate", { task: "Count 2 plus 2" }),
      call("delegate", { task: "Count 2 plus 2" }),
      call("verify", {}),
      answer("Verified"),
    ]),
    execute: async () => ({ ...pass }),
  });
  const result = await f.agent.run("Original task");
  assert.equal(ids.length, 2);
  assert.equal(ids[0], ids[1]);
  assert.equal(result.success, true);
});
test("delegation status refuses another job or mismatched receipt", async () => {
  const d = new Delegation({
    skillsUrl: "https://fixture",
    request: async () => ({
      status: 200,
      value: { result: { job_id: "dg_wrong", status: "completed" } },
    }),
  });
  await assert.rejects(d.status("dg_expected"), /correlation/);
});
test("local execution deadline is unknown, with no process signals", async () => {
  const r = await execute(process.execPath + ' -e "setTimeout(()=>{},150)"', {
    cwd: os.tmpdir(),
    timeout: 10,
  });
  assert.equal(r.status, "outcome_unknown");
  assert.equal(r.exit_code, null);
  await new Promise((resolve) => setTimeout(resolve, 180));
});
test("metrics reject failed/skipped tests and accept actual framework counts", () => {
  assert.deepEqual(metrics("# pass 4\n# fail 1\n"), { count: 4, failed: true });
  assert.deepEqual(metrics("Ran 2 tests in 0.01s\n\nOK\n"), {
    count: 2,
    failed: false,
  });
  assert.equal(metrics("passed:true").count, 0);
});

test("quoted credentials and PEM are redacted while environment references survive", () => {
  const privateValue = "SYNTHETIC_VALUE_12345";
  assert.ok(
    !clean(JSON.stringify({ password: privateValue })).includes(privateValue),
  );
  assert.equal(
    clean("const apiKey = process.env.API_KEY;"),
    "const apiKey = process.env.API_KEY;",
  );
  assert.ok(
    !clean(
      "-----BEGIN PRIVATE KEY-----\n" +
        privateValue +
        "\n-----END PRIVATE KEY-----",
    ).includes(privateValue),
  );
});

test("SSH requires an explicit alias and a lost connection stays unknown", async (t) => {
  let commands = [];
  const f = fixture(t, {
    sshAlias: "operator-lab",
    model: sequence([call("ssh_exec", { command: "printf synthetic" })]),
    execute: async (c) => {
      commands.push(c);
      return { status: "failed", exit_code: 255, output: "connection lost" };
    },
  });
  const result = await f.agent.run("Authorized remote fixture", {
    maxTurns: 1,
  });
  assert.equal(result.status, "outcome_unknown");
  assert.equal(result.success, false);
  assert.ok(commands[0].includes("StrictHostKeyChecking=yes"));
  assert.ok(commands[0].includes(require("../tools/agent-platform.cjs").shellQuote("operator-lab")));
});

test("SSH service names cannot inject another command", async (t) => {
  let calls = 0;
  const f = fixture(t, {
    sshAlias: "operator-lab",
    model: sequence([
      call("check", { kind: "systemd", service: "nginx; touch unexpected" }),
    ]),
    execute: async () => {
      calls++;
      return pass;
    },
  });
  await f.agent.run("Inspect remote", { maxTurns: 1 });
  assert.equal(calls, 0);
});

test("mechanical memory is reused without persisting task or output", async (t) => {
  const f = fixture(t);
  f.agent.store.memory("verification_failed");
  f.agent.store.memory(null, 1);
  const reloaded = new StateStore(f.agent.store.root);
  assert.equal(reloaded.memorySummary().errors.verification_failed, 1);
  assert.equal(reloaded.memorySummary().completed, 1);
});

test("environment file changes invalidate prior verification without exposing its content", async (t) => {
  const f = fixture(t, {
    model: sequence([call("verify", {}), answer("Done")]),
    execute: async () => pass,
  });
  fs.writeFileSync(path.join(f.cwd, ".env"), "SYNTHETIC_ENV_ONE");
  const result = await f.agent.run("Check revision");
  assert.equal(result.success, true);
  fs.writeFileSync(path.join(f.cwd, ".env"), "SYNTHETIC_ENV_TWO");
  assert.equal(f.agent.complete("Done").success, false);
  assert.ok(
    !fs
      .readFileSync(f.agent.store.file(result.session_id), "utf8")
      .includes("SYNTHETIC_ENV"),
  );
});

test(
  "HTTP deadline includes stalled response body and never logs payload",
  { timeout: 1500 },
  async (t) => {
    const http = require("node:http");
    const server = http.createServer((req, res) => {
      req.resume();
      res.writeHead(200, { "Content-Type": "application/json" });
      res.write("{");
    });
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    t.after(() => {
      server.closeAllConnections();
      server.close();
    });
    await assert.rejects(
      request(
        "http://127.0.0.1:" + server.address().port,
        { synthetic: true },
        { key: "synthetic-test-only", allowLocal: true, timeout: 30 },
      ),
      /gateway_timeout|gateway_response_interrupted/,
    );
  },
);

test("all model retries share one monotonic deadline", async () => {
  let now = 0,
    calls = 0;
  await assert.rejects(
    model([], [], {
      url: "https://fixture",
      budgetMs: 2500,
      now: () => now,
      wait: async (ms) => {
        now += ms;
      },
      request: async (u, b, o) => {
        calls++;
        assert.ok(o.timeout <= 2500 - now);
        return { status: 503, value: {} };
      },
    }),
    /gateway_deadline/,
  );
  assert.equal(calls, 2);
});
