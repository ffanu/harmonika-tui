"use strict";
// Checkpoints contain mechanics only. No prompts, commands, source, output or keys.
const fs = require("node:fs"),
  path = require("node:path"),
  crypto = require("node:crypto"),
  { processBirth: birth, protectStateDirectory, openRegular } = require("./agent-platform.cjs");
const sha = (x) => crypto.createHash("sha256").update(x).digest("hex");
function atomic(file, value) {
  const tmp = file + "." + crypto.randomUUID() + ".tmp";
  try {
    const fd = fs.openSync(tmp, "wx", 0o600);
    try {
      fs.writeFileSync(fd, JSON.stringify(value));
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    fs.renameSync(tmp, file);
    // Windows cannot open/fsync a directory. The file is flushed before the
    // atomic replace on every platform. POSIX additionally flushes the parent.
    if (process.platform !== "win32") {
      const d = fs.openSync(path.dirname(file), "r");
      try { fs.fsyncSync(d); } finally { fs.closeSync(d); }
    }
  } finally {
    try {
      fs.unlinkSync(tmp);
    } catch {}
  }
}
class StateStore {
  constructor(root) {
    this.root = path.resolve(root);
    fs.mkdirSync(this.root, { recursive: true, mode: 0o700 });
    if (fs.lstatSync(this.root).isSymbolicLink())
      throw Error("state_symlink_denied");
    const key = path.join(this.root, ".key");
    if (fs.existsSync(key) && (fs.lstatSync(key).isSymbolicLink() || !fs.lstatSync(key).isFile()))
      throw Error("state_symlink_denied");
    protectStateDirectory(this.root);
    try {
      fs.writeFileSync(key, crypto.randomBytes(32), {
        flag: "wx",
        mode: 0o600,
      });
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
    const fd = openRegular(key, fs.constants.O_RDONLY);
    try {
      if (process.platform !== "win32" && (fs.fstatSync(fd).mode & 0o077))
        throw Error("state_key_permissions");
      this.key = fs.readFileSync(fd);
      if (this.key.length !== 32) throw Error("state_key_invalid");
    } finally {
      fs.closeSync(fd);
    }
  }
  digest(value) {
    return crypto
      .createHmac("sha256", this.key)
      .update(
        typeof value === "string" || Buffer.isBuffer(value)
          ? value
          : JSON.stringify(value),
      )
      .digest("hex");
  }
  file(id) {
    if (!/^[a-f0-9-]{36}$/.test(id)) throw Error("invalid_session_id");
    return path.join(this.root, id + ".json");
  }
  lock(id) {
    const file = this.file(id) + ".lock",
      value = {
        pid: process.pid,
        birth: birth(process.pid),
        nonce: crypto.randomUUID(),
      };
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        fs.writeFileSync(file, JSON.stringify(value), {
          flag: "wx",
          mode: 0o600,
        });
        this.locked = { file, value };
        return;
      } catch (e) {
        if (e.code !== "EEXIST") throw e;
        const old = JSON.parse(fs.readFileSync(file, "utf8"));
        const observed = Number.isInteger(old.pid) ? birth(old.pid) : null;
        if (
          !Number.isInteger(old.pid) ||
          !old.birth ||
          observed === null ||
          observed === old.birth
        )
          throw Error("session_busy");
        const current = fs.readFileSync(file, "utf8");
        if (current !== JSON.stringify(old)) throw Error("session_busy");
        fs.unlinkSync(file);
      }
    }
    throw Error("session_busy");
  }
  unlock() {
    if (!this.locked) return;
    const { file, value } = this.locked;
    try {
      if (fs.readFileSync(file, "utf8") === JSON.stringify(value))
        fs.unlinkSync(file);
    } catch {}
    this.locked = null;
  }
  save(state) {
    const fields = [
      "id",
      "signature",
      "tool",
      "status",
      "at",
      "exit_code",
      "error",
      "artifact",
      "hash",
      "request_id",
      "job_id",
      "pid",
    ];
    const steps = state.steps
      .slice(-128)
      .map((s) =>
        Object.fromEntries(
          fields.filter((k) => s[k] !== undefined).map((k) => [k, s[k]]),
        ),
      );
    const safe = {
      version: 1,
      id: state.id,
      workspace: state.workspace,
      task: state.task,
      policy: state.policy,
      ...(state.turn ? { turn: state.turn } : {}),
      ...(state.pending_question
        ? { pending_question: state.pending_question }
        : {}),
      status: state.status,
      updated: Date.now(),
      steps,
      verification: state.verification || null,
      assertions: state.assertions || {},
    };
    const payload = JSON.stringify(safe);
    atomic(this.file(state.id), { payload, mac: this.digest(payload) });
  }
  load(id) {
    const fd = openRegular(this.file(id), fs.constants.O_RDONLY);
    let value;
    try {
      if (fs.fstatSync(fd).size > 2 * 1024 * 1024)
        throw Error("checkpoint_too_large");
      value = JSON.parse(fs.readFileSync(fd, "utf8"));
    } finally {
      fs.closeSync(fd);
    }
    if (
      typeof value.payload !== "string" ||
      typeof value.mac !== "string" ||
      value.mac !== this.digest(value.payload)
    )
      throw Error("checkpoint_integrity");
    const state = JSON.parse(value.payload);
    if (
      state.version !== 1 ||
      state.id !== id ||
      !Array.isArray(state.steps) ||
      state.steps.length > 128
    )
      throw Error("checkpoint_invalid");
    return state;
  }
  memorySummary() {
    let raw = {};
    try {
      raw = JSON.parse(
        fs.readFileSync(path.join(this.root, "mechanics.json"), "utf8"),
      );
    } catch {}
    const errors = Object.fromEntries(
      Object.entries(raw.errors || {})
        .filter(
          ([k, v]) =>
            /^[a-z_]{1,60}$/.test(k) &&
            !["__proto__", "constructor", "prototype"].includes(k) &&
            Number.isSafeInteger(v) &&
            v >= 0,
        )
        .slice(0, 32),
    );
    return {
      version: 1,
      completed:
        Number.isSafeInteger(raw.completed) && raw.completed >= 0
          ? raw.completed
          : 0,
      errors,
    };
  }
  memory(error, count = 0) {
    const file = path.join(this.root, "mechanics.json"),
      value = this.memorySummary();
    if (
      error &&
      /^[a-z_]{1,60}$/.test(error) &&
      !["__proto__", "constructor", "prototype"].includes(error)
    )
      value.errors[error] = (value.errors[error] || 0) + 1;
    if (count > 0) value.completed++;
    atomic(file, value);
  }
}
const sensitive = (p) =>
  p
    .split(/[\\/]/)
    .some(
      (n) =>
        /^\.(?:git|ssh|aws|config|gnupg)$/.test(n) ||
        /^\.env(?:$|\.)/.test(n) ||
        /^(?:credentials|id_rsa|id_ed25519|.*\.pem|.*\.key)$/i.test(n),
    );
function safePath(root, name) {
  if (
    typeof name !== "string" ||
    !name ||
    name.includes("\0") ||
    sensitive(name)
  )
    throw Error("file_scope_denied");
  const file = path.resolve(root, name);
  if (file !== root && !file.startsWith(root + path.sep))
    throw Error("file_scope_denied");
  let walk = root;
  for (const part of path
    .relative(root, file)
    .split(path.sep)
    .filter(Boolean)) {
    walk = path.join(walk, part);
    try {
      if (fs.lstatSync(walk).isSymbolicLink())
        throw Error("file_symlink_denied");
    } catch (e) {
      if (e.code !== "ENOENT") throw e;
    }
  }
  return file;
}
function snapshot(root, store) {
  const files = {},
    assertions = {};
  let bytes = 0,
    count = 0;
  function walk(dir) {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = path.join(dir, item.name),
        rel = path.relative(root, file);
      if (
        file === store.root ||
        [
          "node_modules",
          "__pycache__",
          ".runtime",
          ".git",
          ".ssh",
          ".aws",
          ".config",
          ".gnupg",
        ].includes(item.name)
      )
        continue;
      if (item.isSymbolicLink()) continue;
      if (item.isDirectory()) {
        walk(file);
        continue;
      }
      if (!item.isFile()) continue;
      const stat = fs.statSync(file);
      if (
        ++count > 8192 ||
        (bytes += stat.size) > 128 * 1024 * 1024 ||
        stat.size > 32 * 1024 * 1024
      )
        throw Error("workspace_snapshot_limit");
      const key = store.digest(rel),
        hash = store.digest(fs.readFileSync(file));
      files[key] = hash;
      if (
        /(?:^|[/\\])(?:test[^/\\]*|[^/\\]+[._]test\.[^/\\]+|[^/\\]+\.spec\.[^/\\]+)$/i.test(
          rel,
        )
      )
        assertions[key] = hash;
    }
  }
  walk(root);
  return {
    revision: sha(JSON.stringify(Object.entries(files).sort())),
    assertions,
    files,
  };
}
module.exports = { StateStore, atomic, sha, safePath, snapshot };
