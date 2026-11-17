#!/usr/bin/env python3
"""Private staged activation. No secrets, text, credentials or process signals in output."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.request import Request, urlopen

CORE = 'harmonika-ai.service'
WORKER = 'harmonika-translation.service'
API = Path('/opt/harmonika-ai/api')
BASE = API.parent
TOKEN = Path('/etc/harmonika-ai/translation-engine.token')
UNIT = Path('/etc/systemd/system') / WORKER

def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.PIPE).strip()

def pid(unit):
    return int(run('systemctl', 'show', unit, '-p', 'MainPID', '--value'))

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def atomic(p, body, mode=0o644):
    q = p.with_name(p.name + '.translation-new')
    with q.open('xb') as f:
        os.fchmod(f.fileno(), mode); f.write(body); f.flush(); os.fsync(f.fileno())
    os.replace(q, p)

def idle(core):
    if pid(CORE) != core: return False
    for p in Path('/proc').glob('[0-9]*/stat'):
        try:
            if int(p.read_text().rsplit(') ',1)[1].split()[1]) == core: return False
        except FileNotFoundError: pass
    owned = set()
    for f in (Path('/proc') / str(core) / 'fd').iterdir():
        try:
            link = os.readlink(f)
            if link.startswith('socket:['): owned.add(link[8:-1])
        except FileNotFoundError: pass
    for name in ('tcp', 'tcp6'):
        for line in (Path('/proc/net') / name).read_text().splitlines()[1:]:
            f = line.split()
            if f[9] in owned and f[3] == '01': return False
    return True

def activate(stage):
    assert os.geteuid() == 0, 'root_required'
    baseline = json.loads((stage/'core-baseline.json').read_text())
    targets = {'server.js': stage/'server.js.candidate',
               'internal-tool-planner.js': stage/'backend/planner/internal-tool-planner.js',
               'translation-mode.cjs': stage/'backend/planner/translation-mode.cjs',
               'translation-chat.cjs': stage/'backend/planner/translation-chat.cjs'}
    for name in targets:
        p = API/name
        assert (sha(p) if p.exists() else None) == baseline[name], 'baseline_changed'
        run('node','--check',str(targets[name]))
    assert not (BASE/'translation_engine').exists() and not UNIT.exists(), 'translation_already_installed'
    report = json.loads((stage/'planner-verified.json').read_text())
    assert report['translation_passed'] == 3 and report['translation_failed'] == 0, 'planner_tests_required'
    assert sha(targets['internal-tool-planner.js']) == report['candidate_sha256'], 'tested_source_changed'
    original = pid(CORE)
    for _ in range(5):
        assert idle(original), 'core_busy_no_restart'
        time.sleep(1)
    backup = Path('/var/backups/harmonika-translation') / datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup.mkdir(parents=True,mode=0o700)
    for name in targets:
        if (API/name).exists(): shutil.copy2(API/name,backup/name)
    (backup/'baseline.json').write_text(json.dumps(baseline));(backup/'baseline.json').chmod(0o600)
    worker_started = False
    try:
        assert idle(original), 'core_became_busy'
        for name, source in targets.items():
            assert (sha(API/name) if (API/name).exists() else None) == baseline[name], 'compare_failed'
            atomic(API/name,source.read_bytes())
        shutil.copytree(stage/'backend/translation_engine',BASE/'translation_engine',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        audit = Path('/var/lib/harmonika-translation');audit.mkdir(mode=0o700,exist_ok=True);audit.chmod(0o700)
        if not TOKEN.exists(): atomic(TOKEN,(secrets.token_urlsafe(48)+'\n').encode(),0o600)
        assert TOKEN.stat().st_mode & 0o777 == 0o600, 'token_permissions'
        atomic(UNIT,('''[Unit]
Description=Harmonika private translation engine
After=network.target harmonika-ai.service
[Service]
Type=simple
WorkingDirectory=/opt/harmonika-ai
ExecStart=/opt/harmonika-ai/builder-venv/bin/python -m translation_engine.service
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/harmonika-translation
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=256M
TasksMax=32
LimitNOFILE=256
[Install]
WantedBy=multi-user.target
''').encode())
        run('systemctl','daemon-reload')
        run('systemctl','restart',CORE)
        for _ in range(30):
            if pid(CORE)>0: break
            time.sleep(.2)
        run('systemctl','enable','--now',WORKER);worker_started=True
        result=None
        for _ in range(30):
            try:
                req=Request('http://127.0.0.1:17918/health',headers={'X-Harmonika-Translation-Secret':TOKEN.read_text().strip()})
                with urlopen(req,timeout=2) as res: result=json.load(res)
                if result.get('ok'):break
            except Exception:time.sleep(.3)
        assert result and result.get('ok'), 'worker_health_failed'
        out={'active':True,'worker':WORKER,'core_restarted':True,'core_pid':pid(CORE),'backup':str(backup),
             'source_sha256':{n:sha(API/n) for n in targets},'inference_verified':result['inference_verified']}
        (backup/'activation.json').write_text(json.dumps(out));print(json.dumps(out))
    except BaseException:
        if worker_started: subprocess.run(['systemctl','stop',WORKER],capture_output=True)
        for name in targets:
            p=API/name
            assert p.exists() and sha(p)==sha(targets[name]), 'rollback_concurrent_change'
            if (backup/name).exists(): atomic(p,(backup/name).read_bytes())
            else:p.unlink()
        subprocess.run(['systemctl','restart',CORE],capture_output=True)
        raise

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--stage',type=Path,required=True)
    args=parser.parse_args()
    try:activate(args.stage.resolve())
    except Exception as e:
        print(json.dumps({'active':False,'error':str(e) if isinstance(e,AssertionError) else type(e).__name__}));raise SystemExit(1)
