#!/usr/bin/env python3
"""Owner activation of a verified template stage. Dry run unless --activate is explicit."""
import argparse,hashlib,json,pathlib,shutil,sqlite3,subprocess,time,urllib.request
P=pathlib.Path
BASELINE={'workflow.py':'563ce3e7a30bb3a88a67c02542ae6fa1d234bf19d090ce08a79a06230562c2f8','templates/scaffolds.py':'79696e7340e46c19ef7bc48a4a96abf77d010a94901fbb6d1151eae1e9ca8558'}
FILES=tuple(BASELINE)+('fullstack_template.py','assets/zai-1785226337571.tar')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(stage,activate=False):
 active=P('/opt/harmonika-ai/web_builder');stage=P(stage).resolve();assert stage.parent==P('/opt/harmonika-ai') and stage.name.startswith('fullstack-template-stage-')
 result=json.loads((stage/'staging-result.json').read_text());assert result['staged_http_auto_build'] and result['private_zip_verified'] and result['cross_tenant_denied'];assert result['production_activated'] is False
 for name,pin in BASELINE.items():assert sha(active/name)==pin,'active_source_changed_requires_review'
 assert all((stage/'backend/web_builder'/name).is_file() and not (stage/'backend/web_builder'/name).is_symlink() for name in FILES)
 db=sqlite3.connect('file:/var/lib/harmonika-builder/state.sqlite?mode=ro',uri=True)
 try:jobs=db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
 finally:db.close()
 assert jobs==0,'builder_jobs_active_retry_after_completion'
 report={'stage':str(stage),'preflight_passed':True,'active_jobs':jobs,'activation_requested':activate,'only_service':'harmonika-web-builder.service'}
 if not activate:return report
 # --activate may be used only after an explicit owner exception to the global
 # no-signals-to-existing-PIDs instruction. Never restart the AI/chat gateway.
 backup=P('/var/backups/harmonika-fullstack-template')/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
 absent=[];changed=[]
 try:
  for name in FILES:
   dest=active/name;src=stage/'backend/web_builder'/name;dest.parent.mkdir(parents=True,exist_ok=True)
   if dest.exists():
    assert not dest.is_symlink();old=backup/name;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,old)
   else:absent.append(name)
   temp=dest.with_name(dest.name+'.template-next');shutil.copy2(src,temp);temp.chmod(0o644);temp.replace(dest);changed.append(name)
  subprocess.run(['systemctl','restart','harmonika-web-builder.service'],check=True,timeout=180)
  for _ in range(50):
   try:
    with urllib.request.urlopen('http://127.0.0.1:18191/healthz',timeout=2) as response:health=json.load(response)
    if health.get('ok') is True:break
   except OSError:pass
   time.sleep(.2)
  else:raise RuntimeError('builder_health_failed')
  report.update(activated=True,backup=str(backup),template_build_verified=False)
 except BaseException:
  for name in reversed(changed):
   if name in absent:(active/name).unlink(missing_ok=True)
   else:shutil.copy2(backup/name,active/name)
  subprocess.run(['systemctl','restart','harmonika-web-builder.service'],check=False,timeout=180)
  raise
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage');p.add_argument('--activate',action='store_true');args=p.parse_args();print(json.dumps(run(args.stage,args.activate)))
