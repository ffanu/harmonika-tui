"use strict";
const test = require("node:test"),
  assert = require("node:assert/strict"),
  cp = require("node:child_process"),
  path = require("node:path");
test("malformed MCP requests and arguments produce errors without crashing the server", async () => {
  const frames = [
    { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
    { jsonrpc: "2.0", id: 2, method: "tools/call" },
    {
      jsonrpc: "2.0",
      id: 3,
      method: "tools/call",
      params: { name: "tokenize", arguments: {} },
    },
    {
      jsonrpc: "2.0",
      id: 4,
      method: "tools/call",
      params: { name: "similarity", arguments: { query: "x", texts: [17] } },
    },
    { jsonrpc: "2.0", id: 5, method: "ping" },
  ];
  const p = cp.spawn(
    process.execPath,
    [path.resolve(__dirname, "../tools/mcp-harmonika-tools.js")],
    { stdio: ["pipe", "pipe", "pipe"] },
  );
  let out = "",
    err = "";
  p.stdout.on("data", (b) => (out += b));
  p.stderr.on("data", (b) => (err += b));
  p.stdin.on("error", () => {});
  p.stdin.end(frames.map((x) => JSON.stringify(x)).join("\n") + "\n");
  const code = await new Promise((resolve) => p.on("close", resolve));
  assert.equal(code, 0, err);
  const replies = out
    .trim()
    .split("\n")
    .map((x) => JSON.parse(x));
  assert.ok(replies.find((x) => x.id === 2)?.error);
  assert.equal(replies.find((x) => x.id === 3)?.result?.isError, true);
  assert.equal(replies.find((x) => x.id === 4)?.result?.isError, true);
  assert.deepEqual(replies.find((x) => x.id === 5)?.result, {});
});
