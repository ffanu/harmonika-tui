#!/usr/bin/env python3
"""Trusted Phase2 coding-tool CLI for Harmonika sandbox jobs (TOOLS worker scope).

Runs ONLY inside the sandbox image as the fixed child of every Phase2 tool job:

    python3 /opt/harmonika/phase2_tools.py run <skill_id> <base64JSONargs>
    python3 /opt/harmonika/phase2_tools.py list

Exactly one bounded JSON object is printed on stdout; exit 0 means success,
nonzero means failure (the JSON still carries a structured diagnostic).
`run(skill_id, args, root='/workspace')` is the library entry point.

Supported skill ids (13; aliases share engines):
    code.format / format.run   black (python) or prettier (js/ts/json), in place
    lint.run                   ruff (python) or `node --check` (JS, syntax-only)
    type.check                 isolated mypy (python) or tsc with a fixed config
    build.run                  python zipapp artifact or esbuild minified bundle
    pkg.list                   workspace harmonika-packages.json inventory
    pkg.install                real offline install from the pinned image cache
    test.single                one exact unittest id / anchored node test name
    test.coverage              coverage.py JSON or node --experimental-test-coverage
    code.rename / refactor.rename   rope scoped rename (cross-file)
    refactor.extract           rope ExtractMethod over whole-line ranges
    refactor.dead_code         ruff F401 report; apply=true removes imports only

Trust model
-----------
The caller (root service) has already authenticated the job; this process runs
as uid 10000 in a read-only-rootfs, network=none container whose /workspace is
the per-job tmpfs volume. Even so this module re-validates everything:

- paths must be relative, no traversal, no backslash, no control characters,
  no credential/private names; files are opened by descending O_NOFOLLOW
  directory descriptors and must be regular single-link files <=512KiB;
- workspace bounds: <=256 files, <=16MiB total (checked before mutations);
- every child command is a FIXED argv constant; job data only reaches the
  child as validated data arguments (path/name), never as code or flags;
  child time and output are bounded (kill on deadline, fail on overflow);
- linter/type configs are fixed files this module writes into the container
  tmpfs (empty black config, minimal mypy config, fixed tsconfig with vendor
  typeRoots/baseUrl), so project configs and plugins are never executed;
- pkg.install installs ONLY (manager, name, version) pairs from the hardcoded
  allowlist below, fully offline, from artifacts baked into the image; the
  workspace manifest is untrusted and is re-checked entry by entry before
  any merge, and prepare-env (phase2_exec.py) re-checks it again.

Known limits (stated, not hidden): refactoring is Python-first via rope;
JavaScript lint is syntax-only (`node --check`), never eslint; plain-JavaScript
type checking is unsupported; unsupported inputs return ok=false with an
explicit error code instead of guessing.
"""
from __future__ import annotations

import ast
import base64
import binascii
import errno
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

__all__ = ['run', 'main', 'ToolError', 'SKILL_IDS', 'PKG_ALLOWLIST',
           'install_python_package', 'install_node_package', 'read_manifest_entries']

# ---------------------------------------------------------------------------
# Bounds (mirror volume_io.py / sandbox.py)
# ---------------------------------------------------------------------------
MAX_FILE_BYTES = 512 * 1024
MAX_FILES = 256
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PATH_LEN = 240
MAX_DIRS = 4096
DEFAULT_ROOT = '/workspace'

CHILD_TIMEOUT = 60.0
CHILD_OUTPUT_LIMIT = 512 * 1024
RESULT_LIMIT = 512 * 1024
BUILD_ARTIFACT_MAX_BYTES = 512 * 1024

# Deployment wiring (image env; never accepted from job args).
VENDOR_NODE = os.environ.get('HARMONIKA_VENDOR_NODE', '/opt/harmonika/node')
PKG_CACHE = os.environ.get('HARMONIKA_PKG_CACHE', '/opt/harmonika/pkg-cache')
DEPS_PY = '/tmp/harmonika-deps/python'
DEPS_NODE = '/tmp/harmonika-deps/node'
MANIFEST_NAME = 'harmonika-packages.json'

# Offline install allowlist: (manager, name) -> pinned versions.
PKG_ALLOWLIST = {
    ('python', 'humanize'): frozenset(('4.12.2',)),
    ('node', 'decimal.js'): frozenset(('10.5.0',)),
}

SKILL_IDS = frozenset((
    'code.format', 'format.run', 'lint.run', 'type.check', 'build.run',
    'pkg.list', 'pkg.install', 'test.single', 'test.coverage',
    'code.rename', 'refactor.rename', 'refactor.extract', 'refactor.dead_code',
))

JS_FAMILY_EXTS = ('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx')

_HEX64 = re.compile(r'\A[0-9a-f]{64}\Z')
_IDENTIFIER = re.compile(r'\A[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
_DOT_IDENT = re.compile(r'\A[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){1,8}\Z')
_PY_NAME = re.compile(r'\A[A-Za-z_][A-Za-z0-9_]{0,99}\Z')
_TEST_NAME = re.compile(r'\A[^ \t\n\r#][^#\n\r]{0,199}\Z')
_VERSION = re.compile(r'\A[0-9A-Za-z.+\-]{1,64}\Z')

# Credential/private path policy (conservative, mirrors the Phase1 file store).
_DENY_NAMES = frozenset((
    '.git', '.ssh', '.gnupg', '.aws', '.azure', '.gcloud', '.kube', '.docker',
    '.netrc', '_netrc', '.npmrc', '.pypirc', '.git-credentials', '.htpasswd',
    '.pgpass', '.my.cnf', '.terraformrc', '.vault-token', '.env',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials', 'credential',
    'secrets',
))
_DENY_GLOBS = (
    '.env*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', '*.ppk',
    'id_rsa*', 'id_dsa*', 'id_ecdsa*', 'id_ed25519*',
)
_DENY_SUBSTRINGS = ('secret', 'credential', 'passwd', 'password')
_SKIP_DIRS = frozenset((
    'node_modules', '__pycache__', '.venv', 'venv', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', '.tox', '.next', '.nuxt', '.gradle', '.terraform', 'build',
    'dist', 'coverage',
))


class ToolError(Exception):
    """Structured failure; `code` is a stable diagnostic identifier."""

    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code
        self.message = message or code

    def as_dict(self):
        return {'code': self.code, 'message': self.message[:500]}


def _fail(code, message=''):
    raise ToolError(code, message)


# ---------------------------------------------------------------------------
# Path and file validation (O_NOFOLLOW descriptor descent)
# ---------------------------------------------------------------------------
_OPEN_DIR = (os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
             getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0))
_OPEN_FILE = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) |
              getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NONBLOCK', 0))


def _denied_name(name):
    low = name.lower()
    if low in _DENY_NAMES:
        return True
    if any(fnmatch.fnmatchcase(low, pattern) for pattern in _DENY_GLOBS):
        return True
    return any(token in low for token in _DENY_SUBSTRINGS)


def validate_rel_path(value, what='path', allow_root_dot=False):
    if not isinstance(value, str):
        _fail('invalid_path', '%s must be a string' % what)
    path = value
    if path == '.':
        if allow_root_dot:
            return '.'
        _fail('invalid_path', '%s must name a file' % what)
    if not path or len(path) > MAX_PATH_LEN:
        _fail('invalid_path', '%s length invalid (1..%d)' % (what, MAX_PATH_LEN))
    if path.startswith('/') or path.startswith('~/') or path == '~' or re.match(r'\A[A-Za-z]:', path):
        _fail('absolute_path', '%s must be relative' % what)
    if '\\' in path:
        _fail('invalid_path', '%s must not contain backslash' % what)
    for char in path:
        if ord(char) < 0x20 or ord(char) == 0x7f:
            _fail('control_character', 'control character in %s' % what)
    segments = []
    for part in path.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            _fail('traversal', 'parent traversal refused in %s' % what)
        if len(part) > 255:
            _fail('path_too_long', '%s segment too long' % what)
        if _denied_name(part):
            _fail('credential_path', 'credential or private path refused in %s' % what)
        segments.append(part)
    if not segments or len(segments) > 32:
        _fail('invalid_path', '%s must name a file (<=32 segments)' % what)
    return '/'.join(segments)


def _close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _open_root_fd(root):
    try:
        return os.open(root, _OPEN_DIR)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            _fail('invalid_root', 'workspace root is a symlink or not a directory')
        _fail('invalid_root', 'workspace root unusable: %s' % exc.strerror)


def _descend_dir(root_fd, segments, rel):
    fd = os.dup(root_fd)
    try:
        for name in segments:
            try:
                child = os.open(name, _OPEN_DIR, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    _fail('symlink_rejected', 'symlink or non-directory refused in %s' % rel)
                if exc.errno == errno.ENOENT:
                    _fail('not_found', 'path does not exist: %s' % rel)
                _fail('os_error', 'cannot descend into %s: %s' % (rel, exc.strerror))
            _close(fd)
            fd = child
        return fd
    except BaseException:
        _close(fd)
        raise


def open_validated(root, rel, enforce_size=True):
    """Open a validated regular workspace file; returns the fd (caller closes).

    enforce_size=False lets callers stat oversized artifacts themselves (used
    by build.run to classify and REMOVE an oversized artifact deliberately).
    """
    segments = rel.split('/')
    root_fd = _open_root_fd(root)
    try:
        dir_fd = _descend_dir(root_fd, segments[:-1], rel)
        try:
            try:
                fd = os.open(segments[-1], _OPEN_FILE, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    _fail('symlink_rejected', 'symlink target refused: %s' % rel)
                if exc.errno == errno.ENOENT:
                    _fail('not_found', 'file does not exist: %s' % rel)
                if exc.errno == errno.EISDIR:
                    _fail('is_a_directory', 'path is a directory: %s' % rel)
                _fail('os_error', 'cannot open %s: %s' % (rel, exc.strerror))
            try:
                info = os.fstat(fd)
                if stat.S_ISDIR(info.st_mode):
                    _fail('is_a_directory', 'path is a directory: %s' % rel)
                if not stat.S_ISREG(info.st_mode):
                    _fail('special_file', 'not a regular file: %s' % rel)
                if info.st_nlink > 1:
                    _fail('hardlinked_file', 'file has more than one link: %s' % rel)
                if enforce_size and info.st_size > MAX_FILE_BYTES:
                    _fail('file_too_large',
                          'file exceeds %d bytes: %s' % (MAX_FILE_BYTES, rel))
            except BaseException:
                _close(fd)
                raise
            return fd
        finally:
            _close(dir_fd)
    finally:
        _close(root_fd)


def read_validated(root, rel):
    fd = open_validated(root, rel)
    try:
        data = b''
        while len(data) <= MAX_FILE_BYTES:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
    finally:
        _close(fd)
    if len(data) > MAX_FILE_BYTES:
        _fail('file_too_large', 'file exceeds %d bytes: %s' % (MAX_FILE_BYTES, rel))
    return data


def check_workspace_bounds(root):
    root_fd = _open_root_fd(root)
    counts = {'files': 0, 'bytes': 0}
    over = [False]

    def walk(fd, depth):
        if over[0]:
            return
        try:
            with os.scandir(fd) as scan:
                entries = list(scan)
        except OSError:
            return
        for entry in entries:
            if over[0]:
                return
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if entry.is_symlink():
                continue
            if stat.S_ISDIR(info.st_mode):
                if depth + 1 <= 32 and entry.name not in _SKIP_DIRS and not _denied_name(entry.name):
                    try:
                        child = os.open(entry.name, _OPEN_DIR, dir_fd=fd)
                    except OSError:
                        continue
                    try:
                        walk(child, depth + 1)
                    finally:
                        _close(child)
                continue
            if not stat.S_ISREG(info.st_mode) or _denied_name(entry.name):
                continue
            counts['files'] += 1
            counts['bytes'] += info.st_size
            if counts['files'] > MAX_FILES or counts['bytes'] > MAX_TOTAL_BYTES:
                over[0] = True

    try:
        walk(root_fd, 0)
    finally:
        _close(root_fd)
    if over[0]:
        _fail('workspace_bounds', 'workspace exceeds %d files or %d bytes'
              % (MAX_FILES, MAX_TOTAL_BYTES))
    return counts


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _expected_hash(value):
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX64.match(value.strip().lower()):
        _fail('invalid_hash', 'expected_sha256 must be 64 hex characters')
    return value.strip().lower()


def _check_expected(expected, data):
    """Returns the verified current hash, or None when no expectation given."""
    if expected is None:
        return None
    actual = sha256_hex(data)
    if actual != expected:
        _fail('sha256_conflict', 'file changed: expected %s, found %s' % (expected, actual))
    return actual


def write_validated(root, rel, data):
    """Atomic in-workspace replace; target must already be a validated file."""
    segments = rel.split('/')
    root_fd = _open_root_fd(root)
    try:
        dir_fd = _descend_dir(root_fd, segments[:-1], rel)
        try:
            tmp = '.hms-tools-tmp-%d' % os.getpid()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
            try:
                tmp_fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
                try:
                    os.write(tmp_fd, data)
                finally:
                    _close(tmp_fd)
                os.rename(tmp, segments[-1], src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except OSError as exc:
                try:
                    os.unlink(tmp, dir_fd=dir_fd)
                except OSError:
                    pass
                _fail('os_error', 'cannot write %s: %s' % (rel, exc.strerror))
        finally:
            _close(dir_fd)
    finally:
        _close(root_fd)


def _ensure_build_dir(root):
    root_fd = _open_root_fd(root)
    try:
        try:
            os.mkdir('build', 0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            _fail('os_error', 'cannot create build/: %s' % exc.strerror)
        try:
            info = os.stat('build', dir_fd=root_fd, follow_symlinks=False)
        except OSError as exc:
            _fail('os_error', 'cannot stat build/: %s' % exc.strerror)
        if not stat.S_ISDIR(info.st_mode):
            _fail('special_file', 'build exists but is not a directory')
    finally:
        _close(root_fd)


# ---------------------------------------------------------------------------
# Bounded fixed-argv child runner
# ---------------------------------------------------------------------------
def _node_bin():
    node = shutil.which('node')
    if not node:
        _fail('tool_unavailable', 'node interpreter not found on PATH')
    return node


def _python_bin():
    # Inside the image python3 IS the pinned tools-venv interpreter (symlink),
    # so sys.executable is the fixed /opt/harmonika/tools-venv/bin/python3.
    return sys.executable or 'python3'


def _vendor_bin(name):
    candidate = os.path.join(VENDOR_NODE, 'node_modules', '.bin', name)
    if not os.path.isfile(candidate):
        _fail('tool_unavailable', 'vendored %s not found under %s' % (name, VENDOR_NODE))
    return candidate


def _ruff_bin():
    found = shutil.which('ruff')
    if not found:
        _fail('tool_unavailable', 'ruff not found on PATH (tools venv missing?)')
    return found


def _run_child(argv, cwd, timeout=CHILD_TIMEOUT, env=None):
    """Run one fixed-argv child; returns (rc, stdout, stderr, timed_out).

    The deadline kills the whole process group. Output is capped per stream;
    overflow is a hard error (truncated text can never be parsed honestly).
    """
    full_env = dict(os.environ)
    full_env['PYTHONDONTWRITEBYTECODE'] = '1'
    full_env.setdefault('HOME', '/tmp')
    if env:
        full_env.update(env)
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=full_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
    except FileNotFoundError:
        _fail('tool_unavailable', 'child executable not found: %s' % argv[0])
    except OSError as exc:
        _fail('tool_unavailable', 'cannot start %s: %s' % (argv[0], exc.strerror))
    timed_out = False
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), 9)
        except (OSError, ProcessLookupError):
            try:
                proc.kill()
            except OSError:
                pass
        out, err = proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    for blob in (out, err):
        if blob and len(blob) > CHILD_OUTPUT_LIMIT:
            _fail('output_overflow', 'child output exceeded %d bytes' % CHILD_OUTPUT_LIMIT)
    return rc, out or b'', err or b'', timed_out


# ---------------------------------------------------------------------------
# Fixed isolated configs (written into container tmpfs; never project configs)
# ---------------------------------------------------------------------------
_BLACK_CONFIG = '[tool.black]\n'
_MYPY_CONFIG = '[mypy]\nignore_missing_imports = True\n'

_TSCONFIG_TEMPLATE = '''{
  "compilerOptions": {
    "noEmit": true,
    "strict": true,
    "target": "es2022",
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "esModuleInterop": true,
    "skipLibCheck": true,
    "baseUrl": "%(vendor_modules)s",
    "paths": {"*": ["@types/*", "*"]},
    "typeRoots": ["%(vendor_types)s"],
    "types": ["react", "node"]
  },
  "files": ["%(entry)s"]
}
'''


def _write_fixed(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_CLOEXEC', 0), 0o600)
    try:
        os.write(fd, content.encode('utf-8'))
    finally:
        _close(fd)
    return path


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------
def detect_language(path, requested=None):
    if requested is not None:
        if requested not in ('python', 'javascript', 'typescript', 'json'):
            _fail('unsupported_language', 'language must be python|javascript|typescript|json')
        return requested
    low = path.lower()
    if low.endswith(('.py', '.pyi')):
        return 'python'
    if low.endswith(('.js', '.mjs', '.cjs')):
        return 'javascript'
    if low.endswith(('.ts', '.tsx', '.jsx')):
        return 'typescript'
    if low.endswith('.json'):
        return 'json'
    _fail('unsupported_language', 'cannot infer language from extension: %s' % path)


# ---------------------------------------------------------------------------
# code.format / format.run
# ---------------------------------------------------------------------------
def _format(root, args):
    rel = validate_rel_path(args.get('path'))
    language = detect_language(rel, args.get('language'))
    expected = _expected_hash(args.get('expected_sha256'))
    before = read_validated(root, rel)
    sha_before = _check_expected(expected, before)
    if language == 'python':
        cfg = _write_fixed('/tmp/.hms-black-config.toml', _BLACK_CONFIG)
        argv = [_python_bin(), '-m', 'black', '--config', cfg, '--quiet', './' + rel]
        env = {'BLACK_CACHE_DIR': '/tmp/.hms-black-cache'}
    elif language in ('javascript', 'typescript', 'json'):
        prettier = _vendor_bin('prettier')
        argv = [prettier, '--no-config', '--no-editorconfig', '--write', './' + rel]
        env = {}
    else:
        _fail('unsupported_language', 'formatting unsupported for %r' % language)
    rc, _out, err, timed = _run_child(argv, cwd=root, env=env)
    if timed:
        _fail('tool_timeout', 'formatter exceeded the time budget')
    if rc != 0:
        _fail('format_failed', (err.decode('utf-8', 'replace').strip() or
                                'formatter exit %d' % rc)[:500])
    after = read_validated(root, rel)
    return {
        'path': rel,
        'language': language,
        'changed': sha256_hex(before) != sha256_hex(after),
        'sha256_before': sha256_hex(before),
        'sha256': sha256_hex(after),
        'bytes': len(after),
        'expected_sha256_verified': sha_before is not None,
    }


# ---------------------------------------------------------------------------
# lint.run
# ---------------------------------------------------------------------------
def _lint(root, args):
    rel = validate_rel_path(args.get('path'))
    # Existence/symlink/hardlink/size validation happens before any child runs,
    # and before language inference so bad targets get precise error codes.
    read_validated(root, rel)
    language = detect_language(rel, args.get('language'))
    if language == 'python':
        argv = [_ruff_bin(), 'check', '--isolated', '--no-cache', '--no-fix',
                '--output-format', 'json', './' + rel]
        rc, out, err, timed = _run_child(argv, cwd=root)
        if timed:
            _fail('tool_timeout', 'ruff exceeded the time budget')
        if rc not in (0, 1):
            _fail('lint_failed', (err.decode('utf-8', 'replace').strip() or
                                  'ruff exit %d' % rc)[:500])
        try:
            raw = json.loads(out.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            _fail('lint_failed', 'ruff produced unreadable JSON output')
        findings = []
        for item in raw[:200]:
            loc = item.get('location') or {}
            findings.append({
                'code': item.get('code'),
                'message': str(item.get('message', ''))[:300],
                'line': loc.get('row'),
                'column': loc.get('column'),
                'fix_safe': bool((item.get('fix') or {}).get('applicability') == 'safe'),
            })
        return {
            'language': 'python',
            'engine': 'ruff',
            'mode': 'lint',
            'clean': not findings,
            'count': len(findings),
            'findings': findings,
        }
    if language == 'javascript':
        argv = [_node_bin(), '--check', './' + rel]
        rc, _out, err, timed = _run_child(argv, cwd=root)
        if timed:
            _fail('tool_timeout', 'node --check exceeded the time budget')
        message = err.decode('utf-8', 'replace').strip()[:500]
        return {
            'language': 'javascript',
            'engine': 'node --check',
            'mode': 'syntax_only',
            'clean': rc == 0,
            'count': 0 if rc == 0 else 1,
            'findings': [] if rc == 0 else [{'code': 'SyntaxError', 'message': message}],
            'note': 'JavaScript lint is syntax-only (node --check); eslint is NOT claimed.',
        }
    if language == 'json':
        prettier = _vendor_bin('prettier')
        argv = [prettier, '--no-config', '--no-editorconfig', '--check', './' + rel]
        rc, out, err, timed = _run_child(argv, cwd=root)
        if timed:
            _fail('tool_timeout', 'prettier exceeded the time budget')
        combined = (out + err).decode('utf-8', 'replace')
        if rc == 0:
            return {
                'language': 'json',
                'engine': 'prettier --check',
                'mode': 'format_check',
                'clean': True,
                'count': 0,
                'findings': [],
                'note': 'clean means parseable AND already prettier-formatted',
            }
        style_issue = 'Code style issues' in combined
        return {
            'language': 'json',
            'engine': 'prettier --check',
            'mode': 'format_check',
            'clean': False,
            'count': 1,
            'findings': [{'code': 'NotFormatted' if style_issue else 'ParseError',
                          'message': combined.strip()[:400]}],
        }
    _fail('unsupported_language',
          'lint unsupported for %r (python/javascript/json only)' % language)


# ---------------------------------------------------------------------------
# type.check
# ---------------------------------------------------------------------------
def _type_check(root, args):
    rel = validate_rel_path(args.get('path'))
    read_validated(root, rel)
    language = detect_language(rel, args.get('language'))
    if language == 'python':
        cfg = _write_fixed('/tmp/.hms-mypy-config.ini', _MYPY_CONFIG)
        argv = [_python_bin(), '-m', 'mypy', '--config-file', cfg,
                '--cache-dir', '/tmp/.hms-mypy-cache', './' + rel]
        rc, out, _err, timed = _run_child(argv, cwd=root, timeout=90.0)
        if timed:
            _fail('tool_timeout', 'mypy exceeded the time budget')
        errors = []
        for line in out.decode('utf-8', 'replace').splitlines():
            match = re.match(r'\A[^:]+:(\d+)(?::(\d+))?: error: (.*)\Z', line.strip())
            if match:
                errors.append({'line': int(match.group(1)), 'message': match.group(3)[:300]})
        return {
            'language': 'python',
            'engine': 'mypy (fixed isolated config)',
            'clean': rc == 0 and not errors,
            'errors': errors[:200],
            'error_count': len(errors),
            'raw_output': out.decode('utf-8', 'replace')[:2000],
        }
    if language == 'typescript':
        vendor_modules = os.path.join(VENDOR_NODE, 'node_modules')
        if not os.path.isdir(vendor_modules):
            _fail('tool_unavailable', 'vendor node_modules missing under %s' % VENDOR_NODE)
        tsc = _vendor_bin('tsc')
        cfg_fd, cfg_path = tempfile.mkstemp(prefix='.hms-tsconfig-', suffix='.json', dir='/tmp')
        _close(cfg_fd)
        try:
            _write_fixed(cfg_path, _TSCONFIG_TEMPLATE % {
                'vendor_modules': vendor_modules,
                'vendor_types': os.path.join(vendor_modules, '@types'),
                'entry': os.path.join(root, rel),
            })
            argv = [tsc, '-p', cfg_path]
            rc, out, _err, timed = _run_child(argv, cwd=root, timeout=90.0)
        finally:
            try:
                os.unlink(cfg_path)
            except OSError:
                pass
        if timed:
            _fail('tool_timeout', 'tsc exceeded the time budget')
        errors = []
        for line in out.decode('utf-8', 'replace').splitlines():
            match = re.match(r'\A[^(]+\((\d+),(\d+)\): error (TS\d+): (.*)\Z', line.strip())
            if match:
                errors.append({'line': int(match.group(1)), 'column': int(match.group(2)),
                               'code': match.group(3), 'message': match.group(4)[:300]})
        return {
            'language': 'typescript',
            'engine': 'tsc (fixed isolated config; vendor typeRoots/baseUrl)',
            'clean': rc == 0 and not errors,
            'errors': errors[:200],
            'error_count': len(errors),
            'raw_output': out.decode('utf-8', 'replace')[:2000],
        }
    if language == 'javascript':
        _fail('unsupported_language',
              'type.check does not support plain JavaScript (no types to check); '
              'use lint.run for a syntax check')
    _fail('unsupported_language', 'type.check unsupported for %r (python/typescript only)' % language)


# ---------------------------------------------------------------------------
# build.run
# ---------------------------------------------------------------------------
def _safe_artifact_name(rel):
    stem = os.path.splitext(os.path.basename(rel))[0]
    if not re.match(r'\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z', stem):
        _fail('invalid_path', 'cannot derive a safe artifact name from %s' % rel)
    return stem


def _finish_artifact(root, out_rel):
    # Stat first: an oversized artifact must be classified and REMOVED, not
    # tripped by the generic 512KiB read cap.
    fd = open_validated(root, out_rel, enforce_size=False)
    try:
        size = os.fstat(fd).st_size
    finally:
        _close(fd)
    if size > BUILD_ARTIFACT_MAX_BYTES:
        try:
            os.unlink(os.path.join(root, out_rel))
        except OSError:
            pass
        _fail('artifact_too_large', 'build artifact exceeds %d bytes' % BUILD_ARTIFACT_MAX_BYTES)
    data = read_validated(root, out_rel)
    return {'path': out_rel, 'bytes': len(data), 'sha256': sha256_hex(data)}


def _build(root, args):
    rel = validate_rel_path(args.get('path'))
    language = detect_language(rel, args.get('language'))
    check_workspace_bounds(root)
    if language == 'python':
        data = read_validated(root, rel)
        staging = tempfile.mkdtemp(prefix='.hms-zipapp-', dir='/tmp')
        out_rel = 'build/%s.pyz' % _safe_artifact_name(rel)
        try:
            with open(os.path.join(staging, '__main__.py'), 'wb') as fh:
                fh.write(data)
            _ensure_build_dir(root)
            argv = [_python_bin(), '-m', 'zipapp', staging, '-o', './' + out_rel,
                    '-p', '/usr/bin/env python3']
            rc, _out, err, timed = _run_child(argv, cwd=root, timeout=90.0)
            if timed:
                _fail('tool_timeout', 'zipapp exceeded the time budget')
            if rc != 0:
                _fail('build_failed', (err.decode('utf-8', 'replace').strip() or
                                       'zipapp exit %d' % rc)[:500])
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {'language': 'python', 'kind': 'zipapp',
                'artifact': _finish_artifact(root, out_rel)}
    if rel.lower().endswith(JS_FAMILY_EXTS):
        read_validated(root, rel)
        esbuild = _vendor_bin('esbuild')
        out_rel = 'build/%s.js' % _safe_artifact_name(rel)
        _ensure_build_dir(root)
        argv = [esbuild, './' + rel, '--bundle', '--minify', '--platform=node',
                '--format=cjs', '--log-level=warning', '--outfile=./' + out_rel]
        env = {'NODE_PATH': os.pathsep.join((os.path.join(DEPS_NODE, 'node_modules'), os.path.join(VENDOR_NODE, 'node_modules')))}
        rc, _out, err, timed = _run_child(argv, cwd=root, timeout=90.0, env=env)
        if timed:
            _fail('tool_timeout', 'esbuild exceeded the time budget')
        if rc != 0:
            _fail('build_failed', (err.decode('utf-8', 'replace').strip() or
                                   'esbuild exit %d' % rc)[:500])
        return {'language': language, 'kind': 'esbuild_bundle_minified',
                'artifact': _finish_artifact(root, out_rel)}
    _fail('unsupported_language', 'build unsupported for %r' % language)


# ---------------------------------------------------------------------------
# Workspace manifest (untrusted; strict validation)
# ---------------------------------------------------------------------------
def _validate_manifest_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {'packages'}:
        _fail('manifest_invalid', 'manifest must be an object with only "packages"')
    packages = payload.get('packages')
    if not isinstance(packages, list) or len(packages) > 64:
        _fail('manifest_invalid', 'manifest "packages" must be a list (<=64)')
    entries = []
    for item in packages:
        if not isinstance(item, dict) or set(item) - {'manager', 'name', 'version'}:
            _fail('manifest_invalid', 'each package must have exactly manager/name/version')
        manager, name, version = item.get('manager'), item.get('name'), item.get('version')
        if manager not in ('python', 'node'):
            _fail('manifest_invalid', 'manager must be python|node')
        if not isinstance(name, str) or not name or len(name) > 120:
            _fail('manifest_invalid', 'package name must be a short non-empty string')
        if not isinstance(version, str) or not _VERSION.match(version):
            _fail('manifest_invalid', 'package version invalid: %r' % (version,))
        entries.append({'manager': manager, 'name': name, 'version': version})
    return entries


def read_manifest_entries(root):
    """Returns (allowlisted_entries, rejected_entries) or (None, []) if absent."""
    try:
        data = read_validated(root, MANIFEST_NAME)
    except ToolError as exc:
        if exc.code == 'not_found':
            return None, []
        raise
    try:
        entries = _validate_manifest_payload(json.loads(data.decode('utf-8')))
    except (ValueError, UnicodeDecodeError):
        _fail('manifest_invalid', 'manifest is not valid JSON')
    out, rejected = [], []
    for entry in entries:
        if entry['version'] in PKG_ALLOWLIST.get((entry['manager'], entry['name']), frozenset()):
            out.append(entry)
        else:
            rejected.append(entry)
    return out, rejected


def _write_manifest(root, entries):
    data = (json.dumps({'packages': entries}, indent=2, sort_keys=True) + '\n').encode('utf-8')
    root_fd = _open_root_fd(root)
    try:
        tmp = '.hms-manifest-tmp-%d' % os.getpid()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0)
        try:
            tmp_fd = os.open(tmp, flags, 0o600, dir_fd=root_fd)
            try:
                os.write(tmp_fd, data)
            finally:
                _close(tmp_fd)
            os.rename(tmp, MANIFEST_NAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        except OSError as exc:
            try:
                os.unlink(tmp, dir_fd=root_fd)
            except OSError:
                pass
            _fail('os_error', 'cannot write manifest: %s' % exc.strerror)
    finally:
        _close(root_fd)


# ---------------------------------------------------------------------------
# pkg.list / pkg.install
# ---------------------------------------------------------------------------
def _py_dist_info_version(name, version=None):
    if not os.path.isdir(DEPS_PY):
        return None
    prefix = '%s-' % name
    if os.path.isdir(os.path.join(DEPS_PY, name)):
        return version or name
    for entry in os.listdir(DEPS_PY):
        if entry.startswith(prefix) and entry.endswith('.dist-info'):
            found = entry[len(prefix):-len('.dist-info')]
            if version is None or found == version:
                return found
    return None


def _node_installed_version(name):
    manifest = os.path.join(DEPS_NODE, 'node_modules', name, 'package.json')
    if not os.path.isfile(manifest):
        return None
    try:
        with open(manifest, 'rb') as fh:
            return json.loads(fh.read().decode('utf-8')).get('version')
    except (OSError, ValueError, UnicodeDecodeError):
        return 'unreadable'


def _pkg_list(root, args):
    manager = args.get('manager')
    if manager not in ('python', 'node'):
        _fail('invalid_argument', 'manager must be python|node')
    entries, rejected = read_manifest_entries(root)
    packages = []
    for entry in (entries or []):
        if entry['manager'] != manager:
            continue
        if manager == 'python':
            installed = _py_dist_info_version(entry['name'], entry['version'])
        else:
            installed = _node_installed_version(entry['name'])
            if installed is not None and installed != entry['version']:
                installed = 'mismatch:%s' % installed
        packages.append({
            'name': entry['name'],
            'version': entry['version'],
            'allowlisted': True,
            'installed': installed is not None,
            'installed_version': installed,
        })
    return {
        'manager': manager,
        'manifest_present': entries is not None,
        'packages': packages,
        'rejected_entries': rejected,
        'deps_dirs': {'python': DEPS_PY, 'node': DEPS_NODE},
        'note': 'installed reflects the per-job /tmp deps populated by prepare-env',
    }


def _allowlisted(manager, name, version):
    if manager not in ('python', 'node'):
        _fail('invalid_argument', 'manager must be python|node')
    if manager == 'python' and not _PY_NAME.match(name):
        _fail('invalid_argument', 'python package name must be a simple identifier')
    if manager == 'node' and not re.match(r'\A(@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]{1,100}\Z', name):
        _fail('invalid_argument', 'node package name invalid')
    if not isinstance(version, str) or not _VERSION.match(version):
        _fail('invalid_argument', 'version must be a short release string')
    if version not in PKG_ALLOWLIST.get((manager, name), frozenset()):
        _fail('not_allowlisted',
              'package %s==%s (%s) is not on the offline allowlist' % (name, version, manager))


def _cache_python_wheel(name, version):
    cache = os.path.join(PKG_CACHE, 'python')
    if not os.path.isdir(cache):
        _fail('cache_missing', 'offline python cache missing under %s' % PKG_CACHE)
    matches = [f for f in os.listdir(cache)
               if f.startswith('%s-%s-' % (name, version)) and f.endswith('.whl')]
    if len(matches) != 1:
        _fail('cache_missing', 'expected exactly one %s==%s wheel in %s, found %d'
              % (name, version, cache, len(matches)))
    return os.path.join(cache, matches[0])


def _cache_node_tarball(name, version):
    expected = os.path.join(PKG_CACHE, 'node', '%s-%s.tgz' % (name, version))
    if not os.path.isfile(expected):
        _fail('cache_missing', 'offline tarball missing: %s' % expected)
    return expected


def install_python_package(name, version, *, timeout=90.0):
    """Real offline pip install of one allowlisted wheel; returns details."""
    _allowlisted('python', name, version)
    wheel = _cache_python_wheel(name, version)
    try:
        os.makedirs(DEPS_PY, exist_ok=True)
    except OSError as exc:
        _fail('os_error', 'cannot create %s: %s' % (DEPS_PY, exc.strerror))
    argv = [_python_bin(), '-m', 'pip', 'install', '--no-index', '--no-deps',
            '--no-cache-dir', '--find-links', os.path.dirname(wheel),
            '--target', DEPS_PY, '%s==%s' % (name, version)]
    rc, out, err, timed = _run_child(argv, cwd='/tmp', timeout=timeout)
    if timed:
        _fail('tool_timeout', 'pip install exceeded the time budget')
    if rc != 0:
        _fail('install_failed', (err.decode('utf-8', 'replace').strip() or
                                 out.decode('utf-8', 'replace').strip() or
                                 'pip exit %d' % rc)[:500])
    if not _py_dist_info_version(name, version):
        _fail('install_failed', 'dist-info for %s==%s not found after install' % (name, version))
    return {'manager': 'python', 'name': name, 'version': version,
            'source': 'offline wheel cache', 'path': DEPS_PY}


def install_node_package(name, version, *, timeout=90.0):
    """Real offline npm install of one allowlisted tarball; returns details."""
    _allowlisted('node', name, version)
    tarball = _cache_node_tarball(name, version)
    npm = shutil.which('npm')
    if not npm:
        _fail('tool_unavailable', 'npm not found on PATH')
    try:
        os.makedirs(DEPS_NODE, exist_ok=True)
    except OSError as exc:
        _fail('os_error', 'cannot create %s: %s' % (DEPS_NODE, exc.strerror))
    argv = [npm, 'install', '--prefix', DEPS_NODE, '--no-save', '--offline',
            '--ignore-scripts', '--no-audit', '--no-fund', '--loglevel=error', tarball]
    rc, out, err, timed = _run_child(argv, cwd='/tmp', timeout=timeout,
                                     env={'npm_config_cache': '/tmp/.hms-npm-cache'})
    if timed:
        _fail('tool_timeout', 'npm install exceeded the time budget')
    if rc != 0:
        _fail('install_failed', (err.decode('utf-8', 'replace').strip() or
                                 out.decode('utf-8', 'replace').strip() or
                                 'npm exit %d' % rc)[:500])
    if _node_installed_version(name) != version:
        _fail('install_failed', 'installed version check failed for %s' % name)
    return {'manager': 'node', 'name': name, 'version': version,
            'source': 'offline tarball cache', 'path': os.path.join(DEPS_NODE, 'node_modules', name)}


def _pkg_install(root, args):
    manager, name, version = args.get('manager'), args.get('name'), args.get('version')
    _allowlisted(manager, name, version)
    # Validate the untrusted manifest BEFORE touching /tmp deps: a tampered
    # manifest must fail closed without performing any install.
    entries, rejected = read_manifest_entries(root)
    if entries is None:
        entries = []
    elif rejected:
        _fail('manifest_invalid',
              'existing manifest holds non-allowlisted entries: %s'
              % ', '.join('%s@%s' % (e['name'], e['version']) for e in rejected))
    if manager == 'python':
        installed = install_python_package(name, version)
    else:
        installed = install_node_package(name, version)
    entries = [e for e in entries if not (e['manager'] == manager and e['name'] == name)]
    entries.append({'manager': manager, 'name': name, 'version': version})
    entries.sort(key=lambda e: (e['manager'], e['name']))
    _write_manifest(root, entries)
    return {
        'installed': installed,
        'manifest_updated': True,
        'note': 'root runs prepare-env on the next exec so the module becomes '
                'importable across jobs',
    }


# ---------------------------------------------------------------------------
# test.single
# ---------------------------------------------------------------------------
def _static_python_test_exists(root, rel, name):
    """Zero-match guard without executing user code: AST of the target file.

    Limitation (documented): only tests whose class and method are defined in
    the target file are recognized; inherited-only test ids are reported as
    zero matches rather than run blindly.
    """
    if not rel.lower().endswith('.py'):
        _fail('invalid_path', 'python_unittest path must be a .py file')
    data = read_validated(root, rel)
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        _fail('binary_file', 'test file is not UTF-8')
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        _fail('invalid_source', 'test file does not parse as Python')
    module = rel[:-3].replace('/', '.')
    prefix = module + '.'
    if not name.startswith(prefix):
        _fail('invalid_argument',
              'name must be a qualified id starting with the module path %r' % prefix)
    rest = name[len(prefix):].split('.')
    if len(rest) < 2 or not all(_IDENTIFIER.match(part) for part in rest):
        _fail('invalid_argument', 'name must look like %s.Class.method' % prefix)
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    node = classes.get(rest[0])
    if node is None:
        return False
    for part in rest[1:-1]:
        inner = None
        for child in ast.walk(node):
            if isinstance(child, ast.ClassDef) and child.name == part:
                inner = child
                break
        if inner is None:
            return False
        node = inner
    method = rest[-1]
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method:
            return True
    return False


def _test_single_python(root, args):
    rel = validate_rel_path(args.get('path'))
    name = args.get('name')
    if not isinstance(name, str) or not _DOT_IDENT.match(name) or len(name) > 200:
        _fail('invalid_argument', 'name must be a dotted qualified test id')
    if not _static_python_test_exists(root, rel, name):
        _fail('test_not_found',
              'no test named %r found statically in %s (zero matches; nothing ran)'
              % (name, rel))
    argv = [_python_bin(), '-m', 'unittest', '-v', name]
    rc, out, err, timed = _run_child(argv, cwd=root, timeout=90.0)
    if timed:
        _fail('tool_timeout', 'unittest exceeded the time budget')
    combined = (out + err).decode('utf-8', 'replace')
    match = re.search(r'^Ran (\d+) test', combined, re.MULTILINE)
    ran = int(match.group(1)) if match else 0
    if ran == 0:
        _fail('test_not_found', 'qualified id %r resolved to zero tests (load error?)' % name)
    if ran != 1:
        _fail('not_single_test', 'qualified id %r ran %d tests; exactly 1 required' % (name, ran))
    return {
        'runner': 'python_unittest',
        'name': name,
        'matched': 1,
        'ran': ran,
        'passed': rc == 0 and 'OK' in combined,
        'exit_code': rc,
        'output': combined[-4000:],
    }


def _anchored_pattern(name):
    if not isinstance(name, str) or not _TEST_NAME.match(name) or name != name.strip():
        _fail('invalid_argument',
              'name must be a plain test name (no leading/trailing space, # or newline)')
    return '^%s$' % re.escape(name)


def _test_single_node(root, args):
    rel = validate_rel_path(args.get('path'))
    if not rel.lower().endswith(('.js', '.mjs', '.cjs')):
        _fail('unsupported_language', 'node_test runner supports .js/.mjs/.cjs files')
    name = args.get('name')
    pattern = _anchored_pattern(name)
    argv = [_node_bin(), '--test', '--test-name-pattern=' + pattern, './' + rel]
    rc, out, err, timed = _run_child(argv, cwd=root, timeout=90.0)
    if timed:
        _fail('tool_timeout', 'node --test exceeded the time budget')
    lines = out.decode('utf-8', 'replace').splitlines()
    point = re.compile(r'\A(not ok|ok) \d+ - %s(?:\s|#.*)?\Z' % re.escape(name))
    matched_ok = sum(1 for line in lines if line.startswith('ok') and point.match(line))
    matched_fail = sum(1 for line in lines if line.startswith('not ok') and point.match(line))
    total = matched_ok + matched_fail
    if total == 0:
        _fail('test_not_found', 'pattern %r matched zero tests in %s (nothing ran)'
              % (pattern, rel))
    if total != 1:
        _fail('not_single_test', 'pattern %r matched %d tests; exactly 1 required'
              % (pattern, total))
    return {
        'runner': 'node_test',
        'name': name,
        'pattern': pattern,
        'matched': total,
        'ran': total,
        'passed': matched_ok == 1 and rc == 0,
        'exit_code': rc,
        'output': (out + err).decode('utf-8', 'replace')[-4000:],
    }


def _test_single(root, args):
    runner = args.get('runner')
    if runner == 'python_unittest':
        return _test_single_python(root, args)
    if runner == 'node_test':
        return _test_single_node(root, args)
    _fail('invalid_argument', 'runner must be python_unittest|node_test')


# ---------------------------------------------------------------------------
# test.coverage
# ---------------------------------------------------------------------------
def _coverage_python(root, args):
    patharg = validate_rel_path(args.get('path', '.'), allow_root_dot=True)
    data_file = '/tmp/.hms-coverage-data'
    json_file = '/tmp/.hms-coverage-json'
    for stale in (data_file, json_file):
        try:
            os.unlink(stale)
        except OSError:
            pass
    argv = [_python_bin(), '-m', 'coverage', 'run', '--data-file=' + data_file,
            '--source=.', '-m', 'unittest', 'discover',
            '-s', os.path.join(root, patharg), '-t', os.path.abspath(root)]
    rc, out, err, timed = _run_child(argv, cwd=root, timeout=120.0)
    if timed:
        _fail('tool_timeout', 'coverage run exceeded the time budget')
    combined = (out + err).decode('utf-8', 'replace')
    match = re.search(r'^Ran (\d+) test', combined, re.MULTILINE)
    test_count = int(match.group(1)) if match else 0
    if test_count == 0:
        _fail('no_tests_found', 'unittest discovered 0 tests under %s' % patharg)
    totals = None
    files = {}
    warning = None
    if os.path.isfile(data_file):
        rc2, _o, e2, t2 = _run_child(
            [_python_bin(), '-m', 'coverage', 'json', '--data-file=' + data_file,
             '-o', json_file], cwd=root, timeout=60.0)
        if t2:
            warning = 'coverage json timed out'
        elif rc2 != 0:
            warning = 'coverage json failed: %s' % e2.decode('utf-8', 'replace')[:200]
        else:
            try:
                with open(json_file, 'rb') as fh:
                    blob = fh.read(MAX_FILE_BYTES + 1)
                if len(blob) > MAX_FILE_BYTES:
                    warning = 'coverage JSON too large to report'
                else:
                    payload = json.loads(blob.decode('utf-8'))
                    totals = payload.get('totals')
                    root_real = os.path.realpath(root)
                    for fname, info in list((payload.get('files') or {}).items())[:256]:
                        # coverage reports absolute paths; report workspace-relative
                        real = os.path.realpath(fname if os.path.isabs(fname) else os.path.join(root, fname))
                        if real.startswith(root_real + os.sep):
                            key = os.path.relpath(real, root_real)
                        else:
                            key = fname
                        files[key] = {'summary': (info or {}).get('summary')}
            except (OSError, ValueError, UnicodeDecodeError) as exc:
                warning = 'coverage JSON unreadable: %s' % exc
    else:
        warning = 'coverage produced no data file (nothing measured)'
    result = {
        'runner': 'python_unittest',
        'discovery_path': patharg,
        'test_count': test_count,
        'passed': rc == 0,
        'exit_code': rc,
        'coverage_source': 'coverage.py JSON (real run)',
        'totals': totals,
        'files': files,
        'output': combined[-4000:],
    }
    warnings = [warning] if warning else []
    return result, warnings


def _coverage_node(root, args):
    patharg = validate_rel_path(args.get('path', '.'), allow_root_dot=True)
    argv = [_node_bin(), '--test', '--experimental-test-coverage', '--test-reporter=tap']
    target=os.path.join(root,patharg)
    if os.path.isdir(target):
        candidates=[]
        for folder,dirs,names in os.walk(target,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in _SKIP_DIRS and not _denied_name(d) and not os.path.islink(os.path.join(folder,d)))
            for name in sorted(names):
                rel=os.path.relpath(os.path.join(folder,name),root)
                if name.endswith(('.js','.mjs','.cjs')) and (name.startswith('test') or re.search(r'\.test\.[cm]?js$',name) or 'test' in rel.split('/')[:-1]):
                    rel=validate_rel_path(rel);read_validated(root,rel);candidates.append('./'+rel)
                    if len(candidates)>MAX_FILES:_fail('workspace_bounds','too many Node test files')
        if not candidates:_fail('no_tests_found','no Node tests under '+patharg)
        argv.extend(sorted(candidates))
    else:
        read_validated(root,patharg);argv.append('./'+patharg)
    rc, out, err, timed = _run_child(argv, cwd=root, timeout=120.0)
    if timed:
        _fail('tool_timeout', 'node --test exceeded the time budget')
    text = out.decode('utf-8', 'replace')
    counts = {}
    for key in ('tests', 'pass', 'fail', 'skipped'):
        match = re.search(r'^# %s (\d+)' % key, text, re.MULTILINE)
        counts[key] = int(match.group(1)) if match else 0
    if counts['tests'] == 0:
        _fail('no_tests_found', 'node --test found 0 tests under %s' % patharg)
    totals = None
    files = []
    # Node 22 TAP coverage table (verified shape):
    #   # file      | line % | branch % | funcs % | uncovered lines
    #   # all files | 100.00 |   100.00 |  100.00 |
    for line in text.splitlines():
        if not line.startswith('#') or '|' not in line:
            continue
        cells = [c.strip() for c in line[1:].split('|')]
        if len(cells) < 4 or not re.match(r'\A[\d.]+\Z', cells[1] or 'x'):
            continue
        label = cells[0]
        row = {'lines': cells[1], 'branch': cells[2], 'funcs': cells[3]}
        if label == 'all files':
            totals = row
        elif label and label != 'file':
            files.append({'file': label, **row})
    warnings = []
    if totals is None:
        warnings.append('coverage table not found in node output')
    result = {
        'runner': 'node_test',
        'discovery_path': patharg,
        'test_count': counts['tests'],
        'passed': rc == 0 and counts['fail'] == 0,
        'exit_code': rc,
        'counts': counts,
        'coverage_source': 'node --experimental-test-coverage (real run)',
        'totals': totals,
        'files': files[:64],
        'output': text[-4000:],
    }
    return result, warnings


def _test_coverage(root, args):
    runner = args.get('runner')
    if runner == 'python_unittest':
        return _coverage_python(root, args)
    if runner == 'node_test':
        return _coverage_node(root, args)
    _fail('invalid_argument', 'runner must be python_unittest|node_test')


# ---------------------------------------------------------------------------
# rope refactor engine (code.rename / refactor.rename / refactor.extract)
# ---------------------------------------------------------------------------
def _rope_project(root):
    try:
        from rope.base.project import Project
    except ImportError:
        _fail('tool_unavailable', 'rope is not installed in this image')
    # ropefolder=None keeps rope from creating .ropeproject/ inside the workspace.
    return Project(root, ropefolder=None)


def _offset_from_line_col(text, line, column):
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        _fail('invalid_argument', 'line must be an integer >= 1 (1-based)')
    if isinstance(column, bool) or not isinstance(column, int) or column < 0:
        _fail('invalid_argument', 'column must be an integer >= 0 (0-based)')
    lines = text.splitlines(keepends=True)
    if line > len(lines):
        _fail('invalid_position', 'line %d beyond file (%d lines)' % (line, len(lines)))
    offset = sum(len(entry) for entry in lines[:line - 1])
    content = lines[line - 1].rstrip('\r\n')
    if column > len(content):
        _fail('invalid_position', 'column %d beyond line %d length' % (column, line))
    return offset + column, content


def _token_at(content, column):
    """Return the identifier that CONTAINS the 0-based column, not one that
    merely starts there (rope accepts any offset inside the token)."""
    best = None
    for match in re.finditer(r'[A-Za-z_][A-Za-z0-9_]*', content):
        if match.start() <= column < match.end():
            best = match.group(0)
            break
        if match.start() > column:
            break
    return best


def _changed_rels(changes, root):
    rels = []
    root_real = os.path.realpath(root)
    for resource in changes.get_changed_resources():
        real = os.path.realpath(resource.real_path)
        if real.startswith(root_real + os.sep):
            rels.append(os.path.relpath(real, root_real))
        else:
            rels.append(real)
    return sorted(set(rels))


def _rename(root, args):
    rel = validate_rel_path(args.get('path'))
    expected = _expected_hash(args.get('expected_sha256'))
    data = read_validated(root, rel)
    _check_expected(expected, data)
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        _fail('binary_file', 'refactor target is not UTF-8')
    new_name = args.get('new_name')
    if not isinstance(new_name, str) or not _IDENTIFIER.match(new_name):
        _fail('invalid_argument', 'new_name must be a Python identifier (<=64 chars)')
    offset, content = _offset_from_line_col(text, args.get('line'), args.get('column'))
    old_token = _token_at(content, args.get('column'))
    if not old_token:
        _fail('invalid_position', 'no identifier at the given position')
    project = _rope_project(root)
    try:
        from rope.refactor.rename import Rename
        from rope.base.exceptions import RopeError
        try:
            resource = project.get_resource(rel)
        except Exception:
            _fail('not_found', 'rope cannot open %s' % rel)
        try:
            changes = Rename(project, resource, offset).get_changes(new_name)
        except RopeError as exc:
            _fail('rename_failed', 'rope rejected the rename: %s' % str(exc)[:300])
        except Exception as exc:
            _fail('rename_failed', 'rename could not resolve the name: %s' % str(exc)[:300])
        changes.do()
        changed = _changed_rels(changes, root)
    finally:
        try:
            project.close()
        except Exception:
            pass
    after = read_validated(root, rel)
    return {
        'path': rel,
        'old_name': old_token,
        'new_name': new_name,
        'changed_files': changed,
        'sha256': sha256_hex(after),
        'engine': 'rope (scoped, cross-file)',
    }


def _extract(root, args):
    rel = validate_rel_path(args.get('path'))
    expected = _expected_hash(args.get('expected_sha256'))
    data = read_validated(root, rel)
    _check_expected(expected, data)
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        _fail('binary_file', 'refactor target is not UTF-8')
    name = args.get('name')
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        _fail('invalid_argument', 'name must be a Python identifier (<=64 chars)')
    start_line, end_line = args.get('start_line'), args.get('end_line')
    for value, label in ((start_line, 'start_line'), (end_line, 'end_line')):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail('invalid_argument', '%s must be an integer >= 1' % label)
    if end_line < start_line:
        _fail('invalid_argument', 'end_line must be >= start_line')
    lines = text.splitlines(keepends=True)
    if end_line > len(lines):
        _fail('invalid_position', 'end_line %d beyond file (%d lines)' % (end_line, len(lines)))
    start_offset = sum(len(entry) for entry in lines[:start_line - 1])
    end_offset = start_offset + sum(len(entry) for entry in lines[start_line - 1:end_line])
    project = _rope_project(root)
    try:
        from rope.refactor.extract import ExtractMethod
        from rope.base.exceptions import RopeError
        try:
            resource = project.get_resource(rel)
        except Exception:
            _fail('not_found', 'rope cannot open %s' % rel)
        try:
            changes = ExtractMethod(project, resource, start_offset, end_offset).get_changes(name)
        except RopeError as exc:
            _fail('extract_failed', 'rope rejected the extraction: %s' % str(exc)[:300])
        except Exception as exc:
            _fail('extract_failed', 'extraction failed: %s' % str(exc)[:300])
        changes.do()
        changed = _changed_rels(changes, root)
    finally:
        try:
            project.close()
        except Exception:
            pass
    after = read_validated(root, rel)
    return {
        'path': rel,
        'method': name,
        'lines': [start_line, end_line],
        'changed_files': changed,
        'sha256': sha256_hex(after),
        'engine': 'rope ExtractMethod',
    }


# ---------------------------------------------------------------------------
# refactor.dead_code
# ---------------------------------------------------------------------------
def _dead_code(root, args):
    rel = validate_rel_path(args.get('path'))
    apply_fix = args.get('apply', False)
    if not isinstance(apply_fix, bool):
        _fail('invalid_argument', 'apply must be a boolean')
    expected = _expected_hash(args.get('expected_sha256'))
    before = read_validated(root, rel)
    sha_before = sha256_hex(before)
    _check_expected(expected, before)
    ruff = _ruff_bin()

    def _scan():
        argv = [ruff, 'check', '--isolated', '--no-cache', '--select', 'F401',
                '--output-format', 'json', './' + rel]
        rc, out, err, timed = _run_child(argv, cwd=root)
        if timed:
            _fail('tool_timeout', 'ruff exceeded the time budget')
        if rc not in (0, 1):
            _fail('lint_failed', (err.decode('utf-8', 'replace').strip() or
                                  'ruff exit %d' % rc)[:500])
        try:
            raw = json.loads(out.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            _fail('lint_failed', 'ruff produced unreadable JSON output')
        findings = []
        for item in raw[:200]:
            loc = item.get('location') or {}
            findings.append({'code': item.get('code'), 'line': loc.get('row'),
                             'column': loc.get('column'),
                             'message': str(item.get('message', ''))[:300]})
        return findings

    findings = _scan()
    applied = False
    removed = 0
    if apply_fix:
        if expected is None:
            _fail('expected_sha256_required', 'apply=true requires expected_sha256')
        before_count = len(findings)
        argv = [ruff, 'check', '--isolated', '--no-cache', '--select', 'F401',
                '--fix', './' + rel]
        rc, _out, err, timed = _run_child(argv, cwd=root)
        if timed:
            _fail('tool_timeout', 'ruff fix exceeded the time budget')
        if rc not in (0, 1):
            _fail('lint_failed', (err.decode('utf-8', 'replace').strip() or
                                  'ruff fix exit %d' % rc)[:500])
        findings = _scan()
        applied = True
        removed = max(0, before_count - len(findings))
    after = read_validated(root, rel)
    sha_after = sha256_hex(after)
    return {
        'path': rel,
        'applied': applied,
        'removed_count': removed,
        'remaining_count': len(findings),
        'findings': findings,
        'sha256_before': sha_before,
        'sha256': sha_after,
        'changed': sha_before != sha_after,
        'coverage': 'unused imports only (ruff F401, safe fixes)',
        'note': 'dead-code report is limited to statically unused imports; '
                'dynamically reachable code is NOT claimed removed. '
                'apply=true changes go through the normal root approval/publish flow.',
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_FORMAT = (('path',), ('language', 'expected_sha256'))
_RENAME = (('path', 'line', 'column', 'new_name'), ('expected_sha256',))

SKILL_ARGS = {
    'code.format': _FORMAT,
    'format.run': _FORMAT,
    'lint.run': (('path',), ('language',)),
    'type.check': (('path',), ('language',)),
    'build.run': (('path',), ('language',)),
    'pkg.list': (('manager',), ()),
    'pkg.install': (('manager', 'name', 'version'), ()),
    'test.single': (('runner', 'path', 'name'), ()),
    'test.coverage': (('runner',), ('path',)),
    'code.rename': _RENAME,
    'refactor.rename': _RENAME,
    'refactor.extract': (('path', 'start_line', 'end_line', 'name'), ('expected_sha256',)),
    'refactor.dead_code': (('path',), ('apply', 'expected_sha256')),
}

_HANDLERS = {
    'code.format': _format,
    'format.run': _format,
    'lint.run': _lint,
    'type.check': _type_check,
    'build.run': _build,
    'pkg.list': _pkg_list,
    'pkg.install': _pkg_install,
    'test.single': _test_single,
    'test.coverage': _test_coverage,
    'code.rename': _rename,
    'refactor.rename': _rename,
    'refactor.extract': _extract,
    'refactor.dead_code': _dead_code,
}


def run(skill_id, args, root=DEFAULT_ROOT):
    """Trusted entry point. Never raises; returns a bounded result envelope."""
    envelope = {'ok': False, 'skill_id': skill_id if isinstance(skill_id, str) else None,
                'result': None, 'error': None, 'warnings': [], 'degraded': False}
    try:
        if not isinstance(skill_id, str) or skill_id not in SKILL_IDS:
            _fail('unknown_skill', 'unsupported skill_id: %r' % (skill_id,))
        envelope['skill_id'] = skill_id
        if args is None:
            args = {}
        if not isinstance(args, dict):
            _fail('invalid_arguments', 'args must be an object')
        required, optional = SKILL_ARGS[skill_id]
        allowed = set(required) | set(optional)
        unknown = sorted(key for key in args if key not in allowed)
        if unknown:
            _fail('unknown_argument', 'unsupported arguments: %s' % ', '.join(unknown))
        missing = [key for key in required if key not in args]
        if missing:
            _fail('missing_argument', 'required arguments: %s' % ', '.join(missing))
        if not os.path.isdir(root):
            _fail('invalid_root', 'workspace root is not a directory: %s' % root)
        outcome = _HANDLERS[skill_id](root, args)
        if isinstance(outcome, tuple):
            result, warnings = outcome
        else:
            result, warnings = outcome, []
        envelope['ok'] = True
        envelope['result'] = result
        envelope['warnings'] = warnings
    except ToolError as exc:
        envelope['error'] = exc.as_dict()
    except RecursionError:
        envelope['error'] = {'code': 'analysis_limit', 'message': 'input too deeply nested'}
    except (OSError, ValueError) as exc:
        envelope['error'] = {'code': 'internal_error', 'message': str(exc)[:300]}
    except Exception as exc:
        envelope['error'] = {'code': 'internal_error',
                             'message': '%s: %s' % (type(exc).__name__, str(exc)[:300])}
    return envelope


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _emit(envelope):
    text = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    if len(text) > RESULT_LIMIT:
        envelope = {'ok': envelope.get('ok', False), 'skill_id': envelope.get('skill_id'),
                    'result': None,
                    'error': {'code': 'result_too_large',
                              'message': 'result exceeded %d bytes' % RESULT_LIMIT},
                    'warnings': [], 'degraded': False}
        text = json.dumps(envelope, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Optional trusted wiring flag (used by tests and local runs). The root
    # service invokes the two-argument contract form; the sandbox default is
    # always /workspace. This is deployment wiring, never a job argument.
    root = DEFAULT_ROOT
    if '--root' in argv:
        index = argv.index('--root')
        if index + 1 >= len(argv):
            _emit({'ok': False, 'skill_id': None, 'result': None,
                   'error': {'code': 'usage', 'message': '--root requires a value'},
                   'warnings': [], 'degraded': False})
            return 2
        root = argv[index + 1]
        argv = argv[:index] + argv[index + 2:]
    if argv and argv[0] == 'list':
        _emit({'ok': True, 'skill_id': None,
               'result': {'skills': sorted(SKILL_IDS),
                          'allowlist': {'%s:%s' % k: sorted(v)
                                        for k, v in sorted(PKG_ALLOWLIST.items())}},
               'error': None, 'warnings': [], 'degraded': False})
        return 0
    if len(argv) != 3 or argv[0] != 'run':
        _emit({'ok': False, 'skill_id': None, 'result': None,
               'error': {'code': 'usage',
                         'message': 'usage: phase2_tools.py run <skill_id> <base64JSONargs> | list'},
               'warnings': [], 'degraded': False})
        return 2
    skill_id, b64_args = argv[1], argv[2]
    try:
        raw = base64.b64decode(b64_args.encode('ascii'), validate=True)
        args = json.loads(raw.decode('utf-8'))
    except (binascii.Error, ValueError, UnicodeDecodeError, UnicodeError):
        _emit({'ok': False, 'skill_id': skill_id, 'result': None,
               'error': {'code': 'invalid_encoding',
                         'message': 'args must be base64 of a UTF-8 JSON object'},
               'warnings': [], 'degraded': False})
        return 2
    envelope = run(skill_id, args, root=root)
    _emit(envelope)
    return 0 if envelope['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
