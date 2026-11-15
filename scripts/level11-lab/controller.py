"""Owned Level11 lab controller. Fixed Docker profiles, no arbitrary target/argv."""
import base64,concurrent.futures,fcntl,hashlib,hmac,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;META=json.loads((ROOT/'lab.json').read_text());LABEL='id.harmonika.level11';S=None
def run(args,**kw):return subprocess.run(args,capture_output=True,text=True,timeout=kw.pop('timeout',90),**kw)
def docker(*args):
 p=run(['docker',*args]);
 if p.returncode:raise RuntimeError('docker_operation_failed')
 return p.stdout.strip()
def save():
 p=ROOT/'ledger.tmp'
 with p.open('w') as f:f.write(json.dumps(S));f.flush();os.fsync(f.fileno())
 os.chmod(p,0o600);os.replace(p,ROOT/'ledger.json');fd=os.open(ROOT,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)
def capture_events():
 now=int(time.time());since=S.get('event_scan_since',int(S['created_at']));p=run(['docker','events','--since',str(max(0,since-1)),'--until',str(now),'--filter','label='+LABEL+'='+META['lab_id'],'--format','{{json .}}'],timeout=8)
 if p.returncode:raise RuntimeError('event_capture_failed')
 archive=S.setdefault('event_archive',{});changed=False
 for line in p.stdout.splitlines():
  e=json.loads(line);cid=e.get('Actor',{}).get('ID',e.get('id'));action=e.get('Action')
  if cid not in S['containers'] or e.get('Type')!='container' or action not in ['die','start','restart','stop']:continue
  row={'action':action,'at':e.get('timeNano',0)/1e9,'exit_code':e.get('Actor',{}).get('Attributes',{}).get('exitCode')};rows=archive.setdefault(cid,[])
  if row not in rows:rows.append(row);changed=True
 S['event_scan_since']=now
 if changed or since!=now:save()
def inspect(cid):
 if cid not in S['containers']:raise ValueError('unregistered_container')
 v=json.loads(docker('inspect',cid))[0];assert v['Config']['Labels'].get(LABEL)==META['lab_id'];return v
def record(v):
 if len(S['observations'])>=5000:raise RuntimeError('observation_budget_exhausted')
 S['observations'].append(v);save()
def authorize(r):
 if r.get('origin','agent')=='examiner':
  sig=r.pop('_operator_auth','');actual=hmac.new((ROOT/'examiner-auth.key').read_bytes(),json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
  if not isinstance(sig,str) or not sig.isascii() or not hmac.compare_digest(sig,actual):raise ValueError('invalid_examiner')
 elif r.get('origin','agent')!='agent':raise ValueError('invalid_origin')
 if r.get('op') in ['cleanup','test-image','test-boreal'] and r.get('origin')!='examiner':raise ValueError('examiner_only')
HTTP_CODE=r'''import base64,http.client,json,sys,concurrent.futures,time,hashlib
from pathlib import Path
r=json.load(sys.stdin);config=json.loads(Path('/data/access.json').read_text())
def req(v):
 owner=v.get('owner');project=v.get('project');method=v.get('method','GET');path=v['path'];headers=dict(v.get('headers',{}))
 if v.get('channel')!='browser':
  headers={'Authorization':'Bearer '+config[owner],**headers};path='/p/'+project+path if path not in ['/healthz','/readyz'] else path
 if v.get('key') is not None:headers['Idempotency-Key']=v['key']
 if v.get('lost_ack'):headers['X-Lab-Crash']=config['operator']
 mode=v.get('auth_mode')
 if mode=='missing':headers.pop('Authorization',None)
 if mode=='invalid':headers['Authorization']='Bearer invalid-fixture'
 if mode=='nonascii':headers['Authorization']='Bearer caf\u00e9'.encode().decode('unicode_escape')
 if mode=='forged':headers['X-Owner']='beta' if owner=='alpha' else 'alpha'
 body=base64.b64decode(v['raw_b64']) if 'raw_b64' in v else json.dumps(v['body']).encode() if 'body' in v else None
 if body is not None:headers.setdefault('Content-Type','application/json')
 c=http.client.HTTPConnection('127.0.0.1',18081,timeout=8);began=time.monotonic()
 try:
  c.request(method,path,body=body,headers=headers);q=c.getresponse();raw=q.read(262145)
  if len(raw)>262144:raise ValueError('response_budget')
  h={k:value for k,value in q.getheaders() if k.lower() in ['content-type','content-disposition'] or v.get('channel')=='browser' and k.lower()=='set-cookie'}
  return {'http_status':q.status,'headers':h,'body_b64':base64.b64encode(raw).decode(),'latency_ms':round((time.monotonic()-began)*1000,3)}
 except (OSError,http.client.HTTPException):return {'http_status':0,'error':'outcome_unknown' if method!='GET' else 'transport_unavailable','body_b64':''}
 finally:c.close()
if isinstance(r,list):
 with concurrent.futures.ThreadPoolExecutor(max_workers=min(8,len(r))) as pool:out=list(pool.map(req,r))
else:out=req(r)
print(json.dumps(out))
'''
def validate_http(v):
 if v.get('channel')=='browser':
  if len(v.get('headers',{}))>32:raise ValueError('headers_budget')
 else:
  if v.get('owner') not in ['alpha','beta'] or v.get('project') not in ['one','two']:raise ValueError('invalid_scope')
 if v.get('method','GET') not in ['GET','POST','PATCH']:raise ValueError('invalid_method')
 path=v.get('path');assert isinstance(path,str) and path.startswith('/') and not path.startswith('//') and len(path)<=1024 and '\r' not in path and '\n' not in path
 if v.get('channel')!='browser' and not re.fullmatch(r'/(?:tickets(?:/[a-f0-9]{32})?|jobs(?:/lookup\?key=[A-Za-z0-9_-]{1,64}|/[a-f0-9]{32}(?:/cancel)?)?|artifacts/[a-f0-9]{32}|events(?:\?after=[0-9]{1,16})?|healthz|readyz)',path):raise ValueError('invalid_route')
 if len(json.dumps(v))>24000:raise ValueError('request_budget')
 if v.get('lost_ack') and (v.get('channel')=='browser' or v.get('method')!='POST' or path!='/jobs'):raise ValueError('invalid_crash_target')
def http(r):
 requests=r.get('requests') if r['op']=='batch' else [r];assert isinstance(requests,list) and 1<=len(requests)<=16
 for v in requests:validate_http(v)
 # Durable attempted-call receipt precedes I/O; raw input/keys/auth never logged.
 started=time.time();receipt=len(S['requests'])+1
 S['requests'].append({'id':receipt,'at_start':started,'origin':r.get('origin','agent'),'release':S['release'],'requests':[{'owner':v.get('owner'),'project':v.get('project'),'method':v.get('method','GET'),'route':v['path'].split('?')[0],'key_sha256':hashlib.sha256(str(v.get('key','')).encode()).hexdigest(),'body_sha256':hashlib.sha256(json.dumps(v.get('body'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest() if 'body' in v else '', 'lost_ack':bool(v.get('lost_ack'))} for v in requests]});save()
 p=run(['docker','exec','-i',S['active'],'python','-c',HTTP_CODE],input=json.dumps(requests if r['op']=='batch' else requests[0]),timeout=45)
 if p.returncode:out=[{'http_status':0,'error':'outcome_unknown','body_b64':''} for _ in requests] if r['op']=='batch' else {'http_status':0,'error':'outcome_unknown','body_b64':''}
 else:out=json.loads(p.stdout)
 S['requests'][-1]['at']=time.time();S['requests'][-1]['results']=[{k:x.get(k) for k in ['http_status','error','latency_ms']} for x in (out if isinstance(out,list) else [out])];save();return out
def operation(r):
 op=r['op'];origin=r.get('origin','agent')
 if S['closed'] and op not in ['ledger']:raise ValueError('lab_closed')
 if op=='build':
  release=r.get('release');assert isinstance(release,str) and re.fullmatch(r'atlas-v1(?:-r[1-3])?',release) and release not in S['releases'];files=r['files'];assert set(files)==set(META['build_files']);data={n:base64.b64decode(v,validate=True) for n,v in files.items()};assert sum(map(len,data.values()))<=1048576
  assert all(hashlib.sha256(data[n]).hexdigest()==h for n,h in META['protected_build'].items());folder=ROOT/'builds'/release;folder.mkdir(parents=True,exist_ok=False)
  for n,b in data.items():p=folder/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
  tag=META['lab_id']+':'+release;docker('build','--network=none','--label',LABEL+'='+META['lab_id'],'-t',tag,str(folder));image=docker('image','inspect','--format','{{.Id}}',tag);S['images'].append(image);value={'image_id':image,'source_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),'files':{n:hashlib.sha256(b).hexdigest() for n,b in data.items()}};S['releases'][release]=value;save();return value
 if op=='activate':
  release=r['release'];assert r.get('request_id')==release and release in S['releases']
  if release in S['activations']:return S['activations'][release]
  if S['active']:
   old=inspect(S['active'])
   if old['State']['Running']:docker('stop','--time','2',S['active'])
  cid=docker('run','-d','--name',META['lab_id']+'-'+release,'--label',LABEL+'='+META['lab_id'],'--network','none','--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=16777216','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','256m','--cpus','1','--pids-limit','96','--restart','on-failure:3','--mount','type=volume,src='+S['volume']+',dst=/data','-e','LAB_RELEASE='+release,S['releases'][release]['image_id']);S['containers'].append(cid);S['active']=cid;S['release']=release;value={'release':release,'container_id':cid,'image_id':S['releases'][release]['image_id']};S['activations'][release]=value;save();record({'kind':'activation','origin':origin,'at':time.time(),**value});return value
 if op in ['http','batch']:return http(r)
 if op=='ledger':
  if origin=='examiner':return S
  own=[x for x in S['requests'] if x['origin']=='agent']
  return {'release':S['release'],'active':S['active'],'closed':S['closed'],'releases':{k:{n:v[n] for n in ['image_id','source_sha256']} for k,v in S['releases'].items()},'activations':S['activations'],'holder_started':{release:bool(entry.get('token')) for release,entry in S.get('holders',{}).items()},'requests':own[-8:],'requests_total':len(own),'requests_truncated':len(own)>8}
 if op=='status':
  if not S['active']:return {'active':False,'release':None,'releases':list(S['releases'])}
  d=inspect(S['active']);health=http({'op':'http','path':'/healthz','owner':'alpha','project':'one','origin':origin});ready=http({'op':'http','path':'/readyz','owner':'alpha','project':'one','origin':origin});value={'release':S['release'],'container_id':d['Id'],'image_id':d['Image'],'running':d['State']['Running'],'restarts':d['RestartCount'],'health':health['http_status'],'ready':ready['http_status'],'isolation':{k:d['HostConfig'][k] for k in ['NetworkMode','ReadonlyRootfs','Memory','NanoCpus','PidsLimit','CapDrop','SecurityOpt','RestartPolicy']},'user':d['Config']['User']};record({'kind':'status','origin':origin,'at':time.time(),**value});return value
 if op=='audit':
  code="""import sqlite3,json,base64,hashlib
from pathlib import Path
from workspace import WorkspaceStore
c=sqlite3.connect('file:/data/app.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row;out={'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'journal_mode':c.execute('PRAGMA journal_mode').fetchone()[0],'tables':{},'counts':{}}
for name in [x[0] for x in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\")]:
 count=c.execute('SELECT count(*) FROM \"'+name.replace('\"','\"\"')+'\"').fetchone()[0]
 if count>5000:raise RuntimeError('audit_budget')
 out['counts'][name]=count;out['tables'][name]=[{k:({'b64':base64.b64encode(v).decode(),'sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v) for k,v in dict(row).items()} for row in c.execute('SELECT * FROM \"'+name.replace('\"','\"\"')+'\"')]
c.close();out['mechanics']=WorkspaceStore('/data/app.sqlite').mechanics();print(json.dumps(out))
""";value=json.loads(docker('exec',S['active'],'python','-c',code));record({'kind':'audit','origin':origin,'at':time.time(),'release':S['release'],'counts':value['counts'],'integrity':value['integrity'],'journal_mode':value['journal_mode']});return value
 if op=='events':
  rows=[]
  for cid in S['containers']:
   d=inspect(cid);rows.append({'container_id':cid,'events':S.get('event_archive',{}).get(cid,[]),'restarts':d['RestartCount']})
  value={'containers':rows};record({'kind':'events','origin':origin,'release':S['release'],'at':time.time(),'containers':rows});return value
 if op=='resources':
  value={'container_id':S['active'],'stats':json.loads(docker('stats','--no-stream','--format','{{json .}}',S['active']))};record({'kind':'resources','origin':origin,'at':time.time(),'release':S['release']});return value
 if op=='logs':
  scans=[]
  for cid in S['containers']:
   p=run(['docker','logs',cid]);raw=p.stdout+p.stderr;scans.append({'container_id':cid,'bytes':len(raw.encode()),'output_sha256':hashlib.sha256(raw.encode()).hexdigest(),'private_canary_present':any(x in raw for x in ['PRIVATE_DATA_CANARY','PRIVATE_ERROR_CANARY','Bearer '])})
  record({'kind':'logs','origin':origin,'release':S['release'],'at':time.time(),'containers_scanned':len(scans),'private_canary_present':any(x['private_canary_present'] for x in scans)});return {'scans':scans}
 if op=='lock':
  action=r.get('action');assert action in ['start','state','release'];entry=S.setdefault('holders',{}).setdefault(S['release'],{})
  if action=='state' and not entry.get('token'):
   value={'state':'not_started','started':False};record({**value,'kind':'lock','action':'state','origin':origin,'release':S['release'],'at':time.time()});return value
  if action=='start':
   assert not entry;token=os.urandom(8).hex();entry['token']=token;save();docker('exec','-d',S['active'],'python','/app/holder.py',token)
  token=entry.get('token');assert token
  if action=='release':docker('exec',S['active'],'python','-c',"from pathlib import Path;Path('/data/lock-"+token+".release').touch()")
  code="from pathlib import Path;import json,sys,time;p=Path('/data/lock-'+sys.argv[1]+'.json');time.sleep(.15);print(p.read_text() if p.exists() else json.dumps({'state':'starting'}))";value=json.loads(docker('exec',S['active'],'python','-c',code,token));record({**value,'kind':'lock','action':action,'origin':origin,'release':S['release'],'at':time.time()});return value
 if op=='test-image':
  code=(ROOT/'atlas_tests.py').read_text();p=run(['docker','exec','-i',S['active'],'python','-'],input=code,timeout=90);return {'exit_code':p.returncode,'output':p.stdout+p.stderr}
 if op=='test-boreal':
  files=r['files'];assert set(files)=={'report.py','boreal_tests.py'} and sum(map(len,files.values()))<=262144
  code="import json,sys,os,runpy;from pathlib import Path;v=json.load(sys.stdin);root=Path('/work');[(root/n).write_text(s) for n,s in v.items()];os.chdir(root);os.environ['HARMONIKA_EXAM_PROJECT']=str(root);sys.argv=['boreal_tests.py'];runpy.run_path(str(root/'boreal_tests.py'),run_name='__main__')"
  cid=docker('create','-i','--label',LABEL+'='+META['lab_id'],'--network','none','--read-only','--user','10001:10001','--tmpfs','/work:rw,nosuid,size=16777216,uid=10001,gid=10001','--tmpfs','/tmp:rw,nosuid,size=16777216,uid=10001,gid=10001','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--cpus','.5','--pids-limit','32',META['base_ref'],'python','-c',code);S['containers'].append(cid);save();p=run(['docker','start','-ai',cid],input=json.dumps(files),timeout=90);return {'exit_code':inspect(cid)['State']['ExitCode'],'output':p.stdout+p.stderr,'source_sha256':hashlib.sha256(files['report.py'].encode()).hexdigest(),'container_id':cid}
 if op=='cleanup':
  for cid in S['containers']:
   d=inspect(cid)
   if d['State']['Running']:docker('stop','--time','2',cid)
   docker('rm',cid)
  for release in S['releases']:docker('image','rm',META['lab_id']+':'+release)
  docker('volume','rm',S['volume']);S['closed']=True;save();return {'closed':True,'containers_removed':len(S['containers'])}
 raise ValueError('unknown_operation')
if __name__=='__main__':
 with (ROOT/'control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);S=json.loads((ROOT/'ledger.json').read_text())
  try:r=json.load(sys.stdin);authorize(r);capture_events();v=operation(r);capture_events();print(json.dumps(v),flush=True)
  except Exception:print(json.dumps({'error':'lab_operation_rejected'}));sys.exit(2)
