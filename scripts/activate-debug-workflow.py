#!/usr/bin/env python3
"""Activate the verified debugging controller on the Harmonika Linux gateway."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import fcntl

BASE = Path('/opt/harmonika-ai/api')
EXPECTED = {
    'agent-controller.cjs': '5e8a6a0acebb088bff2cf06618e537a67b23d6385be860e5bb7fc953409d19dd',
    'internal-tool-planner.js': 'f89751d893167f1f06509d8c95bd02cf1ef283528678596a78aee9d724482d02',
}
FILES = ['debug-workflow.cjs', 'agent-controller.cjs']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def pid(unit):
    return run('systemctl', 'show', unit, '-p', 'MainPID', '--value')


def idle(allow_stopped=False):
    main = pid('harmonika-ai')
    connected = run('ss', '-Hnt', 'state', 'established', 'sport = :17890 or dport = :17890')
    children = any(line.split()[1] == main for line in run('ps', '-eo', 'pid=,ppid=,comm=').splitlines() if len(line.split()) >= 3)
    return (main != '0' or allow_stopped) and not connected and not children


def await_idle(allow_stopped=False):
    for _ in range(23):
        if idle(allow_stopped):
            time.sleep(1)
            if idle(allow_stopped):
                return
        time.sleep(1)
    raise RuntimeError('gateway_busy_no_restart')


def replace(source, target, mode):
    temporary = target.with_name(target.name + '.debug-activation.tmp')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, mode)
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def health():
    for _ in range(15):
        try:
            with urllib.request.urlopen('http://127.0.0.1:17890/health', timeout=2) as response:
                if response.status == 200:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(1)
    return False


def main():
    stage = Path(sys.argv[1]).resolve()
    lock = open('/run/lock/harmonika-debug-workflow.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for name, expected in EXPECTED.items():
        if digest(BASE/name) != expected:
            raise RuntimeError('baseline_changed_' + name)
    if (BASE/'debug-workflow.cjs').exists():
        raise RuntimeError('helper_already_exists')
    candidate = stage/'backend/planner'
    hashes = {name: digest(candidate/name) for name in FILES}
    for name in FILES:
        subprocess.run(['node', '--check', str(candidate/name)], check=True, capture_output=True)
    smoke = json.loads((stage/'model-smoke.json').read_text())
    if len(smoke) != 6 or not all(x['passed'] and not x['internal_task_state_exposed'] for x in smoke):
        raise RuntimeError('model_canary_not_passed')
    await_idle()
    others = {unit: pid(unit) for unit in ['harmonika-web-chat-live', 'harmonika-shared-core']}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/harmonika-debug-workflow')/stamp
    backup.mkdir(mode=0o700, parents=True)
    shutil.copy2(BASE/'agent-controller.cjs', backup/'agent-controller.cjs')
    mode = (BASE/'agent-controller.cjs').stat().st_mode & 0o777
    installed = []
    restart_attempted = False
    report = {'status': 'failed', 'backup': str(backup), 'candidate_sha256': hashes}
    try:
        for name in FILES:
            replace(candidate/name, BASE/name, mode)
            installed.append(name)
        await_idle()
        restart_attempted = True
        subprocess.run(['systemctl', 'restart', 'harmonika-ai'], check=True, capture_output=True)
        if not health():
            raise RuntimeError('health_failed')
        if any(pid(unit) != previous for unit, previous in others.items()):
            raise RuntimeError('unrelated_service_pid_changed')
        report.update(status='active_pending_native_verification', health_http=200,
                      unrelated_service_pids_unchanged=True,
                      installed_sha256={name: digest(BASE/name) for name in FILES})
    except Exception:
        # Refuse to overwrite a subsequent operator change during recovery.
        if all(digest(BASE/name) == hashes[name] for name in installed):
            if 'agent-controller.cjs' in installed:
                replace(backup/'agent-controller.cjs', BASE/'agent-controller.cjs', mode)
            if 'debug-workflow.cjs' in installed:
                (BASE/'debug-workflow.cjs').unlink()
            if restart_attempted:
                await_idle(allow_stopped=True)
                subprocess.run(['systemctl', 'restart', 'harmonika-ai'], check=True, capture_output=True)
                report['rollback_healthy'] = health()
            report['status'] = 'rolled_back'
        raise
    finally:
        (stage/'activation.json').write_text(json.dumps(report, indent=2)+'\n')
        os.chmod(stage/'activation.json', 0o600)
        print(json.dumps(report))


if __name__ == '__main__':
    main()
