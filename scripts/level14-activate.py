#!/usr/bin/env python3
"""Activate only the coding service: sealed tests, SQLite admission fence and CAS rollback.

Run on the Linux backend after independent exam evidence is sealed by the operator.
Never handles credentials or changes the planner, public gateway or TUI binary.
"""
import datetime,fcntl,hashlib,json,os,shutil,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path

BASE=Path('/opt/harmonika-ai/coding_runtime')
DB=Path('/var/lib/harmonika-coding/state.sqlite')
UNIT='harmonika-coding-runtime'
FILES=('core.py','agent.py','service.py','sandbox.py','action_evidence.py')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def compare(root,expected):
    if any(digest(root/n)!=h for n,h in expected.items()):raise RuntimeError('source_compare_failed')

def replace(src,dst):
    tmp=dst.with_name(dst.name+'.level14-new')
    with tmp.open('xb') as f:f.write(src.read_bytes());f.flush();os.fsync(f.fileno())
    tmp.chmod(0o600);os.replace(tmp,dst)

def rollback(base,backup,installed,candidate,prior):
    # Check ALL installed files before changing any. A concurrent operator wins.
    compare(base,{n:candidate[n] for n in installed})
    for n in installed:
        if prior[n] is None:(base/n).unlink()
        else:replace(backup/n,base/n)
    compare(base,{n:prior[n] for n in installed})

def system(*args):
    return subprocess.run(['systemctl',*args],check=True,capture_output=True,text=True).stdout.strip()

def pid(unit):return system('show',unit,'--property=MainPID','--value')

def idle(d):
    # Approval requests are user-owned work too; wait rather than interrupt them.
    if d.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','approval_required')").fetchone()[0]:raise RuntimeError('coding_jobs_not_idle')
    if d.execute("SELECT count(*) FROM turns WHERE state='running'").fetchone()[0]:raise RuntimeError('coding_turns_not_idle')
    if d.execute("SELECT count(*) FROM sqlite_master WHERE name='debug_sessions'").fetchone()[0]:
        # Table layout is verified before querying; unexpected layout fails closed.
        columns={r[1] for r in d.execute('PRAGMA table_info(debug_sessions)')}
        field='state' if 'state' in columns else 'status' if 'status' in columns else None
        if field is None:raise RuntimeError('unknown_debug_schema')
        if d.execute('SELECT count(*) FROM debug_sessions WHERE '+field+" NOT IN ('closed','expired','interrupted','failed')").fetchone()[0]:raise RuntimeError('debug_session_not_idle')

def health():
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:18187/healthz',timeout=2) as r:
                if r.status==200 and json.load(r).get('ok') is True:return True
        except Exception:pass
        time.sleep(.5)
    return False

def main():
    stage=Path(sys.argv[1]);manifest=json.loads((stage/'level14-release.json').read_text())
    source=stage/'backend/coding_runtime';candidate=manifest['candidate_sha256'];prior=manifest['prior_sha256']
    companions=manifest['companion_sha256'];baseline={**companions,**prior}
    if set(companions)&set(FILES):raise RuntimeError('companion_manifest_overlap')
    if set(candidate)!=set(FILES) or set(prior)!=set(FILES):raise RuntimeError('manifest_files')
    if not all(manifest.get(k) for k in ('linux_tests_passed','activation_guard_tests_passed','go_bridge_passed','independent_review_addressed','native_missions_passed','candidate_api_sse_passed')):raise RuntimeError('verification_required')
    if manifest.get('native_count')!=9 or manifest.get('adversarial_count')!=36:raise RuntimeError('exam_incomplete')
    # The operator seals actual result digests, not only pass booleans.
    evidence=stage/'level14-evidence'
    if len(manifest.get('evidence_sha256',{}))<12:raise RuntimeError('evidence_missing')
    compare(evidence,manifest['evidence_sha256']);compare(source,candidate)
    current_image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',manifest['executor_image']],text=True).strip()
    if current_image!=manifest['executor_image_id']:raise RuntimeError('executor_image_changed')
    with open('/run/lock/harmonika-coding-activation.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);compare(BASE,baseline)
        others={u:pid(u) for u in ('harmonika-ai','harmonika-web-chat-live','harmonika-shared-core')}
        oldpid=pid(UNIT)
        if oldpid=='0':raise RuntimeError('coding_service_not_active')
        for _ in range(2):
            with sqlite3.connect(DB,timeout=2) as d:idle(d)
            time.sleep(1)
        backup=Path('/var/backups/harmonika-level14')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(mode=0o700,parents=True,exist_ok=False)
        for n,h in prior.items():
            if h is not None:shutil.copy2(BASE/n,backup/n)
        (backup/'prior-sha256.json').write_text(json.dumps(prior))
        with sqlite3.connect(DB) as d,sqlite3.connect(backup/'state.sqlite') as b:d.backup(b)
        (backup/'state.sqlite').chmod(0o600)
        installed=[];stopped=False;report={'status':'failed','backup':str(backup),'candidate_sha256':candidate,'prior_sha256':prior,'old_pid':oldpid}
        try:
            compare(BASE,baseline);compare(source,candidate)
            d=sqlite3.connect(DB,timeout=2)
            try:
                d.execute('BEGIN IMMEDIATE');idle(d)
                if pid(UNIT)!=oldpid:raise RuntimeError('coding_pid_changed')
                # Fence admission before the graceful unit stop; no new job can commit.
                system('stop',UNIT);stopped=True
                idle(d)
            finally:d.rollback();d.close()
            compare(BASE,baseline)
            for n in FILES:replace(source/n,BASE/n);installed.append(n)
            compare(BASE,candidate);system('start',UNIT)
            if not health():raise RuntimeError('coding_health_failed')
            if any(pid(u)!=p for u,p in others.items()):raise RuntimeError('unrelated_service_changed')
            report.update(status='active',health_http=200,new_pid=pid(UNIT),unrelated_service_pids_unchanged=True,admission_fenced=True)
        except Exception:
            if installed:
                # A started candidate may have accepted real work. Fence and check
                # again; never roll files back under an active user job.
                with sqlite3.connect(DB,timeout=2) as d:
                    d.execute('BEGIN IMMEDIATE');idle(d)
                    compare(BASE,{n:candidate[n] for n in installed})
                    system('stop',UNIT)
                rollback(BASE,backup,installed,candidate,prior)
                report['status']='rolled_back'
            if stopped:system('start',UNIT);report['rollback_healthy']=health()
            raise
        finally:
            out=stage/'level14-activation.json';out.write_text(json.dumps(report,indent=2));out.chmod(0o600);print(json.dumps(report))

if __name__=='__main__':main()
