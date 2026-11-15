"""Activate verified guidance after native exit; durable state permits read-only reconciliation."""
import argparse,base64,fcntl,hashlib,json,os,shlex,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
REMOTE=r'''import base64,fcntl,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
r=json.load(sys.stdin);stage=Path(r['stage']);state=stage/'level11-finish-state.json'
def save(value):
 p=state.with_suffix('.tmp')
 with p.open('w') as f:os.chmod(p,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
 os.replace(p,state);fd=os.open(stage,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def canary(script,report,count):
 q=subprocess.run(['node',str(stage/'scripts'/script),str(stage)],capture_output=True,text=True,timeout=1800);p=stage/(script+'.output');p.write_text(q.stdout+q.stderr);os.chmod(p,0o600);rows=json.loads((stage/report).read_text());assert q.returncode==0 and len(rows)==count and all(x['passed'] and not x['client_tools_executed'] and not x['internal_state_exposed'] for x in rows),'canary_failed';return rows
with open('/run/lock/harmonika-level11-finish.lock','a') as operation_lock:
 fcntl.flock(operation_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if state.exists():raise RuntimeError('finish_already_attempted_reconcile_only')
 save({'status':'preflight','candidate_sha256':r['hashes']});activation=None
 try:
  verified=json.loads((stage/'verification.json').read_text());assert verified['candidate_sha256']==r['hashes'];p=stage/'scripts/level11-decision-smoke.cjs';p.write_bytes(base64.b64decode(r['canary']));os.chmod(p,0o600)
  spec=importlib.util.spec_from_file_location('guards',stage/'scripts/activate-debug-workflow.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g);others={u:g.pid(u) for u in ['harmonika-web-chat-live','harmonika-shared-core']}
  save({'status':'activating','candidate_sha256':r['hashes']});q=subprocess.run(['python3',str(stage/'scripts/activate-source-reference.py'),str(stage)],capture_output=True,text=True,timeout=180)
  activation=json.loads((stage/'activation.json').read_text()) if (stage/'activation.json').exists() else None
  assert q.returncode==0 and activation and activation['status']=='active','activation_failed';save({'status':'verifying','activation':activation})
  source=canary('source-reference-smoke.cjs','source-after.json',5);decisions=canary('level11-decision-smoke.cjs','level11-model-smoke.json',8)
  assert all(g.digest(Path('/opt/harmonika-ai/api')/n)==h for n,h in r['hashes'].items());assert g.health();assert all(g.pid(u)==pid for u,pid in others.items());value={'status':'active','activation':activation,'source_canaries':source,'decision_canaries':decisions,'unrelated_service_pids_unchanged':True};save(value);print(json.dumps(value))
 except Exception as original:
  value={'status':'activation_failed','error_type':type(original).__name__,'activation':activation};save(value)
  if activation and activation['status']=='active':
   try:
    save({'status':'rolling_back','activation':activation,'error_type':type(original).__name__})
    with open('/run/lock/harmonika-planner-activation.lock','a') as lock:
     deadline=time.monotonic()+30
     while True:
      try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
      except BlockingIOError:
       if time.monotonic()>=deadline:raise RuntimeError('rollback_lock_busy')
       time.sleep(.5)
     base=Path('/opt/harmonika-ai/api');assert all(g.digest(base/n)==h for n,h in r['hashes'].items()),'rollback_baseline_changed';g.await_idle();backup=Path(activation['backup']);expected={n:g.digest(backup/n) for n in r['hashes']}
     for n in expected:g.replace(backup/n,base/n,(base/n).stat().st_mode&0o777)
     assert all(g.digest(base/n)==h for n,h in expected.items()),'rollback_hash_failed';g.await_idle();subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True);assert g.health();assert all(g.pid(u)==pid for u,pid in others.items());rows=canary('source-reference-smoke.cjs','source-after.json',5);value={'status':'rolled_back','reason':'post_activation_verification_failed','rollback_healthy':True,'rollback_sha256':expected,'rollback_source_canaries':rows};save(value)
   except Exception as rollback_error:
    value={'status':'rollback_failed','error_type':type(rollback_error).__name__,'original_error_type':type(original).__name__,'activation':activation};save(value)
  print(json.dumps(value));sys.exit(1)
'''
def atomic_json(path,value):
 p=path.with_suffix('.tmp')
 with p.open('w') as f:os.chmod(p,0o600);json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(p,path);fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def fetch_state(stage):
 code="from pathlib import Path;import json,sys;p=Path(json.load(sys.stdin)['stage'])/'level11-finish-state.json';print(p.read_text() if p.exists() else json.dumps({'status':'remote_state_absent_outcome_unknown'}))"
 q=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'stage':stage}),capture_output=True,text=True,timeout=30);assert q.returncode==0;return json.loads(q.stdout)
def main():
 p=argparse.ArgumentParser();p.add_argument('--atlas',type=Path,required=True);p.add_argument('--boreal',type=Path,required=True);p.add_argument('--stage',required=True);p.add_argument('--reconcile',action='store_true');a=p.parse_args();root=Path(Path('/tmp/harmonika-level11-root.path').read_text().strip());locks=[]
 for name in ['examiner','atlas','boreal']:
  f=(root/(name+'.lock')).open('a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
 for name,file in [('atlas',a.atlas),('boreal',a.boreal)]:
  value=json.loads(file.read_text());assert value['case']==name and value['phase']=='final' and value['passed'] and all(value['checks'].values());meta=json.loads((root/(name+'-meta.json')).read_text());assert all(hashlib.sha256((root/name/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items())
 attempt=root/'backend-activation-attempt.json';final=root/'backend-activation.json'
 if a.reconcile:
  assert attempt.exists() and json.loads(attempt.read_text())['stage']==a.stage;value=fetch_state(a.stage)
 else:
  if attempt.exists() or final.exists():raise RuntimeError('activation_already_attempted_use_reconcile')
  files=['python-reference-spans.py','secret-firewall.cjs','planner-context.js','agent-controller.cjs','long-decision.cjs','internal-tool-planner.js','debug-workflow.cjs','decision-recovery.cjs'];hashes={n:hashlib.sha256((REPO/'backend/planner'/n).read_bytes()).hexdigest() for n in files};atomic_json(attempt,{'stage':a.stage,'candidate_sha256':hashes,'status':'attempted_not_yet_confirmed'})
  try:
   q=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(REMOTE)],input=json.dumps({'stage':a.stage,'hashes':hashes,'canary':base64.b64encode((REPO/'scripts/level11-decision-smoke.cjs').read_bytes()).decode()}),capture_output=True,text=True,timeout=4000);value=json.loads(q.stdout)
  except (subprocess.TimeoutExpired,ValueError):
   try:value=fetch_state(a.stage)
   except Exception:value={'status':'transport_unknown_reconcile_required'}
 if value['status'] in ['active','rolled_back','rollback_failed','activation_failed']:atomic_json(final,value)
 else:atomic_json(root/'backend-activation-pending.json',value)
 print(json.dumps(value));assert value['status']=='active'
if __name__=='__main__':main()
