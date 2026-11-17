"use strict";
const fs = require("node:fs"),
  path = require("node:path"),
  os = require("node:os"),
  cp = require("node:child_process");

function configDirectory(env = process.env, home = os.homedir()) {
  return path.resolve(env.HARMONIKA_GLOBAL_CONFIG || env.HARMONIKA_CONFIG_DIR || path.join(home, ".config", "harmonika"));
}
function pythonCommand(env = process.env, platform = process.platform) {
  return env.HARMONIKA_PYTHON || env.HARMONIKA_PYTHON_PATH || (platform === "win32" ? "python" : "python3");
}
function windowsHelper(args, options = {}) {
  return JSON.parse(cp.execFileSync(pythonCommand(), ["-I", path.join(__dirname, "agent-platform.py"), ...args], {
    encoding: "utf8", timeout: 10000, windowsHide: true,
    stdio: ["ignore", "pipe", "ignore"], ...options,
  }));
}
function processBirth(pid) {
  if (!Number.isSafeInteger(pid) || pid <= 0) return null;
  if (process.platform === "win32") {
    try { return windowsHelper(["--birth", String(pid)]).birth ?? null; } catch { return null; }
  }
  if (process.platform === "linux") {
    try {
      const text = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      const tail = text.slice(text.lastIndexOf(") ") + 2).split(" ");
      return "linux:" + fs.readFileSync("/proc/sys/kernel/random/boot_id", "utf8").trim() + ":" + tail[19];
    } catch (error) { return error.code === "ENOENT" ? "" : null; }
  }
  try {
    return cp.execFileSync("/bin/ps", ["-p", String(pid), "-o", "lstart="], {
      encoding: "utf8", timeout: 10000,
      env: { ...process.env, TZ: "UTC", LC_ALL: "C" },
      stdio: ["ignore", "pipe", "pipe"],
    }).trim();
  } catch (error) {
    return error.status === 1 && !String(error.stderr || "").trim() ? "" : null;
  }
}
function protectStateDirectory(directory) {
  if (process.platform !== "win32") return fs.chmodSync(directory, 0o700);
  try {
    if (windowsHelper(["--private-state", directory]).ok !== true) throw Error();
  } catch { throw Error("state_privacy_unavailable"); }
}
function shellQuote(value, platform = process.platform) {
  value = String(value);
  if (value.includes("\0")) throw Error("invalid_shell_argument");
  if (platform === "win32") {
    // cmd.exe expands these even in double quotes; refuse rather than
    // interpreting a path or commit message as environment/shell syntax.
    if (/["%!\r\n]/.test(value)) throw Error("unsupported_windows_shell_argument");
    return '"' + value + '"';
  }
  return "'" + value.replace(/'/g, "'\"'\"'") + "'";
}
function openRegular(file, flags) {
  const before = fs.lstatSync(file);
  if (before.isSymbolicLink() || !before.isFile()) throw Error("state_symlink_denied");
  const fd = fs.openSync(file, flags | (fs.constants.O_NOFOLLOW || 0));
  const after = fs.fstatSync(fd);
  if (!after.isFile() || before.dev !== after.dev || before.ino !== after.ino) {
    fs.closeSync(fd); throw Error("state_symlink_denied");
  }
  return fd;
}
module.exports = { configDirectory, pythonCommand, processBirth, protectStateDirectory, shellQuote, openRegular };
