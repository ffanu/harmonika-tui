"""Prepare by default. --apply is only for an explicitly approved gateway restart."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

SERVICE='harmonika-web-chat-live.service'
OTHERS=('harmonika-ai.service','harmonika-shared-core.service','harmonika-quality-audit.service')
LOCK_PATH=Path('/var/lock/harmonika-quality-activation.lock')
DROPIN_PATH=Path('/etc/systemd/system/'+SERVICE+'.d/quality-audit.conf')


def command(*args):
    return subprocess.run(args,check=True,capture_output=True,text=True).stdout.strip()


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def pid(service):return command('systemctl','show',service,'-p','MainPID','--value')


def atomic(source,target,mode=0o600):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.quality-new')
    shutil.copyfile(source,temporary);temporary.chmod(mode);temporary.replace(target)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-dir',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--restart-approved',action='store_true',help='Operator explicitly approved the named gateway restart after drain was deferred')
    args=parser.parse_args();stage=args.candidate_dir.resolve()
    manifest=json.loads((stage/'manifest.json').read_text());binary=stage.parent/'gateway-candidate'
    proof=json.loads((binary.parent/'quality-build-proof.json').read_text())
    if proof.get('instrumentation_link_verified') is not True or proof.get('binary_sha256')!=sha(binary):
        raise ValueError('verified_instrumented_build_required')
    overlay=json.loads((stage/'overlay.json').read_text())['Replace']
    plan_path=stage/'activation-plan.json'
    if not args.apply:
        execution=command('systemctl','show',SERVICE,'-p','ExecStart','--value')
        match=re.search(r'path=([^ ;]+)',execution)
        if not match:raise ValueError('gateway_executable_unknown')
        live=Path(match[1]);plan={'service':SERVICE,'gateway_pid':pid(SERVICE),'live_binary':str(live),
            'live_binary_sha256':sha(live),'candidate_binary_sha256':sha(binary),
            'dropin':str(DROPIN_PATH),
            'activation':'pending_explicit_restart_approval','services_to_restart':[SERVICE]}
        if Path(plan['dropin']).exists():raise ValueError('existing_quality_dropin_preserved')
        plan_path.write_text(json.dumps(plan,indent=2));plan_path.chmod(0o600)
        print(json.dumps(plan));return
    if os.geteuid()!=0:raise ValueError('root_required')
    plan=json.loads(plan_path.read_text())
    with LOCK_PATH.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if pid(SERVICE)!=plan['gateway_pid'] or sha(Path(plan['live_binary']))!=plan['live_binary_sha256'] or sha(binary)!=plan['candidate_binary_sha256']:
            raise ValueError('gateway_baseline_changed')
        for name,digest in manifest['baseline'].items():
            if sha(Path(name))!=digest:raise ValueError('gateway_source_changed')
        for name,digest in manifest['candidate'].items():
            if sha(Path(overlay[name]))!=digest:raise ValueError('candidate_source_changed')
            if name not in manifest['baseline'] and Path(name).exists():raise ValueError('new_gateway_module_exists')
        dropin=Path(plan['dropin'])
        if dropin.exists():raise ValueError('existing_quality_dropin_preserved')
        # Refuse busy state; never terminate an active request to make installation fit.
        stable=0
        for _ in range(3 if args.restart_approved else 30):
            busy=command('ss','-Hnt','state','established','( sport = :3210 or dport = :3210 )')
            stable=0 if busy else stable+1
            if stable>=3:break
            time.sleep(1)
        else:
            if not args.restart_approved:raise ValueError('activation_deferred_gateway_busy')
        other_pids={service:pid(service) for service in OTHERS}
        backup=Path('/var/backups/harmonika-quality-gateway')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup.mkdir(parents=True,mode=0o700);saved={}
        for path in [Path(plan['live_binary']),*[Path(n) for n in manifest['baseline']]]:
            destination=backup/path.name;shutil.copy2(path,destination);destination.chmod(0o600);saved[path]=destination
        installed={}
        try:
            for target_name in sorted(manifest['candidate'],key=lambda n:Path(n).name=='main.go'):
                target=Path(target_name);atomic(Path(overlay[target_name]),target);installed[target]=sha(target)
            live=Path(plan['live_binary']);atomic(binary,live,0o755);installed[live]=sha(live)
            dropin.parent.mkdir(parents=True,exist_ok=True)
            dropin.write_text('[Service]\nEnvironment=HARMONIKA_QUALITY_AUDIT_URL=http://127.0.0.1:18208/ingest\nEnvironment=HARMONIKA_QUALITY_INGEST_TOKEN_FILE=/etc/harmonika-ai/quality-audit/ingest_token.key\n')
            dropin.chmod(0o600)
            command('systemctl','daemon-reload');command('systemctl','restart',SERVICE)
            for _ in range(30):
                try:
                    urllib.request.urlopen('http://127.0.0.1:3210/v1/models',timeout=2)
                except urllib.error.HTTPError as error:
                    if error.code==401:break
                except Exception:pass
                time.sleep(.5)
            else:raise ValueError('gateway_auth_smoke_failed')
            if other_pids!={service:pid(service) for service in OTHERS}:raise ValueError('unrelated_service_changed')
            result={'gateway_capture':'active_pending_authenticated_request_smoke','service':SERVICE,
                    'backup':str(backup),'new_pid':pid(SERVICE),'unrelated_service_pids_unchanged':True,
                    'logging_keys_rotated':False,'idle_drain_verified':stable>=3,
                    'explicit_gateway_restart_approval':args.restart_approved}
            (stage/'activation-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        except Exception:
            if any(sha(path)!=digest for path,digest in installed.items()):raise ValueError('rollback_refused_concurrent_change') from None
            for path in installed:
                if path in saved:atomic(saved[path],path,0o755 if str(path)==plan['live_binary'] else 0o600)
                else:path.unlink()
            dropin.unlink(missing_ok=True)
            command('systemctl','daemon-reload');command('systemctl','restart',SERVICE)
            raise ValueError('gateway_activation_rolled_back') from None

if __name__=='__main__':main()
