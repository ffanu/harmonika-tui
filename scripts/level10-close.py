#!/usr/bin/env python3
"""Close only the passed, registered lab; verify backend health without restart."""
import argparse,fcntl,hashlib,importlib.util,json,shlex,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('level10_grade',Path(__file__).with_name('level10-grade.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--passed-report',required=True,type=Path);a=p.parse_args();root=a.root;report=json.loads(a.passed_report.read_text());assert report['phase']=='v4' and report['passed']
 lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if (root/'lab-cleanup.json').exists():raise RuntimeError('cleanup_already_recorded')
 evidence={k:g.call(root,{'op':v}) for k,v in [('ledger','ledger'),('status','status'),('db','db-audit'),('docker_events','events')]};(root/'final-native-evidence.json').write_text(json.dumps(evidence,indent=2))
 result=g.call(root,{'op':'cleanup'});assert result['closed']
 meta=json.loads((root/'lab.json').read_text());code="""import sys,json,subprocess,hashlib,urllib.request
from pathlib import Path
r=json.load(sys.stdin);label='id.harmonika.level10='+r['lab_id'];remaining={}
for kind,args in [('containers',['docker','ps','-aq','--filter','label='+label]),('images',['docker','image','ls','-q','--filter','label='+label]),('volumes',['docker','volume','ls','-q','--filter','label='+label]),('networks',['docker','network','ls','-q','--filter','label='+label])]:
 p=subprocess.run(args,capture_output=True,text=True,check=True);remaining[kind]=len(set(p.stdout.split()))
health={};services={}
for name,url in [('local','http://127.0.0.1:17890/health'),('public','https://chat.harmonika.id/health')]:
 try:
  with urllib.request.urlopen(url,timeout=15) as q:health[name]={'http_status':q.status,'json':isinstance(json.loads(q.read(65536)),dict)}
 except Exception:health[name]={'http_status':0,'json':False}
for unit in ['harmonika-ai','harmonika-web-chat-live','harmonika-shared-core']:
 services[unit]=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()
digest=hashlib.sha256(Path('/opt/harmonika-ai/api/debug-workflow.cjs').read_bytes()).hexdigest()
hashes={n:hashlib.sha256((Path('/opt/harmonika-ai/api')/n).read_bytes()).hexdigest() for n in r['expected_hashes']};value={'health':health,'services':services,'source_sha256':hashes,'debug_workflow_sha256':digest,'passed':all(x['http_status']==200 and x['json'] for x in health.values()) and all(s=='active' for s in services.values()) and hashes==r['expected_hashes']}
print(json.dumps({'remaining_owned_objects':remaining,'backend':value}))
"""
 expected=hashlib.sha256((REPO/'backend/planner/debug-workflow.cjs').read_bytes()).hexdigest();p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'lab_id':meta['lab_id'],'expected_hashes':{n:hashlib.sha256((REPO/'backend/planner'/n).read_bytes()).hexdigest() for n in ['long-decision.cjs','internal-tool-planner.js','decision-recovery.cjs','debug-workflow.cjs']}}),capture_output=True,text=True,timeout=60);assert p.returncode==0;v=json.loads(p.stdout);result['remaining_owned_objects']=v['remaining_owned_objects']
 (root/'lab-cleanup.json').write_text(json.dumps(result,indent=2));(root/'backend-final-health.json').write_text(json.dumps(v['backend'],indent=2));print(json.dumps({'cleanup':result,'backend_healthy':v['backend']['passed']}))
 assert all(x==0 for x in v['remaining_owned_objects'].values()) and v['backend']['passed']
if __name__=='__main__':main()
