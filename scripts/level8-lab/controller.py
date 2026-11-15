"""Typed controller for one owned Level8 cluster; base helpers are hash-staged."""
import base64,fcntl,hashlib,hmac,importlib.util,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('lab_base',ROOT/'base_controller.py');B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
META=B.META;LAB=B.LAB;LABEL=B.LABEL
NAMES=['sink','api','worker1','worker2','worker3','coordinator']
B.FILES={'engine.py','stream_reader.py','storage_tools.py','workflow_engine.py','Dockerfile','server.py','worker.py','entrypoint.py','effect_connector.py','effect_service.py','workflow_adapter.py','coordinator.py','fault_store.py'}
def authorize_request(request):
 role=request.get('origin','agent')
 if role not in ['agent','examiner']:raise ValueError('invalid_origin')
 if role=='examiner':
  supplied=request.get('_operator_auth','');body={k:v for k,v in request.items() if k!='_operator_auth'}
  digest=hmac.new((ROOT/'examiner-auth.key').read_bytes(),json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
  if not isinstance(supplied,str) or not hmac.compare_digest(digest,supplied):raise ValueError('invalid_examiner_auth')
 if request.get('op') in ['test-engine','capture-v1'] and role!='examiner':raise ValueError('examiner_operation_only')
def save():B.save()
def record(value):
 critical=value.get('kind') in ['fault','role_control'] or value.get('kind')=='http' and (value.get('paused') is True or value.get('sink_running') is False or value.get('route') in ['/artifacts/:id','/workflow-artifacts/:id'] and value.get('status')==200)
 if critical:
  witnesses=S.setdefault('critical_witnesses',[])
  if len(witnesses)>=4096:raise RuntimeError('witness_budget_exhausted')
  witnesses.append(value)
 S.setdefault('observations',[]).append(value);S['observations']=S['observations'][-512:];save()
def operation_exit_code(request,value):
 return 0 if request.get('op')=='test-engine' else value.get('exit_code',0)
def merge_docker_events(cid,events):
 if cid not in S.get('role_history',{}):raise ValueError('unregistered_event_container')
 archive=S.setdefault('docker_event_archive',{}).setdefault(cid,[])
 for event in events:
  if event.get('action') not in ['start','die','restart'] or type(event.get('at')) is not int or event.get('exit_code') is not None and not isinstance(event.get('exit_code'),str):raise ValueError('invalid_docker_event')
  if event not in archive:
   if len(archive)>=4096:raise RuntimeError('docker_event_archive_budget')
   archive.append(event)
 return sorted(archive,key=lambda x:x['at'])
def inspect_role(name):
 cid=S.get('roles',{}).get(name)
 return B.owned(cid) if cid else None
def http(r):
 path=r.get('path','');method=r.get('method','GET');owner=r.get('owner','alpha');key=r.get('key');cursor=r.get('cursor',0)
 if not re.fullmatch(r'/(?:healthz|readyz|queue-readyz|workflows(?:/[A-Za-z0-9_-]{1,80}(?:/(?:cancel|pause|resume))?)?|workflow-requests/[A-Za-z0-9_-]{1,64}|workflow-artifacts/[A-Za-z0-9_-]{1,80}|workflow-events|jobs(?:/[A-Za-z0-9_-]{1,80}(?:/cancel)?)?|requests/[A-Za-z0-9_-]{1,64}|events|artifacts/[A-Za-z0-9_-]{1,80}|control/(?:drain|resume))',path) or method not in ['GET','POST'] or owner not in ['alpha','beta','anonymous']:raise ValueError('invalid_http_scope')
 if key is not None and (not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key)):raise ValueError('invalid_key')
 if not isinstance(cursor,(int,str)) or not re.fullmatch(r'-?[A-Za-z0-9]{1,20}',str(cursor)):raise ValueError('invalid_cursor')
 headers={'Content-Type':'application/json','Last-Event-ID':str(cursor)}
 if owner!='anonymous':headers['X-Lab-Owner']=owner
 if key is not None:headers['Idempotency-Key']=key
 body=base64.b64encode(json.dumps(r['body'],ensure_ascii=False).encode()).decode() if 'body' in r else None
 if body is not None and len(body)>10000:raise ValueError('too_large')
 cid=S['roles']['api'];B.owned(cid)
 p=B.run(['docker','exec','-i',cid,'python','-c',B.HTTP],input=json.dumps({'path':path,'method':method,'headers':headers,'body_b64':body}))
 value=json.loads(p.stdout) if p.returncode==0 else {'http_status':502,'headers':{},'body_b64':base64.b64encode(b'{"error":"lab_transport_unavailable"}').decode()}
 stamp={'origin':r.get('origin','agent'),'kind':'http','route':re.sub(r'/(?:workflows|workflow-requests|workflow-artifacts|jobs|requests|artifacts)/[^/]+',lambda m:m[0].rsplit('/',1)[0]+'/:id',path),'method':method,'status':value['http_status'],'at':time.time(),'release':S.get('release')}
 try:payload=json.loads(base64.b64decode(value['body_b64']))
 except ValueError:payload=None
 if method=='POST' and path=='/workflows':
  h=hashlib.sha256((owner+'\0'+str(key)).encode()).hexdigest();S.setdefault('submit_attempts',{}).setdefault(h,0);S['submit_attempts'][h]+=1
 if method=='GET' and path.startswith('/workflow-requests/'):
  h=hashlib.sha256((owner+'\0'+path.rsplit('/',1)[1]).encode()).hexdigest();S.setdefault('request_lookups',{}).setdefault(h,0);S['request_lookups'][h]+=1
 if isinstance(payload,dict) and isinstance(payload.get('workflow'),dict):
  item=payload['workflow'];stamp.update(workflow_id_sha256=hashlib.sha256(str(item.get('id')).encode()).hexdigest(),state=item.get('state'),paused=item.get('paused'),child_count=sum(t.get('child_id') is not None for t in item.get('tasks',[])),task_states={t['name']:t['state'] for t in item.get('tasks',[])},sink_running=bool(inspect_role('sink')['State']['Running']),coordinator_running=bool(inspect_role('coordinator')['State']['Running']))
 record(stamp);return value
def status(origin='examiner'):
 if not S.get('roles',{}).get('api'):return {'state':'absent','roles':{n:{'running':False} for n in NAMES}}
 roles={}
 for name in NAMES:
  d=inspect_role(name)
  if not d:roles[name]={'running':False};continue
  h=d['HostConfig'];roles[name]={'container_id':d['Id'],'image_id':d['Image'],'running':d['State']['Running'],'restart_count':d['RestartCount'],'isolation':{'user':d['Config']['User'],'network':h['NetworkMode'],'readonly':h['ReadonlyRootfs'],'memory':h['Memory'],'nano_cpus':h['NanoCpus'],'pids':h['PidsLimit'],'cap_drop':h['CapDrop'],'security_opt':h['SecurityOpt']}}
  S.setdefault('max_restarts',{});S['max_restarts'][name]=max(S['max_restarts'].get(name,0),d['RestartCount'])
 record({'kind':'cluster_status','origin':origin,'at':time.time(),'roles':{n:{'running':v['running'],'restart_count':v.get('restart_count',0),'container_sha256':hashlib.sha256(v.get('container_id','').encode()).hexdigest()} for n,v in roles.items()}})
 return {'release':S.get('release'),'roles':roles,'health':http({'path':'/healthz','origin':'controller'}),'ready':http({'path':'/readyz','origin':'controller'})}
def activate(r):
 name=r['release'];key=r['request_id']
 if not re.fullmatch(r'(?:stage-v[12](?:-r[1-3])?|rollback-v1(?:-r[1-3])?|restore-v2(?:-r[1-3])?)',key) or name not in S['releases']:raise ValueError('invalid_activation')
 S.setdefault('activation_attempts',{}).setdefault(key,0);S['activation_attempts'][key]+=1;save()
 if key in S['requests']:
  if S['requests'][key]['release']!=name:raise ValueError('activation_conflict')
  return S['requests'][key]
 for role,cid in sorted(S.get('roles',{}).items(),key=lambda x:0 if x[0]=='coordinator' else 1):
  d=B.owned(cid)
  S.setdefault('max_restarts',{})[role]=max(S.get('max_restarts',{}).get(role,0),d['RestartCount'])
  if d['State']['Running']:B.docker('stop','--time','3',cid)
 roles={};image=S['releases'][name]['image_id']
 for role in NAMES:
  mem,cpu,pids=('384m','1','384') if role=='api' else ('192m','0.5','64') if role in ['sink','coordinator'] else ('128m','0.5','32')
  args=['run','-d','--name',LAB+'-'+key+'-'+role,'--label',LABEL+'='+LAB,'--network','none','--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432','--cap-drop','ALL','--security-opt','no-new-privileges','--memory',mem,'--cpus',cpu,'--pids-limit',pids,'--restart','on-failure:3','--mount','type=volume,src='+S['volume']+',dst=/data','-e','LAB_ROLE='+('worker' if role.startswith('worker') else role),'-e','WORKER_ID='+role,'-e','LAB_RELEASE='+name,'-e','WORKFLOW_SCHEMA='+('2' if name.startswith('workflow-v2') else '1'),image]
  cid=B.docker(*args);roles[role]=cid;S['containers'].append(cid);S['roles']=dict(roles);S.setdefault('role_history',{})[cid]={'role':role,'release':name,'request_id':key};save()
  if role=='api':
   S['active']=cid;save()
   clear="from pathlib import Path;root=Path('/data/faults');[(root/n).unlink(missing_ok=True) for n in ['child-submit-ack-loss','child-lookup-fail-two','workflow-crash-after-submit','bad-child-artifact']]"
   B.docker('exec',cid,'python','-c',clear)
   for _ in range(20):
    if http({'path':'/readyz','origin':'controller'})['http_status']==200:break
    time.sleep(.1)
 S['release']=name;S['active']=roles['api'];save()
 for _ in range(20):
  if http({'path':'/readyz','origin':'controller'})['http_status']==200:break
  time.sleep(.1)
 else:return {'error':'readiness_unavailable','exit_code':70,'release':name}
 S['requests'][key]={'release':name,'image_id':image,'api_container_id':roles['api'],'roles':roles,'at':time.time()};save();return S['requests'][key]
def operation(r):
 op=r.get('op')
 if op=='ledger':
  fingerprints={}
  for name,release in S['releases'].items():
   folder=ROOT/'contexts'/release['source_sha256'];files={n:base64.b64encode((folder/n).read_bytes()).decode() for n in B.FILES}
   if hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()!=release['source_sha256']:raise RuntimeError('immutable_context_changed')
   fingerprints[name]={n:hashlib.sha256(base64.b64decode(raw)).hexdigest() for n,raw in files.items()}
  return {**S,'release_file_sha256':fingerprints}
 if S.get('closed') and op!='cleanup':raise ValueError('lab_closed')
 if op=='build':return B.main(r)
 if op=='activate':return activate(r)
 if op=='status':return status(r.get('origin','agent'))
 if op=='http':return http(r)
 if op in ['workers','sink','coordinator']:
  names=r.get('names',[]) if op=='workers' else [op];action=r.get('action')
  if not names or len(set(names))!=len(names) or action not in ['stop','start'] or any(n not in ['worker1','worker2','worker3'] for n in names if op=='workers'):raise ValueError('invalid_role_operation')
  result={}
  for name in names:
   d=inspect_role(name);cid=d['Id']
   if action=='stop' and d['State']['Running']:B.docker('stop','--time','3',cid)
   if action=='start' and not d['State']['Running']:B.docker('start',cid)
   result[name]=bool(inspect_role(name)['State']['Running'])
  record({'kind':'role_control','action':action,'roles':names,'origin':r.get('origin','agent'),'at':time.time(),'release':S.get('release')});return {'running':result}
 if op=='fault':
  kind=r.get('kind');worker=r.get('worker','worker1')
  if worker not in ['worker1','worker2','worker3']:raise ValueError('invalid_worker')
  if kind=='drop-next-submit':S['drop_next_submit']=True
  else:
   if kind not in ['hold-all','release-all','child-submit-ack-loss','child-lookup-fail-two','workflow-crash-after-submit','bad-child-artifact']:raise ValueError('invalid_fault')
   target='hold-all' if kind=='release-all' else 'hold-'+worker if kind in ['hold-worker','release-worker'] else kind
   code="from pathlib import Path;p=Path('/data/faults');p.mkdir(exist_ok=True);f=p/"+repr(target)+';'+('f.unlink(missing_ok=True)' if kind.startswith('release') else "f.write_text('armed')")
   B.docker('exec',S['active'],'python','-c',code)
  witness={}
  if kind in ['hold-worker','release-worker']:
   d=inspect_role(worker);witness={'worker_running':bool(d['State']['Running']),'worker_restart_count':d['RestartCount'],'worker_container_sha256':hashlib.sha256(d['Id'].encode()).hexdigest()}
  record({'kind':'fault','fault':kind,'worker':worker,'origin':r.get('origin','agent'),'at':time.time(),**witness});save();return {'fault':kind,'armed':True,**witness}
 if op in ['workflow-burst','migration-drill','test-engine']:
  label=r.get('label','native')
  if label not in ['native','examiner']:raise ValueError('invalid_probe_label')
  cid=S['active'];B.owned(cid);phase=r.get('phase','v2')
  if phase not in ['v1','v2']:raise ValueError('invalid_phase')
  probe={'workflow-burst':'burst_probe.py','migration-drill':'migration_probe.py','test-engine':'independent.py'}[op]
  revision=re.search(r'-r([1-3])$',S.get('release',''));base_label=label
  if op=='workflow-burst':
   label+='-'+S['release']
   if label in S.get('bursts',{}):
    if r.get('origin','agent')=='agent':S['last_agent_burst']=label;save()
    return S['bursts'][label]
  if op=='migration-drill':
   attempts=sum(x.get('base_label')==label for x in S.get('probes',{}).get(op,[]))
   if attempts>=4:raise ValueError('probe_attempt_budget')
   label+='-a'+str(attempts+1)
  p=B.run(['docker','exec','-i','-e','LEVEL8_LABEL='+label,'-e','HARMONIKA_EXAM_PROJECT=/app','-e','HARMONIKA_EXAM_PHASE='+phase,'-e','LEVEL8_V1_SHA256='+S.get('v1_snapshot',{}).get('sha256',''),cid,'python','-I','-'],input=(ROOT/probe).read_text())
  if op=='test-engine':return {'exit_code':p.returncode,'output':(p.stdout+p.stderr)[-25000:]}
  try:value=json.loads(p.stdout)
  except ValueError:value={'passed':False,'error_type':'probe_invalid_output'}
  value.update(probe_exit_code=p.returncode,release=S['release'],image_id=S['releases'][S['release']]['image_id'],origin=r.get('origin','agent'),base_label=base_label,attempt_label=label)
  if op=='workflow-burst':
   S.setdefault('bursts',{})[label]=value
   if r.get('origin','agent')=='agent':S['last_agent_burst']=label
  else:S.setdefault('probes',{}).setdefault(op,[]).append(value)
  save();return value
 if op=='capture-v1':
  if r.get('origin')!='examiner' or not isinstance(r.get('checkpoint_id'),str) or not re.fullmatch(r'[a-f0-9]{32}',r['checkpoint_id']):raise ValueError('examiner_capture_only')
  code="""import sqlite3,json,hashlib,os
from pathlib import Path
p=Path('/data/proofs');p.mkdir(exist_ok=True);dst=p/'workflow-v1.sqlite'
assert not dst.exists()
checkpoint_id="""+repr(r['checkpoint_id'])+"""
src=sqlite3.connect('/data/workflows.sqlite')
assert src.execute('PRAGMA user_version').fetchone()[0]==1
assert src.execute('SELECT state FROM workflows WHERE id=?',(checkpoint_id,)).fetchone()==('completed',)
assert src.execute("SELECT count(*) FROM workflows WHERE state NOT IN ('completed','cancelled')").fetchone()[0]==0
fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
out=sqlite3.connect(dst);src.backup(out);out.execute('PRAGMA journal_mode=DELETE');out.close();src.close()
print(json.dumps(dict(sha256=hashlib.sha256(dst.read_bytes()).hexdigest(),bytes=dst.stat().st_size,schema=1)))
"""
  value=json.loads(B.docker('exec',S['active'],'python','-c',code));value.update(origin='examiner',authenticated=True,at=time.time(),release=S['release'],image_id=S['releases'][S['release']]['image_id'],checkpoint_id_sha256=hashlib.sha256(r['checkpoint_id'].encode()).hexdigest());S['v1_snapshot']=value;save();return value
 if op=='db-audit':return B.main(r)
 if op=='events':
  return {'containers':[{**role,'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'events':S.get('docker_event_archive',{}).get(cid,[])} for cid,role in S.get('role_history',{}).items()]}
 if op=='logs':
  result={}
  for name,cid in S.get('roles',{}).items():
   B.owned(cid);p=B.run(['docker','logs','--tail','15',cid]);result[name]=(p.stdout+p.stderr)[-3000:]
  scans=[]
  for cid,role in S.get('role_history',{}).items():
   B.owned(cid);p=B.run(['docker','logs',cid]);raw=p.stdout+p.stderr
   if p.returncode:raise RuntimeError('log_scan_failed')
   scans.append({'role':role['role'],'release':role['release'],'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'bytes':len(raw.encode()),'sha256':hashlib.sha256(raw.encode()).hexdigest(),'canary_present':any(x in raw for x in ['Level8 checkpoint','PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY'])})
  return {'logs':result,'complete_history_scans':scans}
 if op=='cleanup':return B.main(r)
 raise ValueError('unknown_operation')
def capture_events():
 if S.get('closed'):return
 raw=B.docker('events','--since',str(S['created_at']),'--until',str(int(time.time())+1),'--filter','label='+LABEL+'='+LAB,'--format','{{json .}}')
 for line in raw.splitlines():
  e=json.loads(line);cid=e.get('Actor',{}).get('ID',e.get('id'));action=e.get('Action')
  if cid in S.get('role_history',{}) and action in ['start','die','restart']:
   B.owned(cid);merge_docker_events(cid,[{'action':action,'at':e['time'],'exit_code':e.get('Actor',{}).get('Attributes',{}).get('exitCode')}])
 save()
if __name__=='__main__':
 with (ROOT/'control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);S=json.loads((ROOT/'ledger.json').read_text());B.S=S
  try:
   request=json.load(sys.stdin);authorize_request(request);capture_events();value=operation(request);capture_events();print(json.dumps(value),flush=True)
   code=operation_exit_code(request,value)
   if code:raise SystemExit(code)
  except (ValueError,KeyError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'error':'lab_operation_rejected'}));raise SystemExit(2)
