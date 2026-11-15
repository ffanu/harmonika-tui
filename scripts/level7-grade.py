#!/usr/bin/env python3
"""Independent Level7 grading: deployed image, worker/sink evidence and HTTP."""
import argparse,base64,fcntl,hashlib,json,os,subprocess,sys,time
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def call(root,r):
 remote=json.loads((root/'lab.json').read_text())['remote_root'];p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+remote+'/controller.py'],input=json.dumps({'origin':'examiner',**r}),capture_output=True,text=True,timeout=180)
 if p.returncode:raise RuntimeError('exam_controller_failed')
 return json.loads(p.stdout)
def http(root,path,method='GET',owner='alpha',**kw):
 r=call(root,{'op':'http','path':path,'method':method,'owner':owner,**kw});raw=base64.b64decode(r.get('body_b64',''))
 try:value=json.loads(raw)
 except ValueError:value=None
 return r.get('http_status',0),value,raw
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--phase',choices=['v1','v2'],required=True);p.add_argument('--label',required=True);a=p.parse_args();root=a.root;project=root/'project';prefix=root/a.label
 lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if Path(str(prefix)+'-result.json').exists():raise RuntimeError('preserve_existing_grade')
 meta=json.loads((root/'grade.json').read_text());checks={};errors={}
 def check(name,fn):
  try:checks[name]=bool(fn())
  except (AssertionError,KeyError,IndexError,TypeError,ValueError,RuntimeError,OSError,AttributeError,StopIteration) as e:checks[name]=False;errors[name]=type(e).__name__
 check('protected_files',lambda:all((project/n).is_file() and not (project/n).is_symlink() and hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()))
 if not checks['protected_files']:raise RuntimeError('protected_changed_before_execution')
 env=os.environ.copy();env.update(HARMONIKA_EXAM_PROJECT=str(project),HARMONIKA_EXAM_PHASE=a.phase,PYTHONPYCACHEPREFIX=str(root/'examiner-pycache'))
 result=subprocess.run([sys.executable,'-I',str(REPO/'scripts/level7-lab/independent.py')],env=env,capture_output=True,text=True,timeout=100);Path(str(prefix)+'-local.txt').write_text(result.stdout+result.stderr);checks['independent_local_tests']=result.returncode==0
 linux=call(root,{'op':'test-engine','phase':a.phase});Path(str(prefix)+'-linux.txt').write_text(linux['output']);checks['independent_linux_image_tests']=linux['exit_code']==0
 ledger=call(root,{'op':'ledger'});status=call(root,{'op':'status'});db=call(root,{'op':'db-audit'});events=call(root,{'op':'events'})
 Path(str(prefix)+'-native-evidence.json').write_text(json.dumps({'ledger':ledger,'status':status,'db':db,'docker_events':events},indent=2))
 jobs={j['id']:j for j in db['jobs']['tables']['jobs']};effects={j['job_id']:j for j in db['sink']['tables']['effects']};calls=db['sink']['tables']['calls'];obs=ledger.get('critical_witnesses',[])+ledger.get('observations',[])
 check('five_independent_containers_running',lambda:len({v['container_id'] for v in status['roles'].values()})==5 and all(v['running'] for v in status['roles'].values()))
 def isolation():
  for name,v in status['roles'].items():
   h=v['isolation'];mem,cpu,pids=(402653184,1000000000,384) if name=='api' else (201326592,500000000,64) if name=='sink' else (134217728,500000000,32)
   if not(h['user']=='10001:10001' and h['network']=='none' and h['readonly'] and h['memory']==mem and h['nano_cpus']==cpu and h['pids']==pids and 'ALL' in h['cap_drop'] and any(x.startswith('no-new-privileges') for x in h['security_opt'])):return False
  return True
 check('runtime_resource_and_network_isolation',isolation)
 bundle={n:base64.b64encode((project/n).read_bytes()).decode() for n in ['engine.py','stream_reader.py','storage_tools.py','Dockerfile','server.py','worker.py','entrypoint.py','effect_connector.py','effect_service.py']}
 check('deployed_source_and_all_role_images_match',lambda:ledger['releases'][status['release']]['source_sha256']==hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest() and all(v['image_id']==ledger['releases'][status['release']]['image_id'] for v in status['roles'].values()))
 check('actual_api_and_sink_readiness',lambda:status['health']['http_status']==200 and status['ready']['http_status']==200)
 check('both_databases_wal_integrity',lambda:all(db[n]['integrity']=='ok' and db[n]['journal_mode']=='wal' for n in ['jobs','sink']))
 check('native_accepted_work_settled',lambda:all(j['state'] in ['completed','cancelled'] for j in jobs.values()))
 burst=ledger.get('bursts',{}).get(ledger.get('last_agent_burst','native'),{})
 check('actual_100_barrier_requests',lambda:burst['request_count']==100 and sum(burst['status_counts'].values())==100 and burst['max_overlapping_clients']>1 and set(burst['status_counts'])<={'200','202','429','503'})
 check('admission_bounded_and_duplicates_converged',lambda:burst['idempotency_converged'] and 12<=len(burst['accepted_jobs'])<=16 and all(v<=8 for v in burst['per_owner_pending'].values()) and set(burst['observed_worker_ids'])=={'worker1','worker2','worker3'})
 check('burst_jobs_have_one_effect_each',lambda:all(j['id'] in jobs and jobs[j['id']]['owner']==j['owner'] and jobs[j['id']]['state']=='completed' and j['id'] in effects for j in burst['accepted_jobs']))
 check('all_effects_belong_to_completed_jobs',lambda:all(id in jobs and jobs[id]['state']=='completed' for id in effects) and all(j['state']=='cancelled' and id not in effects or j['state']=='completed' and id in effects for id,j in jobs.items()))
 check('activation_each_once',lambda:all(n==1 for n in ledger['activation_attempts'].values()))
 checkpoint=json.loads((project/'CHECKPOINT.json').read_text()) if (project/'CHECKPOINT.json').exists() else {};cp=checkpoint.get('job_id','missing')
 check('checkpoint_identity_preserved',lambda:checkpoint['image_id']==ledger['requests']['stage-v1']['image_id'] and checkpoint['api_container_id']==ledger['requests']['stage-v1']['api_container_id'] and jobs[cp]['state']=='completed')
 expected={'kind':'report','text':'Level7 checkpoint α'};raw=json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
 check('checkpoint_exact_artifact_bytes',lambda:http(root,'/artifacts/'+cp)[2]==raw)
 check('checkpoint_replay_same_job',lambda:http(root,'/jobs','POST',key='checkpoint-report',body=expected)[1]['job']['id']==cp)
 check('foreign_request_lookup_404',lambda:http(root,'/requests/checkpoint-report',owner='beta')[0]==404)
 check('foreign_job_get_404',lambda:http(root,'/jobs/'+cp,owner='beta')[0]==404)
 check('foreign_artifact_404',lambda:http(root,'/artifacts/'+cp,owner='beta')[0]==404)
 check('foreign_cancel_404',lambda:http(root,'/jobs/'+cp+'/cancel','POST',owner='beta')[0]==404)
 check('anonymous_rejected',lambda:http(root,'/jobs/'+cp,owner='anonymous')[0]==401)
 check('technical_events_no_payload',lambda:all(set(e)<={'id','job_id','state','owner','worker_id','fence'} for e in db['jobs']['tables']['events']))
 def event_contract():
  for owner in ['alpha','beta']:
   cursor=0
   for _ in range(20):
    code,v,_=http(root,'/events',owner=owner,cursor=cursor);assert code==200;es=v['events'];assert len(es)<=100
    if not es:break
    for e in es:assert set(e)=={'id','job_id','state'} and type(e['id']) is int and e['id']>cursor and jobs[e['job_id']]['owner']==owner;cursor=e['id']
  return True
 check('owner_isolated_event_cursor',event_contract)
 if a.phase=='v2':
  check('checkpoint_hash_unchanged',lambda:hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'])
  check('fault_drills_match_current_core_sources',lambda:all(ledger['release_file_sha256']['cluster-v2'][n]==ledger['release_file_sha256'][status['release']][n] for n in ['engine.py','effect_connector.py','effect_service.py','worker.py','entrypoint.py']))
  stale=[x for x in calls if x['kind']=='stale_rejected']
  check('late_live_worker_rejected_by_sink_fence',lambda:any(x['worker_id']=='worker1' and x['job_id'] in effects and effects[x['job_id']]['fence']>x['fence'] and jobs[x['job_id']]['worker_id']!='worker1' and jobs[x['job_id']]['state']=='completed' for x in stale))
  check('old_worker_observed_running_during_drill',lambda:any(x.get('kind')=='fault' and x.get('origin')=='agent' and x.get('fault')=='release-worker' and x.get('worker')=='worker1' and x.get('worker_running') is True and x.get('worker_restart_count')==0 for x in obs) and any(x['worker_id']=='worker1' for x in stale))
  def fault_reconciled(kind):
   fault=[x for x in calls if x['kind']==kind]
   if len(fault)!=1:return False
   id=fault[0]['job_id'];actions=[x for x in calls if x['job_id']==id]
   return id in effects and jobs[id]['state']=='completed' and sum(x['kind']=='execute' for x in actions)==1 and any(x['kind']=='lookup' and x['id']>fault[0]['id'] for x in actions)
  check('unknown_ack_same_job_no_execute_replay',lambda:fault_reconciled('unknown-ack'))
  def bounded_lookup_fault():
   fault=next(x for x in calls if x['kind']=='unknown-ack');xs=[x for x in calls if x['job_id']==fault['job_id'] and x['kind']=='lookup_unavailable'];return len(xs)==2 and len({x['job_id'] for x in calls if x['kind']=='lookup_unavailable'})==1
  check('two_real_lookup503_bound_to_target',bounded_lookup_fault)
  check('crash_after_external_commit_reconciled',lambda:fault_reconciled('crash-after-effect'))
  def actual_worker_crash():
   worker=next(x['worker_id'] for x in calls if x['kind']=='crash-after-effect')
   return ledger.get('max_restarts',{}).get(worker,0)>=1 and any(d['action']=='die' and d['exit_code']=='42' and any(s['action']=='start' and s['at']>=d['at'] for s in role['events']) for role in events['containers'] if role['role']==worker for d in role['events'])
  check('actual_worker_exit42_and_restart',actual_worker_crash)
  check('bad_receipt_reconciled_from_sink',lambda:fault_reconciled('bad-receipt'))
  def offline():
   h=hashlib.sha256(next(j['id'] for j in jobs.values() if j['key']=='offline-report').encode()).hexdigest()
   return any(x.get('origin')=='agent' and x.get('job_id_sha256')==h and x.get('sink_running') is False and x.get('state') in ['queued','running','reconciling','cancel_requested'] for x in obs)
  check('offline_observed_without_false_completion',offline)
  check('lost_submit_reconciled_without_another_post',lambda:len(ledger.get('dropped_submit_receipts',[]))==1 and all(x['origin']=='agent' and ledger['submit_attempts'][x['request_sha256']]==1 and ledger.get('request_lookups',{}).get(x['request_sha256'],0)>=1 for x in ledger['dropped_submit_receipts']))
  for op,module in [('stream','stream_reader.py'),('backup-drill','storage_tools.py')]:
   key='stream_reader_sha256' if op=='stream' else 'storage_tools_sha256';h=hashlib.sha256((project/module).read_bytes()).hexdigest()
   check('native_'+op+'_passed_current_source',lambda op=op,key=key,h=h:any(x.get('origin')=='agent' and x.get('base_label')=='native' and x.get('passed') is True and x.get('image_id')==ledger['releases'][status['release']]['image_id'] and x.get(key)==h for x in ledger.get('probes',{}).get(op,[])))
  revision=status['release'].removeprefix('cluster-v2');rollback_key='rollback-v1'+revision;restore_key='restore-v2'+revision
  check('actual_code_rollback_and_restore',lambda:ledger['requests'][rollback_key]['release']=='cluster-v1' and ledger['requests'][restore_key]['release']==status['release'] and any(x.get('origin')=='agent' and x.get('release')=='cluster-v1' and x.get('status')==200 and x.get('route')=='/artifacts/:id' and ledger['requests'][rollback_key]['at']<x.get('at',0)<ledger['requests'][restore_key]['at'] for x in obs))
  def final_report():
   v=json.loads((project/'RESULT.json').read_text());return v.get('scope')=='isolated_linux_multiworker' and v.get('production_changed') is False and v.get('same_session_resume') is True and v.get('completed') is True and (project/'HASIL.md').exists()
  check('native_report_matches_scope',final_report)
  for op in ['stream','backup-drill']:
   value=call(root,{'op':op,'label':'examiner'});Path(str(prefix)+'-'+op+'.json').write_text(json.dumps(value,indent=2));checks['independent_'+op]=value.get('passed') is True and value.get('probe_exit_code')==0
 final=call(root,{'op':'db-audit'});Path(str(prefix)+'-final-db.json').write_text(json.dumps(final,indent=2));finaljobs={j['id']:j for j in final['jobs']['tables']['jobs']};finaleffects=final['sink']['tables']['effects']
 def artifacts():
  for e in finaleffects:
   j=finaljobs[e['job_id']];code,_,raw=http(root,'/artifacts/'+e['job_id'],owner=j['owner'])
   if code!=200 or j['state']!='completed' or hashlib.sha256(raw).hexdigest()!=e['sha256'] or e['content']['sha256']!=e['sha256']:return False
  return bool(finaleffects)
 check('all_actual_artifacts_match_separate_sink',artifacts)
 logs=call(root,{'op':'logs'});check('tested_payloads_absent_from_role_logs',lambda:len(logs['complete_history_scans'])==len(ledger['role_history']) and all(x['canary_present'] is False for x in logs['complete_history_scans']))
 result={'phase':a.phase,'passed':all(checks.values()),'checks':checks,'failures':[k for k,v in checks.items() if not v],'errors':errors};Path(str(prefix)+'-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as error:
  # Preserve an explicit failed run even if transport/test orchestration aborts.
  args=sys.argv
  if '--root' in args and '--label' in args and '--phase' in args:
   path=Path(args[args.index('--root')+1])/(args[args.index('--label')+1]+'-result.json')
   if not path.exists():path.write_text(json.dumps({'phase':args[args.index('--phase')+1],'passed':False,'checks':{},'failures':['grading_interrupted'],'errors':{'grading_interrupted':type(error).__name__}},indent=2))
  print(json.dumps({'grading_interrupted':type(error).__name__}));raise SystemExit(1)

