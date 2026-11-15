"""Typed controller for one owned Level8 cluster; base helpers are hash-staged."""
import base64,fcntl,hashlib,hmac,importlib.util,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('lab_base',ROOT/'base_controller.py');B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)
META=B.META;LAB=B.LAB;LABEL=B.LABEL
NAMES=['sink','api','worker1','worker2','worker3','proxy','coordinator1','coordinator2']
B.FILES={'engine.py','stream_reader.py','storage_tools.py','workflow_engine.py','Dockerfile','server.py','worker.py','entrypoint.py','effect_connector.py','effect_service.py','workflow_adapter.py','coordinator.py','fault_store.py','proxy.py'}
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
 critical=value.get('kind') in ['fault','role_control','cluster_status','resource_snapshot','database_witness','network_witness','running_witness'] or value.get('kind')=='http' and (value.get('paused') is True or value.get('sink_running') is False or value.get('route') in ['/artifacts/:id','/workflow-artifacts/:id'] and value.get('status')==200)
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
  item=payload['workflow'];stamp.update(workflow_id_sha256=hashlib.sha256(str(item.get('id')).encode()).hexdigest(),state=item.get('state'),paused=item.get('paused'),child_count=sum(t.get('child_id') is not None for t in item.get('tasks',[])),task_states={t['name']:t['state'] for t in item.get('tasks',[])},sink_running=bool(inspect_role('sink')['State']['Running']),coordinator_running=any(bool(inspect_role(n)['State']['Running']) for n in ['coordinator1','coordinator2']))
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
 if not re.fullmatch(r'stage-v[23](?:-r[1-3])?',key) or name not in S['releases']:raise ValueError('invalid_activation')
 S.setdefault('activation_attempts',{}).setdefault(key,0);S['activation_attempts'][key]+=1;save()
 if key in S['requests']:
  if S['requests'][key]['release']!=name:raise ValueError('activation_conflict')
  return S['requests'][key]
 for role,cid in sorted(S.get('roles',{}).items(),key=lambda x:0 if x[0].startswith('coordinator') else 1):
  d=B.owned(cid)
  S.setdefault('max_restarts',{})[role]=max(S.get('max_restarts',{}).get(role,0),d['RestartCount'])
  if d['State']['Running']:B.docker('stop','--time','3',cid)
 roles={};image=S['releases'][name]['image_id']
 for role in NAMES:
  mem,cpu,pids=('512m','1','640') if role=='api' else ('192m','0.5','64') if role=='sink' or role.startswith('coordinator') else ('128m','0.5','96') if role=='proxy' else ('128m','0.5','32')
  network_args=['--network',S['network'],'--network-alias',role] if role in ['api','proxy','coordinator1','coordinator2'] else ['--network','none']
  args=['run','-d','--name',LAB+'-'+key+'-'+role,'--label',LABEL+'='+LAB,*network_args,'--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432','--cap-drop','ALL','--security-opt','no-new-privileges','--memory',mem,'--cpus',cpu,'--pids-limit',pids,'--restart','on-failure:3','--mount','type=volume,src='+S['volume']+',dst=/data','-e','LAB_ROLE='+('worker' if role.startswith('worker') else 'coordinator' if role.startswith('coordinator') else role),'-e','WORKER_ID='+role,'-e','LAB_RELEASE='+name,'-e','COORDINATOR_ID='+role,'-e','WORKFLOW_SCHEMA='+('3' if name.startswith('workflow-v3') else '2'),image]
  cid=B.docker(*args);roles[role]=cid;S['containers'].append(cid);S['roles']=dict(roles);S.setdefault('role_history',{})[cid]={'role':role,'release':name,'request_id':key};save()
  if role=='api':
   S['active']=cid;save()
   clear="from pathlib import Path;root=Path('/data/faults');[(root/n).unlink(missing_ok=True) for n in ['child-submit-ack-loss','child-lookup-fail-two','workflow-crash-after-submit','bad-child-artifact','network-partition','release-held']]"
   B.docker('exec',cid,'python','-c',clear)
   B.docker('exec',cid,'python','-c',"from pathlib import Path;[(Path('/data')/n).unlink(missing_ok=True) for n in ['network-partition','release-held']]")
   for _ in range(20):
    if http({'path':'/queue-readyz','origin':'controller'})['http_status']==200:break
    time.sleep(.1)
 S['release']=name;S['active']=roles['api'];save()
 for _ in range(20):
  if http({'path':'/readyz','origin':'controller'})['http_status']==200:break
  time.sleep(.1)
 else:return {'error':'readiness_unavailable','exit_code':70,'release':name}
 S['requests'][key]={'release':name,'image_id':image,'api_container_id':roles['api'],'roles':roles,'at':time.time()};save();return S['requests'][key]
def observe_running(r):
 wid=r.get('workflow_id');owner=r.get('owner','alpha');control=r.get('control')
 if control not in [None,'pause','cancel']:raise ValueError('invalid_control')
 if not isinstance(wid,str) or not re.fullmatch(r'[a-f0-9]{32}',wid) or owner not in ['alpha','beta']:raise ValueError('invalid_workflow')
 code="""import json,sqlite3,time,urllib.request,urllib.error,re,sys
from pathlib import Path
from contextlib import closing
r=json.load(sys.stdin);end=time.monotonic()+10;value={'observed':False,'reason':'sample_window_expired'}
while time.monotonic()<end:
 if not Path('/data/faults/hold-all').exists():value={'observed':False,'reason':'hold_not_active'};break
 with closing(sqlite3.connect('file:/data/workflows.sqlite?mode=ro',uri=True,timeout=.15)) as c:
  c.row_factory=sqlite3.Row;row=c.execute("SELECT s.child_id FROM steps s JOIN workflows w ON w.id=s.workflow_id WHERE w.id=? AND w.owner=? AND s.name='prepare'",(r['id'],r['owner'])).fetchone()
 if row and row['child_id'] and re.fullmatch(r'[a-f0-9]{32}',row['child_id']):
  q=urllib.request.Request('http://127.0.0.1:18081/jobs/'+row['child_id'],headers={'X-Lab-Owner':r['owner']})
  with urllib.request.urlopen(q,timeout=1) as response:j=json.loads(response.read(8192))['job']
  with closing(sqlite3.connect('file:/data/sink.sqlite?mode=ro',uri=True,timeout=.15)) as c:
   authorized=c.execute("SELECT count(*) FROM calls WHERE job_id=? AND kind='authorize' AND fence=?",(j['id'],j['fence'])).fetchone()[0];effects=c.execute('SELECT count(*) FROM effects WHERE job_id=?',(j['id'],)).fetchone()[0]
  sampled=time.time()
  if j['state']=='running' and j['lease_until']>sampled and authorized and effects==0:
   value={'observed':True,'job_id':j['id'],'worker_id':j['worker_id'],'fence':j['fence'],'state':'running','lease_until':j['lease_until'],'sampled_at':sampled,'hold_active':True,'sink_effect_rows':0};break
 time.sleep(.02)
print(json.dumps(value))
"""
 cid=S['roles']['api'];B.owned(cid);p=B.run(['docker','exec','-i',cid,'python','-c',code],input=json.dumps({'id':wid,'owner':owner}));value=json.loads(p.stdout)
 if value.get('observed'):
  worker=value['worker_id']
  if worker not in ['worker1','worker2','worker3']:raise ValueError('invalid_observed_worker')
  d=inspect_role(worker);value.update(worker_running=bool(d['State']['Running']),worker_container_sha256=hashlib.sha256(d['Id'].encode()).hexdigest(),worker_restart_count=d['RestartCount'])
  if not value['worker_running']:value.update(observed=False,reason='worker_not_alive')
 record({'kind':'running_witness','origin':r.get('origin','agent'),'release':S['release'],'workflow_id_sha256':hashlib.sha256(wid.encode()).hexdigest(),'at':time.time(),**value})
 if value.get('observed') and control:
  value['control']=control;value['control_response']=http({'method':'POST','path':'/workflows/'+wid+'/'+control,'owner':owner,'body':{},'origin':r.get('origin','agent')})
 return value

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
 if op in ['workers','sink','coordinators']:
  names=r.get('names',[]) if op in ['workers','coordinators'] else [op];action=r.get('action')
  if not names or len(set(names))!=len(names) or action not in ['stop','start'] or any(n not in (['worker1','worker2','worker3'] if op=='workers' else ['coordinator1','coordinator2'] if op=='coordinators' else ['sink']) for n in names):raise ValueError('invalid_role_operation')
  result={}
  for name in names:
   d=inspect_role(name);cid=d['Id']
   if action=='stop' and d['State']['Running']:B.docker('stop','--time','3',cid)
   if action=='start' and not d['State']['Running']:B.docker('start',cid)
   result[name]=bool(inspect_role(name)['State']['Running'])
  record({'kind':'role_control','action':action,'roles':names,'origin':r.get('origin','agent'),'at':time.time(),'release':S.get('release')});return {'running':result}
 if op=='observe-running':return observe_running(r)
 if op=='proxy-state':
  code="import sqlite3,json;c=sqlite3.connect('file:/data/network.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row;print(json.dumps({'calls':[dict(x) for x in c.execute(\"SELECT * FROM calls ORDER BY id DESC LIMIT 50\")],'faults':[dict(x) for x in c.execute(\"SELECT * FROM faults\")]}))"
  value=json.loads(B.docker('exec',S['roles']['proxy'],'python','-c',code));record({'kind':'network_witness','origin':r.get('origin','agent'),'release':S['release'],'at':time.time(),'calls':value['calls']});return value
 if op=='stats':
  result=[]
  for name,cid in S['roles'].items():
   B.owned(cid);v=json.loads(B.docker('stats','--no-stream','--format','{{json .}}',cid));result.append({'role':name,**{k:v.get(k) for k in ['CPUPerc','MemUsage','PIDs']}})
  record({'kind':'resource_snapshot','origin':r.get('origin','agent'),'release':S['release'],'at':time.time(),'roles':result});return {'roles':result}
 if op=='fault':
  kind=r.get('kind');worker=r.get('worker','worker1')
  if kind in ['hold-after-submit','drop-ack','partial-artifact','partition-on','partition-off','release-held']:
   target=r.get('target','all')
   if target not in ['all','coordinator1','coordinator2']:raise ValueError('invalid_target')
   code="import sqlite3;from pathlib import Path;root=Path('/data');"
   if kind in ['partition-on','partition-off','release-held']:
    name='network-partition' if kind.startswith('partition-') else 'release-held';code+="p=root/"+repr(name)+";"+("p.unlink(missing_ok=True)" if kind=='partition-off' else "p.write_text('armed')")
   else:
    code+="c=sqlite3.connect(root/'network.sqlite');c.execute('INSERT INTO faults VALUES(?,?,?,1)',"+repr((None,kind,target)).replace('None',repr(S['release']),1)+");c.commit();"
    if kind=='hold-after-submit':code+="(root/'release-held').unlink(missing_ok=True)"
   B.docker('exec',S['roles']['proxy'],'python','-c',code);record({'kind':'fault','fault':kind,'target':target,'origin':r.get('origin','agent'),'release':S['release'],'at':time.time()});return {'fault':kind,'target':target,'armed':True}
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
 if op in ['workflow-burst','test-engine']:
  label=r.get('label','native')
  if label not in ['native','examiner']:raise ValueError('invalid_probe_label')
  cid=S['active'];B.owned(cid);phase=r.get('phase','v2')
  if phase not in ['v2','v3']:raise ValueError('invalid_phase')
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
  probe_started=time.time();coordinator_before={n:bool(inspect_role(n)['State']['Running']) for n in ['coordinator1','coordinator2']} if op=='workflow-burst' else {}
  p=B.run(['docker','exec','-i','-e','LEVEL9_LABEL='+label,'-e','HARMONIKA_EXAM_PROJECT=/app','-e','HARMONIKA_EXAM_PHASE=v2','-e','LEVEL8_V1_SHA256='+S.get('v1_snapshot',{}).get('sha256',''),cid,'python','-I','-'],input=(ROOT/probe).read_text())
  if op=='test-engine':return {'exit_code':p.returncode,'output':(p.stdout+p.stderr)[-25000:]}
  try:value=json.loads(p.stdout)
  except ValueError:value={'passed':False,'error_type':'probe_invalid_output'}
  value.update(probe_exit_code=p.returncode,release=S['release'],image_id=S['releases'][S['release']]['image_id'],origin=r.get('origin','agent'),base_label=base_label,attempt_label=label)
  if op=='workflow-burst':
   value.update(started_at=probe_started,finished_at=time.time(),coordinators_running_before=coordinator_before,coordinators_running_after={n:bool(inspect_role(n)['State']['Running']) for n in ['coordinator1','coordinator2']})
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
 if op=='db-audit':
  value=json.loads(B.docker('exec',S['active'],'python','-c','import sqlite3,json,hashlib\nout={}\nfor name in [\'jobs\',\'sink\',\'mechanics\',\'workflows\',\'network\']:\n c=sqlite3.connect(\'file:/data/\'+name+\'.sqlite?mode=ro\',uri=True);c.row_factory=sqlite3.Row\n d={\'user_version\':c.execute(\'PRAGMA user_version\').fetchone()[0],\'integrity\':c.execute(\'PRAGMA integrity_check\').fetchone()[0],\'journal_mode\':c.execute(\'PRAGMA journal_mode\').fetchone()[0],\'tables\':{},\'table_counts\':{}}\n for t in c.execute("SELECT name FROM sqlite_master WHERE type=\'table\' AND name NOT LIKE \'sqlite_%\'").fetchall():\n  quoted=\'"\'+t[0].replace(\'"\',\'""\')+\'"\';total=c.execute(\'SELECT count(*) FROM \'+quoted).fetchone()[0]\n  if total>20000:raise RuntimeError(\'audit_row_budget_exceeded\')\n  d[\'table_counts\'][t[0]]=total\n  d[\'tables\'][t[0]]=[{k:({\'bytes\':len(v),\'sha256\':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v) for k,v in dict(row).items()} for row in c.execute(\'SELECT * FROM \'+quoted)]\n out[name]=d;c.close()\nprint(json.dumps(out))\n'));trace=value.get('workflows',{}).get('tables',{}).get('coordinator_audit',[])
  record({'kind':'database_witness','origin':r.get('origin','agent'),'release':S['release'],'at':time.time(),'claim_trace':[{'id':x['id'],'coordinator_id':x['coordinator_id'],'workflow_id_sha256':hashlib.sha256(x['workflow_id'].encode()).hexdigest(),'task':x['task'],'epoch':x['epoch'],'event':x['event']} for x in trace[-256:]]});return value
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
   scans.append({'role':role['role'],'release':role['release'],'container_sha256':hashlib.sha256(cid.encode()).hexdigest(),'bytes':len(raw.encode()),'sha256':hashlib.sha256(raw.encode()).hexdigest(),'canary_present':any(x in raw for x in ['Level9 checkpoint','PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY'])})
  return {'logs':result,'complete_history_scans':scans}
 if op=='cleanup':
  value=B.main(r);network=json.loads(B.docker('network','inspect',S['network']))[0]
  if not network.get('Internal') or network.get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned_network')
  B.docker('network','rm',S['network']);value['network_removed']=True;return value
 raise ValueError('unknown_operation')
def capture_events():
 if S.get('closed'):return
 raw=B.docker('events','--since',str(S['created_at']),'--until',str(time.time()),'--filter','label='+LABEL+'='+LAB,'--format','{{json .}}')
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
