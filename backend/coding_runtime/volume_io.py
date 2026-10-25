#!/usr/bin/env python3
"""Trusted snapshot import/export helper for Harmonika coding sandbox jobs.

Runs ONLY as the fixed entrypoint of trusted helper containers, never with
untrusted code, never on the host in production:

    python3 /opt/harmonika/volume_io.py --import    snapshot JSON on stdin
    python3 /opt/harmonika/volume_io.py --export    snapshot JSON on stdout

The workspace is the per-job local tmpfs Docker volume mounted at /workspace.
--workspace exists solely so tests can exercise this helper against a plain
directory; the sandbox always uses the default mount.

Guarantees:
- never follows symlinks; never reads outside the workspace root;
- import validates the entire snapshot (count/size/path) before mutating
  anything, purges the previous tree, then atomically renames each file;
- export includes only regular files (skips symlinks/special/hardlinked
  files with warnings), validates bytes/count before emitting JSON;
- bounds: <=256 files, <=512KiB per file, <=16MiB total, path <=240 chars.

Exit codes: 0 ok, 2 validation error, 3 I/O error.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import stat
import sys

MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_FILES = 256
MAX_FILE_BYTES = 512 * 1024
MAX_PATH_LEN = 240
MAX_DIRS = 4096

CREDENTIAL_COMPONENTS_EXACT = {'.env', '.ssh', 'credentials', 'credential'}
CREDENTIAL_PREFIXES = ('.env.',)


class VolumeIOError(Exception):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def _fail(message, code=2):
    sys.stderr.write(json.dumps({'error': str(message)[:500]}) + '\n')
    sys.exit(code)


def validate_rel_path(path):
    if not isinstance(path, str):
        raise VolumeIOError('path must be a string')
    if not path or len(path) > MAX_PATH_LEN:
        raise VolumeIOError('path length invalid (1..%d)' % MAX_PATH_LEN)
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in path):
        raise VolumeIOError('path contains control characters')
    if path.startswith('/'):
        raise VolumeIOError('path must be relative')
    if '\\' in path:
        raise VolumeIOError('path must not contain backslash')
    if path == '.':
        raise VolumeIOError('path must name a file')
    for part in path.split('/'):
        if part in ('', '.', '..'):
            raise VolumeIOError('path traversal component rejected: %r' % path)
    return path


def _is_credential_path(rel):
    parts = rel.split('/')
    for part in parts:
        if part in CREDENTIAL_COMPONENTS_EXACT or part.startswith(CREDENTIAL_PREFIXES):
            return True
    return False


def decode_snapshot(raw_mapping):
    if not isinstance(raw_mapping, dict):
        raise VolumeIOError('snapshot must be an object')
    if len(raw_mapping) > MAX_FILES:
        raise VolumeIOError('too many files: %d > %d' % (len(raw_mapping), MAX_FILES))
    decoded = {}
    total = 0
    for rel in sorted(raw_mapping):
        validate_rel_path(rel)
        b64 = raw_mapping[rel]
        if not isinstance(b64, str):
            raise VolumeIOError('snapshot value must be base64 string: %s' % rel)
        try:
            data = base64.b64decode(b64.encode('ascii'), validate=True)
        except (binascii.Error, ValueError, UnicodeEncodeError):
            raise VolumeIOError('invalid base64 for %s' % rel)
        if len(data) > MAX_FILE_BYTES:
            raise VolumeIOError('file too large: %s (%d > %d)' % (rel, len(data), MAX_FILE_BYTES))
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise VolumeIOError('snapshot too large (%d > %d bytes)' % (total, MAX_TOTAL_BYTES))
        decoded[rel] = data
    return decoded


def _purge(root):
    def _rm(path):
        st = os.lstat(path)
        if stat.S_ISDIR(st.st_mode):
            for name in os.listdir(path):
                _rm(os.path.join(path, name))
            os.rmdir(path)
        else:
            os.unlink(path)

    for name in os.listdir(root):
        _rm(os.path.join(root, name))


def _write_all(root, decoded):
    counter = [0]
    for rel in sorted(decoded):
        data = decoded[rel]
        final = os.path.join(root, rel)
        parent = os.path.dirname(final)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        counter[0] += 1
        tmp = os.path.join(parent or root, '.hms-import-%d-%d.tmp' % (os.getpid(), counter[0]))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            os.replace(tmp, final)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def do_import(root, payload):
    if not isinstance(payload, dict):
        raise VolumeIOError('payload must be a JSON object')
    mapping = payload.get('snapshot', payload)
    decoded = decode_snapshot(mapping)
    try:
        _purge(root)
        _write_all(root, decoded)
    except OSError as exc:
        raise VolumeIOError('I/O error: %s' % exc, code=3)
    return {'ok': True, 'files': len(decoded), 'bytes': sum(len(v) for v in decoded.values())}


def _read_regular(entry, rel, warnings):
    if _is_credential_path(rel):
        warnings.append('skipped credential path: %s' % rel)
        return None
    st = entry.stat(follow_symlinks=False)
    if st.st_nlink > 1:
        warnings.append('skipped hardlinked file: %s' % rel)
        return None
    if st.st_size > MAX_FILE_BYTES:
        raise VolumeIOError('file too large to export: %s (%d > %d)' % (rel, st.st_size, MAX_FILE_BYTES))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(entry.path, flags)
    try:
        with os.fdopen(fd, 'rb') as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        raise VolumeIOError('read error %s: %s' % (rel, exc), code=3)
    if len(data) > MAX_FILE_BYTES:
        raise VolumeIOError('file too large to export: %s' % rel)
    return data


def _walk(dirpath, out, warnings, ctx, base=None):
    ctx['dirs'] += 1
    if ctx['dirs'] > MAX_DIRS:
        raise VolumeIOError('too many directories in workspace')
    with os.scandir(dirpath) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        rel = entry.name if base is None else base + '/' + entry.name
        if len(rel) > MAX_PATH_LEN:
            warnings.append('skipped overlong path: %s' % rel[:80])
            continue
        try:
            if entry.is_symlink():
                warnings.append('skipped symlink: %s' % rel)
                continue
            if entry.is_dir(follow_symlinks=False):
                _walk(entry.path, out, warnings, ctx, base=rel)
                continue
            if entry.is_file(follow_symlinks=False):
                data = _read_regular(entry, rel, warnings)
                if data is not None:
                    out[rel] = data
                continue
            warnings.append('skipped special file: %s' % rel)
        except VolumeIOError:
            raise
        except OSError as exc:
            raise VolumeIOError('walk error at %s: %s' % (rel, exc), code=3)


def _walk_root(root):
    out = {}
    warnings = []
    ctx = {'dirs': 0}
    _walk(root, out, warnings, ctx)
    if len(out) > MAX_FILES:
        raise VolumeIOError('too many files to export: %d > %d' % (len(out), MAX_FILES))
    total = sum(len(v) for v in out.values())
    if total > MAX_TOTAL_BYTES:
        raise VolumeIOError('export too large: %d > %d bytes' % (total, MAX_TOTAL_BYTES))
    return out, warnings


def do_export(root):
    try:
        if not os.path.isdir(root):
            raise VolumeIOError('workspace missing', code=3)
        files, warnings = _walk_root(root)
    except VolumeIOError:
        raise
    except OSError as exc:
        raise VolumeIOError('I/O error: %s' % exc, code=3)
    snapshot = {rel: base64.b64encode(files[rel]).decode('ascii') for rel in sorted(files)}
    return {'snapshot': snapshot, 'warnings': warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='volume_io', description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--import', dest='mode_import', action='store_true')
    group.add_argument('--export', dest='mode_export', action='store_true')
    parser.add_argument('--workspace', default='/workspace')
    args = parser.parse_args(argv)
    try:
        if args.mode_import:
            payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))
            result = do_import(args.workspace, payload)
        else:
            result = do_export(args.workspace)
    except VolumeIOError as exc:
        _fail(exc, exc.code)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _fail('invalid JSON input: %s' % exc)
    sys.stdout.write(json.dumps(result, sort_keys=True))
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    sys.exit(main())
