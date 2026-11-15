#!/usr/bin/env python3
"""Independent native Level9 grading; no model claims are execution evidence."""
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
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--phase',choices=['v3'],required=True);p.add_argument('--label',required=True);a=p.parse_args();root=a.root;project=root/'project';prefix=root/a.label;lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if Path(str(prefix)+'-result.json').exists():raise RuntimeError('preserve_existing_grade')
 meta=json.loads((root/'grade.json').read_text());checks={};errors={}
 def check(name,fn):
  try:checks[name]=bool(fn())
  except (AssertionError,KeyError,IndexError,TypeError,ValueError,RuntimeError,OSError,AttributeError,StopIteration) as e:checks[name]=False;errors[name]=type(e).__name__
 check('protected_files',lambda:all((project/n).is_file() and not (project/n).is_symlink() and hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()))
 if not checks['protected_files']:raise RuntimeError('protected_changed')
 env=os.environ.copy();env.update(HARMONIKA_EXAM_PROJECT=str(project),HARMONIKA_EXAM_PHASE='v2',PYTHONPYCACHEPREFIX=str(root/'examiner-pycache'))
 local=subprocess.run([sys.executable,'-I',str(REPO/'scripts/level9-lab/independent.py')],env=env,capture_output=True,text=True,timeout=120);Path(str(prefix)+'-local.txt').write_text(local.stdout+local.stderr);checks['independent_local_tests']=local.returncode==0
 linux=call(root,{'op':'test-engine','phase':a.phase});Path(str(prefix)+'-linux.txt').write_text(linux['output']);checks['independent_linux_image_tests']=linux['exit_code']==0
 ledger=call(root,{'op':'ledger'});status=call(root,{'op':'status'});db=call(root,{'op':'db-audit'});events=call(root,{'op':'events'});Path(str(prefix)+'-native-evidence.json').write_text(json.dumps({'ledger':ledger,'status':status,'db':db,'docker_events':events},indent=2))
 workflows={w['id']:w for w in db['workflows']['tables']['workflows']};steps=db['workflows']['tables']['steps'];jobs={j['id']:j for j in db['jobs']['tables']['jobs']};effects={e['job_id']:e for e in db['sink']['tables']['effects']};mechanics=db['mechanics']['tables']['calls'];obs=ledger.get('critical_witnesses',[])+ledger.get('observations',[])
 check('eight_independent_containers_running',lambda:len({v['container_id'] for v in status['roles'].values()})==8 and all(v['running'] for v in status['roles'].values()))
 def isolation():
  for name,v in status['roles'].items():
   h=v['isolation'];mem,cpu,pids=(536870912,1000000000,640) if name=='api' else (201326592,500000000,64) if name=='sink' or name.startswith('coordinator') else (134217728,500000000,96) if name=='proxy' else (134217728,500000000,32)
   if not(h['user']=='10001:10001' and h['network']==(ledger['network'] if name in ['api','proxy','coordinator1','coordinator2'] else 'none') and h['readonly'] and h['memory']==mem and h['nano_cpus']==cpu and h['pids']==pids and 'ALL' in h['cap_drop'] and any(x.startswith('no-new-privileges') for x in h['security_opt'])):return False
  return True
 check('runtime_resource_and_network_isolation',isolation)
 code="""import json,subprocess,hashlib,sys
r=json.load(sys.stdin);d=json.loads(subprocess.check_output(['docker','network','inspect',r['network']]))[0];assert d.get('Labels',{}).get('id.harmonika.level9')==r['lab'];ports=[]
for cid in set(r['current'])|set(d.get('Containers',{})):
 v=json.loads(subprocess.check_output(['docker','inspect',cid]))[0];assert v['Config']['Labels']['id.harmonika.level9']==r['lab'];ports.append(bool(v['HostConfig'].get('PortBindings')))
print(json.dumps({'internal':d['Internal'],'all_members_registered':set(d.get('Containers',{}))<=set(r['current']),'published_ports':any(ports)}))
"""
 import shlex
 n=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'network':ledger['network'],'lab':json.loads((root/'lab.json').read_text())['lab_id'],'current':[v['container_id'] for v in status['roles'].values()],'registered':ledger['containers']}),capture_output=True,text=True,timeout=30);network_isolation=json.loads(n.stdout);Path(str(prefix)+'-network-isolation.json').write_text(json.dumps(network_isolation,indent=2));check('owned_internal_network_no_published_ports',lambda:network_isolation['internal'] and network_isolation['all_members_registered'] and not network_isolation['published_ports'])
 bundle={n:base64.b64encode((project/n).read_bytes()).decode() for n in json.loads((project/'BUILD-FILES.json').read_text())}
 check('source_image_and_all_roles_match',lambda:ledger['releases'][status['release']]['source_sha256']==hashlib.sha256(json.dumps(bundle,sort_keys=True).encode()).hexdigest() and all(v['image_id']==ledger['releases'][status['release']]['image_id'] for v in status['roles'].values()))
 check('api_and_dependency_readiness',lambda:status['health']['http_status']==status['ready']['http_status']==200)
 check('all_live_databases_wal_and_integrity',lambda:all(db[n]['integrity']=='ok' and db[n]['journal_mode']=='wal' for n in ['workflows','jobs','sink','mechanics','network']))
 check('workflow_schema_version',lambda:db['workflows']['user_version']==3)
 check('all_accepted_workflows_settled',lambda:bool(workflows) and all(w['state'] in ['completed','cancelled'] for w in workflows.values()))
 burst=ledger.get('bursts',{}).get(ledger.get('last_agent_burst','native'),{})
 check('burst_matches_current_release_image',lambda:burst['release']==status['release'] and burst['image_id']==ledger['releases'][status['release']]['image_id'])
 check('two_hundred_actual_barrier_requests',lambda:burst['request_count']==200 and sum(burst['status_counts'].values())==200 and set(burst['status_counts'])<={'200','202','429','503'} and burst['max_overlapping_clients']>1)
 check('initial_and_filled_capacity_reported_separately',lambda:6<=burst['initial_distinct_accepted']<=8 and set(burst['initial_per_owner'])=={'alpha','beta'} and all(3<=n<=4 for n in burst['initial_per_owner'].values()) and len(burst['accepted_workflows'])==8 and burst['per_owner_accepted']=={'alpha':4,'beta':4} and len(burst['fill_calls'])<=8 and all(x['prior_lookup_status']==404 for x in burst['fill_calls']) and burst['quota_probe_status']=={'alpha':429,'beta':429})
 def burst_stopped_both():
  assert burst['coordinators_running_before']==burst['coordinators_running_after']=={'coordinator1':False,'coordinator2':False};begin,end=burst['started_at'],burst['finished_at'];assert begin<=end
  controls=[x for x in obs if x.get('origin')=='agent' and x.get('release')==status['release'] and x.get('kind')=='role_control']
  return all(any(role in x.get('roles',[]) and x.get('action')=='stop' and x['at']<=begin for x in controls) and any(role in x.get('roles',[]) and x.get('action')=='start' and x['at']>=end for x in controls) for role in ['coordinator1','coordinator2'])
 check('burst_actual_window_both_coordinators_stopped_then_started',burst_stopped_both)
 check('burst_replay_convergence_and_completion',lambda:burst['idempotency_converged'] and all(workflows[w['id']]['owner']==w['owner'] and workflows[w['id']]['state']=='completed' for w in burst['accepted_workflows']))
 check('activation_request_keys_each_once',lambda:all(n==1 for n in ledger['activation_attempts'].values()))
 cp=json.loads((project/'CHECKPOINT.json').read_text());baseline=json.loads((project/'BASELINE.json').read_text());id=cp['workflow_id'];expected=baseline['checkpoint_request'];check('checkpoint_identity_and_content',lambda:workflows[id]['state']=='completed' and json.loads(workflows[id]['payload'])==expected and ledger['requests']['stage-v2']['image_id']==baseline['image_id'])
 def legacy_rows():
  for table,old_rows in baseline['legacy_tables'].items():
   current=db['workflows']['tables'][table]
   for old in old_rows:assert any(all(row.get(k)==v for k,v in old.items()) for row in current)
  code,_,raw=http(root,'/workflow-artifacts/'+id);return code==200 and raw==base64.b64decode(baseline['artifact_b64']) and hashlib.sha256(raw).hexdigest()==baseline['artifact_sha256']
 check('legacy_row_values_and_checkpoint_bytes_preserved',legacy_rows)
 check('checkpoint_file_unchanged',lambda:hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'])
 check('checkpoint_replay_same_workflow',lambda:http(root,'/workflows','POST',key='checkpoint-l9',body=expected)[1]['workflow']['id']==id)
 for name,path,method in [('foreign_workflow','/workflows/'+id,'GET'),('foreign_lookup','/workflow-requests/checkpoint-l9','GET'),('foreign_artifact','/workflow-artifacts/'+id,'GET'),('foreign_cancel','/workflows/'+id+'/cancel','POST')]:check(name+'_404',lambda path=path,method=method:http(root,path,method,owner='beta')[0]==404)
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
   status,value,_=http(root,'/workflows/'+w['id'],owner=w['owner']);assert status==200;v=value['workflow'];assert {'id','owner','key','state','tasks'}<=set(v) and not set(v)&{'payload','text','input','exception','content','prompt'};assert not any(marker in json.dumps(v,ensure_ascii=False) for marker in ['PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY','Level9 checkpoint α'])
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
 if a.phase=='v3':
  network=db['network']['tables']['calls'];audit=db['workflows']['tables']['coordinator_audit'];release=status['release'];current=[x for x in network if x['release']==release]
  correction_path=root/'case-correction.json';correction=json.loads(correction_path.read_text()) if correction_path.exists() else None
  aliases={}
  if correction:
   assert correction['release']==release and correction['image_id']==status['roles']['api']['image_id'] and correction['reason']=='operator_audit_truncation' and correction['cases']=={'stale':'stale-a2'}
   aliases=correction['cases']
  def case(suffix):return next(w for w in workflows.values() if w['key']==release+'-'+aliases.get(suffix,suffix))
  def root_step(w):return next(s for s in steps if s['workflow_id']==w['id'] and s['name']=='prepare')
  def tcp_fault(event,suffix):
   w=case(suffix);s=root_step(w);faults=[x for x in current if x['event']==event and x['job_id']==s['child_id']];assert len(faults)==1;f=faults[0]
   assert sum(x['kind']=='child_submit' and x['job_id']==s['child_id'] and x['status'] in [200,202] for x in mechanics)==1
   assert any(x['kind']=='child_lookup' and x['job_id']==s['child_id'] and x['status']==200 for x in mechanics) if event=='ack_dropped' else True
   return f
  check('real_tcp_postcommit_ack_drop_reconciled',lambda:bool(tcp_fault('ack_dropped','ack')))
  def partial():
   f=tcp_fault('artifact_truncated','partial');return any(x['event']=='forward' and x['route']=='/artifacts/:id' and x['job_id']==f['job_id'] and x['status']==200 and x['id']>f['id'] for x in current)
  check('real_truncated_http_body_reread',partial)
  def stale():
   w=case('stale');s=root_step(w);h=hashlib.sha256(w['id'].encode()).hexdigest();hold=tcp_fault('response_hold','stale');assert hold['coordinator_id']=='coordinator1'
   released=next(x for x in current if x['event']=='response_released' and x['job_id']==s['child_id']);assert released['id']>hold['id'];assert not any(x['event']=='hold_timeout' for x in current)
   rows=[x for x in audit if x['workflow_id']==w['id'] and x['task']=='prepare'];first=next(x for x in rows if x['event']=='claim' and x['coordinator_id']=='coordinator1');settle=next(x for x in rows if x['event']=='settle' and x['coordinator_id']=='coordinator2' and x['epoch']>first['epoch']);late=next(x for x in rows if x['event']=='stale_rejected' and x['coordinator_id']=='coordinator1' and first['epoch']<=x['epoch']<settle['epoch']);assert first['id']<settle['id']<late['id'];assert any(x['event']=='claim' and x['coordinator_id']=='coordinator1' and x['epoch']==late['epoch'] for x in rows)
   assert any(x.get('kind')=='database_witness' and x.get('origin')=='agent' and x.get('release')==release and hold['at']<=x['at']<=released['at'] and any(t['id']==settle['id'] and t['workflow_id_sha256']==h for t in x['claim_trace']) for x in obs)
   assert any(x.get('kind')=='http' and x.get('origin')=='agent' and x.get('release')==release and x.get('workflow_id_sha256')==h and x.get('state')=='completed' and hold['at']<=x['at']<=released['at'] for x in obs)
   cid=status['roles']['coordinator1']['container_id'];ch=hashlib.sha256(cid.encode()).hexdigest();assert any(x.get('kind')=='cluster_status' and x.get('origin')=='agent' and hold['at']<=x['at']<=released['at'] and x.get('roles',{}).get('coordinator1',{}).get('running') is True and x['roles']['coordinator1']['container_sha256']==ch for x in obs)
   assert not any(x['action']=='die' and hold['at']<=x['at']<=released['at'] for role in events['containers'] if role['container_sha256']==ch for x in role['events']);return True
  check('physically_live_stale_coordinator_fenced_after_takeover',stale)
  def epochs():
   claims=[x for x in audit if x['event']=='claim'];assert claims and all(type(x['epoch']) is int and x['epoch']>0 for x in audit);assert {'coordinator1','coordinator2'}<=set(x['coordinator_id'] for x in claims)
   by={}
   for x in claims:by.setdefault((x['workflow_id'],x['task']),[]).append(x)
   return all([x['epoch'] for x in sorted(rows,key=lambda x:x['id'])]==sorted(set(x['epoch'] for x in rows)) for rows in by.values())
  check('two_coordinators_durable_integer_epochs',epochs)
  def audit_schema():
   return all(set(x)=={'id','coordinator_id','workflow_id','task','epoch','event'} and x['event'] in ['claim','settle','stale_rejected'] and x['workflow_id'] in workflows and x['coordinator_id'] in ['coordinator1','coordinator2'] for x in audit) and all(set(x)=={'id','event','coordinator_id','method','route','key_sha256','job_id','status','latency_ms','at','release'} for x in network) and not any(marker in json.dumps({'audit':audit,'network':network}) for marker in ['PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY','Level9 checkpoint α'])
  check('coordinator_and_tcp_logs_mechanics_only',audit_schema)
  def partition():
   w=case('partition');h=hashlib.sha256(w['id'].encode()).hexdigest();controls=[x for x in obs if x.get('origin')=='agent' and x.get('kind')=='fault' and x.get('release')==release]
   for stop in controls:
    if stop.get('fault')!='partition-on':continue
    starts=[x for x in controls if x.get('fault')=='partition-off' and x['at']>stop['at']]
    if not starts:continue
    lo,hi=stop['at'],min(x['at'] for x in starts)
    if not any(x['event']=='partition_closed' and x['coordinator_id'] in ['coordinator1','coordinator2'] and lo<=x['at']<=hi for x in current):continue
    if not any(x.get('origin')=='agent' and x.get('workflow_id_sha256')==h and lo<=x['at']<=hi and x.get('state') in ['queued','running','reconciling'] for x in obs):continue
    probes=[x for x in obs if x.get('release')==release and x.get('origin') in ['agent','controller'] and lo<=x.get('at',0)<=hi and x.get('kind')=='http']
    if all(any(x.get('route')==route and x.get('status')==code for x in probes) for route,code in [('/healthz',200),('/readyz',503)]):return True
   return False
  check('tcp_partition_nonterminal_readiness_and_recovery',partition)
  def crash():
   w=case('crash');s=root_step(w);f=next(x for x in mechanics if x['kind']=='coordinator_exit42' and x['job_id']==s['child_id'] and x.get('release')==release);assert sum(x['kind']=='child_submit' and x['job_id']==s['child_id'] and x['status'] in [200,202] for x in mechanics)==1;assert any(x['kind']=='child_lookup' and x['job_id']==s['child_id'] and x['status']==200 and x['id']>f['id'] for x in mechanics)
   return any(role['role'] in ['coordinator1','coordinator2'] and role['release']==release and any(d['action']=='die' and d['exit_code']=='42' and any(s['action']=='start' and s['at']>=d['at'] for s in role['events']) for d in role['events']) for role in events['containers'])
  check('coordinator_exit42_and_same_child_recovery',crash)
  def pause_restart():
   w=case('pause');h=hashlib.sha256(w['id'].encode()).hexdigest();ws=[x for x in obs if x.get('origin')=='agent' and x.get('release')==release and x.get('workflow_id_sha256')==h and x.get('paused') is True and x.get('task_states',{}).get('prepare')=='completed' and x.get('task_states',{}).get('publish')=='pending'];controls=[x for x in obs if x.get('origin')=='agent' and x.get('release')==release and x.get('kind')=='role_control' and set(x.get('roles',[]))=={'coordinator1','coordinator2'}]
   return any(a['at']<=stop['at']<start['at']<=b['at'] for a in ws for b in ws for stop in controls if stop['action']=='stop' for start in controls if start['action']=='start')
  check('pause_survives_both_coordinator_restarts',pause_restart)
  def cancellation():
   w=case('cancel');s=[s for s in steps if s['workflow_id']==w['id'] and s['child_id']];assert w['state']=='cancelled' and len(s)==1;es=[x['state'] for x in db['jobs']['tables']['events'] if x['job_id']==s[0]['child_id']];return 'running' in es and 'cancel_requested' in es and es.index('running')<es.index('cancel_requested')
  check('running_child_cancel_suppresses_descendant',cancellation)
  def physical_running_controls():
   for suffix in ['pause','cancel']:
    w=case(suffix);h=hashlib.sha256(w['id'].encode()).hexdigest();child=root_step(w)['child_id']
    witnesses=[x for x in obs if x.get('kind')=='running_witness' and x.get('origin')=='agent' and x.get('release')==release and x.get('workflow_id_sha256')==h and x.get('job_id')==child and x.get('observed') is True and x.get('state')=='running' and x.get('hold_active') is True and x.get('sink_effect_rows')==0 and x.get('worker_running') is True and x.get('sampled_at',0)<=x.get('at',0)<x.get('lease_until',0)]
    assert any(y.get('kind')=='http' and y.get('origin')=='agent' and y.get('release')==release and y.get('workflow_id_sha256')==h and y.get('method')=='POST' and y.get('route')=='/workflows/:id/'+suffix and y.get('status')==200 and x['at']<=y.get('at',0)<x['lease_until'] for x in witnesses for y in obs)
   return True
  check('pause_cancel_controls_observed_physical_running_child',physical_running_controls)
  check('observed_load_latency_and_native_resource_snapshot',lambda:0<=burst['latency_ms']['p50']<=burst['latency_ms']['p95']<=burst['latency_ms']['max'] and any(x.get('kind')=='resource_snapshot' and x.get('origin')=='agent' and x.get('release')==release and len(x['roles'])==8 for x in obs))
  def complete_report():
   if not (project/'RESULT.json').is_file() or not (project/'HASIL.md').is_file():return False
   result=json.loads((project/'RESULT.json').read_text());return result.get('scope')=='isolated_dual_coordinator_tcp' and result.get('production_changed') is False and result.get('completed') is True and result.get('checkpoint_preserved') is True
  check('native_report_complete_and_scoped',complete_report)
 logs=call(root,{'op':'logs'});check('all_registered_container_logs_scanned',lambda:len(logs['complete_history_scans'])==len(ledger['role_history']) and all(x['canary_present'] is False for x in logs['complete_history_scans']))
 result={'phase':a.phase,'passed':all(checks.values()),'checks':checks,'failures':[n for n,v in checks.items() if not v],'errors':errors};Path(str(prefix)+'-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as error:
  args=sys.argv
  if '--root' in args and '--label' in args and '--phase' in args:
   p=Path(args[args.index('--root')+1])/(args[args.index('--label')+1]+'-result.json')
   if not p.exists():p.write_text(json.dumps({'phase':args[args.index('--phase')+1],'passed':False,'checks':{},'failures':['grading_interrupted'],'errors':{'grading_interrupted':type(error).__name__}},indent=2))
  print(json.dumps({'grading_interrupted':type(error).__name__}));raise SystemExit(1)
