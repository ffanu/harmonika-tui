"""Owner deployment, after isolated Phase2 smoke; rollback only these two services."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4

ROOT=Path('/opt/harmonika-ai');stage=ROOT/'coding-phase2-stage/coding_runtime';go=ROOT/'coding-phase2-gateway-stage'
live=Path('/srv/harmonika-web-chat/current');package=ROOT/'coding_runtime';state=Path('/var/lib/harmonika-coding/state.sqlite')
marker=state.parent/'ready.json';dropin=Path('/etc/systemd/system/harmonika-coding-runtime.service.d/phase2.conf')
CHANGED=('public_ai_coding.go','public_ai_coding_test.go','public_ai_coding_live_test.go','public_api_docs.go')
def run(argv,**kw):return subprocess.run(argv,check=True,timeout=150,**kw)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def health(url,expected=200):
    for _ in range(40):
        try:
            with urllib.request.urlopen(url,timeout=2) as r:
                if r.status==expected:return
        except urllib.error.HTTPError as e:
            if e.code==expected:return
        except OSError:pass
        time.sleep(.25)
    raise RuntimeError('service_health_failed')
def main():
    report=json.loads(Path('/tmp/harmonika-phase2-smoke-result.json').read_text())
    assert report['ok'] is True,'phase2_acceptance_required'
    assert len(report['phase2_skills_verified'])==20,'all_20_skills_required'
    current_digest=hashlib.sha256(json.dumps({p.name:sha(p) for p in sorted(stage.glob('*.py'))},sort_keys=True).encode()).hexdigest()
    assert report.get('runtime_source_sha256')==current_digest,'smoke_must_match_current_source'
    extra=json.loads(Path('/tmp/harmonika-phase2-opencode-smoke.json').read_text())
    assert extra['summary']['failed']==0 and extra['summary']['passed']>=78,'opencode_acceptance_required'
    assert 'PASS: TestCodingLive' in Path('/tmp/harmonika-phase2-public-stage.out').read_text(),'public_contract_smoke_required'
    assert '\nOK\n' in Path('/tmp/harmonika-phase2-regression-linux.out').read_text(),'regression_required'
    assert '\nOK\n' in Path('/tmp/harmonika-phase2-tools-linux.out').read_text(),'toolchain_required'
    assert '\nOK\n' in Path('/tmp/harmonika-phase2-debug-linux.out').read_text(),'actual_debugger_test_required'
    for name,wanted in json.loads((go/'baseline-live-hashes.json').read_text()).items():
        assert sha(live/name)==wanted,'live_source_changed_rebase_required'
    with sqlite3.connect(state) as d:
        assert d.execute("SELECT count(*) FROM jobs WHERE state IN ('running','queued')").fetchone()[0]==0,'active_jobs_retry_later'
        assert d.execute("SELECT count(*) FROM turns WHERE state='running'").fetchone()[0]==0,'active_turns_retry_later'
    run(['go','test','./...'],cwd=go)
    run(['go','build','-o',str(go/'harmonika-web.phase2'),'.'],cwd=go)
    backup=Path('/var/backups/harmonika-coding-phase2')/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]);backup.mkdir(mode=0o700,parents=True)
    shutil.copytree(package,backup/'coding_runtime',ignore=shutil.ignore_patterns('__pycache__'))
    with sqlite3.connect(state) as src,sqlite3.connect(backup/'state.sqlite') as dst:src.backup(dst)
    os.chmod(backup/'state.sqlite',0o600)
    for n in (*CHANGED,'harmonika-web'):shutil.copy2(live/n,backup/n)
    if marker.exists():shutil.copy2(marker,backup/'ready.json')
    if dropin.exists():shutil.copy2(dropin,backup/'phase2.conf')
    candidate=ROOT/('coding_runtime.phase2-'+uuid4().hex);shutil.copytree(package,candidate,ignore=shutil.ignore_patterns('__pycache__'))
    for p in stage.glob('*.py'):shutil.copy2(p,candidate/p.name)
    saved=ROOT/('coding_runtime.before-'+uuid4().hex)
    modified=False
    try:
        # Stop/restart only authorized coding runtime; queued/executing work was checked.
        run(['systemctl','stop','harmonika-coding-runtime.service']);modified=True
        os.replace(package,saved);os.replace(candidate,package)
        dropin.parent.mkdir(parents=True,exist_ok=True)
        dropin.write_text('[Service]\nEnvironment=HARMONIKA_CODING_PHASE2_ENABLED=true\nEnvironment=HARMONIKA_CODING_IMAGE=harmonika-coding:20261024-phase2\nEnvironment=PYTHONDONTWRITEBYTECODE=1\nTimeoutStopSec=40\n')
        run(['systemctl','daemon-reload']);run(['systemctl','start','harmonika-coding-runtime.service']);health('http://127.0.0.1:18187/healthz')
        for n in CHANGED:shutil.copy2(go/n,live/n)
        new=live/'harmonika-web.phase2-next';shutil.copy2(go/'harmonika-web.phase2',new);os.chmod(new,0o755);os.replace(new,live/'harmonika-web')
        run(['systemctl','restart','harmonika-web-chat-live.service']);health('http://127.0.0.1:3210/v1/coding/capabilities',401)
        value={'ok':True,'release':'2026.10.24.21','backup':str(backup),'phase2_marker_pending_public_smoke':True,'services':['harmonika-coding-runtime.service','harmonika-web-chat-live.service']}
        Path('/tmp/harmonika-phase2-activation.json').write_text(json.dumps(value));print(json.dumps(value))
    except BaseException:
        if modified:
            run(['systemctl','stop','harmonika-coding-runtime.service'])
            if saved.exists():
                failed=ROOT/('coding_runtime.failed-'+uuid4().hex);os.replace(package,failed);os.replace(saved,package)
            if (backup/'phase2.conf').exists():shutil.copy2(backup/'phase2.conf',dropin)
            else:dropin.unlink(missing_ok=True)
            for n in CHANGED:shutil.copy2(backup/n,live/n)
            rollback=live/'harmonika-web.rollback-next';shutil.copy2(backup/'harmonika-web',rollback);os.replace(rollback,live/'harmonika-web')
            if (backup/'ready.json').exists():shutil.copy2(backup/'ready.json',marker)
            run(['systemctl','daemon-reload']);run(['systemctl','start','harmonika-coding-runtime.service']);run(['systemctl','restart','harmonika-web-chat-live.service'])
        raise
if __name__=='__main__':main()
