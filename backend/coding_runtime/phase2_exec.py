#!/usr/bin/env python3
"""Phase2 exec adapter: fixed tool plans and the offline prepare-env prelude.

Installed at /opt/harmonika/phase2_exec.py next to phase2_tools.py.

Two responsibilities (both ROOT-invoked, never user-invoked):

1. build_phase2_plan(skill_id, args) -> {'steps': [[argv...]],
                                         'step_timeout': float,
                                         'overall_default': float}
   ROOT adapts this into the existing sandbox._Plan, wraps every step with
   `timeout -k 2 <seconds>`, and enforces auth/approval/ownership. The argv is
   always the fixed trusted child:

       [python3, /opt/harmonika/phase2_tools.py, run, <skill_id>, <b64args>]

   Nothing from user args can change the interpreter, script path, or flags.
   The 13 tool skill ids and basic arg-shape validation live in phase2_tools.

2. `prepare-env` CLI: validate the workspace harmonika-packages.json against
   the hardcoded offline allowlist and install every allowlisted entry into
   the fixed per-job dirs /tmp/harmonika-deps/{python,node} with --no-index /
   --offline. ROOT's prelude calls this before every generic exec and sets
   PYTHONPATH=/tmp/harmonika-deps/python and
   NODE_PATH=/tmp/harmonika-deps/node/node_modules, so a package installed by
   pkg.install in one job is importable in later jobs of the same workspace.
   The prelude is NOT run for the trusted import/export snapshot helpers.

Exit codes: 0 ok (including "nothing to install"), 2 invalid manifest.
Output: one bounded JSON object on stdout in every case.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from phase2_tools import (  # noqa: E402
    DEPS_NODE,
    DEPS_PY,
    MANIFEST_NAME,
    SKILL_ARGS,
    SKILL_IDS,
    ToolError,
    install_node_package,
    install_python_package,
    read_manifest_entries,
    run as run_tool,
)

TOOLS_SCRIPT = '/opt/harmonika/phase2_tools.py'
OVERALL_MARGIN = 30.0
OVERALL_MAX = 300.0
STEP_MAX = 120.0
ARGS_JSON_MAX = 65536

# Per-skill step budgets (<= the sandbox's 120s skill cap).
STEP_TIMEOUTS = {
    'code.format': 60.0,
    'format.run': 60.0,
    'lint.run': 30.0,
    'type.check': 90.0,
    'build.run': 90.0,
    'pkg.list': 30.0,
    'pkg.install': 90.0,
    'test.single': 90.0,
    'test.coverage': 120.0,
    'code.rename': 90.0,
    'refactor.rename': 90.0,
    'refactor.extract': 90.0,
    'refactor.dead_code': 60.0,
}


class PlanError(Exception):
    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _fail(code, message=''):
    raise PlanError(code, message)


def build_phase2_plan(skill_id, args):
    """Return the fixed sandbox plan for one Phase2 tool skill."""
    if not isinstance(skill_id, str) or skill_id not in SKILL_IDS:
        _fail('unsupported_skill_id', 'unsupported skill_id: %r' % (skill_id,))
    if args is None:
        args = {}
    if not isinstance(args, dict):
        _fail('invalid_args', 'args must be an object')
    required, optional = SKILL_ARGS[skill_id]
    allowed = set(required) | set(optional)
    unknown = sorted(key for key in args if key not in allowed)
    if unknown:
        _fail('invalid_args', 'unsupported arguments: %s' % ', '.join(unknown))
    missing = [key for key in required if key not in args]
    if missing:
        _fail('invalid_args', 'required arguments: %s' % ', '.join(missing))
    try:
        encoded = json.dumps(args, ensure_ascii=True, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        _fail('invalid_args', 'args are not JSON-serializable: %s' % exc)
    if len(encoded) > ARGS_JSON_MAX:
        _fail('args_too_large', 'args JSON exceeds %d bytes' % ARGS_JSON_MAX)
    b64 = base64.b64encode(encoded.encode('utf-8')).decode('ascii')
    step_timeout = STEP_TIMEOUTS[skill_id]
    return {
        'steps': [['python3', TOOLS_SCRIPT, 'run', skill_id, b64]],
        'step_timeout': step_timeout,
        'overall_default': min(step_timeout + OVERALL_MARGIN, OVERALL_MAX),
    }


def prepare_env(workspace):
    """Offline-install every allowlisted manifest entry; print one JSON line.

    The manifest is untrusted: entries are checked against the hardcoded
    allowlist again here. Malformed manifests exit 2 (fail closed); entries
    that are merely not allowlisted are skipped and reported as `rejected`
    so a hand-edited manifest cannot brick the runtime, and can never be
    installed either.
    """
    out = {
        'manifest': None,
        'installed': [],
        'rejected': [],
        'python_path': DEPS_PY,
        'node_path': os.path.join(DEPS_NODE, 'node_modules'),
    }
    try:
        if not os.path.isdir(workspace):
            _fail('workspace_missing', 'workspace is not a directory: %s' % workspace)
        entries, rejected = read_manifest_entries(workspace)
    except ToolError as exc:
        if exc.code == 'not_found':
            out['manifest'] = 'absent'
            _emit(out)
            return 0
        out['error'] = exc.as_dict()
        _emit(out)
        return 2
    out['rejected'] = rejected
    if entries is None:
        out['manifest'] = 'absent'
        _emit(out)
        return 0
    out['manifest'] = MANIFEST_NAME
    failures = []
    for entry in sorted(entries, key=lambda e: (e['manager'], e['name'])):
        try:
            if entry['manager'] == 'python':
                result = install_python_package(entry['name'], entry['version'])
            else:
                result = install_node_package(entry['name'], entry['version'])
            out['installed'].append(result)
        except ToolError as exc:
            failures.append({'package': entry, 'error': exc.as_dict()})
    if failures:
        out['error'] = {'code': 'prepare_env_failed', 'message': 'one or more installs failed'}
        out['failures'] = failures
        _emit(out)
        return 1
    _emit(out)
    return 0


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + '\n')
    sys.stdout.flush()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == 'plan':
        try:
            raw = base64.b64decode(argv[2].encode('ascii'), validate=True)
            args = json.loads(raw.decode('utf-8'))
            plan = build_phase2_plan(argv[1], args)
        except (binascii.Error, ValueError, UnicodeDecodeError):
            _emit({'ok': False, 'error': {'code': 'invalid_encoding',
                                          'message': 'args must be base64 UTF-8 JSON'}})
            return 2
        except PlanError as exc:
            _emit({'ok': False, 'error': {'code': exc.code, 'message': exc.message}})
            return 2
        _emit({'ok': True, 'plan': plan})
        return 0
    if len(argv) in (1, 2) and argv[0] == 'prepare-env':
        workspace = argv[1] if len(argv) == 2 else '/workspace'
        return prepare_env(workspace)
    _emit({'ok': False,
           'error': {'code': 'usage',
                     'message': 'usage: phase2_exec.py prepare-env [workspace] | '
                                'plan <skill_id> <base64JSONargs>'}})
    return 2


if __name__ == '__main__':
    sys.exit(main())
