#!/usr/bin/env python3
"""Reconcile the actual N03 interrupted response; never re-execute its write."""
import argparse,hashlib,importlib.util,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('lab',Path(__file__).with_name('level14-lab-operator.py'));lab=importlib.util.module_from_spec(spec);spec.loader.exec_module(lab)

def main():
    p=argparse.ArgumentParser();p.add_argument('--attempt',type=int,required=True);a=p.parse_args();r=lab.R;failed=r/('N03-attempt'+str(a.attempt)+'.json');old=json.loads(failed.read_text());assert old['passed'] is False
    out=r/('N03-attempt'+str(a.attempt)+'-reconciled.json');assert not out.exists(),'reconcile_existing_result'
    assert old['source_sha256']==json.loads((r/'lab-final-candidate.json').read_text())['sha256'],'old_candidate_cannot_count_as_current_native_mission'
    target='Laporan sintetis Level14\nStatus: siap diperiksa\n';body={'messages':[{'role':'user','content':'Buat result.txt memakai fs_write dengan isi persis '+repr(target)+'. Tidak perlu eksekusi.'}],'verification':{'profile':'artifact','paths':['result.txt']},'stream':True};key='N03-'+str(a.attempt)+'-agent-1'
    res=lab.call('POST','/workspaces/'+old['workspace_id']+'/agent',body,key,old['actor_ref']);value=res['value']
    assert res['status']==200 and res.get('sse_done') is True and value.get('replayed') is True and value['status']=='completed'
    assert value['proof']['workspace_version']==1 and value['proof']['operation_count']>=1
    artifact=lab.call('POST','/workspaces/'+old['workspace_id']+'/invoke',{'skill_id':'fs.read','input':{'path':'result.txt'}},'N03-reconcile-read-'+str(a.attempt),old['actor_ref']);assert artifact['status']==200 and artifact['value']['result']['content']==target
    result={'id':'N03','attempt':a.attempt,'passed':True,'subject':'actual_backend_native_agent_with_live_planner','source_sha256':json.loads((r/'lab-final-candidate.json').read_text())['sha256'],'baseline':None,'turns':[{'key':key,'status':value['status'],'proof':value['proof'],'steps':len(value.get('steps',[])),'sse_done':True}],'approvals':[],'workspace_id':old['workspace_id'],'actor_ref':old['actor_ref'],'original_assertions_sha256':{},'http_calls':2,'duration_seconds':None,'extra':{'artifact_sha256':hashlib.sha256(target.encode()).hexdigest(),'original_failure_sha256':hashlib.sha256(failed.read_bytes()).hexdigest(),'same_body_and_key_replayed':True,'effects':1,'operator_assistance':'reconnect and independent readback only; no code correction or duplicate write'},'scope':'same dedicated synthetic workspace; original failure preserved'}
    out.write_text(json.dumps(result,indent=2));print(json.dumps({'id':'N03','reconciled':True,'same_key':True,'effects':1}))

if __name__=='__main__':main()
