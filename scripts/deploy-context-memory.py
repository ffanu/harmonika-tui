#!/usr/bin/env python3
"""Manual owner activation on Linux from a verified private staging directory.

No database schema/key migration, binary update or unrelated service restart.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

BASE = Path('/opt/harmonika-ai')
ALLOWED = {'api/planner-context.js', 'api/secret-firewall.cjs', 'api/internal-tool-planner.js',
           'core_engine/project_memory.py', 'core_engine/registry.py'}
SERVICES = ('harmonika-ai', 'harmonika-shared-core')
UNCHANGED = ('harmonika-web-chat-live', 'harmonika-coding-runtime', 'harmonika-telco-engine')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(source, target):
    tmp = target.with_name(target.name+'.context-new')
    shutil.copyfile(source, tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)


def healthy():
    for port, path in ((17890, '/health'), (18195, '/healthz')):
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+path, timeout=3) as response:
            if response.status != 200:
                raise ValueError('service_health_failed')


def idle():
    # Refuse to interrupt requests or an active model child. Metadata only.
    lines = run('ss', '-Hnt', 'state', 'established').splitlines()
    if any(any(':'+str(p)+' ' in line for p in (17890, 18195)) for line in lines):
        return False
    parent = run('systemctl', 'show', 'harmonika-ai', '-p', 'MainPID', '--value')
    children = run('ps', '-eo', 'ppid=,comm=').splitlines()
    return not any(line.strip().split(None, 1)[0] == parent for line in children if line.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('owner_required')
    stage = args.stage.resolve()
    manifest = json.loads((stage/'manifest.json').read_text())
    if set(manifest['files']) != ALLOWED:
        raise ValueError('unexpected_deploy_files')
    for name, digest in manifest['files'].items():
        if sha(stage/name) != digest:
            raise ValueError('stage_hash_changed')
    for name, digest in manifest['baseline'].items():
        path = BASE/name
        if (sha(path) if path.exists() else None) != digest:
            raise ValueError('production_baseline_changed')
    for name in ('core_engine/registry.py', 'core_engine/project_memory.py'):
        compile((stage/name).read_text(), name, 'exec')
    for name in ('api/planner-context.js', 'api/secret-firewall.cjs', 'api/internal-tool-planner.js'):
        run('node', '--check', str(stage/name))
    healthy()
    others = {s: run('systemctl', 'show', s, '-p', 'MainPID', '--value') for s in UNCHANGED}
    for _ in range(30):
        if idle():
            time.sleep(1)
            if idle():
                break
        time.sleep(1)
    else:
        raise ValueError('activation_deferred_busy')
    backup = Path('/var/backups/harmonika-context')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(backup, 0o700)
    existing = []
    for name in ALLOWED:
        src = BASE/name
        if src.exists():
            dst = backup/name
            dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(src, dst)
            existing.append(name)
    (backup/'baseline.json').write_text(json.dumps(manifest['baseline']))
    os.chmod(backup/'baseline.json', 0o600)
    try:
        # Dependencies first. Already-running processes retain their old module cache.
        for name in sorted(ALLOWED, key=lambda x: x.endswith(('registry.py', 'internal-tool-planner.js'))):
            atomic(stage/name, BASE/name)
        for service in reversed(SERVICES):
            run('systemctl', 'restart', service)
        for _ in range(15):
            try:
                healthy()
                break
            except Exception:
                time.sleep(1)
        else:
            raise ValueError('activation_health_failed')
        if others != {s: run('systemctl', 'show', s, '-p', 'MainPID', '--value') for s in UNCHANGED}:
            raise ValueError('unrelated_service_changed')
        # Authenticated read smoke against an unused synthetic principal; no customer reads.
        headers = {'X-Harmonika-Core-Secret': Path('/etc/harmonika-ai/core-engine.token').read_text().strip(),
                   'X-Harmonika-Principal': 'api-token:9223372036854775806', 'Content-Type': 'application/json'}
        data = json.dumps({'skill_id': 'memory.get', 'input': {'project_id': 'activation-smoke', 'key': 'build'}}).encode()
        request = urllib.request.Request('http://127.0.0.1:18195/invoke', data=data, headers=headers)
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.load(response)
        if result.get('data', {}).get('found') is not False:
            raise ValueError('project_memory_smoke_failed')
        report = {'status': 'active', 'backup': str(backup), 'files': manifest['files'], 'services_restarted': list(SERVICES),
                  'unrelated_service_pids_unchanged': True, 'database_schema_unchanged': True, 'synthetic_read_smoke': True}
        (backup/'activation.json').write_text(json.dumps(report, indent=2))
        os.chmod(backup/'activation.json', 0o600)
        print(json.dumps(report))
    except Exception:
        for name in ALLOWED:
            if name in existing:
                atomic(backup/name, BASE/name)
            else:
                (BASE/name).unlink(missing_ok=True)
        for service in reversed(SERVICES):
            run('systemctl', 'restart', service)
        healthy()
        print(json.dumps({'status': 'rolled_back', 'backup': str(backup)}))
        raise


if __name__ == '__main__':
    main()
