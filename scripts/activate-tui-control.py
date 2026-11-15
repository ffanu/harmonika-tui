#!/usr/bin/env python3
"""Manual signed native-TUI plugin activation and guarded rollback; no process signals."""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tui_release_activation', SOURCE/'runtime/tools/tui_release.py')
release = importlib.util.module_from_spec(spec);spec.loader.exec_module(release)
REQUIRED = {'package.json', 'tui.tsx', 'model.mjs', 'runtime.mjs', 'retry.mjs', 'helper/tui_control.py', 'helper/tui_config_audit.py'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.control-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.chmod(name, 0o600);os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 524288:
        raise ValueError('unsafe_config_file')
    if os.name != 'nt' and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('unsafe_config_file')
    return path.read_bytes()


def activate(package, public_key, root, binary, smoke):
    root = Path(root).resolve();binary = Path(binary)
    if not root.is_dir():
        raise ValueError('owner_config_required')
    before_binary = sha(binary)
    metadata = release.verify_release(package, public_key)
    if metadata['api'] != 'harmonika-tui-plugin-v2' or not REQUIRED <= set(metadata['files']):
        raise ValueError('invalid_control_release')
    if smoke.get('ok') is not True or smoke.get('native_sha256') != before_binary or smoke.get('version') != metadata['version'] or smoke.get('package_sha256') != metadata['package_sha256']:
        raise ValueError('matching_native_smoke_required')
    names = ('harmonika.json', 'cli.json', 'control-active.json')
    originals = {name: read(root/name) for name in names}
    if originals['harmonika.json'] is None:
        raise ValueError('owner_config_required')
    if originals['cli.json'] is None and (root/'tui.json').exists():
        raise ValueError('legacy_cli_migration_required')
    core = json.loads(originals['harmonika.json'])
    cli = json.loads(originals['cli.json'] or '{}')
    previous = json.loads(originals['control-active.json'] or '{}')
    if type(core) is not dict or type(cli) is not dict or type(cli.get('plugins', [])) is not list:
        raise ValueError('invalid_owner_config')
    versions = root/'control-releases'
    if versions.is_symlink():
        raise ValueError('unsafe_control_directory')
    versions.mkdir(mode=0o700, exist_ok=True)
    candidate = versions/metadata['version']
    if candidate.is_symlink():
        raise ValueError('unsafe_control_directory')
    if candidate.exists():
        # Re-activation is allowed only if the original verified files still match.
        payload = release.read_verified_manifest(package, public_key)
        if payload['version'] != metadata['version'] or sha(package) != metadata['package_sha256']:
            raise ValueError('control_package_changed')
        for name, item in payload['files'].items():
            if (candidate/name).is_symlink() or not (candidate/name).is_file() or sha(candidate/name) != item['sha256']:
                raise ValueError('installed_control_file_changed')
    else:
        release.extract_release(package, public_key, candidate)
    # Preserve provider/model/MCP/permission/brand settings exactly. The native
    # normalizer supports the existing legacy fields; no broad migration needed.
    core['update'] = 'disable'
    old_target = previous.get('package')
    def target(entry):
        return entry.get('package') if type(entry) is dict else entry
    cli['plugins'] = [entry for entry in cli.get('plugins', []) if target(entry) not in (old_target, str(candidate))]
    cli['plugins'].append({'package': str(candidate), 'options': {'python': sys.executable}})
    if type(core.get('plugins', [])) is not list:
        raise ValueError('invalid_owner_config')
    # Companion server hook; preserve unrelated native and legacy plugins.
    core['plugins'] = [entry for entry in core.get('plugins', []) if target(entry) not in (old_target, str(candidate))]
    core['plugins'].append({'package': str(candidate)})
    current = {'version': metadata['version'], 'package': str(candidate), 'package_sha256': metadata['package_sha256'], 'native_sha256': before_binary}
    updates = {'harmonika.json': json.dumps(core, indent=2, ensure_ascii=False).encode()+b'\n',
               'cli.json': json.dumps(cli, indent=2, ensure_ascii=False).encode()+b'\n',
               'control-active.json': json.dumps(current, indent=2).encode()+b'\n'}
    backup_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = root/'control-backups'/backup_id;backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup.parent, 0o700)
    info = {'version': metadata['version'], 'native_sha256': before_binary, 'files': {}}
    for name in names:
        data = originals[name]
        if data is not None:
            atomic(backup/name, data)
        info['files'][name] = {'existed': data is not None, 'activated_sha256': hashlib.sha256(updates[name]).hexdigest()}
    atomic(backup/'rollback.json', json.dumps(info).encode())
    for name in names:
        if read(root/name) != originals[name]:
            raise ValueError('owner_config_changed')
    written = []
    try:
        for name in names:
            atomic(root/name, updates[name])
            written.append(name)
        if sha(binary) != before_binary:
            raise ValueError('native_binary_changed')
    except Exception:
        for name in written:
            if read(root/name) != updates[name]:
                continue
            if originals[name] is None:
                (root/name).unlink(missing_ok=True)
            else:
                atomic(root/name, originals[name])
        raise
    return {'ok': True, 'version': metadata['version'], 'backup_id': backup_id, 'native_binary_unchanged': True,
            'user_processes_signalled': False, 'automatic_update': False, 'legacy_permissions_preserved': True,
            'package_verified': True, 'status': 'configured_for_native_tui', 'runtime_guard_configured': True, 'package_sha256': metadata['package_sha256']}


def rollback(root, binary, backup_id):
    root = Path(root).resolve()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z', backup_id):
        raise ValueError('invalid_backup_id')
    backup = root/'control-backups'/backup_id
    info = json.loads(read(backup/'rollback.json') or '{}')
    if set(info.get('files', {})) != {'harmonika.json', 'cli.json', 'control-active.json'}:
        raise ValueError('invalid_rollback_manifest')
    if sha(binary) != info['native_sha256']:
        raise ValueError('native_binary_changed')
    for name, row in info['files'].items():
        if (sha(root/name) if (root/name).exists() else None) != row['activated_sha256']:
            raise ValueError('configuration_changed_since_activation')
    for name, row in info['files'].items():
        if row['existed']:
            atomic(root/name, read(backup/name))
        else:
            (root/name).unlink()
    return {'ok': True, 'status': 'rolled_back', 'native_binary_unchanged': True, 'user_processes_signalled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.home()/'.config/harmonika-terminal')
    parser.add_argument('--binary', type=Path, default=Path.home()/'Projects/harmonika-engine/bin/harmonika')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--public-key', type=Path)
    parser.add_argument('--smoke-report', type=Path)
    parser.add_argument('--rollback')
    args = parser.parse_args()
    try:
        if args.rollback:
            out = rollback(args.root, args.binary, args.rollback)
        else:
            if not all((args.package, args.smoke_report)):
                raise ValueError('package_and_smoke_required')
            out = activate(args.package, args.public_key or args.root/'trusted-public.pem', args.root, args.binary, json.loads(args.smoke_report.read_text()))
    except Exception as error:
        code = str(error) if type(error) is ValueError and re.fullmatch(r'[a-z][a-z0-9_]{1,63}', str(error)) else getattr(error, 'code', 'control_activation_failed')
        print(json.dumps({'ok': False, 'error': code}));return 2
    print(json.dumps(out));return 0


if __name__ == '__main__':
    raise SystemExit(main())
