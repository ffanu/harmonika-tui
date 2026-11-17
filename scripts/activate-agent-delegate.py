#!/usr/bin/env python3
"""Run on the gateway host: compare, wait idle, install, health-check or rollback.

Read an operator-prepared baseline JSON from stdin; never print environment,
database credentials, provider output or user data. Does not restart the planner.
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
from urllib.parse import urlsplit, unquote
from urllib.request import urlopen

UNIT = 'harmonika-web-chat-live.service'
ROOT = Path('/srv/harmonika-web-chat/releases/20261105-ocr-rtx-160607')
CHANGED = ('delegate_skill.go', 'delegate_jobs.go')

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def pid(unit):
    return int(subprocess.check_output(['systemctl', 'show', unit, '--value', '-p', 'MainPID'], text=True).strip())

def atomic(p, data, mode):
    q = p.with_name('.'+p.name+'.delegate-new')
    with q.open('xb') as f:
        os.fchmod(f.fileno(), mode); f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(q, p)
    fd = os.open(p.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def sockets(process):
    owned = set()
    for f in (Path('/proc')/str(process)/'fd').iterdir():
        try:
            link = os.readlink(f)
            if link.startswith('socket:['): owned.add(link[8:-1])
        except FileNotFoundError: pass
    rows = []
    for name in ('tcp', 'tcp6'):
        for line in (Path('/proc/net')/name).read_text().splitlines()[1:]:
            f = line.split()
            if f[9] in owned:
                rows.append((int(f[1].split(':')[-1],16), int(f[2].split(':')[-1],16), f[3], f[4]))
    return rows

def child_count(process):
    count = 0
    for p in Path('/proc').glob('[0-9]*'):
        try:
            if int((p/'stat').read_text().rsplit(') ',1)[1].split()[1]) == process: count += 1
        except FileNotFoundError: pass
    return count

def active_images(process):
    values = dict(x.split(b'=',1) for x in (Path('/proc')/str(process)/'environ').read_bytes().split(b'\0') if b'=' in x)
    u = urlsplit(values[b'DATABASE_URL'].decode())
    env = dict(os.environ, PGHOST=u.hostname, PGPORT=str(u.port or 5432),
               PGUSER=unquote(u.username or ''), PGPASSWORD=unquote(u.password or ''),
               PGDATABASE=u.path.lstrip('/'), PGCONNECT_TIMEOUT='3')
    p = subprocess.run(['psql','-X','-tA','-c',"SELECT count(*) FROM member_ai_image_jobs WHERE status IN ('queued','running')"], env=env,capture_output=True,text=True,timeout=6)
    if p.returncode: raise RuntimeError('idle_database_check_failed')
    return int(p.stdout.strip())

def idle(process):
    planner = pid('harmonika-ai.service')
    return (pid(UNIT) == process and child_count(process) == 0 and
            child_count(planner) == 0 and active_images(process) == 0 and
            not any(state=='01' and (local==3210 or remote==443 or queue!='00000000:00000000')
                    for local,remote,state,queue in sockets(process)))

def health():
    try:
        with urlopen('http://127.0.0.1:3210/healthz',timeout=3) as r:
            return r.status == 200 and json.load(r).get('status') == 'ok'
    except Exception: return False

def activate(stage, baseline):
    manifest = json.loads((stage/'verified.json').read_text())
    assert manifest['stage'] == str(stage), 'stage_identity'
    tests = manifest['go_tests']
    assert tests['passed'] >= 36 and tests['failed']==tests['skipped']==0, 'tests_not_verified'
    assert digest(stage/'harmonika-web-candidate') == manifest['binary_sha256'], 'candidate_binary_changed'
    for n,h in manifest['source_sha256'].items():
        assert digest(stage/'app'/n)==h, 'candidate_source_changed'
    candidate_names = set(manifest['source_sha256'])
    production_go = {p.name for p in ROOT.glob('*.go')}
    staged_go = {p.name for p in (stage/'app').glob('*.go')}
    assert production_go | candidate_names == staged_go, 'source_set_changed'
    for p in (stage/'app').iterdir():
        if p.is_file() and p.name not in candidate_names:
            assert (ROOT/p.name).is_file() and digest(ROOT/p.name)==digest(p), 'companion_source_changed'
    for n,h in baseline['sources'].items():
        assert digest(ROOT/n)==h, 'baseline_source_changed'
    assert not (ROOT/'delegate_jobs.go').exists(), 'new_source_already_exists'
    binary = ROOT/'harmonika-web'
    assert digest(binary)==baseline['binary_sha256'], 'baseline_binary_changed'
    original_pid = pid(UNIT)
    assert Path(os.readlink(Path('/proc')/str(original_pid)/'cwd')) == ROOT, 'active_unit_changed'
    others = {u:pid(u) for u in ('harmonika-ai.service','harmonika-master-skills.service')}
    # Consecutive observations; fail closed rather than restart active work.
    for _ in range(5):
        if not idle(original_pid): raise RuntimeError('gateway_busy_no_restart')
        time.sleep(1)
    assert digest(binary)==baseline['binary_sha256'], 'binary_changed_before_install'
    backup = Path('/var/backups/harmonika-agent-v32')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True,mode=0o700)
    saved = {}
    for n in ('harmonika-web',*CHANGED):
        p=ROOT/n;saved[n]=p.read_bytes() if p.exists() else None
        if saved[n] is not None:
            (backup/n).write_bytes(saved[n]);(backup/n).chmod(0o600)
    installed = {}
    try:
        assert idle(original_pid), 'gateway_became_busy'
        for n in CHANGED:
            data=(stage/'app'/n).read_bytes();atomic(ROOT/n,data,0o644);installed[n]=hashlib.sha256(data).hexdigest()
        data=(stage/'harmonika-web-candidate').read_bytes();atomic(binary,data,0o755);installed['harmonika-web']=hashlib.sha256(data).hexdigest()
        subprocess.run(['systemctl','restart',UNIT],check=True,capture_output=True,timeout=40)
        for _ in range(20):
            if health(): break
            time.sleep(.5)
        assert health(), 'gateway_health_failed'
        assert others == {u:pid(u) for u in others}, 'unrelated_service_changed'
        result={'active':True,'unit':UNIT,'old_pid':original_pid,'new_pid':pid(UNIT),'binary_sha256':digest(binary),'source_sha256':{n:digest(ROOT/n) for n in CHANGED},'unrelated_pids_unchanged':True,'backup':str(backup),'health':200,'idle_checks':6}
        (backup/'activation.json').write_text(json.dumps(result,indent=2));(backup/'activation.json').chmod(0o600)
        return result
    except BaseException:
        for n,h in installed.items():
            assert digest(ROOT/n)==h, 'rollback_refuses_concurrent_change'
        for n in installed:
            if saved[n] is None: (ROOT/n).unlink()
            else: atomic(ROOT/n,saved[n],0o755 if n=='harmonika-web' else 0o644)
        if installed:
            subprocess.run(['systemctl','restart',UNIT],check=True,capture_output=True,timeout=40)
        raise

if __name__=='__main__':
    import sys
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    try: print(json.dumps(activate(a.stage.resolve(),json.load(sys.stdin))))
    except Exception as e:
        # Only fixed technical signatures; exceptions must not echo private data.
        print(json.dumps({'active':False,'error':str(e) if isinstance(e,(AssertionError,RuntimeError)) else type(e).__name__}));raise SystemExit(1)
