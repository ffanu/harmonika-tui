#!/usr/bin/env python3
"""Activate Level15 task continuity after 18 independently graded native missions."""
import datetime
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

FILES = ('task-continuity.cjs', 'decision-recovery.cjs', 'agent-controller.cjs', 'internal-tool-planner.js')
BASE = Path('/opt/harmonika-ai/api')
GUARDS_SHA256 = 'dc67eb94a1d16dbca9c878a07cc14ed63687e81c9642ee814852340b0faf791d'


def digest(path):
    if path.is_symlink():
        raise RuntimeError('source_symlink_denied')
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def load_guards(stage):
    source = stage/'scripts/activate-debug-workflow.py'
    if digest(source) != GUARDS_SHA256:
        raise RuntimeError('activation_guards_changed')
    spec = importlib.util.spec_from_file_location('guards', source)
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)
    return guards


def restore_owned(installed, hashes, backup, base, modes, replace):
    conflicts = [n for n in installed if not (base/n).is_file() or digest(base/n) != hashes[n]]
    for n in installed:
        if n not in conflicts:
            if (backup/n).is_file():
                replace(backup/n, base/n, modes[n])
            else:
                (base/n).unlink()
    return conflicts


def verify(stage, base=BASE):
    expected = json.loads((stage/'expected-baseline.json').read_text())
    verified = json.loads((stage/'verification.json').read_text())
    native = json.loads((stage/'level15-candidate.json').read_text())
    candidate = stage/'backend/planner'
    if set(expected) != set(FILES) or set(verified['candidate_sha256']) != set(FILES):
        raise RuntimeError('manifest_mismatch')
    if not all(verified.get(k) for k in ('local_passed', 'integration_passed', 'activation_guards_passed')):
        raise RuntimeError('verification_required')
    cases = {prefix+str(i).zfill(2) for prefix in ('I','R','E') for i in range(1,7)}
    if (native.get('production') is not False or native.get('synthetic_only') is not True
            or native.get('passed') is not True or native.get('independent_operator_proof') is not True
            or len(native['rows']) != 18 or {r['id'] for r in native['rows']} != cases
            or not all(r.get('passed') is True and r.get('verified') and not r.get('missing')
                       and r.get('scope_denials') == 0 for r in native['rows'])):
        raise RuntimeError('candidate_canary_required')
    hashes = verified['candidate_sha256']
    if native.get('source_sha256') != hashes or any(digest(candidate/n) != hashes[n] for n in FILES):
        raise RuntimeError('candidate_changed')
    if any(digest(base/n) != expected[n] for n in FILES):
        raise RuntimeError('baseline_changed')
    return expected, hashes


def main():
    stage = Path(sys.argv[1])
    guards = load_guards(stage)
    with open('/run/lock/harmonika-planner-activation.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        expected, hashes = verify(stage)
        for n in FILES:
            subprocess.run(['node', '--check', str(stage/'backend/planner'/n)], capture_output=True, check=True)
        guards.await_idle()
        verify(stage)
        others = {u: guards.pid(u) for u in ('harmonika-web-chat-live', 'harmonika-shared-core', 'harmonika-coding-runtime')}
        backup = Path('/var/backups/harmonika-level15')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(parents=True, mode=0o700)
        modes = {n: (BASE/n).stat().st_mode & 0o777 if (BASE/n).exists() else 0o600 for n in FILES}
        for n in FILES:
            if (BASE/n).exists():
                shutil.copy2(BASE/n, backup/n)
        installed, restarted = [], False
        report = {'status': 'failed', 'candidate_sha256': hashes, 'backup': str(backup), 'prior_absent': [n for n in FILES if expected[n] is None]}
        try:
            for n in FILES:
                if digest(BASE/n) != expected[n]:
                    raise RuntimeError('baseline_changed')
                guards.replace(stage/'backend/planner'/n, BASE/n, modes[n])
                installed.append(n)
            guards.await_idle()
            if any(digest(BASE/n) != hashes[n] for n in FILES):
                raise RuntimeError('installed_changed')
            restarted = True
            subprocess.run(['systemctl', 'restart', 'harmonika-ai'], capture_output=True, check=True)
            if not guards.health():
                raise RuntimeError('health_failed')
            if any(guards.pid(u) != pid for u, pid in others.items()):
                raise RuntimeError('unrelated_service_changed')
            report.update(status='active_pending_postcheck', health_http=200, unrelated_service_pids_unchanged=True)
        except Exception:
            conflicts = restore_owned(installed, hashes, backup, BASE, modes, guards.replace)
            if not conflicts:
                if restarted:
                    guards.await_idle(allow_stopped=True)
                    subprocess.run(['systemctl', 'restart', 'harmonika-ai'], capture_output=True, check=True)
                    report['rollback_healthy'] = guards.health()
                report['status'] = 'rolled_back'
            else:
                report.update(status='rollback_operator_conflict_no_restart', conflict_files=conflicts)
            raise
        finally:
            (stage/'activation.json').write_text(json.dumps(report, indent=2))
            (stage/'activation.json').chmod(0o600)
            print(json.dumps(report))


if __name__ == '__main__':
    main()
