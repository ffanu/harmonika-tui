"""Typed controller for one owned Level7 cluster; base helpers are hash-staged."""
import base64,fcntl,hashlib,importlib.util,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('lab_base',ROOT/'base_controller.py');B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
META=B.META;LAB=B.LAB;LABEL=B.LABEL
NAMES=['sink','api','worker1','worker2','worker3']
B.FILES={'engine.py','stream_reader.py','storage_tools.py','Dockerfile','server.py','worker.py','entrypoint.py','effect_connector.py','effect_service.py'}
def save():B.save()
def record(value):
 critical=value.get('kind') in ['fault','role_control'] or value.get('kind')=='http' and (value.get('sink_running') is False or value.get('route')=='/artifacts/:id' and value.get('status')==200)
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
 if not re.fullmatch(r'/(?:healthz|readyz|jobs(?:/[A-Za-z0-9_-]{1,80}(?:/cancel)?)?|requests/[A-Za-z0-9_-]{1,64}|events|artifacts/[A-Za-z0-9_-]{1,80}|control/(?:drain|resume))',path) or method not in ['GET','POST'] or owner not in ['alpha','beta','anonymous']:raise ValueError('invalid_http_scope')
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
 stamp={'origin':r.get('origin','agent'),'kind':'http','route':re.sub(r'/(?:jobs|requests|artifacts)/[^/]+',lambda m:m[0].rsplit('/',1)[0]+'/:id',path),'method':method,'status':value['http_status'],'at':time.time(),'release':S.get('release')}
 try:payload=json.loads(base64.b64decode(value['body_b64']))
 except ValueError:payload=None
 if method=='POST' and path=='/jobs':
  h=hashlib.sha256((owner+'\0'+str(key)).encode()).hexdigest();S.setdefault('submit_attempts',{}).setdefault(h,0);S['submit_attempts'][h]+=1
  if S.get('drop_next_submit',False) and value['http_status'] in [200,202]:
   S.pop('drop_next_submit')
   S.setdefault('dropped_submit_receipts',[]).append({'request_sha256':h,'job_id_sha256':hashlib.sha256(payload['job']['id'].encode()).hexdigest(),'origin':r.get('origin','agent')});save();record(stamp);return {'error':'ACK_LOST','outcome':'unknown','exit_code':75}
 if method=='GET' and path.startswith('/requests/'):
  h=hashlib.sha256((owner+'\0'+path.rsplit('/',1)[1]).encode()).hexdigest();S.setdefault('request_lookups',{}).setdefault(h,0);S['request_lookups'][h]+=1
 if isinstance(payload,dict) and isinstance(payload.get('job'),dict):
  job=payload['job'];state=job.get('state')
  if state in ['queued','running','reconciling','cancel_requested','completed','cancelled','failed']:
   stamp.update(job_id_sha256=hashlib.sha256(str(job.get('id')).encode()).hexdigest(),state=state,fence=job.get('fence'),worker_id=job.get('worker_id'),sink_running=bool(inspect_role('sink')['State']['Running']))
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
 for role,cid in S.get('roles',{}).items():
  d=B.owned(cid)
  S.setdefault('max_restarts',{})[role]=max(S.get('max_restarts',{}).get(role,0),d['RestartCount'])
  if d['State']['Running']:B.docker('stop','--time','3',cid)
 roles={};image=S['releases'][name]['image_id']
 for role in NAMES:
  mem,cpu,pids=('384m','1','384') if role=='api' else ('192m','0.5','64') if role=='sink' else ('128m','0.5','32')
  args=['run','-d','--name',LAB+'-'+key+'-'+role,'--label',LABEL+'='+LAB,'--network','none','--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432','--cap-drop','ALL','--security-opt','no-new-privileges','--memory',mem,'--cpus',cpu,'--pids-limit',pids,'--restart','on-failure:3','--mount','type=volume,src='+S['volume']+',dst=/data','-e','LAB_ROLE='+('worker' if role.startswith('worker') else role),'-e','WORKER_ID='+role,image]
  cid=B.docker(*args);roles[role]=cid;S['containers'].append(cid);S['roles']=dict(roles);S.setdefault('role_history',{})[cid]={'role':role,'release':name,'request_id':key};save()
  if role=='api':
   S['active']=cid;save()
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
 if op in ['workers','sink']:
  names=r.get('names',[]) if op=='workers' else ['sink'];action=r.get('action')
  if not names or len(set(names))!=len(names) or action not in ['stop','start'] or any(n not in ['worker1','worker2','worker3'] for n in names if op=='workers'):raise ValueError('invalid_role_operation')
  result={}
  for name in names:
   d=inspect_role(name);cid=d['Id']
   if action=='stop' and d['State']['Running']:B.docker('stop','--time','3',cid)
   if action=='start' and not d['State']['Running']:B.docker('start',cid)
   result[name]=bool(inspect_role(name)['State']['Running'])
  record({'kind':'role_control','action':action,'roles':names,'origin':r.get('origin','agent'),'at':time.time()});return {'running':result}
 if op=='fault':
  kind=r.get('kind');worker=r.get('worker','worker1')
  if worker not in ['worker1','worker2','worker3']:raise ValueError('invalid_worker')
  if kind=='drop-next-submit':S['drop_next_submit']=True
  else:
   if kind not in ['hold-all','release-all','hold-worker','release-worker','unknown-ack','crash-after-effect','lookup-fail-two','bad-receipt']:raise ValueError('invalid_fault')
   target='hold-all' if kind=='release-all' else 'hold-'+worker if kind in ['hold-worker','release-worker'] else kind
   code="from pathlib import Path;p=Path('/data/faults');p.mkdir(exist_ok=True);f=p/"+repr(target)+';'+('f.unlink(missing_ok=True)' if kind.startswith('release') else "f.write_text('armed')")
   B.docker('exec',S['active'],'python','-c',code)
  witness={}
  if kind in ['hold-worker','release-worker']:
   d=inspect_role(worker);witness={'worker_running':bool(d['State']['Running']),'worker_restart_count':d['RestartCount'],'worker_container_sha256':hashlib.sha256(d['Id'].encode()).hexdigest()}
  record({'kind':'fault','fault':kind,'worker':worker,'origin':r.get('origin','agent'),'at':time.time(),**witness});save();return {'fault':kind,'armed':True,**witness}
 if op in ['burst','stream','backup-drill','test-engine']:
  probe={'burst':'burst_probe.py','stream':'stream_probe.py','backup-drill':'backup_probe.py','test-engine':'independent.py'}[op]
  label=r.get('label','native')
  if label not in ['native','examiner','examiner-r1','examiner-r2']:raise ValueError('invalid_probe_label')
  if op=='burst' and label=='native':
   revision=re.search(r'-r([1-3])$',S.get('release',''))
   if revision:label='native-r'+revision.group(1)
  if op=='burst' and label in S.get('bursts',{}):return S['bursts'][label]
  if op in ['stream','backup-drill']:
   attempts=sum(x.get('base_label')==label for x in S.get('probes',{}).get(op,[]))
   if attempts>=4:raise ValueError('probe_attempt_budget')
   base_label=label;label=label+'-a'+str(attempts+1)
  cid=S['active'];B.owned(cid);phase=r.get('phase','v2')
  if phase not in ['v1','v2']:raise ValueError('invalid_phase')
  p=B.run(['docker','exec','-i','-e','LEVEL7_LABEL='+label,'-e','HARMONIKA_EXAM_PROJECT=/app','-e','HARMONIKA_EXAM_PHASE='+phase,cid,'python','-I','-'],input=(ROOT/probe).read_text())
  if op=='test-engine':return {'exit_code':p.returncode,'output':(p.stdout+p.stderr)[-20000:]}
  try:value=json.loads(p.stdout)
  except ValueError:value={'passed':False,'error_type':'probe_invalid_output'}
  value['probe_exit_code']=p.returncode
  if op=='burst':
   value.update(release=S['release'],image_id=S['releases'][S['release']]['image_id']);S.setdefault('bursts',{})[label]=value
   if r.get('origin','agent')=='agent':S['last_agent_burst']=label
  else:S.setdefault('probes',{}).setdefault(op,[]).append({'origin':r.get('origin','agent'),'base_label':base_label,'attempt_label':label,'release':S['release'],'image_id':S['releases'][S['release']]['image_id'],**value});S['probes'][op]=S['probes'][op][-12:]
  save();return value
 if op=='db-audit':return B.main(r)
 if op=='events':
  result=[]
  for cid,role in S.get('role_history',{}).items():
   B.owned(cid);raw=B.docker('events','--since',str(S['created_at']),'--until',str(int(time.time())+1),'--filter','container='+cid,'--format','{{json .}}');events=[]
   for line in raw.splitlines():
    e=json.loads(line)
    if e.get('Action') in ['start','die','restart']:events.append({'action':e['Action'],'at':e.get('time'),'exit_code':e.get('Actor',{}).get('Attributes',{}).get('exitCode')})
   result.append({**role,'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'events':merge_docker_events(cid,events)})
  save();return {'containers':result,'archive_provenance':S.get('docker_event_archive_provenance',[])}
 if op=='logs':
  result={}
  for name,cid in S.get('roles',{}).items():
   B.owned(cid);p=B.run(['docker','logs','--tail','15',cid]);result[name]=(p.stdout+p.stderr)[-3000:]
  scans=[]
  for cid,role in S.get('role_history',{}).items():
   B.owned(cid);p=B.run(['docker','logs',cid]);raw=p.stdout+p.stderr
   if p.returncode:raise RuntimeError('log_scan_failed')
   scans.append({'role':role['role'],'release':role['release'],'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'bytes':len(raw.encode()),'sha256':hashlib.sha256(raw.encode()).hexdigest(),'canary_present':any(x in raw for x in ['Level7 checkpoint','burst fixture','PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY'])})
  return {'logs':result,'complete_history_scans':scans}
 if op=='cleanup':return B.main(r)
 raise ValueError('unknown_operation')
if __name__=='__main__':
 with (ROOT/'control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);S=json.loads((ROOT/'ledger.json').read_text());B.S=S
  try:
   request=json.load(sys.stdin);value=operation(request);print(json.dumps(value),flush=True)
   code=operation_exit_code(request,value)
   if code:raise SystemExit(code)
  except (ValueError,KeyError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'error':'lab_operation_rejected'}));raise SystemExit(2)
