#!/usr/bin/env python3
"""Independent Level10 grading: actual bytes/state and provenance, never agent claims."""
import argparse,base64,fcntl,hashlib,importlib.util,json,os,re,shlex,subprocess,sys
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('prior_grade',Path(__file__).with_name('level9-grade.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
call=g.call;http=g.http;canonical=g.canonical

def eligible_replay_admissions(accepted,observations,release,snapshot_at,reconciled_at,highwater):
 acknowledged={x.get('owner_key_sha256') for x in observations if x.get('origin')=='agent' and x.get('release')==release and x.get('kind')=='http' and x.get('method')=='POST' and x.get('route')=='/workflows' and x.get('status') in [200,202] and snapshot_at<x.get('at',0)<=reconciled_at}
 return [a for a in accepted if a['seq']>highwater and a['key'].startswith(release+'-') and hashlib.sha256((a['owner']+'\0'+a['key']).encode()).hexdigest() in acknowledged]

def safe_reconcile_receipt(receipt):
 try:
  v=receipt['result'];c=receipt['effect_witness']
  return v['complete'] is True and v['active'] is False and v['unresolved']==0 and c['before']['effects']==c['after']['effects'] and c['before']['child_posts']==c['after']['child_posts'] and c['after']['child_lookups']>c['before']['child_lookups']
 except (KeyError,TypeError):return False

def probe_witnesses(observations,probe):
 # Old controller flattened holder.kind over the envelope kind. Read the actual
 # retained legacy records without rewriting their provenance or timestamps.
 if probe not in ['writer','lock']:raise ValueError('invalid_probe')
 rows=[]
 for x in observations:
  action=x.get('action');kind=x.get('kind');at=x.get('at');started=x.get('started_at')
  if x.get('origin')!='agent' or x.get('probe')!=probe or action not in ['start','state','release']:continue
  expected='probe_state' if action=='state' else 'probe_control'
  if kind not in [probe,expected] or type(at) not in [int,float] or type(started) not in [int,float] or not 0<started<=at:continue
  state=x.get('state');alive=x.get('alive')
  if not (state=='holding' and alive is True or state=='committed' and alive is False):continue
  if action=='start' and state!='holding' or action=='release' and state!='committed':continue
  if state=='committed' and (type(x.get('finished_at')) not in [int,float] or not started<=x['finished_at']<=at):continue
  rows.append({**x,'kind':expected})
 return rows

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--label',required=True);a=p.parse_args();root=a.root;project=root/'project';prefix=root/a.label
 if not re.fullmatch(r'[a-z0-9-]{1,48}',a.label):raise ValueError('invalid_label')
 lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if Path(str(prefix)+'-result.json').exists():raise RuntimeError('preserve_existing_grade')
 checks={};errors={};meta=json.loads((root/'grade.json').read_text())
 def check(name,fn):
  try:checks[name]=bool(fn())
  except (AssertionError,KeyError,ValueError,TypeError,IndexError,StopIteration,OSError,RuntimeError,AttributeError) as e:checks[name]=False;errors[name]=type(e).__name__
 check('protected_project_files',lambda:{'BASELINE.json','CHECKPOINT.json'}<=set(meta['protected']) and all((project/n).is_file() and not (project/n).is_symlink() and hashlib.sha256((project/n).read_bytes()).hexdigest()==sha for n,sha in meta['protected'].items()))
 if not checks['protected_project_files']:raise RuntimeError('protected_changed')
 harness=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(REPO/'tests'),'-p','test_level10_harness.py','-v'],capture_output=True,text=True,timeout=30);Path(str(prefix)+'-harness.txt').write_text(harness.stdout+harness.stderr);checks['protected_harness_14_guards']=harness.returncode==0 and 'Ran 14 tests' in harness.stderr
 env={**os.environ,'HARMONIKA_EXAM_PROJECT':str(project),'HARMONIKA_EXAM_PHASE':'v2','PYTHONPYCACHEPREFIX':str(root/'examiner-pycache')};local=subprocess.run([sys.executable,'-I',str(REPO/'scripts/level10-lab/independent.py')],env=env,capture_output=True,text=True,timeout=120);Path(str(prefix)+'-local.txt').write_text(local.stdout+local.stderr);checks['independent_mac_56_cases']=local.returncode==0 and 'Ran 56 tests' in local.stderr
 linux=call(root,{'op':'test-engine','phase':'v4'});Path(str(prefix)+'-linux.txt').write_text(linux['output']);checks['independent_linux_image_56_cases']=linux['exit_code']==0 and 'Ran 56 tests' in linux['output']
 ledger=call(root,{'op':'ledger'});status=call(root,{'op':'status'});db=call(root,{'op':'db-audit'});events=call(root,{'op':'events'});Path(str(prefix)+'-native-evidence.json').write_text(json.dumps({'ledger':ledger,'status':status,'db':db,'docker_events':events},indent=2));release=status['release'];image=ledger['releases'][release]['image_id'];baseline=json.loads((project/'BASELINE.json').read_text());base_image=ledger['releases']['recovery-v3']['image_id'];obs=ledger.get('critical_witnesses',[])+ledger.get('observations',[]);native=[x for x in obs if x.get('origin')=='agent'];current=[x for x in native if x.get('release')==release]
 jobs={x['id']:x for x in db['jobs']['tables']['jobs']};workflows={x['id']:x for x in db['workflows']['tables']['workflows']};steps=db['workflows']['tables']['steps'];audit=db['workflows']['tables']['coordinator_audit'];mechanics=db['mechanics']['tables']['calls'];admissions=db.get('admissions',{}).get('tables',{}).get('admissions',[]);effects=db['sink']['tables']['effects'];accepted=[x for x in admissions if x['state']=='accepted'];roles=status['roles'];history=ledger['role_history']
 check('eight_registered_running_roles',lambda:set(roles)=={'api','coordinator1','coordinator2','sink','worker1','worker2','worker3','proxy'} and len({x['container_id'] for x in roles.values()})==8 and all(x['running'] for x in roles.values()) and all(x['container_id'] in history for x in roles.values()))
 check('current_api_and_both_coordinators_image',lambda:all(roles[n]['image_id']==image for n in ['api','coordinator1','coordinator2']) and all(roles[n]['image_id']==base_image for n in ['sink','worker1','worker2','worker3','proxy']))
 check('current_image_matches_all_source_files',lambda:all(hashlib.sha256((project/n).read_bytes()).hexdigest()==sha for n,sha in ledger['release_file_sha256'][release].items()))
 def profiles():
  for n,v in roles.items():
   x=v['isolation'];assert x['user']=='10001:10001' and x['readonly'] and 'ALL' in x['cap_drop'] and any(y.startswith('no-new-privileges') for y in x['security_opt'])
   mem,cpu,pids=(512*1024**2,10**9,640) if n=='api' else (192*1024**2,5*10**8,64) if n=='sink' or n.startswith('coordinator') else (128*1024**2,5*10**8,96 if n=='proxy' else 32)
   assert (x['memory'],x['nano_cpus'],x['pids'])==(mem,cpu,pids)
   assert x['network']==ledger['network'] if n in ['api','proxy','coordinator1','coordinator2'] else x['network']=='none'
  return True
 check('runtime_resource_and_isolation_caps',profiles)
 remote=json.loads((root/'lab.json').read_text())['remote_root'];lab=json.loads((root/'lab.json').read_text())['lab_id']
 code="""import json,subprocess,sys,hashlib
r=json.load(sys.stdin);label='id.harmonika.level10';network=json.loads(subprocess.check_output(['docker','network','inspect',r['network']]))[0];assert network['Internal'] and network['Labels'].get(label)==r['lab'];assert network['Options'].get('com.docker.network.bridge.enable_ip_masquerade')=='false'
current=set(r['roles'].values());members=set(network.get('Containers',{}));assert members<=current
for cid in current|members:
 d=json.loads(subprocess.check_output(['docker','inspect',cid]))[0];assert d['Config']['Labels'].get(label)==r['lab'];assert not d['HostConfig'].get('PortBindings');assert not d['NetworkSettings'].get('Ports')
print(json.dumps({'passed':True,'published_ports':0,'internal':True,'actual_members':len(members)}))
"""
 q=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'network':ledger['network'],'roles':{n:v['container_id'] for n,v in roles.items()},'lab':lab}),capture_output=True,text=True,timeout=30);Path(str(prefix)+'-network.json').write_text(q.stdout+q.stderr);checks['owned_internal_network_no_host_ports']=q.returncode==0
 check('all_live_databases_integrity_wal_complete_audit',lambda:all(x['integrity']=='ok' and x['journal_mode']=='wal' and all(len(x['tables'][n])==count for n,count in x['table_counts'].items()) for x in db.values()) and {'jobs','sink','workflows','mechanics','network','admissions','recovery_audit','chaos'}<=set(db))
 check('workflow_additive_schema4',lambda:db['workflows']['user_version']==4 and all(w['retention_class']=='standard' for w in workflows.values()))
 check('all_accepted_live_workflows_settled',lambda:all(w['state'] in ['completed','cancelled'] for w in workflows.values()) and len(workflows)>5)
 check('no_duplicate_effects_or_unowned_jobs',lambda:g.effect_proof(db) and {s['child_id'] for s in steps if s['child_id']}==set(jobs))
 def identity():
  for a in accepted:
   w=workflows[a['workflow_id']];assert (a['owner'],a['key'],a['payload'])==(w['owner'],w['key'],w['payload']);assert a['payload_sha256']==hashlib.sha256(a['payload'].encode()).hexdigest();assert re.fullmatch('[a-f0-9]{32}',w['id'])
  assert len({(a['owner'],a['key']) for a in admissions})==len(admissions);assert len({a['workflow_id'] for a in admissions})==len(admissions)
  return True
 check('journal_accepted_bindings_and_stable_identity',identity)
 def preserved_observed():
  completed={x['workflow_id_sha256'] for x in obs if x.get('kind')=='http' and x.get('state')=='completed' and x.get('workflow_id_sha256')}
  now={hashlib.sha256(w['id'].encode()).hexdigest():w for w in workflows.values()}
  assert all(h in now and now[h]['state']=='completed' for h in completed)
  bykey={hashlib.sha256((w['owner']+'\0'+w['key']).encode()).hexdigest():w for w in workflows.values()}
  for x in obs:
   if x.get('kind')=='http' and x.get('method')=='POST' and x.get('route')=='/workflows' and x.get('status') in [200,202] and x.get('owner_key_sha256') and x.get('payload_sha256'):
    w=bykey[x['owner_key_sha256']];assert hashlib.sha256(w['payload'].encode()).hexdigest()==x['payload_sha256']
  for prior in root.glob('*-native-evidence.json'):
   if prior.name in ['final-native-evidence.json',Path(str(prefix)+'-native-evidence.json').name]:continue
   old=json.loads(prior.read_text()).get('db',{}).get('admissions',{}).get('tables',{}).get('admissions',[]);present={(a['owner'],a['key']):a for a in admissions}
   for a in old:assert all(present[(a['owner'],a['key'])][k]==a[k] for k in ['seq','owner','key','workflow_id','payload','payload_sha256'])
  return True
 check('prior_observed_completion_payloads_and_journal_identities_preserved',preserved_observed)
 def byte_artifacts():
  for w in workflows.values():
   group=sorted([s for s in steps if s['workflow_id']==w['id']],key=lambda s:s['name']);payload=json.loads(w['payload']);expected=[]
   for s in group:
    assert s['request_key']==g.key_for(w['owner'],w['id'],s['name'])
    if s['child_id']:assert jobs[s['child_id']]['owner']==w['owner'] and jobs[s['child_id']]['key']==s['request_key']
    if s['state']!='completed':continue
    needs=sorted(json.loads(s['needs']));digest=hashlib.sha256(canonical([{'name':n,'sha256':next(t['sha256'] for t in group if t['name']==n)} for n in needs])).hexdigest();raw=canonical({'kind':'report','text':s['name']+':'+(digest if needs else payload['text'])});sha=hashlib.sha256(raw).hexdigest();assert sha==s['sha256']==s['artifact']['sha256'];code,_,actual=http(root,'/artifacts/'+s['child_id'],owner=w['owner']);assert code==200 and raw==actual;expected.append({'name':s['name'],'sha256':sha})
   code,_,raw=http(root,'/workflow-artifacts/'+w['id'],owner=w['owner'])
   if w['state']=='completed':assert code==200 and raw==canonical({'workflow_id':w['id'],'tasks':expected}) and hashlib.sha256(raw).hexdigest()==w['sha256']
   else:assert code==409
  return True
 check('actual_live_parent_child_artifact_bytes',byte_artifacts)
 def legacy_preserved():
  for table,rows in baseline['legacy_tables'].items():
   actual=db['workflows']['tables'][table]
   for row in rows:assert any(all(x[k]==v for k,v in row.items()) for x in actual)
  code,_,raw=http(root,'/workflow-artifacts/'+baseline['checkpoint_id'],owner='alpha');assert code==200 and raw==base64.b64decode(baseline['artifact_b64']) and hashlib.sha256(raw).hexdigest()==baseline['artifact_sha256'];return True
 check('original_checkpoint_and_legacy_row_values_preserved',legacy_preserved)
 check('checkpoint_file_unchanged',lambda:hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'])
 def rolling_window(kind,role,expected_image):
  start=next(x for x in current if x.get('kind')=='rolling' and x.get('operation')==kind);end=next(x for x in current if x.get('kind')=='rolling' and x.get('operation')==('roll' if kind=='upgrade' else 'restore-current') and x['at']>start['at']);physical=start['roles'][role];assert physical['running'] and physical['image_id']==expected_image
  if kind=='upgrade':assert start['roles_replaced']==['api','coordinator2'] and start['unchanged_roles']['coordinator1']
  ws=[x for x in current if x.get('kind')=='database_witness' and start['at']<=x['at']<end['at']]+[x for x in obs if x.get('kind')=='rolling_preflight' and x.get('origin')=='controller' and x.get('parent_origin')=='agent' and x.get('release')==release and x.get('running') is True and x.get('container_sha256')==physical['container_sha256'] and x.get('image_id')==expected_image and start['at']<=x['at']<end['at']]
  bound={a['workflow_id'] for a in accepted if a['key'].startswith(release+'-')}
  matched=[]
  for w in bound:
   h=hashlib.sha256(w.encode()).hexdigest()
   for x in ws:
    trace=[t for t in x['claim_trace'] if t['workflow_id_sha256']==h and t['coordinator_id']==role and t['id']>start['audit_floor']]
    alive_through_proof=not any(e['action']=='die' and start['at']<=e['at']<=x['at'] for c in events['containers'] if c['container_sha256']==physical['container_sha256'] for e in c['events'])
    if alive_through_proof and any(t['event']=='settle' and any(c['event']=='claim' and c['task']==t['task'] and c['epoch']==t['epoch'] and c['id']<t['id'] for c in trace) for t in trace) and (any(o.get('workflow_id_sha256')==h and o.get('state')=='completed' and start['at']<=o['at']<end['at'] for o in current) or x.get('workflow_states',{}).get(h)=='completed'):matched.append(w)
  assert matched
  return True
 check('rolling_old_coordinator_claims_on_schema4',lambda:rolling_window('upgrade','coordinator1',base_image))
 check('code_rollback_without_database_rollback',lambda:rolling_window('rollback','coordinator2',base_image))
 check('upgrade_once_and_other_rolling_requests_idempotent',lambda:len(ledger['rolling_requests'])>=4 and ledger['rolling_attempts']['stage-v4'+release.removeprefix('recovery-v4')]-ledger.get('rolling_cache_hits',{}).get('stage-v4'+release.removeprefix('recovery-v4'),0)==1 and all(n>=1 for n in ledger['rolling_attempts'].values()))
 def crash():
  gen=ledger['crash_generation'];assert gen['release']==release;rows=[x for x in db['chaos']['tables']['crashes'] if x['generation']==gen['generation']];assert len(rows)==8 and {x['role'] for x in rows}==set(roles)
  for role,h in gen['before_roles'].items():
   e=next(x for x in events['containers'] if x['container_sha256']==h);assert any(d['action']=='die' and str(d['exit_code'])=='42' and gen['at']-1<=d['at']<=gen['at']+15 and any(s['action']=='start' and s['at']>=d['at'] for s in e['events']) for d in e['events'])
  before=[x for x in current if x.get('kind')=='http' and x.get('method')=='GET' and x.get('route')=='/workflows/:id' and x.get('state')=='reconciling' and gen['at']-30<=x['at']<gen['at']]
  assert any(any(y.get('workflow_id_sha256')==x.get('workflow_id_sha256') and y.get('state')=='completed' and y['at']>gen['at'] for y in current) for x in before)
  assert any(x.get('kind')=='cluster_status' and gen['at']-30<=x['at']<gen['at'] and set(x['roles'])==set(gen['before_roles']) and all(v['running'] and v['container_sha256']==gen['before_roles'][n] for n,v in x['roles'].items()) for x in native)
  valid=False
  for observation in before:
   w=next(w for w in workflows.values() if hashlib.sha256(w['id'].encode()).hexdigest()==observation['workflow_id_sha256']);key=hashlib.sha256((w['owner']+'\0'+w['key']).encode()).hexdigest();submitted=[x for x in current if x.get('route')=='/workflows' and x.get('method')=='POST' and x.get('status') in [200,202] and x.get('owner_key_sha256')==key and x['at']<gen['at']]
   if not submitted:continue
   for on in current:
    if on.get('kind')!='fault' or on.get('fault')!='partition-on' or on['at']>=min(x['at'] for x in submitted):continue
    if not any(x['event']=='partition_closed' and x['coordinator_id'] in ['coordinator1','coordinator2'] and on['at']<=x['at']<gen['at'] for x in db['network']['tables']['calls']):continue
    valid=True
  assert valid
  return True
 check('all_eight_actual_main_process_exit42_and_same_workflow_recovery',crash)
 rc=[x for x in current if x.get('kind')=='recovery_call'];snapshots=[x for x in rc if x['operation']=='snapshot' and x['status']==201];restores=[x for x in rc if x['operation']=='restore' and x['status']==201];reconciles=[x for x in rc if x['operation']=='reconcile' and x['status']==200]
 def online_backup():
  holders=probe_witnesses(current,'writer')
  for s in snapshots:
   before=[x for x in holders if x.get('state')=='holding' and x.get('alive') and x['at']<s['at']];after=[x for x in holders if x.get('state')=='holding' and x.get('alive') and x['at']>s['at']]
   if not before or not after:continue
   manifest=s['result']['snapshot'];w=s['snapshot_witness'];assert w['integrity']=='ok' and w['user_version']==4 and w['database_sha256']==manifest['database_sha256'] and w['database_bytes']==manifest['database_bytes']<=8388608
   assert w['online_backup_probe']==before[-1]['before'];assert db['workflows']['tables']['online_backup_probe'][0]['value']>w['online_backup_probe'];actual=call(root,{'op':'snapshot-proof','label':s['label']});assert actual==w;return True
  return False
 check('actual_online_backup_with_live_uncommitted_writer',online_backup)
 def clone_recovery():
  for r in reconciles:
   v=r['result'];c=r.get('effect_witness');assert v['complete'] is True and v['active'] is False and v['unresolved']==0
   if not safe_reconcile_receipt(r):continue
   restore=next(x for x in restores if x['clone_label']==r['clone_label'] and x['at']<r['at']);snap=next(x for x in snapshots if x['label']==restore['label'] and x['at']<restore['at']);mark=snap['result']['snapshot']['admission_highwater'];later=eligible_replay_admissions(accepted,current,release,snap['at'],r.get('at_start',r['at']),mark);assert {'alpha','beta'}<={a['owner'] for a in later}
   clone=call(root,{'op':'clone-inspect','clone_label':r['clone_label']});assert clone['integrity']=='ok' and clone['user_version']==4 and clone['online_backup_probe']==snap['snapshot_witness']['online_backup_probe'];cws={w['id']:w for w in clone['workflows']}
   for a in later:
    wid=a['workflow_id'];assert wid in cws;assert cws[wid]['state']==workflows[wid]['state']=='completed';assert cws[wid]['result']['sha256']==workflows[wid]['result']['sha256'];assert base64.b64decode(cws[wid]['result']['b64'])==canonical({'workflow_id':wid,'tasks':[{'name':t['name'],'sha256':t['sha256']} for t in sorted([s for s in steps if s['workflow_id']==wid],key=lambda s:s['name'])]})
    original=sorted([s for s in steps if s['workflow_id']==wid],key=lambda s:s['name']);adopted=sorted([s for s in clone['steps'] if s['workflow_id']==wid],key=lambda s:s['name']);assert len(original)==len(adopted)
    for old,new in zip(original,adopted):assert all(old[k]==new[k] for k in ['name','request_key','child_id','sha256','state']) and old['artifact']['sha256']==new['artifact']['sha256']
   return True
  return False
 check('inactive_stale_clone_recovers_original_ids_without_new_child_posts_or_effects',clone_recovery)
 def unsafe_controls():
  fixtures={label for x in current if x.get('kind')=='snapshot_fixture' for label in x['labels']}
  for attempt in rc:
   if attempt['operation']!='restore' or attempt['status']!=409 or attempt['result'].get('error')!='control_reconciliation_required' or attempt.get('label') in fixtures:continue
   snapshot=next((x for x in snapshots if x['label']==attempt['label'] and x['at']<attempt.get('at_start',attempt['at'])),None)
   if snapshot is None:continue
   highwater=snapshot['result']['snapshot']['control_highwater'];controls=[x for x in db['admissions']['tables']['controls'] if x['seq']>highwater and x['state']=='applied']
   for w in workflows.values():
    if w['state']!='cancelled':continue
    actions={x['action'] for x in controls if x['workflow_id']==w['id']};h=hashlib.sha256(w['id'].encode()).hexdigest()
    if not {'pause','cancel'}<=actions:continue
    if all(any(x.get('kind')=='http' and x.get('workflow_id_sha256')==h and x.get('method')=='POST' and x.get('route')=='/workflows/:id/'+action and x.get('status')==200 and snapshot['at']<x['at']<attempt['at'] for x in current) for action in ['pause','cancel']):return True
  return False
 check('newer_control_blocks_unsafe_stale_restore',unsafe_controls)
 def storage():
  x=ledger['storage_probes'][release];return x['tmpfs_bytes']==2097152 and x['free_bytes_at_failure']==0 and x['sqlite_error_code'] is not None and x['failure_status'] in [503,507] and isinstance(x['failure_error'],str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',x['failure_error']) is not None and x['exception_escaped'] is False and x['recovered_status'] in [200,202] and x['fixture_only'] is True and x['live_data_modified'] is False
 check('actual_bounded_tmpfs_sqlite_full_and_recovered_same_key_retry',storage)
 def lock_recovery():
  lock_rows=probe_witnesses(current,'lock');starts=[x for x in lock_rows if x.get('kind')=='probe_control' and x.get('action')=='start' and x.get('state')=='holding' and x.get('alive')]
  for start in starts:
   end=next((x for x in lock_rows if x.get('kind')=='probe_control' and x.get('action')=='release' and x['at']>start['at']),None)
   if end is None:continue
   during=[x for x in current if start['at']<=x['at']<=end['at']];failed=[x for x in during if x.get('route')=='/workflows' and x.get('method')=='POST' and x.get('status')==503 and x.get('owner_key_sha256')]
   assert any(x.get('route')=='/healthz' and x.get('status')==200 for x in during) or any(x.get('route')=='/healthz' and x.get('status')==200 and start['at']<=x['at']<=end['at'] for x in obs)
   assert any(x.get('route')=='/readyz' and x.get('status')==503 and start['at']<=x['at']<=end['at'] for x in obs)
   for f in failed:
    retry=next((x for x in current if x.get('route')=='/workflows' and x.get('method')=='POST' and x.get('status') in [200,202] and x.get('owner_key_sha256')==f['owner_key_sha256'] and x.get('payload_sha256')==f['payload_sha256'] and x['at']>end['at']),None)
    if retry and any(hashlib.sha256((a['owner']+'\0'+a['key']).encode()).hexdigest()==f['owner_key_sha256'] and workflows[a['workflow_id']]['state']=='completed' for a in accepted):return True
  return False
 check('real_live_journal_lock_readiness_and_same_key_recovery',lock_recovery)
 def permissions():
  rows=[x for x in current if x.get('kind')=='security_probe'];assert rows
  expected={'foreign_read':404,'foreign_cancel':404,'foreign_artifact':404,'forged_owner':403,'missing':401,'invalid':401,'nonascii':401,'ambiguous':401}
  return any({v['case']:v['http_status'] for v in row['rows']}==expected for row in rows)
 check('actual_bearer_tenant_isolation_and_header_forgery_rejected',permissions)
 def negative_snapshots():
  fixtures=[x for x in current if x.get('kind')=='snapshot_fixture'];assert fixtures
  labels={label for f in fixtures for label in f['labels']};attempts={x.get('label'):x for x in rc if x['operation']=='restore' and x.get('label') in labels};assert labels<=set(attempts)
  for label,x in attempts.items():
   assert (x['status'],x['result'].get('error'))==((404,'not_found') if label.endswith('-bad-owner') else (409,'invalid_snapshot'))
  return True
 check('actual_bad_backup_fixture_restores_fail_closed',negative_snapshots)
 check('recovery_audit_mechanics_only',lambda:all(set(x)=={'id','event','owner_sha256','key_sha256','error_type'} for x in db['recovery_audit']['tables']['events']) and len(db['recovery_audit']['tables']['events'])>0 and not any(w in json.dumps(db['recovery_audit']['tables']['events']) for w in ['PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY','Level10 checkpoint','Bearer ','"alpha"','"beta"']))
 check('actual_native_eight_role_resource_snapshot',lambda:any(x.get('kind')=='resource_snapshot' and len(x['roles'])==8 for x in current))
 check('native_report_complete_and_honestly_scoped',lambda:all((project/n).is_file() for n in ['RESULT.json','HASIL.md','INCIDENT.md']) and all(json.loads((project/'RESULT.json').read_text()).get(k)==v for k,v in {'scope':'isolated_recovery_rolling','completed':True,'production_changed':False,'checkpoint_preserved':True}.items()))
 logs=call(root,{'op':'logs'});check('all_registered_history_logs_scanned_no_canaries',lambda:len(logs['complete_history_scans'])==len(history) and all(x['canary_present'] is False for x in logs['complete_history_scans']))
 code="""import json,sys,subprocess,hashlib
from pathlib import Path
r=json.load(sys.stdin);secrets=json.loads(subprocess.check_output(['docker','exec',r['api'],'cat','/data/access.json']));out=[]
for cid in r['cids']:
 d=json.loads(subprocess.check_output(['docker','inspect',cid]))[0];assert d['Config']['Labels'].get('id.harmonika.level10')==r['lab'];p=subprocess.run(['docker','logs',cid],capture_output=True);raw=p.stdout+p.stderr;out.append({'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'secret_present':any(v.encode() in raw for v in secrets.values())})
print(json.dumps(out))
"""
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 -c '+shlex.quote(code)],input=json.dumps({'api':roles['api']['container_id'],'cids':list(history),'lab':lab}),capture_output=True,text=True,timeout=60);check('actual_fixture_keys_absent_from_all_main_logs',lambda:p.returncode==0 and all(not x['secret_present'] for x in json.loads(p.stdout)))
 result={'phase':'v4','release':release,'passed':all(checks.values()),'checks':checks,'failures':[k for k,v in checks.items() if not v],'errors':errors};Path(str(prefix)+'-result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':
 try:main()
 except Exception as e:
  if '--root' in sys.argv and '--label' in sys.argv:
   target=Path(sys.argv[sys.argv.index('--root')+1])/(sys.argv[sys.argv.index('--label')+1]+'-result.json')
   if not target.exists():target.write_text(json.dumps({'phase':'v4','passed':False,'checks':{},'failures':['grading_interrupted'],'errors':{'grading_interrupted':type(e).__name__}},indent=2))
  print(json.dumps({'grading_interrupted':type(e).__name__}));raise SystemExit(1)
