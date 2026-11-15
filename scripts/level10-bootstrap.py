"""Operator creates a real completed schema3 checkpoint from the passed seed."""
import argparse,base64,hashlib,importlib.util,json,time
from pathlib import Path
spec=importlib.util.spec_from_file_location('lab_calls',Path(__file__).with_name('level8-grade.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root;project=root/'project'
 if (project/'BASELINE.json').exists():raise RuntimeError('preserve_baseline')
 bundle={n:base64.b64encode((project/n).read_bytes()).decode() for n in json.loads((project/'BUILD-FILES.json').read_text())}
 build=g.call(root,{'op':'build','release':'recovery-v3','files':bundle});assert build.get('image_id');activation=g.call(root,{'op':'activate','release':'recovery-v3','request_id':'stage-v3'});assert activation.get('image_id')==build['image_id']
 payload={'text':'Level10 checkpoint α','tasks':[{'name':'prepare','needs':[]},{'name':'publish','needs':['prepare']}]};status,value,_=g.http(root,'/workflows',method='POST',owner='alpha',key='checkpoint-l10',body=payload);assert status==202;wid=value['workflow']['id']
 for _ in range(30):
  status,value,_=g.http(root,'/workflows/'+wid)
  if status==200 and value['workflow']['state']=='completed':break
  time.sleep(.1)
 else:raise RuntimeError('baseline_did_not_complete')
 status,_,raw=g.http(root,'/workflow-artifacts/'+wid);assert status==200
 db=g.call(root,{'op':'db-audit'});assert db['workflows']['user_version']==3
 baseline={'scope':'operator_seeded_schema3_checkpoint','release':'recovery-v3','image_id':build['image_id'],'source_sha256':build['source_sha256'],'checkpoint_id':wid,'checkpoint_request':payload,'checkpoint_key':'checkpoint-l10','artifact_b64':base64.b64encode(raw).decode(),'artifact_sha256':hashlib.sha256(raw).hexdigest(),'schema':3,'legacy_tables':db['workflows']['tables'],'live_db_file_hash_unchanged_claimed':False}
 (root/'baseline-evidence.json').write_text(json.dumps({'activation':activation,'db':db,'status':g.call(root,{'op':'status'})},indent=2));(project/'BASELINE.json').write_text(json.dumps(baseline,indent=2));(project/'CHECKPOINT.json').write_text(json.dumps({'workflow_id':wid,'artifact_sha256':baseline['artifact_sha256']},indent=2))
 meta=json.loads((root/'grade.json').read_text())
 for n in ['BASELINE.json','CHECKPOINT.json']:meta['protected'][n]=hashlib.sha256((project/n).read_bytes()).hexdigest()
 meta['checkpoint_sha256']=meta['protected']['CHECKPOINT.json'];(root/'grade.json').write_text(json.dumps(meta,indent=2));print(json.dumps({'baseline_ready':True,'schema':3,'artifact_bytes':len(raw),'eight_roles':True}))
if __name__=='__main__':main()
