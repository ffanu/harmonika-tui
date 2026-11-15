#!/usr/bin/env python3
"""Held-out examiner; checks actual Linux source/image, DB, API, SSE and faults."""
import argparse,base64,fcntl,hashlib,json,os,subprocess,sys,time,uuid
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def call(root,r):
 r={'origin':'examiner',**r};remote=json.loads((root/'lab.json').read_text())['remote_root']
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+remote+'/controller.py'],input=json.dumps(r),text=True,capture_output=True,timeout=180)
 if p.returncode:raise RuntimeError('exam_control_failed')
 return json.loads(p.stdout)
def http(root,path,method='GET',owner='alpha',**kw):
 r=call(root,{'op':'http','path':path,'method':method,'owner':owner,**kw});raw=base64.b64decode(r.get('body_b64',''))
 try:body=json.loads(raw)
 except ValueError:body=None
 return r['http_status'],body,raw,r.get('headers',{})
def wait(root,id,owner='alpha',want='completed'):
 for _ in range(20):
  status,v,_,_=http(root,'/jobs/'+id,owner=owner)
  if status==200 and v['job']['state']==want:return v['job']
  time.sleep(.25)
 raise AssertionError('job_state_not_reached')
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--phase',choices=['v1','v2'],required=True);p.add_argument('--label',required=True);a=p.parse_args();root=a.root;project=root/'project';prefix=root/a.label
 lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if Path(str(prefix)+'-result.json').exists():raise RuntimeError('existing_grade')
 meta=json.loads((root/'grade.json').read_text());checks={};errors={}
 def check(name,fn):
  try:value=fn();checks[name]=bool(value)
  except (AssertionError,KeyError,IndexError,AttributeError,TypeError,ValueError,RuntimeError,OSError,StopIteration) as e:checks[name]=False;errors[name]=type(e).__name__
 check('protected_files',lambda:all((project/n).is_file() and not (project/n).is_symlink() and hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()))
 if not checks['protected_files']:raise RuntimeError('protected_changed_before_candidate_execution')
 env=os.environ.copy();env.update(HARMONIKA_EXAM_PROJECT=str(project),HARMONIKA_EXAM_PHASE=a.phase,PYTHONPYCACHEPREFIX=str(root/'examiner-pycache'))
 out=subprocess.run([sys.executable,'-I',str(REPO/'scripts/level6-lab/independent.py')],env=env,text=True,capture_output=True,timeout=90)
 Path(str(prefix)+'-local.txt').write_text(out.stdout+out.stderr);checks['independent_local_tests']=out.returncode==0
 linux=call(root,{'op':'test-engine','phase':a.phase});Path(str(prefix)+'-linux.txt').write_text(linux['test_output']);checks['independent_linux_image_tests']=linux['exit_code']==0
 ledger=call(root,{'op':'ledger'});status=call(root,{'op':'status'});db=call(root,{'op':'db-audit'});docker_events=call(root,{'op':'events'})
 Path(str(prefix)+'-native-evidence.json').write_text(json.dumps({'ledger':ledger,'status':status,'db':db,'docker_events':docker_events},indent=2))
 check('active_source_image_matches',lambda:ledger['releases'][status['release']]['image_id']==status['image_id'] and ledger['releases'][status['release']]['source_sha256']==hashlib.sha256(json.dumps({n:base64.b64encode((project/n).read_bytes()).decode() for n in ['engine.py','server.py','effect_connector.py','Dockerfile']},sort_keys=True).encode()).hexdigest())
 check('isolated_linux_runtime',lambda:status['isolation']['user']=='10001:10001' and status['isolation']['network']=='none' and status['isolation']['readonly'] and status['isolation']['memory']==201326592 and status['isolation']['nano_cpus']==500000000 and status['isolation']['pids']==32 and 'ALL' in status['isolation']['cap_drop'] and any(x.startswith('no-new-privileges') for x in status['isolation']['security_opt']))
 check('actual_readiness',lambda:status['ready']['http_status']==200)
 check('wal_integrity_both_databases',lambda:all(db[n]['integrity']=='ok' and db[n]['journal_mode']=='wal' for n in ['jobs','sink']))
 check('native_jobs_settled_before_examiner_writes',lambda:all(j['state'] in ['completed','cancelled'] for j in db['jobs']['tables']['jobs']))
 checkpoint=json.loads((project/'CHECKPOINT.json').read_text()) if (project/'CHECKPOINT.json').exists() else {}
 cp=checkpoint.get('job_id','missing')
 check('checkpoint_exact_release_identity',lambda:set(checkpoint)=={'job_id','image_id','container_id'} and checkpoint['image_id']==ledger['requests']['stage-v1']['image_id'] and checkpoint['container_id']==ledger['requests']['stage-v1']['container_id'])
 check('checkpoint_completed',lambda:http(root,'/jobs/'+cp)[1]['job']['state']=='completed')
 payload={'kind':'report','text':'Synthetic checkpoint α'};expected=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
 check('checkpoint_artifact_exact',lambda:http(root,'/artifacts/'+cp)[2]==expected)
 check('checkpoint_replay_same_id',lambda:http(root,'/jobs','POST',key='checkpoint-report',body=payload)[1]['job']['id']==cp)
 check('activation_each_once',lambda:all(v==1 for v in ledger['activation_attempts'].values()))
 if a.phase=='v2':
  check('checkpoint_hash_preserved',lambda:hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'])
  calls=db['sink']['tables']['calls'];effects=db['sink']['tables']['effects']
  jobs={x['id']:x for x in db['jobs']['tables'].get('jobs',[])}
  def recovery(kind):
   fault=[x for x in calls if x['kind']==kind]
   if len(fault)!=1:return False
   id=fault[0]['job_id'];actions=[x for x in calls if x['job_id']==id];kinds=[x['kind'] for x in actions]
   return kinds.count('execute')==1 and kinds.index('lookup')>kinds.index(kind) and len([x for x in effects if x['job_id']==id])==1 and jobs[id]['state']=='completed'
  check('native_crash_after_commit_reconciled',lambda:recovery('crash-after-effect'))
  check('native_exit42_auto_restart',lambda:max(status['restart_count'],ledger.get('max_restart_count',0))>=1 and any(x['action']=='die' and x['exit_code']=='42' for x in docker_events['events']))
  check('native_unknown_ack_reconciled',lambda:recovery('unknown-ack'))
  es=db['jobs']['tables'].get('events',[])
  check('native_cancel_requested_then_actual_completion',lambda:any(jobs[e['job_id']]['state']=='completed' and any(s['job_id']==e['job_id'] for s in effects) for e in es if e['state']=='cancel_requested'))
  check('native_cancelled_queue_has_no_effect',lambda:any(j['state']=='cancelled' and not any(e['job_id']==j['id'] for e in effects) for j in jobs.values()))
  observed=ledger.get('observed_errors',{}).get('agent',{})
  check('native_observed_real_error_states',lambda:all(observed.get(x,0)>0 for x in ['queue_full','database_busy','draining']))
  check('native_scoped_manual_restart',lambda:ledger.get('manual_restarts')==1)
  sse=[x for x in ledger.get('observations',[]) if x.get('origin')=='agent' and x.get('route')=='/events' and x.get('status')==200]
  check('native_sse_reconnect_cursor',lambda:any(x.get('last_event_id') is not None and any(y.get('cursor')==str(x['last_event_id']) and (y.get('first_event_id') is None or y['first_event_id']>x['last_event_id']) for y in sse[i+1:]) for i,x in enumerate(sse)))
 # Actual live API/transport checks; no source import used as Linux evidence.
 tag='exam-'+uuid.uuid4().hex[:12]
 parallel=call(root,{'op':'parallel','key':tag})['responses']
 check('six_real_simultaneous_http_submits',lambda:len(parallel)==6 and sum(x['status']==202 for x in parallel)==1 and all(x['status'] in [200,202] for x in parallel) and len({x['body']['job']['id'] for x in parallel})==1)
 id=parallel[0].get('body',{}).get('job',{}).get('id','missing');check('parallel_job_completes',lambda:wait(root,id)['state']=='completed')
 check('foreign_owner_get_404',lambda:http(root,'/jobs/'+id,owner='beta')[0]==404)
 check('foreign_owner_cancel_404',lambda:http(root,'/jobs/'+id+'/cancel','POST',owner='beta')[0]==404)
 check('foreign_owner_artifact_404',lambda:http(root,'/artifacts/'+id,owner='beta')[0]==404)
 check('anonymous_rejected',lambda:http(root,'/jobs/'+id,owner='anonymous')[0]==401)
 check('conflict_not_replaced',lambda:http(root,'/jobs','POST',key=tag,body={'kind':'report','text':'changed'})[0]==409 and http(root,'/jobs/'+id)[1]['job']['id']==id)
 check('invalid_payload_no_acceptance',lambda:all(http(root,'/jobs','POST',key=tag+'bad',body=x)[0]==422 for x in [None,[],{'kind':'report','text':True},{'kind':'report','text':'x','owner':'beta'}]))
 check('invalid_sse_cursor',lambda:http(root,'/events',cursor='-1')[0]==422 and http(root,'/events',cursor='oops')[0]==422)
 def wire_events(owner,cursor):
  s,_,raw,h=http(root,'/events',owner=owner,cursor=cursor);assert s==200 and h.get('Content-Type')=='text/event-stream'
  events=[]
  for block in raw.decode().split('\n\n'):
   if not block:continue
   lines=[x for x in block.splitlines() if x and not x.startswith(':')]
   if not lines:continue
   data=json.loads(next(x[6:] for x in lines if x.startswith('data: ')));assert set(data)=={'id','job_id','state'};assert int(next(x[4:] for x in lines if x.startswith('id: ')))==data['id'];events.append(data)
  return events
 def sse_check():
  events=wire_events('alpha',0);assert events and all(x['id']<y['id'] for x,y in zip(events,events[1:]));cursor=events[-1]['id'];again=wire_events('alpha',cursor);assert all(e['id']>cursor for e in again);beta=wire_events('beta',0);assert all(e['job_id'] not in {x['job_id'] for x in events} for e in beta);return True
 check('real_sse_wire_cursor_owner_and_schema',sse_check)
 if a.phase=='v2':
  check('drain_admission',lambda:http(root,'/control/drain','POST')[1]=={'draining':True} and http(root,'/jobs','POST',key=tag+'drain',body={'kind':'report','text':'drain'})[0]==503)
  check('drain_replay_accepted',lambda:http(root,'/jobs','POST',key=tag,body={'kind':'report','text':'parallel synthetic'})[0]==200)
  check('resume_admission',lambda:http(root,'/control/resume','POST')[1]=={'draining':False})
 finaldb=call(root,{'op':'db-audit'});Path(str(prefix)+'-final-db.json').write_text(json.dumps(finaldb,indent=2))
 finaljobs={j['id']:j for j in finaldb['jobs']['tables']['jobs']};finaleffects={e['job_id']:e for e in finaldb['sink']['tables']['effects']}
 check('all_effects_match_completed_states',lambda:all(id in finaljobs and finaljobs[id]['state']=='completed' for id in finaleffects) and all(j['state']=='cancelled' and id not in finaleffects or j['state']=='completed' and id in finaleffects for id,j in finaljobs.items()))
 def artifact_checks():
  for id,e in finaleffects.items():
   status,_,raw,_=http(root,'/artifacts/'+id,owner=finaljobs[id]['owner'])
   if status!=200 or hashlib.sha256(raw).hexdigest()!=e['sha256'] or e['content']['sha256']!=e['sha256']:return False
  return bool(finaleffects)
 check('all_completed_artifacts_match_independent_sink',artifact_checks)
 check('effect_ids_unique',lambda:len({e['job_id'] for e in finaldb['sink']['tables']['effects']})==len(finaldb['sink']['tables']['effects']))
 check('technical_events_no_payload',lambda:all(set(e)<= {'id','job_id','state','owner'} for e in finaldb['jobs']['tables']['events']))
 log=call(root,{'op':'logs'})['logs'];check('logs_exclude_tested_payloads',lambda:all(marker not in log for marker in ['Synthetic checkpoint','parallel synthetic','sensitive raw value','private downstream failure']))
 if a.phase=='v2':
  def final_report_check():
   v=json.loads((project/'RESULT.json').read_text())
   return isinstance(v,dict) and v.get('scope')=='isolated_linux_docker' and v.get('production_changed') is False and v.get('same_session_resume') is True and v.get('completed') is True and (project/'HASIL.md').is_file()
  check('native_final_report_scope',final_report_check)
 result={'phase':a.phase,'passed':all(checks.values()),'checks':checks,'failures':[k for k,v in checks.items() if not v],'errors':errors}
 Path(str(prefix)+'-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
