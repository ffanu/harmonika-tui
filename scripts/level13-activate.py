#!/usr/bin/env python3
"""Linux-only hash/CAS activation. New proof module rollback tracks prior absence."""
import datetime,fcntl,hashlib,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('guards',Path(__file__).with_name('activate-debug-workflow.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
BASE=Path('/opt/harmonika-ai/api')
def main():
 stage=Path(sys.argv[1]);expected=json.loads((stage/'expected-baseline.json').read_text());approval=json.loads((stage/'verification.json').read_text());candidate=stage/'backend/planner';files=list(expected)+['proof-contract.cjs'];installed=[];restarted=False
 if set(approval['candidate_sha256'])!=set(files):raise RuntimeError('candidate_manifest_mismatch')
 with open('/run/lock/harmonika-planner-activation.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  def baseline():
   if any(g.digest(BASE/n)!=h for n,h in expected.items()) or (BASE/'proof-contract.cjs').exists():raise RuntimeError('baseline_changed')
  baseline()
  if not all(approval.get(k) for k in ['local_passed','integration_passed','activation_guards_passed','level13_activation_guards_passed']):raise RuntimeError('verification_required')
  canaries=json.loads((stage/'proof-live-candidate.json').read_text())
  if len(canaries)!=7 or not all(c['passed'] for c in canaries):raise RuntimeError('live_candidate_required')
  live_hashes=json.loads((stage/'proof-live-candidate-manifest.json').read_text())
  if set(live_hashes)!=set(['agent-controller.cjs','proof-contract.cjs','internal-tool-planner.js']) or any(g.digest(candidate/n)!=h for n,h in live_hashes.items()):raise RuntimeError('live_candidate_source_changed')
  hashes=approval['candidate_sha256']
  if any(g.digest(candidate/n)!=hashes[n] for n in files):raise RuntimeError('candidate_changed')
  for n in files:
   if n.endswith(('.js','.cjs')):subprocess.run(['node','--check',str(candidate/n)],check=True,capture_output=True)
  g.await_idle();baseline();others={u:g.pid(u) for u in ['harmonika-web-chat-live','harmonika-shared-core']}
  backup=Path('/var/backups/harmonika-level13')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(mode=0o700,parents=True)
  for n in expected:shutil.copy2(BASE/n,backup/n)
  (backup/'prior-absence.json').write_text(json.dumps(['proof-contract.cjs']))
  report={'status':'failed','backup':str(backup),'prior_absent':['proof-contract.cjs'],'candidate_sha256':hashes}
  try:
   for n in files:g.replace(candidate/n,BASE/n,0o600);installed.append(n)
   g.await_idle()
   if any(g.digest(BASE/n)!=hashes[n] for n in files):raise RuntimeError('installed_changed')
   restarted=True;subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
   if not g.health():raise RuntimeError('health_failed')
   if any(g.pid(u)!=pid for u,pid in others.items()):raise RuntimeError('unrelated_service_changed')
   report.update(status='active',health_http=200,unrelated_service_pids_unchanged=True)
  except Exception:
   if all(g.digest(BASE/n)==hashes[n] for n in installed):
    for n in installed:
     if n in expected:g.replace(backup/n,BASE/n,0o600)
     else:(BASE/n).unlink()
    if restarted:g.await_idle(allow_stopped=True);subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True);report['rollback_healthy']=g.health()
    report['status']='rolled_back'
   raise
  finally:
   p=stage/'activation.json';p.write_text(json.dumps(report,indent=2));p.chmod(0o600);print(json.dumps(report))
if __name__=='__main__':main()
