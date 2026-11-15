#!/usr/bin/env python3
"""Activate the verified async-job controller; retain the existing executor policy."""
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location(
    'activation_guards', Path(__file__).with_name('activate-debug-workflow.py'))
guards = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guards)
BASE = Path('/opt/harmonika-ai/api')
EXPECTED = {
    'agent-controller.cjs': '14ea332d26171b9e19d01b8044c400d8fe62dd0f3bef7b3efbb9812f4e584b7b',
    'internal-tool-planner.js': 'f89751d893167f1f06509d8c95bd02cf1ef283528678596a78aee9d724482d02',
    'debug-workflow.cjs': '4e6e654dae019500500370129be722ee2c146c2480501091224e7913f73f380c',
}
FILES = ['job-lifecycle.cjs', 'agent-controller.cjs']


def main():
    stage = Path(sys.argv[1]).resolve()
    lock = open('/run/lock/harmonika-planner-activation.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    for name, expected in EXPECTED.items():
        if guards.digest(BASE/name) != expected:
            raise RuntimeError('baseline_changed_'+name)
    if (BASE/'job-lifecycle.cjs').exists():
        raise RuntimeError('helper_already_exists')
    candidate = stage/'backend/planner'
    approved = json.loads((stage/'engine-stage.json').read_text())['files']
    for name in FILES:
        if guards.digest(candidate/name) != approved[name]:
            raise RuntimeError('candidate_changed_'+name)
        subprocess.run(['node','--check',str(candidate/name)],check=True,capture_output=True)
    smoke = json.loads((stage/'model-smoke.json').read_text())
    if len(smoke)!=10 or not all(v['passed'] and not v['internal_task_state_exposed'] for v in smoke):
        raise RuntimeError('model_canary_not_passed')
    if not all(json.loads((stage/(case+'-initial-result.json')).read_text())['passed'] for case in ['easy','medium','hard']):
        raise RuntimeError('native_prechecks_not_passed')
    guards.await_idle()
    others = {unit:guards.pid(unit) for unit in ['harmonika-web-chat-live','harmonika-shared-core']}
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/harmonika-job-lifecycle')/stamp
    backup.mkdir(mode=0o700,parents=True)
    shutil.copy2(BASE/'agent-controller.cjs',backup/'agent-controller.cjs')
    mode = (BASE/'agent-controller.cjs').stat().st_mode & 0o777
    installed=[];restart_attempted=False
    report={'status':'failed','backup':str(backup),'candidate_sha256':approved}
    try:
        for name in FILES:
            guards.replace(candidate/name,BASE/name,mode);installed.append(name)
        guards.await_idle();restart_attempted=True
        subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
        if not guards.health():raise RuntimeError('health_failed')
        if any(guards.pid(unit)!=previous for unit,previous in others.items()):
            raise RuntimeError('unrelated_service_pid_changed')
        report.update(status='active_pending_native_resume',health_http=200,
                      unrelated_service_pids_unchanged=True,
                      installed_sha256={name:guards.digest(BASE/name) for name in FILES})
    except Exception:
        if all(guards.digest(BASE/name)==approved[name] for name in installed):
            if 'agent-controller.cjs' in installed:
                guards.replace(backup/'agent-controller.cjs',BASE/'agent-controller.cjs',mode)
            if 'job-lifecycle.cjs' in installed:(BASE/'job-lifecycle.cjs').unlink()
            if restart_attempted:
                guards.await_idle(allow_stopped=True)
                subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
                report['rollback_healthy']=guards.health()
            report['status']='rolled_back'
        raise
    finally:
        (stage/'activation.json').write_text(json.dumps(report,indent=2)+'\n')
        os.chmod(stage/'activation.json',0o600)
        print(json.dumps(report))


if __name__=='__main__':main()
