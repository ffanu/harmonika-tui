"use strict";
const test = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os"),
  cp = require("node:child_process");
const { Agent, parse } = require("../tools/agent-v32.js");
const gateway = require("../tools/agent-http.cjs");
async function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "harmonika-update-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const cwd = path.join(root, "work");
  fs.mkdirSync(cwd);
  const a = new Agent({
    cwd,
    stateDir: path.join(root, "state"),
    emit: () => {},
    model: async () => ({ message: { content: "" } }),
    ...options,
  });
  await a.run("Synthetic tool audit", { maxTurns: 1 });
  return { root, cwd, a };
}
test("surgical edit inserts dollar replacement syntax literally and verifies the artifact", async (t) => {
  const f = await fixture(t, { verification: "artifact" });
  fs.writeFileSync(path.join(f.cwd, "source.txt"), "old value");
  const r = await f.a.tool("edit_file", {
    path: "source.txt",
    old_string: "old",
    new_string: "$& and $' and $$",
  });
  assert.equal(r.status, "completed");
  assert.equal(
    fs.readFileSync(path.join(f.cwd, "source.txt"), "utf8"),
    "$& and $' and $$ value",
  );
  const verified = await f.a.tool("run_tests", {});
  assert.equal(verified.status, "completed");
  assert.equal(f.a.complete("Readback proved the literal edit.").success, true);
});
test("grep and glob respect the same secret-file boundary as read_file", async (t) => {
  const f = await fixture(t);
  fs.writeFileSync(
    path.join(f.cwd, ".env"),
    "PRIVATE_CUSTOMER=UNIQUE_PRIVATE_FIXTURE",
  );
  fs.writeFileSync(path.join(f.cwd, "private.key"), "UNIQUE_PRIVATE_FIXTURE");
  fs.writeFileSync(path.join(f.cwd, "public.txt"), "public marker");
  const r = await f.a.tool("grep", { pattern: "UNIQUE_PRIVATE_FIXTURE" });
  assert.equal(r.status, "completed");
  assert.ok(!r.output.includes("UNIQUE_PRIVATE_FIXTURE"));
  const files = await f.a.tool("glob", { pattern: "*" });
  assert.ok(!files.output.includes(".env"));
  assert.ok(!files.output.includes("private.key"));
});
test("grep accepts a single scoped file and reports bounded search", async (t) => {
  const f = await fixture(t);
  fs.writeFileSync(path.join(f.cwd, "source.txt"), "first\nneedle\nneedle\n");
  const r = await f.a.tool("grep", {
    path: "source.txt",
    pattern: "needle",
    max_results: 1,
  });
  assert.match(r.output, /source.txt:2:/);
  assert.equal(r.truncated, true);
});
test("chunked read does not claim the stopping line is the total file length", async (t) => {
  const f = await fixture(t);
  fs.writeFileSync(
    path.join(f.cwd, "source.txt"),
    "one\ntwo\nthree\nfour\nfive\nsix\n",
  );
  const r = await f.a.tool("read_file", {
    path: "source.txt",
    offset: 2,
    limit: 2,
  });
  assert.equal(r.status, "completed");
  assert.match(r.output, /two\nthree/);
  assert.ok(!r.output.includes("of 4"));
  assert.equal(r.next_offset, 4);
  const bad = await f.a.tool("read_file", {
    path: "source.txt",
    offset: 1.5,
    limit: 2,
  });
  assert.equal(bad.status, "failed");
});
test("git diff treats shell substitutions in a filename as literal data", async (t) => {
  const f = await fixture(t);
  const name = "$(touch AUDIT_INJECTED).txt";
  cp.execFileSync("git", ["init", "-q"], { cwd: f.cwd });
  fs.writeFileSync(path.join(f.cwd, name), "before\n");
  cp.execFileSync("git", ["add", "--", name], { cwd: f.cwd });
  fs.appendFileSync(path.join(f.cwd, name), "after\n");
  const r = await f.a.tool("git_diff", { path: name });
  assert.equal(r.status, "completed");
  assert.equal(fs.existsSync(path.join(f.cwd, "AUDIT_INJECTED")), false);
  assert.match(r.output, /after/);
});
test("inspection mode rejects commit, batch execution, and image/video generation before effects", async (t) => {
  let effects = 0;
  const f = await fixture(t, {
    verification: "inspection",
    execute: async () => {
      effects++;
      return { status: "completed", exit_code: 0, output: "" };
    },
  });
  const saved = gateway.toolsPost;
  gateway.toolsPost = async () => {
    effects++;
    return { ok: true, job: "a".repeat(16) };
  };
  t.after(() => (gateway.toolsPost = saved));
  for (const [name, args] of [
    ["git_commit", { message: "synthetic" }],
    ["batch_run", { commands: ["printf x"] }],
    ["image_generate", { prompt: "synthetic" }],
    ["video_generate", { prompt: "synthetic" }],
    ["kb_store", { text: "synthetic" }],
  ]) {
    await assert.rejects(f.a.tool(name, args), /inspection_mutation_denied/);
  }
  assert.equal(effects, 0);
});
test("a batch with one failed and one uncertain executor retains the uncertain outcome", async (t) => {
  const f = await fixture(t, {
    execute: async (c) =>
      c === "first"
        ? { status: "failed", exit_code: 7, output: "" }
        : {
            status: "outcome_unknown",
            exit_code: null,
            output: "",
            pid: 987654,
          },
  });
  const r = await f.a.tool("batch_run", { commands: ["first", "second"] });
  assert.equal(r.status, "outcome_unknown");
  assert.ok(f.a.view().pending.length > 0);
});
test("completed command batches are not replayed", async (t) => {
  let effects = 0;
  const f = await fixture(t, {
    execute: async () => {
      effects++;
      return { status: "completed", exit_code: 0, output: "ok" };
    },
  });
  await f.a.tool("batch_run", { commands: ["printf x"] });
  await f.a.tool("batch_run", { commands: ["printf x"] });
  assert.equal(effects, 1);
});
test("new interactive instructions invalidate proof and cannot reuse completed effects from earlier turns", async (t) => {
  const f = await fixture(t, {
    execute: async () => ({
      status: "completed",
      exit_code: 0,
      output: "TAP version 13\n# pass 1\n# fail 0\n",
    }),
  });
  await f.a.tool("run_tests", {});
  f.a._messages = [];
  f.a.model = async () => ({ message: { content: "Done" } });
  const r = await f.a.run("A different follow-up task", {
    followUp: true,
    maxTurns: 1,
  });
  assert.equal(r.success, false);
  assert.equal(r.verification, null);
});
test("model selection and sandbox flags are bound to resumed policy", async (t) => {
  const f = await fixture(t, { modelName: "harmonika-ai" });
  const other = new Agent({ ...f.a.options, modelName: "different-model" });
  await assert.rejects(
    other.run("Synthetic tool audit", { resume: f.a.state.id, maxTurns: 1 }),
    /resume_scope_mismatch/,
  );
});
test("--model selects a gateway model without replacing the model callback", async (t) => {
  const options = parse([
    "--model",
    "harmonika-fast",
    "Synthetic task",
  ]).options;
  assert.equal(options.modelName, "harmonika-fast");
  const f = await fixture(t, options);
  assert.equal(typeof f.a.model, "function");
});
test("video timeout persists its original job and reconciles without a second submit", async (t) => {
  const post = gateway.toolsPost,
    get = gateway.toolsGet;
  let submits = 0;
  t.after(() => {
    gateway.toolsPost = post;
    gateway.toolsGet = get;
  });
  gateway.toolsPost = async () => {
    submits++;
    return { job: "a".repeat(16) };
  };
  gateway.toolsGet = async () => ({ state: "running" });
  const f = await fixture(t, { videoTimeoutMs: 0, videoPollMs: 0 });
  const first = await f.a.tool("video_generate", {
    prompt: "Synthetic motion",
  });
  assert.equal(first.status, "outcome_unknown");
  assert.equal(f.a.view().pending[0].job_id, "a".repeat(16));
  await f.a.tool("video_generate", { prompt: "Synthetic motion" });
  assert.equal(submits, 1);
  gateway.toolsGet = async () => ({ state: "done", size: 100 });
  const final = await f.a.tool("video_status", { job_id: "a".repeat(16) });
  assert.equal(final.status, "completed");
  assert.equal(f.a.view().pending.length, 0);
  assert.equal(submits, 1);
});
test("a lost video submit acknowledgement remains unknown and cannot be replayed", async (t) => {
  const post = gateway.toolsPost;
  let calls = 0;
  t.after(() => (gateway.toolsPost = post));
  gateway.toolsPost = async () => {
    calls++;
    throw Error("gateway_timeout");
  };
  const f = await fixture(t);
  const r = await f.a.tool("video_generate", { prompt: "Synthetic motion" });
  assert.equal(r.status, "outcome_unknown");
  await f.a.tool("video_generate", { prompt: "Synthetic motion" });
  assert.equal(calls, 1);
});
test("malformed and oversized mutation replies never authorize a second submit", async (t) => {
  const post = gateway.toolsPost;
  t.after(() => (gateway.toolsPost = post));
  for (const code of ["gateway_invalid_json", "gateway_output_limit", "gateway_deadline", "gateway_outcome_unknown"]) {
    let submissions = 0;
    gateway.toolsPost = async () => { submissions++; throw Error(code); };
    const f = await fixture(t);
    const first = await f.a.tool("kb_store", { text: "synthetic " + code });
    assert.equal(first.status, "outcome_unknown");
    await f.a.tool("kb_store", { text: "synthetic " + code });
    assert.equal(submissions, 1);
  }
});
test("a knowledge write requires a valid acknowledgement and malformed replies remain unknown", async (t) => {
  const post = gateway.toolsPost;
  t.after(() => (gateway.toolsPost = post));
  let submissions = 0;
  gateway.toolsPost = async () => { submissions++; return {}; };
  const f = await fixture(t);
  const first = await f.a.tool("kb_store", { text: "synthetic" });
  assert.equal(first.status, "outcome_unknown");
  assert.equal(first.error, "kb_invalid_receipt");
  await f.a.tool("kb_store", { text: "synthetic" });
  assert.equal(submissions, 1);
  const valid = await fixture(t);
  gateway.toolsPost = async () => ({ id: 1, stored: true });
  assert.equal((await valid.a.tool("kb_store", { text: "synthetic" })).status, "completed");
});
test("image generation rejects an existing symlink before submitting GPU work", async (t) => {
  const f = await fixture(t);
  fs.writeFileSync(path.join(f.root, "outside"), "keep");
  fs.symlinkSync(
    path.join(f.root, "outside"),
    path.join(f.cwd, "synthetic.png"),
  );
  const post = gateway.toolsPost;
  let calls = 0;
  t.after(() => (gateway.toolsPost = post));
  gateway.toolsPost = async () => {
    calls++;
    return { ok: true, png_b64: "bad" };
  };
  const r = await f.a.tool("image_generate", {
    prompt: "synthetic",
    filename: "synthetic",
  });
  assert.equal(r.status, "failed");
  assert.equal(calls, 0);
  assert.equal(fs.readFileSync(path.join(f.root, "outside"), "utf8"), "keep");
});
test("vision uses the authenticated backend route and rejects empty analysis", async (t) => {
  const f = await fixture(t);
  fs.writeFileSync(path.join(f.cwd, "sample.png"), "synthetic input");
  const post = gateway.toolsPost;
  let url;
  t.after(() => (gateway.toolsPost = post));
  gateway.toolsPost = async (u) => {
    url = u;
    return { response: "Visible synthetic text" };
  };
  const r = await f.a.tool("vision", { path: "sample.png" });
  assert.equal(r.status, "completed");
  assert.equal(url, "https://chat.harmonika.id/api/tools/vlm/generate");
  assert.match(r.output, /Visible synthetic text/);
  gateway.toolsPost = async () => ({});
  assert.equal(
    (await f.a.tool("vision", { path: "sample.png" })).status,
    "failed",
  );
});
test("sandbox command quoting does not expand a literal shell substitution before entry", async (t) => {
  const f = await fixture(t, { sandbox: true });
  const bin = path.join(f.root, "bin");
  fs.mkdirSync(bin);
  const stub = path.join(bin, "sandbox-exec");
  fs.writeFileSync(stub, '#!/bin/sh\nshift 2\nexec "$@"\n');
  fs.chmodSync(stub, 0o700);
  const previous = process.env.PATH;
  process.env.PATH = bin + path.delimiter + previous;
  t.after(() => (process.env.PATH = previous));
  const r = await f.a.tool("bash", {
    command: "printf '%s' '$(touch SANDBOX_ESCAPED)'",
  });
  assert.equal(r.status, "completed");
  assert.equal(fs.existsSync(path.join(f.cwd, "SANDBOX_ESCAPED")), false);
  assert.equal(r.output, "$(touch SANDBOX_ESCAPED)");
});
test("tool HTTP deadlines include an incomplete response body", async (t) => {
  const http = require("node:http");
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write("{");
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  const url = "http://127.0.0.1:" + server.address().port;
  await assert.rejects(
    gateway.toolsGet(url, { allowLocal: true, timeout: 35 }),
    /gateway_timeout/,
  );
});
test("HTTP JSON preserves a multibyte character split across chunks", async (t) => {
  const http = require("node:http");
  const body = Buffer.from(JSON.stringify({ text: "صورة 🌊" }));
  const cut = body.indexOf(Buffer.from("🌊")) + 2;
  const server = http.createServer((req, res) => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.write(body.subarray(0, cut));
    setTimeout(() => res.end(body.subarray(cut)), 15);
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => server.close());
  const result = await gateway.toolsGet("http://127.0.0.1:" + server.address().port, { allowLocal: true });
  assert.equal(result.text, "صورة 🌊");
});
test("HTTP mutation failures distinguish an unknown server outcome from a rejected request", async (t) => {
  const http = require("node:http");
  const server = http.createServer((req, res) => {
    res.writeHead(req.url === "/server" ? 500 : 400, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "synthetic backend detail" }));
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  t.after(() => server.close());
  const base = "http://127.0.0.1:" + server.address().port;
  await assert.rejects(gateway.toolsPost(base + "/server", {}, { allowLocal: true }), /gateway_outcome_unknown/);
  await assert.rejects(gateway.toolsPost(base + "/rejected", {}, { allowLocal: true }), /gateway_rejected/);
});
