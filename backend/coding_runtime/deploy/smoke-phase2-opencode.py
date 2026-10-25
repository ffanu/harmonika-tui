#!/usr/bin/env python3
"""Deterministic real-HTTP acceptance harness for all 20 Harmonika Phase2 skill IDs.

Scope
-----
Root-run acceptance only. The harness talks HTTP to a running private loopback
coding-runtime service (aioapp from backend/coding_runtime/service.py), exercises
every Phase2 skill ID with real operations, and asserts on the real job results.
It performs NO SSH, NO deploy, NO container/CLI work, and NO local filesystem
writes: it only reads the token file, prints one JSON report to stdout, and
exits nonzero when any assertion fails.

Run (on the Linux VPS stage, by root):
    python3 smoke_phase2.py [--base http://127.0.0.1:18188]
                            [--token-file /etc/harmonika-ai/coding-runtime.token]

Guarantees
----------
- Secret is read privately from the token file and is never logged, stored or
  included in the report. No other credentials exist in this file.
- Two actor principals are used (api-token:900000000000000201 / ...202) to stay
  far below the 30-executions/hour/token quota. "New requests" per actor in the
  report counts NEW job submissions (invokes); approve/poll/debug-REST calls
  operate on already-submitted jobs and are counted separately as HTTP calls.
- Workspaces are created by this run with permissions write=True, execute=True,
  git_commit=True, under unique per-run names; the harness never writes to any
  workspace other than its own, and never writes to arbitrary localhost paths.
- Approvals use the input_hash returned by the server for the exact submitted
  payload; completion is awaited through the real SSE job event stream (with a
  JSON-poll fallback) and the final job view is fetched for assertions.
- Debug acceptance uses one approved attach job, one breakpoint job, then DIRECT
  debug REST actions (continue -> inspect -> continue -> stop) and verifies the
  debuggee's source write is persisted back into the workspace (value == 42).

Known limitations (stated, not hidden)
--------------------------------------
- The harness cannot verify image-internal toolchain availability (black,
  prettier, ruff, mypy, rope, esbuild, debugpy, offline humanize/decimal.js
  caches) from outside the HTTP boundary; failures there surface as job errors
  and are reported honestly instead of being mocked away.
- Repeated runs create new workspaces; the server allows 3 per actor before
  returning workspace_quota (429), which is reported as a fatal precondition.
- test.coverage (python) asserts discovery at the workspace root; subdirectory
  scaffolds are intentionally kept free of importable test files at root so the
  run stays deterministic across CPython 3.11+ discovery behavior.
- If SSE is blocked by a proxy in front of the service, the JSON poll fallback
  adds HTTP calls but keeps the assertions identical.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')

DEFAULT_BASE = "http://127.0.0.1:18188"
DEFAULT_TOKEN_FILE = "/etc/harmonika-ai/coding-runtime.token"
ACTOR_SEED=800000000000000000+int(time.time())
ACTOR_A = "api-token:"+str(ACTOR_SEED)
ACTOR_B = "api-token:"+str(ACTOR_SEED+1)

PHASE2_IDS = sorted((
    "scaffold.project", "scaffold.component", "scaffold.api", "code.diff",
    "code.patch", "code.format", "format.run", "lint.run", "type.check",
    "build.run", "pkg.list", "pkg.install", "test.single", "test.coverage",
    "code.rename", "refactor.rename", "refactor.extract", "refactor.dead_code",
    "debug.attach", "debug.breakpoint",
))
EXEC_SKILL_IDS = frozenset((
    "exec.shell", "exec.python", "exec.node", "exec.background", "test.run",
    "git.status", "git.diff", "git.commit", "code.format", "format.run",
    "lint.run", "type.check", "build.run", "pkg.list", "pkg.install",
    "test.single", "test.coverage", "code.rename", "refactor.rename",
    "refactor.extract", "refactor.dead_code", "debug.attach", "debug.breakpoint",
))
TERMINAL_STATES = frozenset((
    "completed", "failed", "stopped", "timeout", "expired", "interrupted",
))
PERMISSIONS = {"write": True, "execute": True, "git_commit": True}

HTTP_TIMEOUT = 60.0
SSE_TIMEOUT = 220.0
JOB_WAIT_BUDGET = 200.0
POLL_BUDGET = 150.0


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dig(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def make_unified_patch(pathname, before, after):
    diff = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/" + pathname, tofile="b/" + pathname)
    lines = list(diff)
    if not lines:
        raise ValueError("empty diff for " + pathname)
    return "--- a/" + pathname + "\n+++ b/" + pathname + "\n" + "".join(lines[2:])


class Api:
    """Minimal stdlib HTTP client for one actor principal. Never logs secrets."""

    def __init__(self, base, secret, actor_label, principal):
        self.base = base.rstrip("/")
        self.secret = secret
        self.actor_label = actor_label
        self.principal = principal
        self.calls = 0
        self.new_jobs = 0
        self.exec_jobs = 0

    def _request(self, method, path, body=None, headers=None, timeout=HTTP_TIMEOUT):
        head = {
            "X-Harmonika-Coding-Secret": self.secret,
            "X-Harmonika-Principal": self.principal,
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        if headers:
            head.update(headers)
        self.calls += 1
        request = urllib.request.Request(self.base + path, data=data,
                                         headers=head, method=method)
        return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)

    def json(self, method, path, body=None, headers=None, timeout=HTTP_TIMEOUT):
        try:
            with self._request(method, path, body, headers, timeout) as response:
                raw = response.read()
            try:
                return response.status, json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return response.status, {"raw": raw[:400].decode("utf-8", "replace")}
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                payload = {"error": {"code": "http_%d" % exc.code}}
            return exc.code, payload

    def open_event_stream(self, path, timeout=SSE_TIMEOUT):
        try:
            response = self._request(
                "GET", path, None,
                headers={"Accept": "text/event-stream"}, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except Exception:
                pass
            return None
        if response.headers.get("Content-Type", "").startswith("text/event-stream"):
            return response
        response.close()
        return None

    def wait_via_events(self, job_id, budget=JOB_WAIT_BUDGET):
        """Block on the real SSE stream until the job reaches a terminal state."""
        response = self.open_event_stream("/jobs/%s/events" % job_id)
        if response is None:
            return False
        deadline = time.monotonic() + budget
        try:
            for raw in response:
                if time.monotonic() > deadline:
                    return False
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    return True
        except (OSError, ValueError):
            return False
        finally:
            response.close()
        return True

    def poll_until_terminal(self, job_id, budget=POLL_BUDGET):
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            status, body = self.json("GET", "/jobs/%s" % job_id)
            if status == 200 and body.get("status") in TERMINAL_STATES:
                return True
            time.sleep(2.0)
        return False

    def await_job(self, job_id):
        if not self.wait_via_events(job_id):
            self.poll_until_terminal(job_id)
        status, body = self.json("GET", "/jobs/%s" % job_id)
        return status, body

    def run_job(self, workspace_id, skill_id, skill_input, idem_key):
        """Submit one job, approve it with the server's input_hash, await terminal."""
        self.new_jobs += 1
        if skill_id in EXEC_SKILL_IDS:
            self.exec_jobs += 1
        status, body = self.json(
            "POST", "/workspaces/%s/invoke" % workspace_id,
            {"skill_id": skill_id, "input": skill_input, "idempotency_key": idem_key})
        if status != 200:
            return {"submitted": False, "http_status": status, "error": body}
        view = body
        approved = None
        if view.get("requires_approval"):
            approved = True
            status, body = self.json(
                "POST", "/jobs/%s/approve" % view["id"],
                {"decision": "approve", "input_hash": view.get("input_hash")})
            if status != 200:
                return {"submitted": True, "approved": False, "http_status": status,
                        "error": body, "job_id": view["id"]}
            view = body
        if view.get("status") in TERMINAL_STATES:
            return {"submitted": True, "approved": approved, "job_id": view["id"],
                    "view": view, "completed": view.get("status") == "completed",
                    "replayed": bool(view.get("replayed"))}
        status, body = self.await_job(view["id"])
        if status != 200:
            return {"submitted": True, "approved": approved, "job_id": view["id"],
                    "http_status": status, "error": body, "completed": False}
        return {"submitted": True, "approved": approved, "job_id": view["id"],
                "view": body, "completed": body.get("status") == "completed",
                "replayed": bool(body.get("replayed"))}


class Report:
    def __init__(self, base):
        self.data = {
            "tool": "smoke_phase2",
            "base": base,
            "started_utc": utcnow(),
            "finished_utc": None,
            "actors": {},
            "tests": [],
            "summary": {"total": 0, "passed": 0, "failed": 0},
            "fatal": [],
            "limitations": [],
        }
        self.exit_code = 0

    def actor(self, api):
        self.data["actors"][api.actor_label] = {
            "http_calls": api.calls,
            "new_job_submissions": api.new_jobs,
            "exec_skill_jobs": api.exec_jobs,
        }

    def test(self, test_id, title, checks, extra=None):
        passed = all(item["pass"] for item in checks) if checks else False
        entry = {"id": test_id, "title": title, "pass": passed, "checks": checks}
        if extra:
            entry["detail"] = extra
        self.data["tests"].append(entry)
        self.data["summary"]["total"] += 1
        if passed:
            self.data["summary"]["passed"] += 1
        else:
            self.data["summary"]["failed"] += 1
            self.exit_code = 1

    def fatal(self, message):
        self.data["fatal"].append(message)
        self.exit_code = 2

    def finish(self):
        self.data["finished_utc"] = utcnow()
        self.data["summary"]["exit_code"] = self.exit_code
        print(json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True))
        return self.exit_code


def ck(checks, name, condition, detail=""):
    checks.append({"name": name, "pass": bool(condition),
                   "detail": "" if condition else str(detail)[:500]})


def job_completed(checks, outcome, label):
    ck(checks, label + ": invoke accepted", outcome.get("submitted"), outcome)
    if not outcome.get("submitted"):
        return False
    if outcome.get("approved") is False:
        ck(checks, label + ": approval accepted", False, outcome)
        return False
    view = outcome.get("view") or {}
    ck(checks, label + ": job completed", outcome.get("completed") is True,
       "status=%s error=%s" % (view.get("status"), view.get("error")))
    return outcome.get("completed") is True


def tool_envelope(checks, view, label):
    envelope = dig(view, "result", "tool_result")
    ck(checks, label + ": structured tool envelope", isinstance(envelope, dict), view)
    if not isinstance(envelope, dict):
        return None
    ck(checks, label + ": tool ok", envelope.get("ok") is True, envelope.get("error"))
    return envelope


# ---------------------------------------------------------------------------
# Actor A: foundation skills, read-only tools, rename/extract/format, regression
# ---------------------------------------------------------------------------

SCAFFOLD_API_ROUTE = "/v1/smoke"
README_BEFORE = ("# SmokePy\n\nRun: python3 app.py\n"
                 "Test: python3 -m unittest discover\n"
                 "Local health: http://127.0.0.1:8080/health\n")
README_AFTER = README_BEFORE + "\nVerified by smoke_phase2 harness.\n"

MATHUTIL_PY = "def double(value):\n    return value * 2\n"
TEST_MATHUTIL_PY = ("import unittest\nfrom mathutil import double\n\n"
                    "class DoubleTests(unittest.TestCase):\n"
                    "    def test_double(self):\n"
                    "        self.assertEqual(double(21), 42)\n")


def scaffold_api_text():
    route_repr = repr(SCAFFOLD_API_ROUTE)
    method_repr = repr("GET")
    return ('"""Generated route helper. Connect to the application router'
            ' explicitly."""\nROUTE = ' + route_repr + "\nMETHOD = " + method_repr +
            "\n\ndef handle(method, path):\n    if method != METHOD or path != ROUTE:\n"
            "        return 404, {'error': 'not_found'}\n"
            "    return 200, {'ok': True}\n")


def run_actor_a(api, report, nonce):
    checks = []
    key = "sm2%s-a" % nonce

    status, caps = api.json("GET", "/capabilities")
    ck(checks, "capabilities readable", status == 200, caps)
    skill_map = {entry.get("id"): entry for entry in (caps.get("skills") or [])} \
        if isinstance(caps, dict) else {}
    missing = [sid for sid in PHASE2_IDS if sid not in skill_map]
    ck(checks, "all 20 phase2 ids exposed", not missing, missing)
    unavailable = [sid for sid in PHASE2_IDS
                   if dig(skill_map.get(sid) or {}, "readiness", "status") != "available"]
    ck(checks, "all 20 phase2 ids available", not unavailable, unavailable)
    ck(checks, "phase2 feature enabled", dig(caps, "features", "phase2") is True, caps)
    debug_ready = dig(caps, "features", "debug_sessions") is True
    ck(checks, "debug sessions enabled", debug_ready, caps)
    ck(checks, "exec quota is 30/hour",
       dig(caps, "limits", "executions_per_hour") == 30, dig(caps, "limits"))
    report.test("A00", "capabilities expose all 20 phase2 skills", checks)
    if dig(caps, "features", "phase2") is not True:
        report.fatal("phase2 feature is not enabled on the service")
        return None, None
    if not debug_ready:
        report.fatal("debug sessions are not enabled on the service")

    checks = []
    status, body = api.json("POST", "/workspaces",
                            {"name": "phase2-smoke-a-%s" % nonce,
                             "permissions": dict(PERMISSIONS)})
    ck(checks, "workspace created (write+execute+git_commit)", status == 201, body)
    if status != 201:
        report.fatal("cannot create actor A workspace: %s" % body)
        report.test("A01", "workspace setup", checks)
        return None, None
    workspace = body.get("workspace") or {}
    ck(checks, "permissions echoed true",
       all(workspace.get("permissions", {}).get(k) is True for k in PERMISSIONS),
       workspace.get("permissions"))
    report.test("A01", "own workspace created", checks)
    wid_a = workspace.get("id")

    def job(skill, inp, suffix, label, test_id, title):
        checks = []
        outcome = api.run_job(wid_a, skill, inp, "%s-%s" % (key, suffix))
        ok = job_completed(checks, outcome, label)
        report.test(test_id, title, checks,
                    None if ok else {"job_id": outcome.get("job_id")})
        return outcome

    out = job("scaffold.project", {"path": "proj", "template": "python_http",
                                   "name": "SmokePy"}, "scaffold-py",
              "scaffold.project", "A02", "scaffold.project python_http")
    checks = []
    result = dig(out, "view", "result") or {}
    writes = {item.get("path") for item in result.get("files") or []}
    ck(checks, "three starter files written",
       writes == {"proj/app.py", "proj/test_app.py", "proj/README.md"}, writes)
    ck(checks, "published completed source files",
       result.get("status") == "completed" and result.get("files_changed") == 3,
       result)
    report.test("A02b", "scaffold.project writes verified", checks)

    out = job("scaffold.component", {"path": "ui/BadgeA.jsx", "name": "BadgeA"},
              "scaffold-comp", "scaffold.component", "A03", "scaffold.component jsx")
    checks = []
    writes = dig(out, "view", "result", "files") or []
    ck(checks, "jsx component created",
       [item.get("path") for item in writes] == ["ui/BadgeA.jsx"], writes)
    report.test("A03b", "scaffold.component write verified", checks)

    out = job("scaffold.api", {"path": "routes_api.py", "route":	SCAFFOLD_API_ROUTE,
                               "method": "GET"}, "scaffold-api",
              "scaffold.api", "A04", "scaffold.api route helper")
    checks = []
    writes = dig(out, "view", "result", "files") or []
    ck(checks, "route helper created",
       [item.get("path") for item in writes] == ["routes_api.py"], writes)
    report.test("A04b", "scaffold.api write verified", checks)

    out = job("code.rename", {"path": "proj/app.py", "line": 4, "column": 6,
                              "new_name": "ServerHandler"}, "rename-1",
              "code.rename", "A05", "code.rename cross-file (class Handler)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "code.rename")
    if envelope:
        changed = dig(envelope, "result", "changed_files") or []
        ck(checks, "rename touched both files",
           "proj/app.py" in changed and "proj/test_app.py" in changed, changed)
        ck(checks, "old name recorded", dig(envelope, "result", "old_name") == "Handler",
           dig(envelope, "result", "old_name"))
    report.test("A05b", "code.rename assertions", checks)

    out = job("refactor.rename", {"path": "proj/app.py", "line": 5, "column": 8,
                                  "new_name": "handle_get"}, "rename-2",
              "refactor.rename", "A06", "refactor.rename (method do_GET)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "refactor.rename")
    if envelope:
        changed = dig(envelope, "result", "changed_files") or []
        ck(checks, "method rename touched both files",
           "proj/app.py" in changed and "proj/test_app.py" in changed, changed)
    report.test("A06b", "refactor.rename assertions", checks)

    out = job("refactor.extract", {"path": "proj/app.py", "start_line": 6,
                                   "end_line": 7, "name": "send_status"},
              "extract-1", "refactor.extract", "A07",
              "refactor.extract (lines 6-7)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "refactor.extract")
    if envelope:
        changed = dig(envelope, "result", "changed_files") or []
        ck(checks, "extract touched app.py", "proj/app.py" in changed, changed)
        ck(checks, "method name recorded",
           dig(envelope, "result", "method") == "send_status",
           dig(envelope, "result", "method"))
    report.test("A07b", "refactor.extract assertions", checks)

    out = job("code.format", {"path": "proj/app.py"}, "format-py",
              "code.format", "A08", "code.format black (python)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "code.format")
    if envelope:
        ck(checks, "formatted as python", dig(envelope, "result", "language") == "python",
           dig(envelope, "result", "language"))
        ck(checks, "sha256 returned", bool(dig(envelope, "result", "sha256")),
           dig(envelope, "result", "sha256"))
        ck(checks, "workspace updated", dig(out, "view", "result", "workspace_updated") is True,
           dig(out, "view", "result"))
    report.test("A08b", "code.format assertions", checks)

    out = job("lint.run", {"path": "proj/app.py"}, "lint-1",
              "lint.run", "A09", "lint.run ruff")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "lint.run")
    if envelope:
        ck(checks, "ruff clean", dig(envelope, "result", "clean") is True,
           dig(envelope, "result", "findings"))
        ck(checks, "engine is ruff", dig(envelope, "result", "engine") == "ruff",
           dig(envelope, "result", "engine"))
    report.test("A09b", "lint.run assertions", checks)
    lint_key = "%s-lint-1" % key
    lint_input = {"path": "proj/app.py"}

    out = job("type.check", {"path": "proj/app.py"}, "type-1",
              "type.check", "A10", "type.check mypy")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "type.check")
    if envelope:
        ck(checks, "mypy clean", dig(envelope, "result", "clean") is True,
           dig(envelope, "result", "errors"))
    report.test("A10b", "type.check assertions", checks)

    out = job("pkg.list", {"manager": "python"}, "pkglist-1",
              "pkg.list", "A11", "pkg.list (python)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "pkg.list")
    if envelope:
        ck(checks, "no manifest yet",
           dig(envelope, "result", "manifest_present") is False,
           dig(envelope, "result"))
        ck(checks, "package list empty",
           dig(envelope, "result", "packages") == [], dig(envelope, "result"))
    report.test("A11b", "pkg.list assertions", checks)

    out = job("build.run", {"path": "proj/app.py"}, "build-py",
              "build.run", "A12", "build.run python zipapp")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "build.run py")
    if envelope:
        artifact = dig(envelope, "result", "artifact") or {}
        ck(checks, "zipapp kind", dig(envelope, "result", "kind") == "zipapp",
           dig(envelope, "result", "kind"))
        ck(checks, "artifact build/app.pyz", artifact.get("path") == "build/app.pyz",
           artifact)
        ck(checks, "artifact nonempty with sha256",
           bool(artifact.get("bytes")) and bool(artifact.get("sha256")), artifact)
        ck(checks, "workspace updated", dig(out, "view", "result", "workspace_updated") is True,
           dig(out, "view", "result"))
    report.test("A12b", "build.run python assertions", checks)

    api.run_job(wid_a, "code.write", {"path": "mathutil.py", "content": MATHUTIL_PY},
                "%s-w1" % key)
    out = api.run_job(wid_a, "code.write",
                      {"path": "test_mathutil.py", "content": TEST_MATHUTIL_PY},
                      "%s-w2" % key)
    checks = []
    ck(checks, "test fixture written", out.get("completed") is True, out.get("view"))
    report.test("A12c", "python test fixture at workspace root", checks)

    out = job("test.single", {"runner": "python_unittest", "path": "test_mathutil.py",
                              "name": "test_mathutil.DoubleTests.test_double"},
              "tsingle-1", "test.single", "A13", "test.single python_unittest")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "test.single")
    if envelope:
        detail = dig(envelope, "result") or {}
        ck(checks, "exactly one test ran", detail.get("ran") == 1, detail)
        ck(checks, "test passed", detail.get("passed") is True, detail)
    report.test("A13b", "test.single assertions", checks)

    out = job("test.coverage", {"runner": "python_unittest", "path": "."},
              "tcover-1", "test.coverage", "A14", "test.coverage coverage.py")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "test.coverage")
    if envelope:
        detail = dig(envelope, "result") or {}
        ck(checks, "real tests discovered", (detail.get("test_count") or 0) >= 1, detail)
        ck(checks, "totals present", isinstance(detail.get("totals"), dict),
           detail.get("totals"))
        ck(checks, "coverage source is coverage.py",
           "coverage.py" in str(detail.get("coverage_source")), detail.get("coverage_source"))
    report.test("A14b", "test.coverage assertions", checks)

    out = job("code.diff", {"path": "proj/README.md", "new_content": README_AFTER},
              "diff-1", "code.diff", "A15", "code.diff preview")
    checks = []
    result = dig(out, "view", "result") or {}
    expected_diff = "".join(difflib.unified_diff(
        README_BEFORE.splitlines(keepends=True), README_AFTER.splitlines(keepends=True),
        fromfile="a/proj/README.md", tofile="b/proj/README.md"))
    ck(checks, "diff matches recomputed unified diff",
       result.get("diff") == expected_diff, result.get("diff"))
    ck(checks, "source sha matches known content",
       result.get("source_sha256") == sha256_hex(README_BEFORE), result)
    ck(checks, "no writes in diff preview", result.get("files") == [], result)
    report.test("A15b", "code.diff assertions", checks)

    before_api = scaffold_api_text()
    after_api = before_api.replace(
        "ROUTE = %s\n" % repr(SCAFFOLD_API_ROUTE),
        "ROUTE = %s\nVERSION = 'smoke-1'\n" % repr(SCAFFOLD_API_ROUTE), 1)
    out = job("code.patch", {"path": "routes_api.py",
                             "patch": make_unified_patch("routes_api.py", before_api, after_api),
                             "expected_sha256": sha256_hex(before_api)},
              "patch-1", "code.patch", "A16", "code.patch applied atomically")
    checks = []
    result = dig(out, "view", "result") or {}
    writes = result.get("files") or []
    ck(checks, "patch targeted routes_api.py",
       len(writes) == 1 and writes[0].get("path") == "routes_api.py", writes)
    ck(checks, "patch overwrote existing file",
       bool(writes) and writes[0].get("overwrote") is True, writes)
    report.test("A16b", "code.patch assertions", checks)

    out = job("fs.write", {"path": "smoke_a.txt", "content": "phase2-ok"},
              "fsw-1", "fs.write", "A17", "phase1 regression: fs.write")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "fs.write created file", result.get("created") is True, result)
    ck(checks, "fs.write sha matches", result.get("sha256") == sha256_hex("phase2-ok"),
       result)
    report.test("A17b", "fs.write assertions", checks)

    out = job("fs.read", {"path": "smoke_a.txt"}, "fsr-1",
              "fs.read", "A18", "phase1 regression: fs.read roundtrip")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "content roundtrip", result.get("content") == "phase2-ok", result)
    report.test("A18b", "fs.read assertions", checks)

    out = job("exec.shell", {"command": "python3 -c \"print(6 * 7)\""},
              "sh-1", "exec.shell", "A19", "phase1 regression: exec.shell approved")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "exit code 0", result.get("exit_code") == 0, result)
    ck(checks, "stdout has 42", "42" in str(result.get("stdout", "")), result.get("stdout"))
    ck(checks, "approval path used", outcome_approved(out), outcome_approved(out))
    report.test("A19b", "exec.shell assertions", checks)

    checks = []
    outcome = api.run_job(wid_a, "lint.run", lint_input, lint_key)
    view = outcome.get("view") or {}
    ck(checks, "replay flagged", outcome.get("replayed") is True, outcome)
    ck(checks, "same job id replayed", view.get("id") == outcome.get("job_id"),
       {"request": outcome.get("job_id"), "view": view.get("id")})
    ck(checks, "replayed job completed", view.get("status") == "completed", view.get("status"))
    report.test("A20", "idempotence: same key+input replays one job", checks)
    return wid_a, None


def outcome_approved(outcome):
    return bool(outcome.get("approved"))


# ---------------------------------------------------------------------------
# Actor B: format.run, pkg.install + fresh-container imports, node tests,
# dead code, and the full debug lifecycle with direct REST actions
# ---------------------------------------------------------------------------

DEBUG_TARGET_PY = (
    "import pathlib\n"
    "\n"
    "value = 40\n"
    "value = value + 2\n"
    "pathlib.Path(\"debug_out.txt\").write_text(\"answer=%d\\n\" % value,"
    " encoding=\"utf-8\")\n"
    "print(\"answer\", value)\n"
    "import os  # deliberately unused (F401 fixture)\n"
)
DEBUG_BP_LINE = 5
DEBUG_OUT_CONTENT = "answer=42\n"


def find_variable(node, name):
    if isinstance(node, dict):
        candidate = node.get(name)
        if isinstance(candidate, dict) and "value" in candidate:
            return candidate
        for child in node.values():
            found = find_variable(child, name)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = find_variable(child, name)
            if found is not None:
                return found
    return None


def run_actor_b(api, report, nonce):
    key = "sm2%s-b" % nonce

    checks = []
    status, body = api.json("POST", "/workspaces",
                            {"name": "phase2-smoke-b-%s" % nonce,
                             "permissions": dict(PERMISSIONS)})
    ck(checks, "workspace created (write+execute+git_commit)", status == 201, body)
    if status != 201:
        report.fatal("cannot create actor B workspace: %s" % body)
        report.test("B01", "workspace setup", checks)
        return None, None
    wid_b = (body.get("workspace") or {}).get("id")
    report.test("B01", "own workspace created", checks)

    def job(skill, inp, suffix, label, test_id, title):
        checks = []
        outcome = api.run_job(wid_b, skill, inp, "%s-%s" % (key, suffix))
        ok = job_completed(checks, outcome, label)
        report.test(test_id, title, checks,
                    None if ok else {"job_id": outcome.get("job_id")})
        return outcome

    out = job("scaffold.project", {"path": "nodesvc", "template": "node_http",
                                   "name": "NodeSvc"}, "scaffold-node",
              "scaffold.project node", "B02", "scaffold.project node_http (fixtures)")
    out = job("scaffold.component", {"path": "widgets/Badge.jsx", "name": "Badge"},
              "scaffold-comp-b", "scaffold.component", "B02b",
              "scaffold.component jsx (build fixture)")
    out = job("code.write", {"path": "debug_target.py", "content": DEBUG_TARGET_PY},
              "dw-1", "code.write", "B03", "debug target fixture written")

    out = job("build.run", {"path": "debug_target.py"}, "build-zip",
              "build.run zipapp", "B04", "build.run python zipapp artifact")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "build.run py")
    if envelope:
        artifact = dig(envelope, "result", "artifact") or {}
        ck(checks, "zipapp artifact present",
           artifact.get("path") == "build/debug_target.pyz" and bool(artifact.get("sha256")),
           artifact)
    report.test("B04b", "build.run zipapp assertions", checks)

    out = job("build.run", {"path": "widgets/Badge.jsx"},
              "build-jsx", "build.run esbuild", "B05", "build.run jsx esbuild bundle")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "build.run jsx")
    if envelope:
        ck(checks, "esbuild minified kind",
           dig(envelope, "result", "kind") == "esbuild_bundle_minified",
           dig(envelope, "result", "kind"))
        artifact = dig(envelope, "result", "artifact") or {}
        ck(checks, "bundle artifact present",
           artifact.get("path") == "build/Badge.js" and bool(artifact.get("sha256")),
           artifact)
    report.test("B05b", "build.run jsx assertions", checks)

    out = job("format.run", {"path": "nodesvc/app.cjs"}, "format-js",
              "format.run", "B06", "format.run prettier (javascript)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "format.run")
    if envelope:
        ck(checks, "formatted as javascript",
           dig(envelope, "result", "language") == "javascript",
           dig(envelope, "result", "language"))
        ck(checks, "sha256 returned", bool(dig(envelope, "result", "sha256")),
           dig(envelope, "result", "sha256"))
    report.test("B06b", "format.run assertions", checks)

    out = job("pkg.install", {"manager": "python", "name": "humanize",
                              "version": "4.12.2"}, "pkg-h",
              "pkg.install python", "B07", "pkg.install humanize (offline)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "pkg.install humanize")
    if envelope:
        installed = dig(envelope, "result", "installed") or {}
        ck(checks, "humanize installed from offline cache",
           installed.get("manager") == "python" and installed.get("name") == "humanize",
           installed)
        ck(checks, "manifest updated",
           dig(envelope, "result", "manifest_updated") is True,
           dig(envelope, "result"))
    report.test("B07b", "pkg.install python assertions", checks)

    out = job("pkg.install", {"manager": "node", "name": "decimal.js",
                              "version": "10.5.0"}, "pkg-d",
              "pkg.install node", "B08", "pkg.install decimal.js (offline)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "pkg.install decimal")
    if envelope:
        installed = dig(envelope, "result", "installed") or {}
        ck(checks, "decimal.js installed from offline cache",
           installed.get("manager") == "node" and installed.get("name") == "decimal.js",
           installed)
    report.test("B08b", "pkg.install node assertions", checks)

    out = job("exec.python",
              {"code": "import humanize\nprint(\"HUMANIZE_OK\","
                       " humanize.intcomma(12345))\n"},
              "exec-py", "exec.python", "B09",
              "fresh exec container imports humanize")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "exit code 0", result.get("exit_code") == 0, result)
    ck(checks, "HUMANIZE_OK in stdout",
       "HUMANIZE_OK" in str(result.get("stdout", "")), result.get("stdout"))
    ck(checks, "intcomma output correct",
       "12,345" in str(result.get("stdout", "")), result.get("stdout"))
    report.test("B09b", "fresh-container python import assertions", checks)

    out = job("exec.node",
              {"code": "const { Decimal } = require('decimal.js');\n"
                       "console.log('DECIMAL_OK', new Decimal('0.1').plus('0.2')"
                       ".toString());\n"},
              "exec-node", "exec.node", "B10",
              "fresh exec container requires decimal.js")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "exit code 0", result.get("exit_code") == 0, result)
    ck(checks, "DECIMAL_OK 0.3 in stdout",
       "DECIMAL_OK 0.3" in str(result.get("stdout", "")), result.get("stdout"))
    report.test("B10b", "fresh-container node import assertions", checks)

    out = job("test.single", {"runner": "node_test", "path": "nodesvc/app.test.cjs",
                              "name": "server factory"}, "tsingle-node",
              "test.single node", "B11", "test.single node_test (anchored name)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "test.single node")
    if envelope:
        detail = dig(envelope, "result") or {}
        ck(checks, "exactly one test matched", detail.get("matched") == 1, detail)
        ck(checks, "test passed", detail.get("passed") is True, detail)
    report.test("B11b", "test.single node assertions", checks)

    out = job("test.coverage", {"runner": "node_test", "path": "nodesvc"},
              "tcover-node", "test.coverage node", "B12",
              "test.coverage node --experimental-test-coverage")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "test.coverage node")
    if envelope:
        detail = dig(envelope, "result") or {}
        counts = detail.get("counts") or {}
        ck(checks, "tests discovered", (counts.get("tests") or 0) >= 1, counts)
        ck(checks, "coverage totals row present",
           isinstance(detail.get("totals"), dict), detail.get("totals"))
        ck(checks, "all tests passed", counts.get("fail", 1) == 0, counts)
    report.test("B12b", "test.coverage node assertions", checks)

    out = job("refactor.dead_code", {"path": "debug_target.py"}, "dead-report",
              "refactor.dead_code report", "B13",
              "refactor.dead_code report-only (F401)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "dead_code report")
    dead_sha = None
    if envelope:
        detail = dig(envelope, "result") or {}
        findings = detail.get("findings") or []
        ck(checks, "F401 finding reported",
           any(item.get("code") == "F401" for item in findings), findings)
        ck(checks, "report-only did not apply",
           detail.get("applied") is False and detail.get("changed") is False, detail)
        dead_sha = detail.get("sha256")
        ck(checks, "sha256 provided for apply step", bool(dead_sha), detail)
    report.test("B13b", "refactor.dead_code report assertions", checks)

    out = job("refactor.dead_code",
              {"path": "debug_target.py", "apply": True,
               "expected_sha256": dead_sha or ("0" * 64)},
              "dead-apply", "refactor.dead_code apply", "B14",
              "refactor.dead_code apply (approved)")
    checks = []
    envelope = tool_envelope(checks, out.get("view"), "dead_code apply")
    if envelope:
        detail = dig(envelope, "result") or {}
        ck(checks, "unused import removed", (detail.get("removed_count") or 0) >= 1, detail)
        ck(checks, "no findings remain", detail.get("remaining_count") == 0, detail)
        ck(checks, "workspace updated",
           dig(out, "view", "result", "workspace_updated") is True,
           dig(out, "view", "result"))
    report.test("B14b", "refactor.dead_code apply assertions", checks)

    out = job("debug.attach", {"action": "start", "path": "debug_target.py",
                               "timeout_seconds": 120}, "dbg-attach",
              "debug.attach", "B15", "debug.attach approved session start")
    checks = []
    session_id = None
    result = dig(out, "view", "result") or {}
    session = result.get("debug_session") or {}
    session_id = session.get("id")
    ck(checks, "session id returned", bool(session_id), result)
    ck(checks, "session paused on entry", session.get("state") == "paused", session)
    report.test("B15b", "debug.attach assertions", checks)
    if not session_id:
        return wid_b, None

    out = job("debug.breakpoint", {"session_id": session_id,
                                   "path": "debug_target.py",
                                   "lines": [DEBUG_BP_LINE]}, "dbg-bp",
              "debug.breakpoint", "B16", "debug.breakpoint verified at line")
    checks = []
    envelope = dig(out, "view", "result", "tool_result")
    ck(checks, "breakpoint envelope ok", isinstance(envelope, dict) and
       envelope.get("ok") is True, envelope)
    reported = dig(envelope, "result", "breakpoints") if isinstance(envelope, dict) else None
    if reported is None and isinstance(envelope, dict):
        reported = envelope.get("breakpoints")
    ck(checks, "breakpoint verified by adapter",
       isinstance(reported, list) and reported and reported[0].get("verified") is True,
       reported)
    report.test("B16b", "debug.breakpoint assertions", checks)

    checks = []
    status, view = api.json(
        "POST", "/debug/sessions/%s/actions" % session_id, {"action": "continue"},
        headers={"Idempotency-Key": "%s-dbgact-1" % key}, timeout=90.0)
    ck(checks, "continue accepted", status == 200, view)
    acted = view.get("result") if isinstance(view, dict) else None
    ck(checks, "paused at breakpoint line",
       isinstance(acted, dict) and acted.get("state") == "paused" and
       dig(acted, "location", "line") == DEBUG_BP_LINE, acted)
    ck(checks, "stop reason is breakpoint",
       isinstance(acted, dict) and acted.get("stopped_reason") == "breakpoint",
       dig(acted, "stopped_reason") if isinstance(acted, dict) else acted)
    report.test("B17", "debug REST: continue to breakpoint", checks)

    checks = []
    status, view = api.json(
        "POST", "/debug/sessions/%s/actions" % session_id, {"action": "inspect"},
        headers={"Idempotency-Key": "%s-dbgact-2" % key}, timeout=90.0)
    ck(checks, "inspect accepted", status == 200, view)
    acted = view.get("result") if isinstance(view, dict) else None
    variable = find_variable(acted, "value") if isinstance(acted, dict) else None
    ck(checks, "variable value inspected as 42",
       isinstance(variable, dict) and variable.get("value") == "42", acted)
    ck(checks, "variable not redacted",
       isinstance(variable, dict) and variable.get("redacted") is False, variable)
    report.test("B18", "debug REST: inspect sees value == 42", checks)

    checks = []
    status, view = api.json(
        "POST", "/debug/sessions/%s/actions" % session_id, {"action": "continue"},
        headers={"Idempotency-Key": "%s-dbgact-3" % key}, timeout=90.0)
    ck(checks, "second continue accepted", status == 200, view)
    ck(checks, "target ran to termination",
       isinstance(view, dict) and dig(view, "result", "terminated") is True and
       view.get("state") == "terminated", view)
    report.test("B19", "debug REST: continue to exit", checks)

    checks = []
    status, view = api.json("POST", "/debug/sessions/%s/stop" % session_id,
                            timeout=120.0)
    ck(checks, "stop accepted", status == 200, view)
    ck(checks, "session closed", isinstance(view, dict) and view.get("state") == "closed",
       view)
    ck(checks, "source write persisted to workspace",
       isinstance(view, dict) and dig(view, "result", "workspace_updated") is True, view)
    report.test("B20", "debug REST: stop with snapshot publish", checks)

    out = job("code.read", {"path": "debug_out.txt"}, "dbg-read",
              "code.read", "B21", "debuggee write persisted (answer=42)")
    checks = []
    result = dig(out, "view", "result") or {}
    ck(checks, "debug_out.txt content is answer=42",
       result.get("content") == DEBUG_OUT_CONTENT, result)
    report.test("B21b", "persisted debug output assertions", checks)
    return wid_b, session_id


def run_cross_actor_probes(api_a, wid_b, session_b, report):
    checks = []
    status, body = api_a.json(
        "POST", "/workspaces/%s/invoke" % wid_b,
        {"skill_id": "lint.run", "input": {"path": "proj/app.py"},
         "idempotency_key": "sm2-cross-404"})
    ck(checks, "foreign workspace invoke rejected", status == 404, {"status": status})
    ck(checks, "error is workspace_not_found",
       dig(body, "error", "code") == "workspace_not_found", body)
    report.test("X01", "cross-actor workspace invoke is 404", checks)

    if session_b:
        checks = []
        status, body = api_a.json("GET", "/debug/sessions/%s" % session_b)
        ck(checks, "foreign debug session rejected", status == 404, {"status": status})
        ck(checks, "error is debug_session_not_found",
           dig(body, "error", "code") == "debug_session_not_found", body)
        report.test("X02", "cross-actor debug session is 404", checks)


def read_secret(token_file):
    import pathlib
    path = pathlib.Path(token_file)
    info = path.stat()
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit("token file %s is group/world readable; refusing" % token_file)
    secret = path.read_text().strip()
    if len(secret) < 32:
        raise SystemExit("token file %s does not contain a usable service secret" % token_file)
    return secret


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deterministic real-HTTP acceptance harness for the 20 Phase2 skill IDs.")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help="coding-runtime base URL (default %(default)s)")
    parser.add_argument("--token-file", default=DEFAULT_TOKEN_FILE,
                        help="service secret file, read private (default %(default)s)")
    args = parser.parse_args(argv)

    if args.base not in ('http://127.0.0.1:18188','http://127.0.0.1:18187'):parser.error('private loopback runtime required')
    secret = read_secret(args.token_file)
    report = Report(args.base)
    nonce = uuid.uuid4().hex[:10]
    api_a = Api(args.base, secret, ACTOR_A, ACTOR_A)
    api_b = Api(args.base, secret, ACTOR_B, ACTOR_B)

    status, body = api_a.json("GET", "/healthz")
    if status != 200 or not isinstance(body, dict) or not body.get("ok"):
        report.fatal("service healthz failed: status=%s" % status)
        return report.finish()

    try:
        wid_a, _ = run_actor_a(api_a, report, nonce)
        wid_b, session_b = run_actor_b(api_b, report, nonce)
        if wid_a and wid_b:
            run_cross_actor_probes(api_a, wid_b, session_b, report)
        elif not wid_a or not wid_b:
            report.fatal("workspace setup incomplete; cross-actor probes skipped")
    except Exception as exc:
        report.fatal("harness error: %s: %s" % (type(exc).__name__, exc))

    report.actor(api_a)
    report.actor(api_b)
    report.data["limitations"].append(
        "quotas honored: per-actor new job submissions A/B are reported under "
        "actors; the 30-exec/hour/token limit applies to exec_skill_jobs.")
    report.data["limitations"].append(
        "image-internal toolchain and offline package caches are exercised for "
        "real but cannot be pre-verified over HTTP alone.")
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
