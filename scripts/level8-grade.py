#!/usr/bin/env python3
"""Independent native Level8 grading; no model claims are execution evidence."""
import argparse,base64,fcntl,hashlib,hmac,json,os,subprocess,sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
def call(root,r):
 remote=json.loads((root/'lab.json').read_text())['remote_root'];payload={**r,'origin':'examiner'};payload['_operator_auth']=hmac.new((root/'examiner-auth.key').read_bytes(),json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest();p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+remote+'/controller.py'],input=json.dumps(payload),capture_output=True,text=True,timeout=180)
 if p.returncode:raise RuntimeError('exam_controller_failed')
 return json.loads(p.stdout)
def http(root,path,method='GET',owner='alpha',**kw):
 r=call(root,{'op':'http','path':path,'method':method,'owner':owner,**kw});raw=base64.b64decode(r.get('body_b64',''))
 try:value=json.loads(raw)
 except ValueError:value=None
 return r.get('http_status',0),value,raw
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def key_for(owner,id,name):return 'w'+hashlib.sha256((owner+'\0'+id+'\0'+name).encode()).hexdigest()[:48]
def effect_proof(db):
 jobs={j['id']:j for j in db['jobs']['tables']['jobs']};rows=db['sink']['tables']['effects'];effects={e['job_id']:e for e in rows};calls=db['sink']['tables']['calls'];assert len(effects)==len(rows)
 assert set(effects)=={j for j,v in jobs.items() if v['state']=='completed'}
 assert all(v['state'] in ['completed','cancelled'] for v in jobs.values())
 for id,effect in effects.items():
  assert effect['owner']==jobs[id]['owner'];actions=sorted([c for c in calls if c['job_id']==id],key=lambda c:c['id']);successes=0
  for c in actions:
   if c['kind']!='execute':continue
   previous=[x for x in actions if x['kind']=='authorize' and x['id']<c['id']];assert previous
   rejection=next((x for x in actions if x['id']==c['id']+1 and x['kind']=='stale_rejected' and x['worker_id']==c['worker_id'] and x['fence']==c['fence']),None)
   if rejection:assert previous[-1]['fence']!=c['fence']
   else:assert c['fence']==previous[-1]['fence']==effect['fence'];successes+=1
  assert successes==1
 return True
def offline_probe_pair(obs,lo,hi,release):
 probes=[x for x in obs if x.get('kind')=='http' and x.get('release')==release and x.get('method')=='GET' and lo<=x.get('at',0)<=hi]
 for health in probes:
  if health.get('route')!='/healthz' or health.get('status')!=200:continue
  for ready in probes:
   if ready.get('route')!='/readyz' or ready.get('status')!=503 or health.get('origin')!=ready.get('origin') or health['at']>ready['at']:continue
   if health.get('origin')=='agent':return True
   if health.get('origin')=='controller' and any(x.get('kind')=='cluster_status' and x.get('origin')=='agent' and lo<=x.get('at',0)<=health['at'] and ready['at']-x['at']<=2 and x.get('roles',{}).get('sink',{}).get('running') is False and x.get('roles',{}).get('coordinator',{}).get('running') is True for x in obs):return True
 return False
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--phase',choices=['v1','v2'],required=True);p.add_argument('--label',required=True);a=p.parse_args();root=a.root;project=root/'project';prefix=root/a.label;lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if Path(str(prefix)+'-result.json').exists():raise RuntimeError('preserve_existing_grade')
 meta=json.loads((root/'grade.json').read_text());checks={};errors={}
 def check(name,fn):
  try:checks[name]=bool(fn())
  except (AssertionError,KeyError,IndexError,TypeError,ValueError,RuntimeError,OSError,AttributeError,StopIteration) as e:checks[name]=False;errors[name]=type(e).__name__
 check('protected_files',lambda:all((project/n).is_file() and not (project/n).is_symlink() and hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()))
 if not checks['protected_files']:raise RuntimeError('protected_changed')
 env=os.environ.copy();env.update(HARMONIKA_EXAM_PROJECT=str(project),HARMONIKA_EXAM_PHASE=a.phase,PYTHONPYCACHEPREFIX=str(root/'examiner-pycache'))
 local=subprocess.run([sys.executable,'-I',str(REPO/'scripts/level8-lab/independent.py')],env=env,capture_output=True,text=True,timeout=120);Path(str(prefix)+'-local.txt').write_text(local.stdout+local.stderr);checks['independent_local_tests']=local.returncode==0
 linux=call(root,{'op':'test-engine','phase':a.phase});Path(str(prefix)+'-linux.txt').write_text(linux['output']);checks['independent_linux_image_tests']=linux['exit_code']==0
 ledger=call(root,{'op':'ledger'});status=call(root,{'op':'status'});db=call(root,{'op':'db-audit'});events=call(root,{'op':'events'});Path(str(prefix)+'-native-evidence.json').write_text(json.dumps({'ledger':ledger,'status':status,'db':db,'docker_events':events},indent=2))
 workflows={w['id']:w for w in db['workflows']['tables']['workflows']};steps=db['workflows']['tables']['steps'];jobs={j['id']:j for j in db['jobs']['tables']['jobs']};effects={e['job_id']:e for e in db['sink']['tables']['effects']};mechanics=db['mechanics']['tables']['calls'];obs=ledger.get('critical_witnesses',[])+ledger.get('observations',[])
 check('six_independent_containers_running',lambda:len({v['container_id'] for v in status['roles'].values()})==6 and all(v['running'] for v in status['roles'].values()))
 def isolation():
  for name,v in status['roles'].items():
   h=v['isolation'];mem,cpu,pids=(402653184,1000000000,384) if name=='api' else (201326592,500000000,64) if name in ['sink','coordinator'] else (134217728,500000000,32)
   if not(h['user']=='10001:10001' and h['network']=='none' and h['readonly'] and h['memory']==mem and h['nano_cpus']==cpu and h['pids']==pids and 'ALL' in h['cap_drop'] and any(x.startswith('no-new-privileges') for x in h['security_opt'])):return False
  return True
 check('runtime_resource_and_network_isolation',isolation)
 bundle={n:base64.b64encode((project/n).read_bytes()).decode() for n in json.loads((project/'BUILD-FILES.json').read_text())}
 check('source_image_and_all_roles_match',lambda:ledger['releases'][status['release']]['source_sha256']==hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest() and all(v['image_id']==ledger['releases'][status['release']]['image_id'] for v in status['roles'].values()))
 check('api_and_dependency_readiness',lambda:status['health']['http_status']==status['ready']['http_status']==200)
 check('all_live_databases_wal_and_integrity',lambda:all(db[n]['integrity']=='ok' and db[n]['journal_mode']=='wal' for n in ['workflows','jobs','sink','mechanics']))
 check('workflow_schema_version',lambda:db['workflows']['user_version']==(1 if a.phase=='v1' else 2))
 check('all_accepted_workflows_settled',lambda:bool(workflows) and all(w['state'] in ['completed','cancelled'] for w in workflows.values()))
 burst=ledger.get('bursts',{}).get(ledger.get('last_agent_burst','native'),{})
 check('burst_matches_current_release_image',lambda:burst['release']==status['release'] and burst['image_id']==ledger['releases'][status['release']]['image_id'])
 check('sixty_actual_barrier_requests',lambda:burst['request_count']==60 and sum(burst['status_counts'].values())==60 and set(burst['status_counts'])<={'200','202','429','503'} and burst['max_overlapping_clients']>1)
 check('initial_and_filled_capacity_reported_separately',lambda:6<=burst['initial_distinct_accepted']<=8 and set(burst['initial_per_owner'])=={'alpha','beta'} and all(3<=n<=4 for n in burst['initial_per_owner'].values()) and len(burst['accepted_workflows'])==8 and burst['per_owner_accepted']=={'alpha':4,'beta':4} and len(burst['fill_calls'])<=8 and all(x['prior_lookup_status']==404 for x in burst['fill_calls']) and burst['quota_probe_status']=={'alpha':429,'beta':429})
 check('burst_replay_convergence_and_completion',lambda:burst['idempotency_converged'] and all(workflows[w['id']]['owner']==w['owner'] and workflows[w['id']]['state']=='completed' for w in burst['accepted_workflows']))
 check('activation_request_keys_each_once',lambda:all(n==1 for n in ledger['activation_attempts'].values()))
 cp=json.loads((project/'CHECKPOINT.json').read_text());id=cp['workflow_id'];expected=request_checkpoint();check('checkpoint_identity_and_content',lambda:workflows[id]['state']=='completed' and json.loads(workflows[id]['payload'])==expected and any(v['release'].startswith('workflow-v1') and v['image_id']==cp['image_id'] and v['api_container_id']==cp['api_container_id'] for v in ledger['requests'].values()))
 check('checkpoint_file_unchanged',lambda:hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'])
 check('checkpoint_replay_same_workflow',lambda:http(root,'/workflows','POST',key='checkpoint-workflow',body=expected)[1]['workflow']['id']==id)
 for name,path,method in [('foreign_workflow','/workflows/'+id,'GET'),('foreign_lookup','/workflow-requests/checkpoint-workflow','GET'),('foreign_artifact','/workflow-artifacts/'+id,'GET'),('foreign_cancel','/workflows/'+id+'/cancel','POST')]:check(name+'_404',lambda path=path,method=method:http(root,path,method,owner='beta')[0]==404)
 check('anonymous_rejected',lambda:http(root,'/workflows/'+id,owner='anonymous')[0]==401)
 def children():
  seen=set()
  for step in steps:
   w=workflows[step['workflow_id']];key=key_for(w['owner'],w['id'],step['name']);assert step['request_key']==key
   if step['child_id'] is None:assert w['state']=='cancelled';continue
   j=jobs[step['child_id']];assert j['owner']==w['owner'] and j['key']==key;assert j['id'] not in seen;seen.add(j['id']);needs=json.loads(step['needs']);p=json.loads(w['payload']);siblings={s['name']:s for s in steps if s['workflow_id']==w['id']}
   text=step['name']+':'+(hashlib.sha256(canonical([{'name':n,'sha256':siblings[n]['sha256']} for n in sorted(needs)])).hexdigest() if needs else p['text']);raw=canonical({'kind':'report','text':text});assert json.loads(j['payload'])==json.loads(raw)
   if step['state']=='completed':assert j['state']=='completed' and step['sha256']==hashlib.sha256(raw).hexdigest() and step['artifact']['sha256']==step['sha256']
  return seen==set(jobs)
 check('all_child_identities_and_deterministic_content',children)
 def dependency_order():
  for s in steps:
   if not s['child_id']:continue
   w=workflows[s['workflow_id']];submit=next(c for c in mechanics if c['kind']=='child_submit' and c['job_id']==s['child_id'] and c['status'] in [200,202]);siblings={t['name']:t for t in steps if t['workflow_id']==w['id']}
   for dep in json.loads(s['needs']):assert any(c['kind']=='child_artifact' and c['job_id']==siblings[dep]['child_id'] and c['status']==200 and c['id']<submit['id'] for c in mechanics)
  return True
 check('descendants_follow_actual_verified_dependency_reads',dependency_order)
 check('no_duplicate_effects_or_unowned_jobs',lambda:effect_proof(db))
 def artifacts():
  for w in workflows.values():
   status,_,raw=http(root,'/workflow-artifacts/'+w['id'],owner=w['owner'])
   if w['state']=='cancelled':assert status==409;continue
   wanted=canonical({'workflow_id':w['id'],'tasks':[{'name':s['name'],'sha256':s['sha256']} for s in sorted([s for s in steps if s['workflow_id']==w['id']],key=lambda x:x['name'])]});assert status==200 and raw==wanted and w['result']['sha256']==w['sha256']==hashlib.sha256(raw).hexdigest()
  for e in effects.values():
   status,_,raw=http(root,'/artifacts/'+e['job_id'],owner=jobs[e['job_id']]['owner']);assert status==200 and hashlib.sha256(raw).hexdigest()==e['sha256']
  return True
 check('all_actual_parent_and_child_artifact_bytes',artifacts)
 def snapshots():
  for w in workflows.values():
   status,value,_=http(root,'/workflows/'+w['id'],owner=w['owner']);assert status==200;v=value['workflow'];assert {'id','owner','key','state','tasks'}<=set(v) and not set(v)&{'payload','text','input','exception','content','prompt'};assert not any(marker in json.dumps(v,ensure_ascii=False) for marker in ['PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY','Level8 checkpoint α'])
  return True
 check('snapshots_exclude_input_and_exception_data',snapshots)
 def event_contract():
  all_events=db['workflows']['tables']['events']
  assert all({'id','workflow_id','owner','task','state'}<=set(e) and not set(e)&{'text','payload','exception','input','content','prompt'} for e in all_events);assert 'PRIVATE_DATA_CANARY' not in json.dumps(all_events)
  for owner in ['alpha','beta']:
   cursor=0
   for _ in range(40):
    code,value,_=http(root,'/workflow-events',owner=owner,cursor=cursor);assert code==200;es=value['events'];assert len(es)<=100
    if not es:break
    for e in es:assert set(e)=={'id','workflow_id','task','state'} and type(e['id']) is int and e['id']>cursor and workflows[e['workflow_id']]['owner']==owner;cursor=e['id']
  return True
 check('owner_isolated_mechanical_event_cursors',event_contract)
 if a.phase=='v2':
  current_mechanics=[c for c in mechanics if c.get('release')==status['release']]
  check('authenticated_v1_snapshot_includes_original_checkpoint',lambda:ledger['v1_snapshot']['authenticated'] is True and ledger['v1_snapshot']['origin']=='examiner' and ledger['v1_snapshot']['release']==meta['passed_v1_release'] and ledger['v1_snapshot']['checkpoint_id_sha256']==hashlib.sha256(id.encode()).hexdigest())
  def target_fault(kind):
   fault=[c for c in current_mechanics if c['kind']==kind];assert len(fault)==1;f=fault[0];related=[c for c in current_mechanics if c['key_sha256']==f['key_sha256'] and c['owner']==f['owner']];assert sum(c['kind']=='child_submit' and c['status'] in [200,202] for c in related)==1;assert any(c['kind']=='child_lookup' and c['status']==200 and c['id']>f['id'] and c['job_id']==f['job_id'] for c in related);return f
  check('lost_child_ack_reconciled_same_identity',lambda:bool(target_fault('child_submit_ack_lost')))
  check('two_lookup503_bound_to_lost_child',lambda:len([c for c in current_mechanics if c['kind']=='child_lookup_unavailable' and c['key_sha256']==target_fault('child_submit_ack_lost')['key_sha256']])==2 and len({c['key_sha256'] for c in current_mechanics if c['kind']=='child_lookup_unavailable'})==1)
  check('coordinator_crash_after_child_commit_recovered',lambda:bool(target_fault('coordinator_exit42')))
  check('actual_coordinator_exit42_restart_archived',lambda:ledger.get('max_restarts',{}).get('coordinator',0)>=1 and any(d['action']=='die' and d['exit_code']=='42' and any(s['action']=='start' and s['at']>=d['at'] for s in role['events']) for role in events['containers'] if role['role']=='coordinator' for d in role['events']))
  def bad_artifact():
   f=next(c for c in current_mechanics if c['kind']=='child_artifact_corrupt');assert any(c['kind']=='child_artifact' and c['job_id']==f['job_id'] and c['id']>f['id'] and c['status']==200 for c in mechanics);return sum(c['kind']=='child_submit' and c['job_id']==f['job_id'] for c in mechanics)==1
  check('corrupt_child_artifact_reread_without_resubmit',bad_artifact)
  def workflow_case(suffix):return next(w for w in workflows.values() if w['key']==status['release']+'-'+suffix)
  def offline_witness():
   w=workflow_case('offline');h=hashlib.sha256(w['id'].encode()).hexdigest();matches=[x for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('workflow_id_sha256')==h and x.get('sink_running') is False and x.get('state') in ['queued','running','reconciling','cancel_requested']]
   for witness in matches:
    stops=[x['at'] for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('kind')=='role_control' and x.get('roles')==['sink'] and x.get('action')=='stop' and x['at']<=witness['at']]
    starts=[x['at'] for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('kind')=='role_control' and x.get('roles')==['sink'] and x.get('action')=='start' and x['at']>=witness['at']]
    if stops and starts:
     lo=max(stops);hi=min(starts)
     if offline_probe_pair(obs,lo,hi,status['release']):return True
   return False
  check('offline_parent_observed_without_false_success',offline_witness)
  def paused_witnesses():
   w=workflow_case('pause');h=hashlib.sha256(w['id'].encode()).hexdigest();return [x for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('workflow_id_sha256')==h and x.get('paused') is True and x.get('state') not in ['completed','cancelled'] and x.get('task_states',{}).get('prepare')=='completed' and x.get('task_states',{}).get('publish')=='pending']
  check('pause_reconciles_root_blocks_descendant',lambda:bool(paused_witnesses()))
  def paused_restart():
   ws=paused_witnesses();controls=[x for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('kind')=='role_control' and x.get('roles')==['coordinator']]
   return any(a['at']<=stop['at']<start['at']<=b['at'] for a in ws for b in ws for stop in controls if stop['action']=='stop' for start in controls if start['action']=='start')
  check('coordinator_manual_restart_during_pause',paused_restart)
  def cancellation():
   queued=workflow_case('cancel-queued');running=workflow_case('cancel-running');assert queued['state']==running['state']=='cancelled';assert not any(s['child_id'] for s in steps if s['workflow_id']==queued['id']);children=[s['child_id'] for s in steps if s['workflow_id']==running['id'] and s['child_id']];assert len(children)==1;states=[e['state'] for e in db['jobs']['tables']['events'] if e['job_id']==children[0]];return 'running' in states and 'cancel_requested' in states and states.index('running')<states.index('cancel_requested')
  check('both_queued_and_running_cancel_without_descendants',cancellation)
  def priority():
   low=next(w for w in workflows.values() if w['key']==status['release']+'-priority-low');high=next(w for w in workflows.values() if w['key']==status['release']+'-priority-high');lo={s['child_id'] for s in steps if s['workflow_id']==low['id']};hi={s['child_id'] for s in steps if s['workflow_id']==high['id']};return high['priority']==9 and low['priority']==0 and min(c['id'] for c in mechanics if c['kind']=='child_submit' and c['job_id'] in hi)<min(c['id'] for c in mechanics if c['kind']=='child_submit' and c['job_id'] in lo)
  check('priority_applied_before_child_dispatch',priority)
  h=hashlib.sha256((project/'workflow_engine.py').read_bytes()).hexdigest();check('native_migration_probe_current_source',lambda:any(x.get('origin')=='agent' and x.get('base_label')=='native' and x.get('passed') is True and x.get('image_id')==ledger['releases'][status['release']]['image_id'] and x.get('workflow_engine_sha256')==h for x in ledger.get('probes',{}).get('migration-drill',[])))
  result=call(root,{'op':'migration-drill','label':'examiner'});Path(str(prefix)+'-migration.json').write_text(json.dumps(result,indent=2));check('independent_actual_snapshot_migration_probe',lambda:result.get('passed') is True and result.get('probe_exit_code')==0)
  revision=status['release'].removeprefix('workflow-v2');rb='rollback-v1'+revision;rs='restore-v2'+revision
  check('settled_v1_code_rollback_then_tested_v2_restore',lambda:ledger['requests'][rb]['release']==meta['passed_v1_release'] and ledger['requests'][rs]['release']==status['release'] and any(x.get('origin')=='agent' and x.get('release')==meta['passed_v1_release'] and x.get('route')=='/workflow-artifacts/:id' and x.get('status')==200 and ledger['requests'][rb]['at']<x.get('at',0)<ledger['requests'][rs]['at'] for x in obs))
  report=json.loads((project/'RESULT.json').read_text());check('native_report_honest_scope',lambda:report.get('scope')=='isolated_durable_dag' and report.get('production_changed') is False and report.get('same_session_resume') is True and report.get('completed') is True and (project/'HASIL.md').is_file())
 logs=call(root,{'op':'logs'});check('all_registered_container_logs_scanned',lambda:len(logs['complete_history_scans'])==len(ledger['role_history']) and all(x['canary_present'] is False for x in logs['complete_history_scans']))
 result={'phase':a.phase,'passed':all(checks.values()),'checks':checks,'failures':[n for n,v in checks.items() if not v],'errors':errors};Path(str(prefix)+'-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
def request_checkpoint():return {'text':'Level8 checkpoint α','tasks':[{'name':'prepare','needs':[]},{'name':'publish','needs':['prepare']}]}
if __name__=='__main__':
 try:main()
 except Exception as error:
  args=sys.argv
  if '--root' in args and '--label' in args and '--phase' in args:
   p=Path(args[args.index('--root')+1])/(args[args.index('--label')+1]+'-result.json')
   if not p.exists():p.write_text(json.dumps({'phase':args[args.index('--phase')+1],'passed':False,'checks':{},'failures':['grading_interrupted'],'errors':{'grading_interrupted':type(error).__name__}},indent=2))
  print(json.dumps({'grading_interrupted':type(error).__name__}));raise SystemExit(1)
