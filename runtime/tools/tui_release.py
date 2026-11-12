#!/usr/bin/env python3
"""Signed local plugin release tooling for Harmonika TUI native plugins.

build_release / verify_release / extract_release package ONLY explicit plugin
files, sign a canonical manifest with Ed25519 through the installed Node
crypto CLI (key contents never appear in argv or logs), and verify + extract
strictly against a caller-supplied trusted public key.

Compatibility contract: every release manifest carries
api='harmonika-tui-plugin-v2'. The native binary stays external: it is never
bundled, replaced or patched, and the source allowlist refuses binaries and
private configuration. No network access, no dependency installs, and no
automatic signing key creation: a missing key is an error, never generated.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

__all__ = ['PLUGIN_API', 'MANIFEST_NAME', 'ALLOWED_SUFFIXES', 'MAX_FILES',
           'MAX_TOTAL_BYTES', 'ReleaseError', 'build_release', 'verify_release',
           'extract_release', 'main']

PLUGIN_API = 'harmonika-tui-plugin-v2'
MANIFEST_NAME = 'plugin-release.json'
ENVELOPE_KEYS = frozenset(('payload', 'signature'))
MANIFEST_KEYS = frozenset(('api', 'version', 'file_count', 'total_bytes',
                           'binary_included', 'files'))

ALLOWED_SUFFIXES = frozenset(('.js', '.mjs', '.cjs', '.json', '.md', '.py',
                              '.ts', '.tsx'))
FORBIDDEN_COMPONENTS = frozenset(('node_modules', '__pycache__'))
SENSITIVE_STEMS = frozenset(('credentials', 'credential', 'creds', 'secret',
                             'secrets', 'password', 'passwd', 'pass', 'token',
                             'session', 'cookie', 'apikey', 'api_key',
                             'privatekey', 'private_key', 'signing',
                             'signingkey'))
SENSITIVE_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.kdbx', '.keystore', '.jks')
SENSITIVE_CONTAINS = ('id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa')

MAX_FILES = 200
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_ENTRY_BYTES = MAX_TOTAL_BYTES
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 200
BOMB_MIN_BYTES = 1000000
MAX_KEY_BYTES = 65536
NODE_TIMEOUT_SECONDS = 60
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

VERSION_RE = re.compile(r'\A\d{1,4}(?:\.\d{1,4}){0,3}(?:-[A-Za-z0-9][A-Za-z0-9._]{0,15})?\Z')
SHA256_RE = re.compile(r'\A[0-9a-f]{64}\Z')
DRIVE_RE = re.compile(r'\A[A-Za-z]:')


class ReleaseError(ValueError):
    """Machine-coded error; the message never carries file or key content."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise ReleaseError(code)


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _validate_version(version):
    _need(type(version) is str and 1 <= len(version) <= 32
          and VERSION_RE.fullmatch(version) is not None, 'invalid_version')
    return version


def _sensitive_component(name):
    lowered = name.lower()
    if lowered.startswith('.env'):
        return True
    if lowered.endswith(SENSITIVE_SUFFIXES):
        return True
    if any(marker in lowered for marker in SENSITIVE_CONTAINS):
        return True
    stem = lowered.split('.', 1)[0]
    return stem in SENSITIVE_STEMS


def _validate_component(component):
    _need(type(component) is str and component not in ('', '.'),
          'unsafe_entry_name')
    _need(component != '..', 'path_traversal')
    _need(not component.startswith('.'), 'hidden_path_forbidden')
    _need(component.lower() not in FORBIDDEN_COMPONENTS, 'forbidden_directory')
    _need(not _sensitive_component(component), 'sensitive_filename')


def _validate_relpath(name):
    _need(type(name) is str and 0 < len(name) <= 400, 'unsafe_entry_name')
    _need('\\' not in name and '\x00' not in name, 'unsafe_entry_name')
    _need(not name.startswith('/'), 'absolute_entry')
    _need(DRIVE_RE.match(name) is None, 'absolute_entry')
    parts = name.split('/')
    for part in parts:
        _validate_component(part)
        _need(part.lower() != MANIFEST_NAME, 'reserved_name_forbidden')
    _need(PurePosixPath(name).suffix.lower() in ALLOWED_SUFFIXES,
          'disallowed_suffix')
    return name


def _node_command():
    candidates = (os.environ.get('HARMONIKA_NODE'), shutil.which('node'),
                  str(Path.home() / '.local/node/bin/node'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ReleaseError('node_missing')


def _run_node(script, args, payload, fail_code):
    try:
        proc = subprocess.run([_node_command(), '-e', script, *args],
                              input=payload, capture_output=True,
                              timeout=NODE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ReleaseError('node_timeout') from None
    except OSError:
        raise ReleaseError('node_unavailable') from None
    _need(proc.returncode == 0, fail_code)
    return proc.stdout


SIGN_JS = ("const fs=require('fs'),c=require('crypto');"
           "const p=fs.readFileSync(0);"
           "const k=c.createPrivateKey(fs.readFileSync(process.argv[1]));"
           "if(k.asymmetricKeyType!=='ed25519')process.exit(4);"
           "process.stdout.write(c.sign(null,p,k).toString('base64'));")

VERIFY_JS = ("const fs=require('fs'),c=require('crypto');"
             "const e=JSON.parse(fs.readFileSync(0,'utf8'));"
             "const k=c.createPublicKey(fs.readFileSync(process.argv[1]));"
             "if(k.asymmetricKeyType!=='ed25519')process.exit(4);"
             "const ok=c.verify(null,Buffer.from(e.payload,'base64'),k,"
             "Buffer.from(e.signature,'base64'));"
             "if(!ok)process.exit(3);process.stdout.write('ok');")


def _check_key_file(path, missing_code):
    _need(isinstance(path, (str, os.PathLike)), missing_code)
    key = Path(path)
    _need(key.is_file(), missing_code)
    _need(50 <= key.stat().st_size <= MAX_KEY_BYTES, 'invalid_key_file')
    return key


def _sign_manifest(manifest, private_key_path):
    payload = _canonical(manifest)
    stdout = _run_node(SIGN_JS, [str(private_key_path)], payload,
                       'signing_failed')
    signature = stdout.decode('ascii', 'replace').strip()
    _need(0 < len(signature) <= 512, 'signing_failed')
    try:
        base64.b64decode(signature, validate=True)
    except (binascii.Error, ValueError):
        raise ReleaseError('signing_failed') from None
    return {'payload': base64.b64encode(payload).decode('ascii'),
            'signature': signature}


def _verify_signature(payload_bytes, signature, public_key_path):
    envelope = json.dumps({'payload':
                           base64.b64encode(payload_bytes).decode('ascii'),
                           'signature': signature}).encode('ascii')
    _run_node(VERIFY_JS, [str(public_key_path)], envelope, 'signature_invalid')
    return True


def _validate_manifest_files(files):
    _need(type(files) is dict and files, 'manifest_invalid')
    total = 0
    for name, spec in sorted(files.items()):
        _validate_relpath(name)
        _need(type(spec) is dict and set(spec) == {'sha256', 'size'},
              'manifest_invalid')
        _need(type(spec.get('sha256')) is str
              and SHA256_RE.fullmatch(spec['sha256']) is not None,
              'manifest_invalid')
        size = spec.get('size')
        _need(type(size) is int and 0 <= size <= MAX_TOTAL_BYTES,
              'manifest_invalid')
        total += size
    return total


def _collect_source(source_directory):
    root = Path(source_directory)
    _need(root.is_dir(), 'invalid_source')
    collected = {}
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(dirnames):
            full = Path(dirpath, name)
            _need(not full.is_symlink(), 'symlink_forbidden')
            relative = os.path.relpath(full, root).replace(os.sep, '/')
            for part in relative.split('/'):
                _validate_component(part)
        for name in sorted(filenames):
            full = Path(dirpath, name)
            _need(not full.is_symlink(), 'symlink_forbidden')
            relative = os.path.relpath(full, root).replace(os.sep, '/')
            _validate_relpath(relative)
            _need(full.is_file(), 'nonregular_file_forbidden')
            _need(len(collected) < MAX_FILES, 'too_many_files')
            _need(full.stat().st_size <= MAX_ENTRY_BYTES, 'entry_too_large')
            with full.open('rb') as stream:
                data = stream.read(MAX_ENTRY_BYTES + 1)
            _need(len(data) <= MAX_ENTRY_BYTES, 'entry_too_large')
            total += len(data)
            _need(total <= MAX_TOTAL_BYTES, 'release_too_large')
            collected[relative] = data
    _need(collected, 'empty_source')
    _need(len(collected) <= MAX_FILES, 'too_many_files')
    _need(sum(map(len, collected.values())) <= MAX_TOTAL_BYTES,
          'release_too_large')
    return collected


def _normalize_required(required_files):
    if required_files is None:
        return []
    if isinstance(required_files, str):
        required_files = [required_files]
    _need(isinstance(required_files, (list, tuple)), 'invalid_required_file')
    normalized = []
    for item in required_files:
        try:
            normalized.append(_validate_relpath(item))
        except ReleaseError:
            raise ReleaseError('invalid_required_file') from None
    return normalized


def _write_package(destination, entries):
    temporary = destination.with_name('%s.tmp-%d' % (destination.name, os.getpid()))
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(entries):
                info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, entries[name])
        os.replace(temporary, destination)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _read_zip_entries(zip_path):
    path = Path(zip_path)
    _need(path.is_file(), 'invalid_archive')
    try:
        archive = zipfile.ZipFile(str(path))
    except (zipfile.BadZipFile, OSError):
        raise ReleaseError('invalid_archive') from None
    with archive:
        infos = archive.infolist()
        _need(len(infos) <= MAX_FILES + 1, 'too_many_files')
        names = [info.filename for info in infos]
        _need(len(set(names)) == len(names), 'duplicate_entry')
        for info in infos:
            _need(not info.is_dir(), 'invalid_entry_type')
            _need(not info.flag_bits & 0x1, 'invalid_archive')
            if info.filename != MANIFEST_NAME:
                _validate_relpath(info.filename)
            _need(info.file_size <= MAX_ENTRY_BYTES, 'entry_too_large')
            if (info.file_size > BOMB_MIN_BYTES and info.compress_size > 0
                    and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO):
                raise ReleaseError('archive_bomb')
        return {name: archive.read(name) for name in names}


def _parse_envelope(entries):
    _need(MANIFEST_NAME in entries, 'manifest_missing')
    raw = entries[MANIFEST_NAME]
    _need(len(raw) <= MAX_MANIFEST_BYTES, 'manifest_too_large')
    try:
        envelope = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseError('manifest_invalid') from None
    _need(type(envelope) is dict and set(envelope) == set(ENVELOPE_KEYS)
          and all(type(envelope[key]) is str for key in ENVELOPE_KEYS),
          'manifest_invalid')
    try:
        payload = base64.b64decode(envelope['payload'], validate=True)
        base64.b64decode(envelope['signature'], validate=True)
    except (binascii.Error, ValueError):
        raise ReleaseError('manifest_invalid') from None
    try:
        manifest = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ReleaseError('manifest_invalid') from None
    _need(_canonical(manifest) == payload, 'canonical_mismatch')
    return envelope, payload, manifest


def _validate_manifest(manifest, entries):
    _need(type(manifest) is dict and set(manifest) == set(MANIFEST_KEYS),
          'manifest_invalid')
    _need(manifest['api'] == PLUGIN_API, 'api_mismatch')
    _validate_version(manifest['version'])
    _need(manifest['binary_included'] is False, 'binary_bundled')
    files = manifest['files']
    total = _validate_manifest_files(files)
    _need(type(manifest['file_count']) is int
          and manifest['file_count'] == len(files), 'manifest_count_mismatch')
    _need(type(manifest['total_bytes']) is int
          and manifest['total_bytes'] == total, 'manifest_size_mismatch')
    _need(total <= MAX_TOTAL_BYTES, 'release_too_large')
    archive_names = set(entries) - {MANIFEST_NAME}
    unmanifested = archive_names - set(files)
    _need(not unmanifested, 'unmanifested_file')
    missing = set(files) - archive_names
    _need(not missing, 'missing_manifest_file')
    for name in sorted(files):
        spec = files[name]
        data = entries[name]
        _need(len(data) == spec['size'], 'size_mismatch')
        _need(_sha256(data) == spec['sha256'], 'hash_mismatch')
    return manifest


def build_release(source_directory, destination_zip, version, private_key_path,
                  required_files=None):
    """Build a signed local plugin release zip from explicit plugin files.

    The source tree is read-only, only allowlisted text suffixes are packaged,
    secrets/private paths are refused, and the private key must already exist.
    Returns sanitized metadata; file contents and key material never are.
    """
    _validate_version(version)
    destination = Path(destination_zip)
    _need(destination.parent.is_dir(), 'invalid_destination')
    _need(not destination.is_dir(), 'invalid_destination')
    required = _normalize_required(required_files)
    collected = _collect_source(source_directory)
    for name in required:
        _need(name in collected, 'required_file_missing')
    key = _check_key_file(private_key_path, 'private_key_missing')
    files = {name: {'sha256': _sha256(data), 'size': len(data)}
             for name, data in sorted(collected.items())}
    manifest = {
        'api': PLUGIN_API,
        'version': version,
        'file_count': len(files),
        'total_bytes': sum(spec['size'] for spec in files.values()),
        'binary_included': False,
        'files': files,
    }
    envelope = _sign_manifest(manifest, key)
    entries = dict(collected)
    entries[MANIFEST_NAME] = _canonical(envelope)
    _write_package(destination, entries)
    return {
        'built': True,
        'api': PLUGIN_API,
        'version': version,
        'file_count': manifest['file_count'],
        'total_bytes': manifest['total_bytes'],
        'binary_included': False,
        'manifest_name': MANIFEST_NAME,
        'package_size': destination.stat().st_size,
        'package_sha256': _sha256(destination.read_bytes()),
        'files': sorted(files),
    }


def verify_release(zip_path, public_key_path):
    """Verify a signed plugin release without extracting it.

    Signature is checked last; structural checks (traversal, duplicates,
    archive bombs, unmanifested or mismatched files) run first so failures
    never depend on trusted-key availability.
    """
    key = _check_key_file(public_key_path, 'public_key_missing')
    entries = _read_zip_entries(zip_path)
    envelope, payload, manifest = _parse_envelope(entries)
    _validate_manifest(manifest, entries)
    _verify_signature(payload, envelope['signature'], key)
    path = Path(zip_path)
    return {
        'verified': True,
        'api': manifest['api'],
        'version': manifest['version'],
        'file_count': manifest['file_count'],
        'total_bytes': manifest['total_bytes'],
        'binary_included': False,
        'manifest_name': MANIFEST_NAME,
        'package_size': path.stat().st_size,
        'package_sha256': _sha256(path.read_bytes()),
        'files': sorted(manifest['files']),
    }


def read_verified_manifest(zip_path, public_key_path):
    """Return only authenticated public file hashes for an installed-file check."""
    entries = _read_zip_entries(zip_path)
    envelope, payload, manifest = _parse_envelope(entries)
    _validate_manifest(manifest, entries)
    _verify_signature(payload, envelope['signature'], public_key_path)
    return manifest


def extract_release(zip_path, public_key_path, destination_directory):
    """Verify then atomically extract a plugin release into a directory.

    Extraction stages into a sibling temporary directory and renames into
    place, so a failure never leaves partial output. An existing destination
    must be empty; extracted files are non-executable.
    """
    metadata = verify_release(zip_path, public_key_path)
    # Verify the exact in-memory bytes that will be extracted. A pathname may
    # have been replaced after the first verification; never reopen it later.
    entries = _read_zip_entries(zip_path)
    envelope, payload, manifest = _parse_envelope(entries)
    _validate_manifest(manifest, entries)
    _verify_signature(payload, envelope['signature'], public_key_path)
    _need(manifest['version'] == metadata['version'] and sorted(manifest['files']) == metadata['files'], 'archive_changed')
    destination = Path(destination_directory)
    parent = destination.parent
    _need(parent.is_dir(), 'invalid_destination')
    existed = False
    if destination.exists():
        _need(destination.is_dir(), 'invalid_destination')
        _need(not any(destination.iterdir()), 'destination_not_empty')
        existed = True
    staging = Path(tempfile.mkdtemp(prefix='.%s.tmp-' % destination.name,
                                    dir=str(parent)))
    try:
        for name in metadata['files']:
            data = entries[name]
            target = staging.joinpath(*name.split('/'))
            _need(str(target.resolve()).startswith(
                str(staging.resolve()) + os.sep), 'path_traversal')
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, 'wb') as handle:
                handle.write(data)
            os.chmod(target, 0o644)
        if existed:
            destination.rmdir()
        os.rename(staging, destination)
    except ReleaseError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise ReleaseError('extraction_failed') from None
    os.chmod(destination, 0o755)
    result = dict(metadata)
    result.pop('verified', None)
    result.pop('package_size', None)
    result.pop('package_sha256', None)
    result.pop('manifest_name', None)
    result['extracted'] = True
    result['destination'] = str(destination)
    return result


def _stdin_private_key():
    content = sys.stdin.buffer.read(MAX_KEY_BYTES + 1)
    _need(bool(content) and len(content) <= MAX_KEY_BYTES, 'invalid_private_key')
    handle, name = tempfile.mkstemp(prefix='harmonika-signing-', suffix='.pem')
    os.close(handle)
    os.chmod(name, 0o600)
    Path(name).write_bytes(content)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='tui_release.py',
        description='Build, verify and extract signed local Harmonika TUI '
                    'plugin releases (no network, no key auto-creation).')
    commands = parser.add_subparsers(dest='command', required=True)

    build = commands.add_parser('build', help='build a signed release zip')
    build.add_argument('--source', required=True)
    build.add_argument('--output', required=True)
    build.add_argument('--version', required=True)
    build.add_argument('--private-key', required=True,
                       help="private key path, or '-' to read key content from stdin")
    build.add_argument('--require', action='append', default=[],
                       metavar='PATH', help='file that must be packaged (repeatable)')

    verify = commands.add_parser('verify', help='verify a signed release zip')
    verify.add_argument('--package', required=True)
    verify.add_argument('--public-key', required=True)

    extract = commands.add_parser('extract', help='verify and extract a release')
    extract.add_argument('--package', required=True)
    extract.add_argument('--public-key', required=True)
    extract.add_argument('--destination', required=True)

    args = parser.parse_args(argv)
    temporary_key = None
    try:
        if args.command == 'build':
            key = args.private_key
            if key == '-':
                temporary_key = _stdin_private_key()
                key = temporary_key
            result = build_release(args.source, args.output, args.version,
                                   key, args.require or None)
        elif args.command == 'verify':
            result = verify_release(args.package, args.public_key)
        else:
            result = extract_release(args.package, args.public_key,
                                     args.destination)
    except ReleaseError as exc:
        print(json.dumps({'ok': False, 'error': exc.code}))
        return 2
    finally:
        if temporary_key is not None:
            try:
                os.unlink(temporary_key)
            except OSError:
                pass
    print(json.dumps({'ok': True, 'result': result}, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
