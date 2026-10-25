"""Harmonika coding MVP: workspace filesystem and code operations.

Owner: Claude worker. Scope: the ten filesystem/code skills of the coding MVP plus
the snapshot transport used to hand a workspace to the sandbox and take it back.

Trust model
-----------
`root` is a fixed, existing, canonical project directory chosen by a trusted caller
(root server). Everything below it is untrusted: paths, file contents, snapshot keys
and the layout itself may be attacker-shaped. Therefore every path is resolved by
descending real directory descriptors with ``O_NOFOLLOW``; we never trust a
``Path.resolve()`` check followed by a plain ``open()``. Writes are atomic
(temp file in the destination directory, then ``linkat``/``renameat``). There is no
unrestricted delete operation; the only way to remove a file is `replace_snapshot`,
which publishes a whole validated file set.

Documented deviations / refinements (helper API only; `execute`, `snapshot` and
`replace_snapshot` signatures are as specified):

1. Errors raise `FileStoreError` (with `.code` and `.as_dict()`) instead of being
   encoded into the result dict, so a partial result can never be mistaken for a
   success by the root REST layer.
2. `execute` rejects unknown argument keys. A typo in a security-relevant flag
   (``overwrite``, ``expected_sha256``, ``literal``) must fail loudly, not default.
3. `snapshot()` returns exactly the specified mapping; `snapshot_report()` is added
   to expose `count`, `total_bytes` and the `warnings` naming everything the
   transport left behind, for callers that need them.
4. `fs.write` / `code.write` create missing parent directories (race-safe, bounded
   depth). The MVP has no mkdir skill and a coding agent must be able to add a file
   in a new package directory.
5. `fs.write` with ``overwrite=True`` requires `expected_sha256`; replacing content
   without stating what you expected to replace is refused.
6. Credential/private matching is deliberately conservative and has no override: a
   path segment containing ``secret``, ``credential``, ``passwd`` or ``password`` is
   refused even when it is ordinary source (``password_rules.py``). Leaking a key is
   worse than refusing a file. Public ``.github`` config is allowed; ``.git`` is not.
7. `code.search`, `code.grep` and `snapshot` skip regenerable vendor/cache
   directories (``node_modules``, ``__pycache__``, ``.venv``, ...) and report them in
   `warnings`. Explicit navigation (`fs.list`, `fs.tree`, `fs.glob`) does not skip
   them. Without this the 256-file bound is spent before reaching any source.
8. `fs.glob` returns regular files only, as deterministic sorted relative strings.
9. `code.grep` always runs the match loop in an isolated ``python3 -I`` subprocess
   with a sub-2s deadline, for literal and regex alike. RE2 is not assumed present.
10. `replace_snapshot` preserves, by default, the entries `snapshot()` cannot carry
    (credential files, symlinks and other non-regular files, vendor/cache directories,
    hardlinked and oversized files). Without this, every sandbox round trip would
    delete the workspace's `.env`, `node_modules` and `.git`. Empty directories are
    not representable in a file mapping and are not preserved.
11. `replace_snapshot` publishes by swapping a staged sibling directory into `root`.
    The swap is two renames (POSIX cannot rename over a non-empty directory and
    macOS exposes no atomic exchange to Python); the caller holds the workspace
    lock, the window contains no partial file set, and a failed second rename is
    rolled back and reported as `publish_failed`.
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
import stat as statmod
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MAX_BYTES = 16 * 1024 * 1024
MAX_FILES = 256
MAX_FILE_BYTES = 512 * 1024
MAX_PATH_LEN = 240
MAX_SEGMENT_LEN = 255
MAX_SEGMENTS = 32
MAX_TREE_DEPTH = 6
DEFAULT_TREE_DEPTH = 3
MAX_GLOB_DEPTH = 24
GLOB_BUDGET = 4096
GREP_MAX_MATCHES = 200
GREP_MAX_OUTPUT = 512 * 1024
GREP_SCAN_BYTES = 8 * 1024 * 1024
GREP_DEADLINE = 1.8
GREP_MAX_PATTERN = 512
SEARCH_MAX_MATCHES = 200
MAX_MKDIR_DEPTH = 8
MAX_PRESERVED = 512

_DENY_NAMES = frozenset((
    '.git', '.ssh', '.gnupg', '.aws', '.azure', '.gcloud', '.kube', '.docker',
    '.netrc', '_netrc', '.npmrc', '.pypirc', '.git-credentials', '.htpasswd',
    '.pgpass', '.my.cnf', '.terraformrc', '.vault-token', '.env',
    'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials', 'secrets',
))
_DENY_GLOBS = (
    '.env*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', '*.ppk',
    'id_rsa*', 'id_dsa*', 'id_ecdsa*', 'id_ed25519*', '*_rsa', '*_ed25519',
)
_DENY_SUBSTRINGS = ('secret', 'credential', 'passwd', 'password')
_SKIP_DIRS = frozenset((
    'node_modules', '__pycache__', '.venv', 'venv', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', '.tox', '.next', '.nuxt', '.gradle', '.terraform',
))
_PY_EXT = ('.py', '.pyi')
_BEST_EFFORT_EXT = ('.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.go')
_TEXT_EXT = _PY_EXT + _BEST_EFFORT_EXT + (
    '.json', '.toml', '.yaml', '.yml', '.md', '.txt', '.cfg', '.ini', '.sh',
    '.html', '.css', '.scss', '.sql', '.rs', '.java', '.rb', '.c', '.h', '.cpp',
    '.hpp', '.cjs', '.env.example', '.proto', '.graphql', '.lock',
)
_TMP_PREFIX = '.hms-tmp-'
_STAGE_PREFIX = '.hms-stage-'
_RETIRED_PREFIX = '.hms-retired-'
_HEX64 = re.compile(r'\A[0-9a-f]{64}\Z')
_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK so that a FIFO or device planted in the workspace cannot park the
# runtime inside open(); the fstat guard then refuses it as a special file.
_OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

_GREP_WORKER = r'''
import json, re, sys
req = json.load(sys.stdin)
pattern, literal = req['pattern'], req['literal']
cap, out_cap = req['max_matches'], req['max_output']
try:
    rx = re.compile(re.escape(pattern) if literal else pattern)
except (re.error, RecursionError, OverflowError) as exc:
    json.dump({'error': 'invalid_pattern', 'detail': str(exc)[:200]}, sys.stdout)
    sys.exit(0)
matches, size, truncated = [], 0, False
for rel, text in req['files']:
    for number, line in enumerate(text.split('\n'), 1):
        found = rx.search(line)
        if found is None:
            continue
        snippet = line[:400]
        if len(matches) >= cap or size + len(snippet) + len(rel) + 24 > out_cap:
            truncated = True
            break
        size += len(snippet) + len(rel) + 24
        matches.append({'path': rel, 'line': number, 'column': found.start() + 1, 'text': snippet})
    if truncated:
        break
json.dump({'matches': matches, 'truncated': truncated}, sys.stdout)
'''

_scratch: List[str] = []


def _scratch_dir() -> str:
    """Empty 0700 directory used as cwd for the grep child so nothing is plantable."""
    if not _scratch:
        _scratch.append(tempfile.mkdtemp(prefix='hms-grep-'))
        os.chmod(_scratch[0], 0o700)
    return _scratch[0]


class FileStoreError(Exception):
    def __init__(self, code: str, message: str = '', path: Optional[str] = None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.path = path

    def as_dict(self) -> Dict[str, Any]:
        out = {'error': self.code, 'message': self.message}
        if self.path is not None:
            out['path'] = self.path
        return out


def _fail(code: str, message: str = '', path: Optional[str] = None) -> 'FileStoreError':
    return FileStoreError(code, message, path)


def _denied_name(name: str) -> bool:
    low = name.lower()
    if low in _DENY_NAMES:
        return True
    if any(fnmatch.fnmatchcase(low, pattern) for pattern in _DENY_GLOBS):
        return True
    return any(token in low for token in _DENY_SUBSTRINGS)


def _segments(path: Any, *, allow_git: bool = False, what: str = 'path') -> List[str]:
    """Validate an untrusted relative path and return its segments."""
    if not isinstance(path, str):
        raise _fail('invalid_path', '%s must be a string' % what)
    if path == '' or path == '.':
        return []
    if len(path) > MAX_PATH_LEN:
        raise _fail('path_too_long', '%s exceeds %d characters' % (what, MAX_PATH_LEN), path)
    for char in path:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            raise _fail('control_character', 'control character in %s' % what, path)
    if '\x00' in path:
        raise _fail('control_character', 'NUL in %s' % what, path)
    if path.startswith('/') or path == '~' or path.startswith('~/') or re.match(r'\A[A-Za-z]:', path):
        raise _fail('absolute_path', '%s must be relative' % what, path)
    if '\\' in path:
        raise _fail('invalid_path', 'backslash is not a path separator here', path)
    raw = [part for part in path.split('/') if part != '']
    segments: List[str] = []
    for part in raw:
        if part == '.':
            continue
        if part == '..':
            raise _fail('traversal', 'parent traversal is refused', path)
        if len(part) > MAX_SEGMENT_LEN:
            raise _fail('path_too_long', 'segment exceeds %d characters' % MAX_SEGMENT_LEN, path)
        if part.startswith(_TMP_PREFIX):
            raise _fail('reserved_name', 'reserved internal prefix', path)
        if _denied_name(part) and not (allow_git and part.lower() == '.git'):
            raise _fail('credential_path', 'credential or private path is refused', path)
        segments.append(part)
    if not segments:
        return []
    if len(segments) > MAX_SEGMENTS:
        raise _fail('path_too_deep', 'more than %d segments' % MAX_SEGMENTS, path)
    return segments


def _rel(segments: Sequence[str]) -> str:
    return '/'.join(segments) if segments else '.'


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_hash(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX64.match(value.strip().lower()):
        raise _fail('invalid_hash', 'expected_sha256 must be 64 hex characters')
    return value.strip().lower()


def _decode(data: bytes, rel: str) -> str:
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        raise _fail('binary_file', 'file is not valid UTF-8 text', rel)


def _close(fd: Optional[int]) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _component_error(exc: OSError, dfd: int, name: str, rel: str) -> FileStoreError:
    """Name the real reason a component open failed.

    Linux reports ELOOP for ``open(symlink, O_DIRECTORY|O_NOFOLLOW)`` while macOS
    reports ENOTDIR; both refuse the traversal, so ask the inode which it was and
    return the accurate code.
    """
    if exc.errno in (errno.ENOTDIR, errno.ELOOP):
        try:
            info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
        except OSError:
            info = None
        if info is not None and statmod.S_ISLNK(info.st_mode):
            return _fail('symlink_rejected', 'symbolic link in path is refused', rel)
    return _translate(exc, rel)


def _translate(exc: OSError, rel: str) -> FileStoreError:
    if exc.errno in (errno.ELOOP, errno.EMLINK):
        return _fail('symlink_rejected', 'symbolic link in path is refused', rel)
    if exc.errno == errno.ENOENT:
        return _fail('not_found', 'path does not exist', rel)
    if exc.errno == errno.ENOTDIR:
        return _fail('not_a_directory', 'path component is not a directory', rel)
    if exc.errno == errno.EISDIR:
        return _fail('is_a_directory', 'path is a directory', rel)
    if exc.errno == errno.EEXIST:
        return _fail('exists', 'path already exists', rel)
    if exc.errno in (errno.EACCES, errno.EPERM):
        return _fail('permission_denied', 'permission denied', rel)
    if exc.errno == errno.ENAMETOOLONG:
        return _fail('path_too_long', 'path too long', rel)
    return _fail('os_error', '%s: %s' % (errno.errorcode.get(exc.errno, exc.errno), exc.strerror), rel)


class FileStore:
    def __init__(self, root: Path, max_bytes: int = MAX_BYTES, max_files: int = MAX_FILES,
                 max_file_bytes: int = MAX_FILE_BYTES):
        root = Path(root)
        if not root.is_absolute():
            raise _fail('invalid_root', 'root must be an absolute path')
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise _translate(exc, str(root))
        if not resolved.is_dir():
            raise _fail('invalid_root', 'root must be an existing directory')
        self.root = resolved
        self.max_bytes = int(max_bytes)
        self.max_files = int(max_files)
        self.max_file_bytes = int(max_file_bytes)
        if min(self.max_bytes, self.max_files, self.max_file_bytes) <= 0:
            raise _fail('invalid_root', 'limits must be positive')

    # --- descriptor traversal -------------------------------------------------

    def _root_fd(self) -> int:
        """Re-opened per operation: `replace_snapshot` may have swapped the inode."""
        try:
            return os.open(str(self.root), _OPEN_DIR)
        except OSError as exc:
            raise _translate(exc, '.')

    def _open_dir(self, segments: Sequence[str], base: Optional[int] = None) -> int:
        fd = self._root_fd() if base is None else base
        owned = base is None
        for index, name in enumerate(segments):
            try:
                child = os.open(name, _OPEN_DIR, dir_fd=fd)
            except OSError as exc:
                raise _component_error(exc, fd, name, _rel(segments[:index + 1]))
            finally:
                if owned or index:
                    _close(fd)
            fd, owned = child, True
        return fd

    def _open_file(self, segments: Sequence[str]) -> Tuple[int, os.stat_result]:
        if not segments:
            raise _fail('is_a_directory', 'path is the workspace root', '.')
        rel = _rel(segments)
        parent = self._open_dir(segments[:-1])
        try:
            fd = os.open(segments[-1], _OPEN_FILE, dir_fd=parent)
        except OSError as exc:
            raise _translate(exc, rel)
        finally:
            _close(parent)
        try:
            info = os.fstat(fd)
            self._guard_regular(info, rel)
        except Exception:
            _close(fd)
            raise
        return fd, info

    def _guard_regular(self, info: os.stat_result, rel: str) -> None:
        if statmod.S_ISDIR(info.st_mode):
            raise _fail('is_a_directory', 'path is a directory', rel)
        if not statmod.S_ISREG(info.st_mode):
            raise _fail('special_file', 'not a regular file', rel)
        if info.st_nlink > 1:
            raise _fail('hardlinked_file', 'file has more than one link', rel)

    def _read_file(self, segments: Sequence[str], limit: Optional[int] = None) -> Tuple[bytes, os.stat_result]:
        rel = _rel(segments)
        cap = self.max_file_bytes if limit is None else limit
        fd, info = self._open_file(segments)
        try:
            if info.st_size > cap:
                raise _fail('file_too_large', 'file exceeds %d bytes' % cap, rel)
            data = b''
            while len(data) <= cap:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                data += chunk
            if len(data) > cap:
                raise _fail('file_too_large', 'file exceeds %d bytes' % cap, rel)
        finally:
            _close(fd)
        return data, info

    def _entries(self, dfd: int, rel: str) -> List[Tuple[str, str, int, int]]:
        """(name, kind, size, nlink) for one directory; kind in file/dir/other."""
        out: List[Tuple[str, str, int, int]] = []
        with os.scandir(dfd) as scan:
            for entry in scan:
                name = entry.name
                if name.startswith(_TMP_PREFIX):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if entry.is_symlink():
                    kind = 'other'
                elif statmod.S_ISDIR(info.st_mode):
                    kind = 'dir'
                elif statmod.S_ISREG(info.st_mode):
                    kind = 'file'
                else:
                    kind = 'other'
                out.append((name, kind, info.st_size, info.st_nlink))
        out.sort(key=lambda item: item[0])
        return out

    # --- atomic write ---------------------------------------------------------

    def _ensure_dirs(self, segments: Sequence[str], base: Optional[int] = None,
                     limit: int = MAX_MKDIR_DEPTH) -> int:
        if len(segments) > limit:
            raise _fail('path_too_deep', 'refusing to create more than %d levels' % limit)
        fd = self._root_fd() if base is None else os.dup(base)
        for index, name in enumerate(segments):
            try:
                child = os.open(name, _OPEN_DIR, dir_fd=fd)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    failure = _component_error(exc, fd, name, _rel(segments[:index + 1]))
                    _close(fd)
                    raise failure
                try:
                    os.mkdir(name, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                except OSError as mkexc:
                    _close(fd)
                    raise _translate(mkexc, _rel(segments[:index + 1]))
                try:
                    child = os.open(name, _OPEN_DIR, dir_fd=fd)
                except OSError as reexc:
                    _close(fd)
                    raise _translate(reexc, _rel(segments[:index + 1]))
            _close(fd)
            fd = child
        return fd

    def _stage(self, dfd: int, data: bytes, rel: str) -> str:
        name = _TMP_PREFIX + uuid.uuid4().hex
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(name, flags, 0o600, dir_fd=dfd)
        except OSError as exc:
            raise _translate(exc, rel)
        try:
            written = 0
            while written < len(data):
                written += os.write(fd, data[written:written + 1024 * 1024])
            os.fsync(fd)
        except OSError as exc:
            _close(fd)
            self._discard(dfd, name)
            raise _translate(exc, rel)
        _close(fd)
        return name

    def _discard(self, dfd: int, name: str) -> None:
        try:
            os.unlink(name, dir_fd=dfd)
        except OSError:
            pass

    def _sync_dir(self, dfd: int) -> None:
        try:
            os.fsync(dfd)
        except OSError:
            pass

    def _publish_file(self, dfd: int, name: str, data: bytes, rel: str, *, replace: bool) -> None:
        temp = self._stage(dfd, data, rel)
        try:
            if replace:
                os.rename(temp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            else:
                # linkat fails EEXIST atomically; a dangling symlink at `name` also exists.
                os.link(temp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
                self._discard(dfd, temp)
        except OSError as exc:
            self._discard(dfd, temp)
            raise _translate(exc, rel)
        self._sync_dir(dfd)

    def _count_files(self) -> int:
        """Bounded count of snapshot-eligible files; stops once over the cap."""
        total = [0]

        def walk(dfd: int, depth: int) -> None:
            for name, kind, _size, _nlink in self._entries(dfd, '.'):
                if total[0] > self.max_files:
                    return
                if kind == 'dir':
                    if depth >= MAX_SEGMENTS or name in _SKIP_DIRS or _denied_name(name):
                        continue
                    try:
                        child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                    except OSError:
                        continue
                    try:
                        walk(child, depth + 1)
                    finally:
                        _close(child)
                elif kind == 'file' and not _denied_name(name):
                    total[0] += 1

        fd = self._root_fd()
        try:
            walk(fd, 0)
        finally:
            _close(fd)
        return total[0]

    # --- collectors -----------------------------------------------------------

    def _collect(self, segments: Sequence[str], *, extensions: Optional[Iterable[str]] = None,
                 scan_cap: int = GREP_SCAN_BYTES, allow_git: bool = False,
                 skip_vendor: bool = True, strict: bool = False) -> Tuple[List[Tuple[str, bytes]], List[str], bool]:
        """Breadth-bounded read of regular files under `segments`.

        strict=True (snapshot transport) raises on exceeding count/byte limits instead
        of truncating, so a snapshot is never silently partial.
        """
        files: List[Tuple[str, bytes]] = []
        warnings: List[str] = []
        state = {'bytes': 0, 'truncated': False}
        exts = tuple(extensions) if extensions is not None else None

        def note(message: str) -> None:
            if len(warnings) < 32 and message not in warnings:
                warnings.append(message)

        def walk(dfd: int, prefix: List[str], depth: int) -> None:
            if state['truncated']:
                return
            for name, kind, size, nlink in self._entries(dfd, _rel(prefix)):
                rel = _rel(prefix + [name])
                if kind == 'other':
                    note('skipped non-regular entry: %s' % rel)
                    continue
                if _denied_name(name) and not (allow_git and kind == 'dir' and name.lower() == '.git'):
                    note('skipped credential or private path: %s' % rel)
                    continue
                if kind == 'dir':
                    if depth + 1 > MAX_SEGMENTS:
                        note('skipped deeper than %d levels: %s' % (MAX_SEGMENTS, rel))
                        continue
                    if skip_vendor and name in _SKIP_DIRS:
                        note('skipped regenerable directory: %s' % rel)
                        continue
                    try:
                        child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                    except OSError as exc:
                        note('skipped unreadable directory: %s (%s)' % (rel, _translate(exc, rel).code))
                        continue
                    try:
                        walk(child, prefix + [name], depth + 1)
                    finally:
                        _close(child)
                    if state['truncated']:
                        return
                    continue
                if nlink > 1:
                    if strict:
                        raise _fail('hardlinked_file', 'file has more than one link', rel)
                    note('skipped hardlinked file: %s' % rel)
                    continue
                if exts is not None and not rel.lower().endswith(exts):
                    continue
                if size > self.max_file_bytes:
                    if strict:
                        raise _fail('file_too_large', 'file exceeds %d bytes' % self.max_file_bytes, rel)
                    note('skipped file over %d bytes: %s' % (self.max_file_bytes, rel))
                    continue
                if len(files) >= self.max_files:
                    if strict:
                        raise _fail('too_many_files', 'more than %d files' % self.max_files, rel)
                    state['truncated'] = True
                    return
                if state['bytes'] + size > scan_cap:
                    if strict:
                        raise _fail('too_many_bytes', 'more than %d bytes' % scan_cap, rel)
                    state['truncated'] = True
                    return
                try:
                    data, _info = self._read_file(prefix + [name])
                except FileStoreError as exc:
                    if strict and exc.code not in ('not_found',):
                        raise
                    note('skipped %s: %s' % (rel, exc.code))
                    continue
                state['bytes'] += len(data)
                files.append((rel, data))

        start = self._open_dir(segments)
        try:
            walk(start, list(segments), len(segments))
        finally:
            _close(start)
        files.sort(key=lambda item: item[0])
        return files, warnings, state['truncated']

    # --- operations -----------------------------------------------------------

    def read(self, path: Any = '.', start_line: Any = None, end_line: Any = None) -> Dict[str, Any]:
        segments = _segments(path)
        if not segments:
            raise _fail('is_a_directory', 'path is the workspace root', '.')
        rel = _rel(segments)
        data, _info = self._read_file(segments)
        text = _decode(data, rel)
        offsets = [0]
        for index, char in enumerate(text):
            if char == '\n' and index + 1 < len(text):
                offsets.append(index + 1)
        total = 0 if text == '' else len(offsets)
        start = 1 if start_line is None else self._line_arg(start_line, 'start_line')
        end = total if end_line is None else self._line_arg(end_line, 'end_line')
        if end_line is not None and end < start:
            raise _fail('invalid_range', 'end_line is before start_line', rel)
        if total == 0 or start > total or end < start:
            content, first, last = '', 0, 0
        else:
            first, last = start, min(end, total)
            begin = offsets[first - 1]
            finish = offsets[last] if last < total else len(text)
            content = text[begin:finish]
        return {
            'skill_id': 'fs.read', 'path': rel, 'content': content,
            'sha256': _sha256(data), 'bytes': len(data), 'total_lines': total,
            'start_line': first, 'end_line': last,
            'truncated': total > 0 and (first != 1 or last != total),
        }

    def _line_arg(self, value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _fail('invalid_range', '%s must be an integer' % name)
        if value < 1:
            raise _fail('invalid_range', '%s must be >= 1' % name)
        return value

    def write(self, path: Any, content: Any, overwrite: Any = False,
              expected_sha256: Any = None) -> Dict[str, Any]:
        segments = _segments(path)
        if not segments:
            raise _fail('invalid_path', 'path must name a file')
        rel = _rel(segments)
        if not isinstance(content, str):
            raise _fail('invalid_content', 'content must be a string', rel)
        if not isinstance(overwrite, bool):
            raise _fail('invalid_argument', 'overwrite must be a boolean', rel)
        data = content.encode('utf-8')
        if len(data) > self.max_file_bytes:
            raise _fail('file_too_large', 'content exceeds %d bytes' % self.max_file_bytes, rel)
        expected = _expected_hash(expected_sha256)
        if overwrite and expected is None:
            raise _fail('expected_sha256_required', 'replacing a file requires expected_sha256', rel)
        if not overwrite and expected is not None:
            raise _fail('invalid_argument', 'expected_sha256 only applies with overwrite', rel)
        created = not overwrite
        if overwrite:
            current, _info = self._read_file(segments)
            actual = _sha256(current)
            if actual != expected:
                raise _fail('sha256_conflict', 'file changed: expected %s, found %s' % (expected, actual), rel)
            parent = self._open_dir(segments[:-1])
        else:
            if self._count_files() >= self.max_files:
                raise _fail('too_many_files', 'workspace already holds %d files' % self.max_files, rel)
            parent = self._ensure_dirs(segments[:-1])
        try:
            if overwrite:
                again, _re = self._read_file(segments)
                if _sha256(again) != expected:
                    raise _fail('sha256_conflict', 'file changed during write', rel)
            self._publish_file(parent, segments[-1], data, rel, replace=overwrite)
        finally:
            _close(parent)
        return {'skill_id': 'fs.write', 'path': rel, 'created': created, 'bytes': len(data),
                'sha256': _sha256(data), 'overwrote': bool(overwrite)}

    def edit(self, path: Any, old: Any, new: Any, expected_sha256: Any = None) -> Dict[str, Any]:
        segments = _segments(path)
        if not segments:
            raise _fail('invalid_path', 'path must name a file')
        rel = _rel(segments)
        if not isinstance(old, str) or not isinstance(new, str):
            raise _fail('invalid_argument', 'old and new must be strings', rel)
        if old == '':
            raise _fail('empty_match', 'old must not be empty', rel)
        if old == new:
            raise _fail('invalid_argument', 'old and new are identical', rel)
        expected = _expected_hash(expected_sha256)
        data, _info = self._read_file(segments)
        before = _sha256(data)
        if expected is not None and expected != before:
            raise _fail('sha256_conflict', 'file changed: expected %s, found %s' % (expected, before), rel)
        text = _decode(data, rel)
        hits = text.count(old)
        if hits == 0:
            raise _fail('match_not_found', 'old does not occur in the file', rel)
        if hits > 1:
            raise _fail('match_not_unique', 'old occurs %d times; it must be unique' % hits, rel)
        updated = text.replace(old, new, 1).encode('utf-8')
        if len(updated) > self.max_file_bytes:
            raise _fail('file_too_large', 'result exceeds %d bytes' % self.max_file_bytes, rel)
        parent = self._open_dir(segments[:-1])
        try:
            verify, _again = self._read_file(segments)
            if _sha256(verify) != before:
                raise _fail('sha256_conflict', 'file changed during edit', rel)
            self._publish_file(parent, segments[-1], updated, rel, replace=True)
        finally:
            _close(parent)
        return {'skill_id': 'code.edit', 'path': rel, 'replacements': 1,
                'sha256_before': before, 'sha256': _sha256(updated), 'bytes': len(updated)}

    def list_dir(self, path: Any = '.') -> Dict[str, Any]:
        segments = _segments(path)
        rel = _rel(segments)
        dfd = self._open_dir(segments)
        try:
            raw = self._entries(dfd, rel)
        finally:
            _close(dfd)
        entries: List[Dict[str, Any]] = []
        skipped: List[Dict[str, str]] = []
        truncated = False
        for name, kind, size, nlink in raw:
            if kind == 'other':
                skipped.append({'name': name, 'reason': 'not_a_regular_entry'})
                continue
            if _denied_name(name):
                skipped.append({'name': name, 'reason': 'credential_path'})
                continue
            if kind == 'file' and nlink > 1:
                skipped.append({'name': name, 'reason': 'hardlinked_file'})
                continue
            if len(entries) >= self.max_files:
                truncated = True
                break
            item: Dict[str, Any] = {'name': name, 'kind': kind,
                                    'path': _rel(segments + [name])}
            if kind == 'file':
                item['bytes'] = size
            entries.append(item)
        return {'skill_id': 'fs.list', 'path': rel, 'entries': entries,
                'skipped': skipped, 'truncated': truncated}

    def tree(self, path: Any = '.', depth: Any = DEFAULT_TREE_DEPTH) -> Dict[str, Any]:
        segments = _segments(path)
        rel = _rel(segments)
        if isinstance(depth, bool) or not isinstance(depth, int):
            raise _fail('invalid_argument', 'depth must be an integer', rel)
        if depth < 1 or depth > MAX_TREE_DEPTH:
            raise _fail('invalid_argument', 'depth must be between 1 and %d' % MAX_TREE_DEPTH, rel)
        state = {'nodes': 0, 'truncated': False}

        def build(dfd: int, prefix: List[str], level: int) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for name, kind, size, nlink in self._entries(dfd, _rel(prefix)):
                if kind == 'other' or _denied_name(name) or (kind == 'file' and nlink > 1):
                    continue
                if state['nodes'] >= self.max_files:
                    state['truncated'] = True
                    return out
                state['nodes'] += 1
                node: Dict[str, Any] = {'name': name, 'kind': kind, 'path': _rel(prefix + [name])}
                if kind == 'file':
                    node['bytes'] = size
                elif level < depth:
                    try:
                        child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                    except OSError:
                        node['children'] = []
                        node['unreadable'] = True
                        out.append(node)
                        continue
                    try:
                        node['children'] = build(child, prefix + [name], level + 1)
                    finally:
                        _close(child)
                else:
                    node['children_elided'] = True
                out.append(node)
            return out

        start = self._open_dir(segments)
        try:
            entries = build(start, list(segments), 1)
        finally:
            _close(start)
        return {'skill_id': 'fs.tree', 'path': rel, 'depth': depth, 'entries': entries,
                'nodes': state['nodes'], 'truncated': state['truncated']}

    def glob(self, pattern: Any) -> Dict[str, Any]:
        if not isinstance(pattern, str) or pattern.strip() == '':
            raise _fail('invalid_pattern', 'pattern must be a non-empty string')
        _segments(pattern, what='pattern')
        parts = [part for part in pattern.split('/') if part not in ('', '.')]
        if not parts:
            raise _fail('invalid_pattern', 'pattern selects nothing')
        found: List[str] = []
        budget = [GLOB_BUDGET]
        truncated = [False]

        def walk(dfd: int, prefix: List[str], pats: List[str], depth: int) -> None:
            if truncated[0] or depth > MAX_GLOB_DEPTH:
                return
            budget[0] -= 1
            if budget[0] <= 0:
                truncated[0] = True
                return
            head, rest = pats[0], pats[1:]
            entries = self._entries(dfd, _rel(prefix))
            if head == '**':
                if rest:
                    walk(dfd, prefix, rest, depth)
                for name, kind, _size, nlink in entries:
                    if truncated[0] or kind == 'other' or _denied_name(name):
                        continue
                    if kind == 'dir':
                        try:
                            child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                        except OSError:
                            continue
                        try:
                            walk(child, prefix + [name], pats, depth + 1)
                        finally:
                            _close(child)
                    elif not rest and nlink == 1:
                        add(prefix + [name])
                return
            for name, kind, _size, nlink in entries:
                if truncated[0] or kind == 'other' or _denied_name(name):
                    continue
                if not fnmatch.fnmatchcase(name, head):
                    continue
                if rest:
                    if kind != 'dir':
                        continue
                    try:
                        child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                    except OSError:
                        continue
                    try:
                        walk(child, prefix + [name], rest, depth + 1)
                    finally:
                        _close(child)
                elif kind == 'file' and nlink == 1:
                    add(prefix + [name])

        def add(segments: Sequence[str]) -> None:
            if len(found) >= self.max_files:
                truncated[0] = True
                return
            found.append(_rel(segments))

        fd = self._root_fd()
        try:
            walk(fd, [], parts, 0)
        finally:
            _close(fd)
        return {'skill_id': 'fs.glob', 'pattern': pattern, 'paths': sorted(set(found)),
                'count': len(set(found)), 'truncated': truncated[0]}

    def search(self, query: Any, path: Any = '.') -> Dict[str, Any]:
        if not isinstance(query, str) or query.strip() == '':
            raise _fail('invalid_query', 'query must be a non-empty string')
        if len(query) > 128:
            raise _fail('invalid_query', 'query exceeds 128 characters')
        for char in query:
            if ord(char) < 0x20 or ord(char) == 0x7F:
                raise _fail('control_character', 'control character in query')
        segments = _segments(path)
        files, warnings, truncated = self._collect(segments, extensions=_TEXT_EXT)
        matches: List[Dict[str, Any]] = []
        analyses = set()
        for rel, data in files:
            if len(matches) >= SEARCH_MAX_MATCHES:
                truncated = True
                break
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                warnings.append('skipped non-utf8 file: %s' % rel)
                continue
            lower = rel.lower()
            if lower.endswith(_PY_EXT):
                found, analysis, note = _python_symbols(text, query, rel)
                if note:
                    warnings.append(note)
            elif lower.endswith(_BEST_EFFORT_EXT):
                found, analysis = _best_effort_symbols(text, query), 'best_effort_regex'
            else:
                found, analysis = _literal_hits(text, query), 'literal'
            analyses.add(analysis)
            for hit in found:
                if len(matches) >= SEARCH_MAX_MATCHES:
                    truncated = True
                    break
                hit.update({'path': rel, 'analysis': analysis})
                matches.append(hit)
        matches.sort(key=lambda item: (item['path'], item['line'], item['column']))
        return {'skill_id': 'code.search', 'query': query, 'path': _rel(segments),
                'matches': matches, 'files_scanned': len(files), 'truncated': truncated,
                'analysis': sorted(analyses), 'warnings': warnings,
                'note': 'python results use the Python AST; js/ts/go results are '
                        'best-effort regex and may be incomplete or wrong'}

    def grep(self, pattern: Any, path: Any = '.', literal: Any = False) -> Dict[str, Any]:
        if not isinstance(pattern, str) or pattern == '':
            raise _fail('invalid_pattern', 'pattern must be a non-empty string')
        if len(pattern) > GREP_MAX_PATTERN:
            raise _fail('invalid_pattern', 'pattern exceeds %d characters' % GREP_MAX_PATTERN)
        if not isinstance(literal, bool):
            raise _fail('invalid_argument', 'literal must be a boolean')
        for char in pattern:
            if ord(char) < 0x20 and char not in '\t':
                raise _fail('control_character', 'control character in pattern')
        segments = _segments(path)
        files, warnings, truncated = self._collect(segments, extensions=_TEXT_EXT)
        payload: List[List[Any]] = []
        for rel, data in files:
            try:
                payload.append([rel, data.decode('utf-8')])
            except UnicodeDecodeError:
                warnings.append('skipped non-utf8 file: %s' % rel)
        request = json.dumps({'pattern': pattern, 'literal': literal, 'files': payload,
                              'max_matches': GREP_MAX_MATCHES, 'max_output': GREP_MAX_OUTPUT})
        try:
            done = subprocess.run(
                [sys.executable, '-I', '-c', _GREP_WORKER], input=request.encode('utf-8'),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GREP_DEADLINE,
                cwd=_scratch_dir(), env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'},
                start_new_session=True)
        except subprocess.TimeoutExpired:
            raise _fail('grep_timeout', 'pattern exceeded the %.1fs match deadline; '
                                        'simplify it or pass literal=true' % GREP_DEADLINE)
        except OSError as exc:
            raise _fail('grep_unavailable', 'could not start the match subprocess: %s' % exc)
        if done.returncode != 0:
            raise _fail('grep_failed', 'match subprocess exited %d' % done.returncode)
        try:
            result = json.loads(done.stdout.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise _fail('grep_failed', 'match subprocess produced unreadable output')
        if 'error' in result:
            raise _fail('invalid_pattern', result.get('detail', 'pattern is not valid'))
        return {'skill_id': 'code.grep', 'pattern': pattern, 'path': _rel(segments),
                'literal': literal, 'matches': result['matches'],
                'files_scanned': len(payload), 'warnings': warnings,
                'truncated': bool(result['truncated']) or truncated,
                'engine': 'python_re_isolated_subprocess'}

    # --- dispatch -------------------------------------------------------------

    _SKILLS = {
        'fs.read': ('read', ('path',), ('start_line', 'end_line')),
        'code.read': ('read', ('path',), ('start_line', 'end_line')),
        'fs.write': ('write', ('path', 'content'), ('overwrite', 'expected_sha256')),
        'code.write': ('write', ('path', 'content'), ('overwrite', 'expected_sha256')),
        'code.edit': ('edit', ('path', 'old', 'new'), ('expected_sha256',)),
        'fs.list': ('list_dir', (), ('path',)),
        'fs.tree': ('tree', (), ('path', 'depth')),
        'fs.glob': ('glob', ('pattern',), ()),
        'code.search': ('search', ('query',), ('path',)),
        'code.grep': ('grep', ('pattern',), ('path', 'literal')),
    }

    def skills(self) -> List[str]:
        return sorted(self._SKILLS)

    def execute(self, skill_id: Any, args: Any) -> Dict[str, Any]:
        if not isinstance(skill_id, str) or skill_id not in self._SKILLS:
            raise _fail('unknown_skill', 'unsupported skill_id for the file store')
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise _fail('invalid_arguments', 'args must be an object')
        method, required, optional = self._SKILLS[skill_id]
        allowed = set(required) | set(optional)
        unknown = sorted(key for key in args if key not in allowed)
        if unknown:
            raise _fail('unknown_argument', 'unsupported arguments: %s' % ', '.join(unknown))
        missing = [key for key in required if key not in args]
        if missing:
            raise _fail('missing_argument', 'required arguments: %s' % ', '.join(missing))
        call = {key: args[key] for key in args}
        result = getattr(self, method)(**call)
        result['skill_id'] = skill_id
        return result

    # --- snapshot transport ---------------------------------------------------

    def snapshot_report(self, include_git: bool = True) -> Dict[str, Any]:
        if not isinstance(include_git, bool):
            raise _fail('invalid_argument', 'include_git must be a boolean')
        files, warnings, truncated = self._collect(
            (), scan_cap=self.max_bytes, allow_git=include_git, strict=True)
        if truncated:
            raise _fail('snapshot_truncated', 'workspace exceeds the snapshot bounds')
        encoded = {rel: base64.b64encode(data).decode('ascii') for rel, data in files}
        total = sum(len(data) for _rel, data in files)
        return {'files': encoded, 'count': len(encoded), 'total_bytes': total,
                'include_git': include_git, 'warnings': warnings}

    def snapshot(self, include_git: bool = True) -> Dict[str, str]:
        return self.snapshot_report(include_git)['files']

    def validate_snapshot(self, snapshot: Any) -> Dict[str, bytes]:
        if not isinstance(snapshot, dict):
            raise _fail('invalid_snapshot', 'snapshot must be a mapping')
        if len(snapshot) > self.max_files:
            raise _fail('too_many_files', 'snapshot holds %d files, limit %d'
                        % (len(snapshot), self.max_files))
        decoded: Dict[str, bytes] = {}
        directories = set()
        total = 0
        for key in sorted(snapshot, key=str):
            segments = _segments(key, allow_git=True, what='snapshot path')
            if not segments:
                raise _fail('invalid_snapshot', 'snapshot path must name a file', str(key))
            rel = _rel(segments)
            if rel in decoded:
                raise _fail('duplicate_path', 'snapshot path repeats after normalisation', rel)
            value = snapshot[key]
            if isinstance(value, str):
                try:
                    data = base64.b64decode(value.encode('ascii'), validate=True)
                except (binascii.Error, UnicodeEncodeError, ValueError):
                    raise _fail('invalid_encoding', 'value is not valid base64', rel)
            elif isinstance(value, (bytes, bytearray)):
                data = bytes(value)
            else:
                raise _fail('invalid_encoding', 'value must be base64 text', rel)
            if len(data) > self.max_file_bytes:
                raise _fail('file_too_large', 'file exceeds %d bytes' % self.max_file_bytes, rel)
            total += len(data)
            if total > self.max_bytes:
                raise _fail('too_many_bytes', 'snapshot exceeds %d bytes' % self.max_bytes, rel)
            decoded[rel] = data
            for index in range(1, len(segments)):
                directories.add(_rel(segments[:index]))
        clash = sorted(set(decoded) & directories)
        if clash:
            raise _fail('path_conflict', 'path is both a file and a directory: %s' % clash[0], clash[0])
        return decoded

    def replace_snapshot(self, snapshot: Any, preserve_untransportable: bool = True
                         ) -> Dict[str, Any]:
        decoded = self.validate_snapshot(snapshot)
        parent = self.root.parent
        if parent == self.root:
            raise _fail('replace_unavailable', 'root has no parent directory to stage in')
        try:
            pfd = os.open(str(parent), _OPEN_DIR)
        except OSError as exc:
            raise _translate(exc, '.')
        stage = _STAGE_PREFIX + uuid.uuid4().hex
        retired = _RETIRED_PREFIX + uuid.uuid4().hex
        try:
            try:
                os.mkdir(stage, 0o700, dir_fd=pfd)
            except OSError as exc:
                raise _fail('replace_unavailable', 'cannot stage beside root: %s' % exc.strerror)
            try:
                self._fill_stage(pfd, stage, decoded)
                moved = False
                try:
                    os.rename(self.root.name, retired, src_dir_fd=pfd, dst_dir_fd=pfd)
                    moved = True
                    os.rename(stage, self.root.name, src_dir_fd=pfd, dst_dir_fd=pfd)
                except OSError as exc:
                    if moved:
                        try:
                            os.rename(retired, self.root.name, src_dir_fd=pfd, dst_dir_fd=pfd)
                        except OSError:
                            raise _fail('publish_failed', 'root was moved aside and could not be '
                                                          'restored; workspace state is unknown '
                                                          '(retired copy: %s)' % retired)
                    raise _fail('publish_failed', 'could not publish snapshot: %s' % exc.strerror)
                self._sync_dir(pfd)
                if preserve_untransportable:
                    kept, warnings, intended = self._migrate_preserved(pfd, retired)
                else:
                    kept, warnings, intended = [], [], []
                if len(kept) == len(intended):
                    _remove_tree(pfd, retired)
                    retired_path = None
                else:
                    retired_path = str(self.root.parent / retired)
                    warnings.append('previous generation kept at %s because not every '
                                    'preserved entry could be moved' % retired_path)
            except BaseException:
                _remove_tree(pfd, stage)
                raise
        finally:
            _close(pfd)
        result = {'status': 'published', 'root': str(self.root), 'files': len(decoded),
                  'bytes': sum(len(data) for data in decoded.values()), 'replaced': True,
                  'preserved': kept, 'warnings': warnings}
        if retired_path is not None:
            result['retired_path'] = retired_path
        return result

    def _collect_preserved(self, dfd: int, prefix: List[str], depth: int,
                           out: List[List[str]]) -> None:
        """Entries `snapshot()` cannot carry, so publishing must not destroy them."""
        for name, kind, size, nlink in self._entries(dfd, _rel(prefix)):
            if len(out) >= MAX_PRESERVED:
                return
            rel = prefix + [name]
            if kind == 'other' or _denied_name(name) or (kind == 'dir' and name in _SKIP_DIRS):
                out.append(rel)
                continue
            if kind == 'file':
                if nlink > 1 or size > self.max_file_bytes:
                    out.append(rel)
                continue
            if depth + 1 > MAX_SEGMENTS:
                out.append(rel)
                continue
            try:
                child = os.open(name, _OPEN_DIR, dir_fd=dfd)
            except OSError:
                continue
            try:
                self._collect_preserved(child, rel, depth + 1, out)
            finally:
                _close(child)

    def _migrate_preserved(self, pfd: int, retired: str
                           ) -> Tuple[List[str], List[str], List[List[str]]]:
        intended: List[List[str]] = []
        warnings: List[str] = []
        try:
            old_fd = os.open(retired, _OPEN_DIR, dir_fd=pfd)
        except OSError as exc:
            return [], ['could not reopen the previous generation (%s); nothing preserved'
                        % exc.strerror], []
        try:
            self._collect_preserved(old_fd, [], 0, intended)
        finally:
            _close(old_fd)
        if len(intended) >= MAX_PRESERVED:
            warnings.append('more than %d non-transportable entries; preserved the first %d'
                            % (MAX_PRESERVED, MAX_PRESERVED))
        kept: List[str] = []
        deep = MAX_SEGMENTS + 1
        for segments in intended:
            rel = _rel(segments)
            src = dst = None
            try:
                src = self._open_dir([retired] + segments[:-1], base=pfd)
                dst = self._ensure_dirs([self.root.name] + segments[:-1], base=pfd, limit=deep)
                try:
                    os.stat(segments[-1], dir_fd=dst, follow_symlinks=False)
                except FileNotFoundError:
                    os.rename(segments[-1], segments[-1], src_dir_fd=src, dst_dir_fd=dst)
                    kept.append(rel)
                    continue
                warnings.append('did not restore previous %s: the snapshot provides that path'
                                % rel)
                kept.append(rel)
            except (OSError, FileStoreError) as exc:
                warnings.append('could not preserve %s (%s)' % (rel, getattr(exc, 'code', exc)))
            finally:
                _close(src)
                _close(dst)
        return kept, warnings, intended

    def _fill_stage(self, pfd: int, stage: str, decoded: Dict[str, bytes]) -> None:
        try:
            base = os.open(stage, _OPEN_DIR, dir_fd=pfd)
        except OSError as exc:
            raise _translate(exc, '.')
        try:
            for rel in sorted(decoded):
                segments = rel.split('/')
                dfd = base
                owned = False
                try:
                    for name in segments[:-1]:
                        try:
                            os.mkdir(name, 0o755, dir_fd=dfd)
                        except FileExistsError:
                            pass
                        child = os.open(name, _OPEN_DIR, dir_fd=dfd)
                        if owned:
                            _close(dfd)
                        dfd, owned = child, True
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
                    fd = os.open(segments[-1], flags, 0o644, dir_fd=dfd)
                    try:
                        data = decoded[rel]
                        written = 0
                        while written < len(data):
                            written += os.write(fd, data[written:written + 1024 * 1024])
                        os.fsync(fd)
                    finally:
                        _close(fd)
                except OSError as exc:
                    raise _translate(exc, rel)
                finally:
                    if owned:
                        _close(dfd)
            try:
                os.fchmod(base, 0o755)
            except OSError:
                pass
            self._sync_dir(base)
        finally:
            _close(base)


def _remove_tree(pfd: int, name: str) -> None:
    """Delete a staging/retired directory we created, never following links out."""
    try:
        dfd = os.open(name, _OPEN_DIR, dir_fd=pfd)
    except OSError:
        return
    try:
        with os.scandir(dfd) as scan:
            children = [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in scan]
        for child, is_dir in children:
            if is_dir:
                _remove_tree(dfd, child)
            else:
                try:
                    os.unlink(child, dir_fd=dfd)
                except OSError:
                    pass
    finally:
        _close(dfd)
    try:
        os.rmdir(name, dir_fd=pfd)
    except OSError:
        pass


def _python_symbols(text: str, query: str, rel: str) -> Tuple[List[Dict[str, Any]], str, Optional[str]]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return (_literal_hits(text, query), 'best_effort_text',
                'python parse failed for %s (%s); fell back to literal matching'
                % (rel, type(exc).__name__))
    lines = text.split('\n')
    hits: List[Dict[str, Any]] = []

    def record(node: ast.AST, kind: str, name: str) -> None:
        line = getattr(node, 'lineno', 0)
        hits.append({'line': line, 'column': getattr(node, 'col_offset', 0) + 1,
                     'kind': kind, 'symbol': name,
                     'text': lines[line - 1][:400] if 0 < line <= len(lines) else ''})

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == query:
            record(node, 'function', node.name)
        elif isinstance(node, ast.ClassDef) and node.name == query:
            record(node, 'class', node.name)
        elif isinstance(node, ast.Name) and node.id == query and isinstance(node.ctx, ast.Store):
            record(node, 'assignment', node.id)
        elif isinstance(node, ast.arg) and node.arg == query:
            record(node, 'parameter', node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                label = alias.asname or alias.name
                if label == query or label.split('.')[0] == query:
                    record(node, 'import', label)
        elif isinstance(node, ast.Attribute) and node.attr == query:
            record(node, 'attribute', node.attr)
        elif isinstance(node, ast.Name) and node.id == query:
            record(node, 'reference', node.id)
    hits.sort(key=lambda item: (item['line'], item['column'], item['kind']))
    return hits, 'python_ast', None


_BEST_EFFORT_FORMS = (
    ('function', r'\bfunction\s+%s\b'),
    ('function', r'\bfunc\s+%s\b'),
    ('function', r'\bfunc\s+\([^)]*\)\s*%s\b'),
    ('class', r'\bclass\s+%s\b'),
    ('type', r'\btype\s+%s\b'),
    ('type', r'\b(?:interface|enum)\s+%s\b'),
    ('binding', r'\b(?:const|let|var)\s+%s\b'),
    ('method', r'(?:^|[\s,{])%s\s*(?:=\s*)?(?:async\s*)?\(' ),
    ('reference', r'\b%s\b'),
)


def _best_effort_symbols(text: str, query: str) -> List[Dict[str, Any]]:
    token = re.escape(query)
    compiled = [(kind, re.compile(form % token)) for kind, form in _BEST_EFFORT_FORMS]
    hits: List[Dict[str, Any]] = []
    for number, line in enumerate(text.split('\n'), 1):
        for kind, rx in compiled:
            found = rx.search(line)
            if found is not None:
                hits.append({'line': number, 'column': found.start() + 1, 'kind': kind,
                             'symbol': query, 'text': line[:400]})
                break
    return hits


def _literal_hits(text: str, query: str) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for number, line in enumerate(text.split('\n'), 1):
        column = line.find(query)
        if column >= 0:
            hits.append({'line': number, 'column': column + 1, 'kind': 'literal',
                         'symbol': query, 'text': line[:400]})
    return hits
