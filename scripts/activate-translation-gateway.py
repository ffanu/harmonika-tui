#!/usr/bin/env python3
"""Compare tested sources, back up, activate only an idle gateway, or roll back."""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import time

def activate(stage):
    spec=importlib.util.spec_from_file_location('idle_guard',Path(__file__).with_name('activate-agent-delegate.py'))
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    guard.run=lambda *args:subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
    baseline=json.loads((stage/'go-baseline.json').read_text())
    guard.ROOT=root=Path(baseline['root']);guard.UNIT=unit='harmonika-web-chat.service'
    assert Path('/srv/harmonika-web-chat/current').resolve()==root,'release_changed'
    for name, h in baseline['sources'].items(): assert guard.digest(root/name)==h,'source_changed'
    binary=root/'harmonika-web';assert guard.digest(binary)==baseline['binary_sha256'],'binary_changed'
    changed=('main.go','translation_gateway.go','translation_chat.go','translation_member.go')
    for name in changed[1:]:assert not (root/name).exists(),'translation_already_installed'
    events=[json.loads(s) for s in (stage/'go-tests.jsonl').read_text().splitlines()]
    passed=[e for e in events if e.get('Action')=='pass' and e.get('Test') and '/' not in e['Test']]
    assert len(passed)>=20 and not any(e.get('Action') in ('fail','skip') for e in events),'go_tests_not_passed'
    for name in baseline['sources']:
        if name!='main.go':assert guard.digest(stage/'app'/name)==baseline['sources'][name],'staged_dependency_changed'
    expected=json.loads((stage/'go-candidate.json').read_text())
    assert expected['binary']==guard.digest(stage/'harmonika-web-candidate'),'candidate_changed'
    assert expected['source']=={n:guard.digest(stage/'app'/n) for n in changed},'tested_source_changed'
    original=guard.pid(unit)
    for _ in range(5):
        assert guard.idle(original),'gateway_busy_no_restart'
        time.sleep(1)
    backup=Path('/var/backups/harmonika-translation')/('gateway-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    backup.mkdir(parents=True,mode=0o700)
    saved={n:(root/n).read_bytes() if (root/n).exists() else None for n in ('harmonika-web',*changed)}
    for n,b in saved.items():
        if b is not None:(backup/n).write_bytes(b);(backup/n).chmod(0o600)
    others={u:guard.pid(u) for u in ('harmonika-ai.service','harmonika-translation.service','harmonika-master-skills.service')}
    # A stale duplicate unit was repeatedly failing to bind the live port. Stop
    # only that verified, non-running duplicate before replacing the shared binary.
    duplicate='harmonika-web-chat-live.service';duplicate_stopped=False
    if guard.pid(duplicate)==0:
        info=guard.run('systemctl','show',duplicate,'-p','ExecStart','--value')
        journal=guard.run('journalctl','-u',duplicate,'-n','5','--no-pager','-o','cat')
        if str(binary) not in info and '/srv/harmonika-web-chat/current/harmonika-web' not in info:raise AssertionError('duplicate_identity_changed')
        assert 'address already in use' in journal,'duplicate_failure_changed'
        guard.run('systemctl','stop',duplicate);duplicate_stopped=True
    else:raise AssertionError('duplicate_has_active_process')
    installed={}
    try:
        assert guard.idle(original),'gateway_became_busy'
        assert guard.digest(binary)==baseline['binary_sha256'],'binary_changed_before_install'
        for name in changed:
            p=root/name
            assert (guard.digest(p) if p.exists() else None)==baseline['sources'].get(name),'source_changed_before_install'
            data=(stage/'app'/name).read_bytes();guard.atomic(p,data,0o644);installed[name]=hashlib.sha256(data).hexdigest()
        data=(stage/'harmonika-web-candidate').read_bytes();guard.atomic(binary,data,0o755);installed['harmonika-web']=hashlib.sha256(data).hexdigest()
        guard.run('systemctl','restart',unit)
        for _ in range(30):
            if guard.health():break
            time.sleep(.3)
        assert guard.health(),'gateway_health_failed'
        assert others=={u:guard.pid(u) for u in others},'unrelated_service_changed'
        result={'active':True,'unit':unit,'old_pid':original,'new_pid':guard.pid(unit),'backup':str(backup),'go_tests_passed':len(passed),'source_sha256':expected['source'],'binary_sha256':guard.digest(binary),'unrelated_pids_unchanged':True,'failed_duplicate_unit_stopped':duplicate_stopped}
        (backup/'activation.json').write_text(json.dumps(result));print(json.dumps(result))
    except BaseException:
        for name,h in installed.items():assert guard.digest(root/name)==h,'rollback_concurrent_change'
        for name in installed:
            if saved[name] is None:(root/name).unlink()
            else:guard.atomic(root/name,saved[name],0o755 if name=='harmonika-web' else 0o644)
        if installed:subprocess.run(['systemctl','restart',unit],capture_output=True)
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True);args=parser.parse_args()
    try:activate(args.stage.resolve())
    except Exception as e:
        print(json.dumps({'active':False,'error':str(e) if isinstance(e,AssertionError) else type(e).__name__}));raise SystemExit(1)
