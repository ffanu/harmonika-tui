#!/usr/bin/env python3
"""Guarded single-file activation of tested completion evidence guidance."""
import datetime
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

spec=importlib.util.spec_from_file_location('guards',Path(__file__).with_name('activate-debug-workflow.py'))
guards=importlib.util.module_from_spec(spec)
spec.loader.exec_module(guards)
BASE=Path('/opt/harmonika-ai/api')
TARGET='agent-controller.cjs'
EXPECTED={
    TARGET:'09a2b8993e1dc2107c552872883e5f9696da0088c0655b640739a7cfdfee4e7f',
    'job-lifecycle.cjs':'a4a99aaa5e4831572465318e840ce7f266cd5f6b1a625c8bfac2692b252dd56f',
    'secret-firewall.cjs':'28ea9236e8f8ef6e2b6d4c7a9f9bd8fbd0c3e315271d7c5ad2613b48f8512fe4',
    'internal-tool-planner.js':'f89751d893167f1f06509d8c95bd02cf1ef283528678596a78aee9d724482d02',
}

def baseline():
    for name,value in EXPECTED.items():
        if guards.digest(BASE/name)!=value:raise RuntimeError('baseline_changed_'+name)

def main():
    stage=Path(sys.argv[1]).resolve()
    with open('/run/lock/harmonika-planner-activation.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        baseline()
        candidate=stage/'backend/planner'/TARGET
        approved=json.loads((stage/'verification.json').read_text())
        digest=approved['candidate_sha256']
        if guards.digest(candidate)!=digest:raise RuntimeError('candidate_changed')
        if not approved['local_passed'] or not approved['integration_passed'] or not approved['activation_guards_passed']:
            raise RuntimeError('verification_not_passed')
        subprocess.run(['node','--check',str(candidate)],check=True,capture_output=True)
        guards.await_idle()
        baseline()
        others={u:guards.pid(u) for u in ['harmonika-web-chat-live','harmonika-shared-core']}
        backup=Path('/var/backups/harmonika-level4-evidence')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(mode=0o700,parents=True)
        shutil.copy2(BASE/TARGET,backup/TARGET)
        mode=(BASE/TARGET).stat().st_mode&0o777
        installed=False
        restarted=False
        report={'status':'failed','backup':str(backup),'candidate_sha256':digest}
        try:
            guards.replace(candidate,BASE/TARGET,mode)
            installed=True
            guards.await_idle()
            if guards.digest(BASE/TARGET)!=digest:raise RuntimeError('installed_changed')
            restarted=True
            subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
            if not guards.health():raise RuntimeError('health_failed')
            if any(guards.pid(u)!=previous for u,previous in others.items()):raise RuntimeError('unrelated_service_pid_changed')
            report.update(status='active',health_http=200,unrelated_service_pids_unchanged=True,
                          installed_sha256=guards.digest(BASE/TARGET))
        except Exception:
            if installed and guards.digest(BASE/TARGET)==digest:
                guards.replace(backup/TARGET,BASE/TARGET,mode)
                if restarted:
                    guards.await_idle(allow_stopped=True)
                    subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
                    report['rollback_healthy']=guards.health()
                report['status']='rolled_back'
            raise
        finally:
            output=stage/'activation.json'
            output.write_text(json.dumps(report,indent=2)+'\n')
            os.chmod(output,0o600)
            print(json.dumps(report))

if __name__=='__main__':main()
