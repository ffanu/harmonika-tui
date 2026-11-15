#!/usr/bin/env python3
"""Guarded planner activation; parser receives source on stdin and never executes it."""
import datetime,fcntl,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('guards',Path(__file__).with_name('activate-debug-workflow.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
BASE=Path('/opt/harmonika-ai/api');OLD={'python-reference-spans.py': '63952c7a083114bff76178a32211227eec0cc204ca929e670a00bb140e5b9679', 'secret-firewall.cjs': '0f102b5cf4af2dc010e8e4fc8c93465b24a8340cb1ed493b6d4e90546f65dbce', 'planner-context.js': '0655cf649d4d659457f847ab30b989e1c89d21858fa2eb987d895fb9a3707dcd', 'agent-controller.cjs': 'a711a47bb9679b478b4fe0d78b499b76714cae0c547e2ddd16a1115c9fc8573d', 'long-decision.cjs': 'bdee0da9c4c38aa7c79f6f293f29b8976d7edaee1988c8ff06df81967b308a85', 'internal-tool-planner.js': 'abc9eb50adbf773a3fc591ddd66f38b8820629cfa1340e39cd95bbab30b876f5', 'debug-workflow.cjs': '8ed79d9e4c2992e0e5fda7bc3a1510121ff3eb625af8de7e5af5abc5c8c2d73f', 'decision-recovery.cjs': '7b2e0187631b8cde2c9bc3608fb5f3e60f71c504a80345d21beeb9180fc4dbe4'}
FILES=['python-reference-spans.py','secret-firewall.cjs','planner-context.js','agent-controller.cjs','long-decision.cjs','internal-tool-planner.js','debug-workflow.cjs','decision-recovery.cjs']
def baseline():
 for n,h in OLD.items():
  if g.digest(BASE/n)!=h:raise RuntimeError('baseline_changed_'+n)

def main():
 stage=Path(sys.argv[1]);candidate=stage/'backend/planner';approved=json.loads((stage/'verification.json').read_text());installed=[];restarted=False
 with open('/run/lock/harmonika-planner-activation.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);baseline()
  if not all(approved.get(k) for k in ['local_passed','integration_passed','activation_guards_passed']):raise RuntimeError('verification_not_passed')
  hashes=approved['candidate_sha256']
  if any(g.digest(candidate/n)!=hashes[n] for n in FILES):raise RuntimeError('candidate_changed')
  subprocess.run(['node','--check',str(candidate/'secret-firewall.cjs')],check=True,capture_output=True)
  subprocess.run(['python3','-I',str(candidate/'python-reference-spans.py')],input='def f(value):\n return {"password":value}\n',text=True,check=True,capture_output=True)
  g.await_idle();baseline();others={u:g.pid(u) for u in ['harmonika-web-chat-live','harmonika-shared-core']}
  backup=Path('/var/backups/harmonika-source-reference')/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,mode=0o700);[shutil.copy2(BASE/n,backup/n) for n in FILES];mode=(BASE/'secret-firewall.cjs').stat().st_mode&0o777
  report={'status':'failed','backup':str(backup),'candidate_sha256':hashes}
  try:
   for n in FILES:g.replace(candidate/n,BASE/n,mode);installed.append(n)
   g.await_idle()
   if any(g.digest(BASE/n)!=hashes[n] for n in FILES):raise RuntimeError('installed_changed')
   restarted=True;subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True)
   if not g.health():raise RuntimeError('health_failed')
   if any(g.pid(u)!=pid for u,pid in others.items()):raise RuntimeError('unrelated_service_changed')
   report.update(status='active',health_http=200,unrelated_service_pids_unchanged=True)
  except Exception:
   if all(g.digest(BASE/n)==hashes[n] for n in installed):
    for n in installed:g.replace(backup/n,BASE/n,mode)
    if restarted:g.await_idle(allow_stopped=True);subprocess.run(['systemctl','restart','harmonika-ai'],check=True,capture_output=True);report['rollback_healthy']=g.health()
    report['status']='rolled_back'
   raise
  finally:
   out=stage/'activation.json';out.write_text(json.dumps(report,indent=2));os.chmod(out,0o600);print(json.dumps(report))
if __name__=='__main__':main()
