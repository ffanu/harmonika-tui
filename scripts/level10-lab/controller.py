"""Typed Level10 controls for registered objects only; caller cannot select host."""
import base64,fcntl,hashlib,importlib.util,json,os,re,secrets,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('cluster',ROOT/'cluster_controller.py');C=importlib.util.module_from_spec(spec);spec.loader.exec_module(C)
B=C.B;META=C.META;LAB=C.LAB;LABEL=C.LABEL;C.B.FILES=set(META['build_files'])
_original_http=B.HTTP
B.HTTP=_original_http.replace("r=json.load(sys.stdin)","r=json.load(sys.stdin)\nfrom fixture_auth import headers as fixture_headers\nr['headers'].update(fixture_headers(r['headers'].get('X-Lab-Owner')))")
assert B.HTTP!=_original_http
original_record=C.record
def record(v):
 if v.get('kind') in ['rolling','rolling_preflight','chaos_generation','probe_control','probe_state','recovery_call','clone_witness','snapshot_fixture','security_probe','storage_probe']:
  rows=C.S.setdefault('critical_witnesses',[])
  if len(rows)>=4096:raise RuntimeError('witness_budget_exhausted')
  rows.append(v)
 original_record(v)
C.record=record

def role_container(role,image,request,schema):
 # Immutable runtime profile; no caller-supplied Docker arguments or paths.
 old=C.inspect_role(role);memory,cpu,pids=('512m','1','640') if role=='api' else ('192m','0.5','64')
 args=['run','-d','--name',LAB+'-'+request+'-'+role,'--label',LABEL+'='+LAB,'--network',C.S['network'],'--network-alias',role,'--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432','--cap-drop','ALL','--security-opt','no-new-privileges','--memory',memory,'--cpus',cpu,'--pids-limit',pids,'--restart','on-failure:3','--mount','type=volume,src='+C.S['volume']+',dst=/data','-e','LAB_ROLE='+('api' if role=='api' else 'coordinator'),'-e','WORKER_ID='+role,'-e','COORDINATOR_ID='+role,'-e','WORKFLOW_SCHEMA='+str(schema),'-e','LAB_RELEASE='+request]
 if role=='api':args+=['--tmpfs','/faultdisk:rw,noexec,nosuid,size=2097152,uid=10001,gid=10001']
 cid=B.docker(*args,image);C.S['containers'].append(cid);C.S['roles'][role]=cid;C.S.setdefault('role_history',{})[cid]={'role':role,'release':request,'image_id':image,'schema':schema};C.S['max_restarts'][role]=0
 if role=='api':C.S['active']=cid
 C.save();return cid

def rolling(r):
 op=r['op'];release=r.get('release',C.S.get('release'));request=r.get('request_id')
 expected={'upgrade':r'stage-v4(?:-r[1-3])?','roll':r'roll-v4(?:-r[1-3])?','rollback':r'rollback-v4(?:-r[1-3])?','restore-current':r'return-v4(?:-r[1-3])?','legacy-first':r'prepare-v4-r[1-3]'}[op]
 if not isinstance(request,str) or not re.fullmatch(expected,request) or release not in C.S['releases'] or not release.startswith('recovery-v4'):raise ValueError('invalid_roll_request')
 attempts=C.S.setdefault('rolling_attempts',{});attempts[request]=attempts.get(request,0)+1;C.save()
 expected_request={'upgrade':'stage-','roll':'roll-','rollback':'rollback-','restore-current':'return-','legacy-first':'prepare-'}[op]+release.removeprefix('recovery-')
 if request!=expected_request:raise ValueError('rolling_request_release_mismatch')
 if request in C.S.setdefault('rolling_requests',{}):
  cached=C.S['rolling_requests'][request]
  if cached['release']!=release or cached['operation']!=op:raise ValueError('rolling_request_conflict')
  hits=C.S.setdefault('rolling_cache_hits',{});hits[request]=hits.get(request,0)+1;C.save();return cached
 before_db=C.operation({'op':'db-audit','origin':'controller'});before_trace=before_db['workflows']['tables']['coordinator_audit'];audit_floor=max((x['id'] for x in before_trace),default=0)
 if op in ['roll','restore-current']:
  target='coordinator1' if op=='roll' else 'coordinator2';d=C.inspect_role(target);start_key=('stage-' if op=='roll' else 'rollback-')+release.removeprefix('recovery-');floor=C.S['rolling_requests'][start_key]['audit_floor'];bound_trace=[x for x in before_trace if x['coordinator_id']==target and x['id']>floor]
  record({'kind':'rolling_preflight','origin':'controller','parent_origin':r.get('origin','agent'),'operation':op,'release':release,'at':time.time(),'container_sha256':hashlib.sha256(d['Id'].encode()).hexdigest(),'image_id':d['Image'],'running':bool(d['State']['Running']),'claim_trace':[{'id':x['id'],'coordinator_id':x['coordinator_id'],'workflow_id_sha256':hashlib.sha256(x['workflow_id'].encode()).hexdigest(),'task':x['task'],'epoch':x['epoch'],'event':x['event']} for x in bound_trace],'workflow_states':{hashlib.sha256(x['id'].encode()).hexdigest():x['state'] for x in before_db['workflows']['tables']['workflows']}})
 before={n:C.inspect_role(n)['Id'] for n in C.NAMES};image=C.S['releases'][release]['image_id'];roles=['api','coordinator2'] if op=='upgrade' else ['coordinator1'] if op in ['roll','legacy-first'] else ['coordinator2']
 if op in ['rollback','legacy-first']:
  # Only the specifically reviewed additive schema4 is compatible with old code.
  code="import sqlite3,json;c=sqlite3.connect('file:/data/workflows.sqlite?mode=ro',uri=True);assert c.execute('PRAGMA user_version').fetchone()[0]==4;assert any(r[1]=='retention_class' and r[4]==\"'standard'\" for r in c.execute('PRAGMA table_info(workflows)'));print('compatible')"
  assert B.docker('exec',C.S['active'],'python','-c',code)=='compatible';image=C.S['releases']['recovery-v3']['image_id']
 for role in roles:
  d=C.inspect_role(role)
  if d['State']['Running']:B.docker('stop','--time','3',d['Id'])
  role_container(role,image,request,3 if op in ['rollback','legacy-first'] else 4)
 if op=='upgrade':C.S['release']=release;C.save()
 result={'operation':op,'release':release,'request':request,'roles_replaced':roles,'unchanged_roles':{n:before[n]==C.S['roles'][n] for n in C.NAMES if n not in roles},'database_restore_performed':False,'audit_floor':audit_floor}
 C.S['rolling_requests'][request]=result;C.save();record({'kind':'rolling','origin':r.get('origin','agent'),'at':time.time(),**result,'roles':{n:{'container_sha256':hashlib.sha256(v['Id'].encode()).hexdigest(),'running':v['State']['Running'],'image_id':v['Image']} for n in C.NAMES for v in [C.inspect_role(n)]}});return result

def probe(r):
 kind=r['op'];action=r.get('action');entry=C.S.setdefault('holders',{}).setdefault(C.S['release']+'-'+kind,{})
 if action=='start':
  if entry:raise ValueError('probe_already_spent')
  token=secrets.token_hex(16);entry.update(token=token,release=C.S['release']);C.save();B.docker('exec','-d',C.S['active'],'python','/app/holder.py',kind,token)
 elif action=='release':
  if not entry:raise ValueError('probe_not_started')
  B.docker('exec',C.S['active'],'python','-c',"from pathlib import Path;Path('/data/probes/"+entry['token']+".release').touch()")
 elif action!='state':raise ValueError('invalid_probe_action')
 code="""import json,sys,time
from pathlib import Path
p=Path('/data/probes/'+sys.argv[1]+'.json');out={'state':'starting','alive':False}
for _ in range(60):
 if p.exists():
  out=json.loads(p.read_text());proc=Path('/proc')/str(out['pid'])/'stat';out['alive']=proc.exists() and proc.read_text().split()[21]==out['start_ticks'];break
 time.sleep(.025)
print(json.dumps({k:v for k,v in out.items() if k not in ['pid','start_ticks']}))
"""
 value=json.loads(B.docker('exec',C.S['active'],'python','-c',code,entry.get('token','0'*32)));record({**value,'kind':'probe_control' if action!='state' else 'probe_state','origin':r.get('origin','agent'),'probe':kind,'action':action,'release':C.S['release'],'at':time.time()});return value

def counters():
 code="import sqlite3,json;out={};a=sqlite3.connect('file:/data/mechanics.sqlite?mode=ro',uri=True);b=sqlite3.connect('file:/data/sink.sqlite?mode=ro',uri=True);out['child_posts']=a.execute(\"SELECT count(*) FROM calls WHERE kind='child_submit'\").fetchone()[0];out['child_lookups']=a.execute(\"SELECT count(*) FROM calls WHERE kind='child_lookup'\").fetchone()[0];out['effects']=b.execute('SELECT count(*) FROM effects').fetchone()[0];print(json.dumps(out))"
 return json.loads(B.docker('exec',C.S['active'],'python','-c',code))

def snapshot_witness(label):
 code="""import json,sqlite3,sys,hashlib,stat
from pathlib import Path
label=sys.argv[1];root=Path('/data/snapshots');manifest=root/(label+'.json');assert manifest.is_file() and not manifest.is_symlink();m=json.loads(manifest.read_text());name=m['database_file'];assert isinstance(name,str) and Path(name).name==name and name not in ['.','..'];p=root/name;assert p.is_file() and not p.is_symlink() and p.resolve().parent==root.resolve();assert p.stat().st_size<=8388608
c=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);has=c.execute("SELECT count(*) FROM sqlite_master WHERE name='online_backup_probe'").fetchone()[0];probe=c.execute('SELECT value FROM online_backup_probe WHERE id=1').fetchone()[0] if has else None;v={'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'database_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'database_bytes':p.stat().st_size,'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'user_version':c.execute('PRAGMA user_version').fetchone()[0],'online_backup_probe':probe,'workflow_rows':c.execute('SELECT count(*) FROM workflows').fetchone()[0]};print(json.dumps(v))
"""
 return json.loads(B.docker('exec',C.S['active'],'python','-c',code,label))

def recovery(r):
 operation=r.get('operation')
 if operation not in ['snapshot','restore','reconcile','health']:raise ValueError('invalid_recovery_operation')
 request={k:r[k] for k in ['owner','label','clone_label'] if k in r};request['operation']=operation
 if any(not isinstance(v,str) or len(v)>256 for v in request.values()):raise ValueError('invalid_recovery_argument')
 at_start=time.time();before=counters() if operation=='reconcile' else None
 p=B.run(['docker','exec','-i',C.S['active'],'python','/app/recovery_rpc.py'],input=json.dumps(request))
 if p.returncode:
  value={'http_status':502,'body':{'error':'recovery_runtime_failed'}}
  record({'kind':'recovery_call','origin':r.get('origin','agent'),'operation':operation,'at_start':at_start,'label':r.get('label'),'clone_label':r.get('clone_label'),'owner':r.get('owner','alpha'),'status':502,'release':C.S['release'],'at':time.time(),'result':value['body']})
  return value
 value=json.loads(p.stdout)
 if operation=='reconcile':value['effect_witness']={'before':before,'after':counters()}
 if operation=='snapshot' and value['http_status']==201 and re.fullmatch(r'[a-z][a-z0-9-]{0,31}',r.get('label','')):value['snapshot_witness']=snapshot_witness(r['label'])
 record({'kind':'recovery_call','origin':r.get('origin','agent'),'operation':operation,'at_start':at_start,'label':r.get('label'),'clone_label':r.get('clone_label'),'owner':r.get('owner','alpha'),'status':value['http_status'],'release':C.S['release'],'at':time.time(),'result':value['body'],'effect_witness':value.get('effect_witness'),'snapshot_witness':value.get('snapshot_witness')});return value

def clone_inspect(r):
 label=r.get('clone_label')
 if not isinstance(label,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',label):raise ValueError('invalid_clone_label')
 code="""import base64,json,sqlite3,sys,hashlib
from pathlib import Path
p=Path('/data/restores')/sys.argv[1]/'workflows.sqlite';assert not p.is_symlink() and p.is_file();c=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;v={'user_version':c.execute('PRAGMA user_version').fetchone()[0],'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'workflow_db_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'online_backup_probe':c.execute('SELECT value FROM online_backup_probe WHERE id=1').fetchone()[0] if c.execute("SELECT count(*) FROM sqlite_master WHERE name='online_backup_probe'").fetchone()[0] else None,'workflows':[],'steps':[]}
for name in ['workflows','steps']:
 for row in c.execute('SELECT * FROM '+name):v[name].append({k:({'bytes':len(x),'sha256':hashlib.sha256(x).hexdigest(),'b64':base64.b64encode(x).decode()} if isinstance(x,bytes) else x) for k,x in dict(row).items()})
print(json.dumps(v))
"""
 value=json.loads(B.docker('exec',C.S['active'],'python','-c',code,label));record({'kind':'clone_witness','origin':r.get('origin','agent'),'clone_label':label,'at':time.time(),'release':C.S['release'],'user_version':value['user_version'],'integrity':value['integrity'],'workflow_ids_sha256':[hashlib.sha256(x['id'].encode()).hexdigest() for x in value['workflows']]});return value

def snapshot_fixtures(r):
 # Fixed bad copies of a real snapshot; never mutate the valid snapshot/live DB.
 label=r.get('label')
 if not isinstance(label,str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',label):raise ValueError('invalid_snapshot_label')
 code="""import hashlib,json,os,sys,shutil
from pathlib import Path
root=Path('/data');sn=root/'snapshots';p=sn/(sys.argv[1]+'.json');manifest=json.loads(p.read_text());assert manifest['label']==sys.argv[1];source=sn/manifest['database_file'];assert source.is_file() and not source.is_symlink();out=[]
for kind in ['bad-hash','bad-path','bad-owner','bad-schema','bad-link']:
 label=sys.argv[1]+'-'+kind;assert len(label)<=32;m={**manifest,'label':label};dst=sn/(label+'.json');assert not dst.exists()
 if kind=='bad-hash':m['database_sha256']='0'*64
 if kind=='bad-path':m['database_file']='../../fixtures/sentinel'
 if kind=='bad-owner':m['owner']='beta' if m['owner']=='alpha' else 'alpha'
 if kind=='bad-schema':m['schema']=999
 if kind=='bad-link':
  target=sn/(label+'.sqlite');assert not target.exists();os.symlink(source.name,target);m['database_file']=target.name
 dst.write_text(json.dumps(m));out.append(label)
fixtures=root/'fixtures';fixtures.mkdir(exist_ok=True);sentinel=fixtures/'sentinel';sentinel.write_bytes(b'PRIVATE_ERROR_CANARY');print(json.dumps({'labels':out,'sentinel_sha256':hashlib.sha256(sentinel.read_bytes()).hexdigest()}))
"""
 value=json.loads(B.docker('exec',C.S['active'],'python','-c',code,label));record({'kind':'snapshot_fixture','origin':r.get('origin','agent'),'at':time.time(),'release':C.S['release'],**value});return value

def security_probe(r):
 wid=r.get('workflow_id')
 if not isinstance(wid,str) or not re.fullmatch(r'[a-f0-9]{32}',wid):raise ValueError('invalid_workflow_id')
 code="""import json,http.client,sys
import fixture_auth
wid=sys.argv[1];out=[]
for name,headers,path,method in [('foreign_read',fixture_auth.headers('beta'),'/workflows/'+wid,'GET'),('foreign_cancel',fixture_auth.headers('beta'),'/workflows/'+wid+'/cancel','POST'),('foreign_artifact',fixture_auth.headers('beta'),'/workflow-artifacts/'+wid,'GET'),('forged_owner',{**fixture_auth.headers('beta'),'X-Lab-Owner':'alpha'},'/workflows/'+wid,'GET'),('missing',{},'/workflows/'+wid,'GET'),('invalid',{'Authorization':'Bearer synthetic-invalid'},'/workflows/'+wid,'GET'),('nonascii',{'Authorization':'Bearer caf\u00e9'},'/workflows/'+wid,'GET'),('ambiguous',None,'/workflows/'+wid,'GET')]:
 c=http.client.HTTPConnection('127.0.0.1',18081,timeout=3)
 if headers is None:
  c.putrequest(method,path)
  for token in [fixture_auth.headers('alpha')['Authorization'],fixture_auth.headers('beta')['Authorization']]:c.putheader('Authorization',token)
  c.endheaders()
 else:c.request(method,path,body=b'{}' if method=='POST' else None,headers=headers)
 try:
  response=c.getresponse();raw=response.read(8192);out.append({'case':name,'http_status':response.status,'bytes':len(raw)})
 except (OSError,http.client.HTTPException):out.append({'case':name,'http_status':0,'bytes':0})
 finally:c.close()
print(json.dumps({'rows':out}))
"""
 value=json.loads(B.docker('exec',C.S['active'],'python','-c',code,wid));record({'kind':'security_probe','origin':r.get('origin','agent'),'at':time.time(),'release':C.S['release'],'workflow_id_sha256':hashlib.sha256(wid.encode()).hexdigest(),**value});return value

def operation(r):
 op=r.get('op')
 if op=='activate' and (r.get('origin')!='examiner' or r.get('release')!='recovery-v3'):raise ValueError('full_activation_examiner_only')
 if op in ['upgrade','roll','rollback','restore-current','legacy-first']:return rolling(r)
 if op in ['writer','lock']:return probe(r)
 if op=='recovery':return recovery(r)
 if op=='snapshot-proof':
  if r.get('origin')!='examiner':raise ValueError('examiner_only')
  return snapshot_witness(r['label'])
 if op=='clone-inspect':return clone_inspect(r)
 if op=='snapshot-fixtures':return snapshot_fixtures(r)
 if op=='security-probe':return security_probe(r)
 if op=='storage-probe':
  label=C.S['release'];spent=C.S.setdefault('storage_probes',{})
  if label in spent:return spent[label]
  p=B.run(['docker','exec',C.S['active'],'python','/app/storage_probe.py']);value=json.loads(p.stdout) if p.returncode==0 else {'error':'protected_storage_probe_failed','probe_exit_code':p.returncode};spent[label]=value;C.save();record({'kind':'storage_probe','origin':r.get('origin','agent'),'release':label,'at':time.time(),**value});return value
 if op=='crash-all':
  if C.S.get('crash_generation',{}).get('release')==C.S['release']:return C.S['crash_generation']
  generation=secrets.token_hex(16);v={'generation':generation,'release':C.S['release'],'before_roles':{n:hashlib.sha256(C.inspect_role(n)['Id'].encode()).hexdigest() for n in C.NAMES},'at':time.time()};C.S['crash_generation']=v;C.save();record({'kind':'chaos_generation','origin':r.get('origin','agent'),**v})
  # Fixed private volume marker; background main-process guards perform exit42.
  code="from pathlib import Path;import sys,os;p=Path('/data/crash-generation.pending');p.write_text(sys.stdin.read());os.replace(p,'/data/crash-generation.json')"
  p=B.run(['docker','exec','-i',C.S['active'],'python','-c',code],input=json.dumps({'generation':generation}));return {**v,'marker_write_ack':p.returncode==0,'outcome':'reconcile_registered_roles'}
 return C.operation(r)

if __name__=='__main__':
 with (ROOT/'control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);C.S=json.loads((ROOT/'ledger.json').read_text());B.S=C.S
  try:
   request=json.load(sys.stdin);C.authorize_request(request);C.capture_events();value=operation(request);C.capture_events();print(json.dumps(value),flush=True)
   code=C.operation_exit_code(request,value)
   if code:raise SystemExit(code)
  except (ValueError,KeyError,RuntimeError,subprocess.SubprocessError,AssertionError,FileNotFoundError):print(json.dumps({'error':'lab_operation_rejected'}));raise SystemExit(2)
