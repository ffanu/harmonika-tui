"""Capture evidence and remove only objects registered to this isolated Level11 lab."""
import argparse,fcntl,hashlib,importlib.util,json,os,shlex,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('level11_grade',REPO/'scripts/level11-grade.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
def durable(path,value):
 temporary=path.with_suffix('.tmp')
 with temporary.open('w') as f:os.chmod(temporary,0o600);json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(temporary,path);fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def main():
 p=argparse.ArgumentParser();p.add_argument('--atlas',type=Path,required=True);p.add_argument('--boreal',type=Path,required=True);a=p.parse_args();root=g.root_path();locks=[]
 for name in ['examiner','atlas','boreal']:
  f=(root/(name+'.lock')).open('a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);locks.append(f)
 for name,file in [('atlas',a.atlas),('boreal',a.boreal)]:
  v=json.loads(file.read_text());assert v['case']==name and v['phase']=='final' and v['passed'] and all(v['checks'].values())
 assert json.loads((root/'backend-activation.json').read_text())['status']=='active'
 resultpath=root/'lab-cleanup.json';intent=root/'lab-cleanup-intent.json'
 if resultpath.exists():result=json.loads(resultpath.read_text());assert result['closed']
 elif intent.exists():
  ledger=g.call(root,{'op':'ledger'});assert ledger['closed'],'cleanup_outcome_unknown_no_automatic_replay';result={'closed':True,'reconciled_from_controller_ledger':True};durable(resultpath,result)
 else:
  evidence={k:g.call(root,{'op':k}) for k in ['status','audit','ledger','events','logs','resources']};durable(root/'final-native-evidence.json',evidence);durable(intent,{'operation':'cleanup','lab_id':json.loads((root/'lab.json').read_text())['lab_id'],'status':'attempted_not_yet_confirmed'});result=g.call(root,{'op':'cleanup'});assert result['closed'];durable(resultpath,result)
 code="""import hashlib,json,subprocess,sys,urllib.request
from pathlib import Path
r=json.load(sys.stdin);label='id.harmonika.level11='+r['lab_id'];remaining={}
for kind,args in [('containers',['docker','ps','-aq','--filter','label='+label]),('images',['docker','image','ls','-q','--filter','label='+label]),('volumes',['docker','volume','ls','-q','--filter','label='+label]),('networks',['docker','network','ls','-q','--filter','label='+label])]:remaining[kind]=len(set(subprocess.check_output(args,text=True).split()))
health={}
for name,url in [('local','http://127.0.0.1:17890/health'),('public','https://chat.harmonika.id/health')]:
 try:
  with urllib.request.urlopen(url,timeout=15) as q:health[name]={'http_status':q.status,'json':isinstance(json.loads(q.read(65536)),dict)}
 except Exception:health[name]={'http_status':0,'json':False}
services={u:subprocess.check_output(['systemctl','is-active',u],text=True).strip() for u in ['harmonika-ai','harmonika-web-chat-live','harmonika-shared-core']};hashes={n:hashlib.sha256((Path('/opt/harmonika-ai/api')/n).read_bytes()).hexdigest() for n in r['hashes']};print(json.dumps({'remaining_owned_objects':remaining,'backend':{'health':health,'services':services,'source_sha256':hashes,'passed':all(x['http_status']==200 and x['json'] for x in health.values()) and all(x=='active' for x in services.values()) and hashes==r['hashes']}}))
"""
 meta=json.loads((root/'lab.json').read_text());activation=json.loads((root/'backend-activation.json').read_text());q=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'lab_id':meta['lab_id'],'hashes':activation['activation']['candidate_sha256']}),capture_output=True,text=True,timeout=90);assert q.returncode==0;v=json.loads(q.stdout);result['remaining_owned_objects']=v['remaining_owned_objects'];durable(resultpath,result);durable(root/'backend-final-health.json',v['backend']);print(json.dumps({'cleanup':result,'backend_healthy':v['backend']['passed']}));assert all(n==0 for n in v['remaining_owned_objects'].values()) and v['backend']['passed']
if __name__=='__main__':main()
