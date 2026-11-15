"""One registered Level6 lab only. No arbitrary commands, targets or host signals."""
import base64,fcntl,hashlib,json,os,re,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent
META=json.loads((ROOT/'lab.json').read_text());LABEL='id.harmonika.level6';LAB=META['lab_id']
FILES={'engine.py','server.py','effect_connector.py','Dockerfile'}
def run(args,**kw):return subprocess.run(args,capture_output=True,text=True,timeout=150,**kw)
def docker(*args):
 p=run(['docker',*args])
 if p.returncode:raise RuntimeError('docker_operation_failed')
 return p.stdout.strip()
def save():
 with (ROOT/'ledger.tmp').open('w') as f:json.dump(S,f);f.flush();os.fsync(f.fileno())
 os.replace(ROOT/'ledger.tmp',ROOT/'ledger.json')
def owned(cid):
 if cid not in S['containers']:raise ValueError('unowned')
 d=json.loads(docker('inspect',cid))[0]
 if d['Config'].get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned')
 return d
HTTP='''import sys,json,base64,urllib.request,urllib.error
r=json.load(sys.stdin)
req=urllib.request.Request('http://127.0.0.1:18081'+r['path'],data=base64.b64decode(r['body_b64']) if r.get('body_b64') is not None else None,headers=r['headers'],method=r['method'])
try:response=urllib.request.urlopen(req,timeout=4)
except urllib.error.HTTPError as e:response=e
with response:
 body=response.read(65537)
 if len(body)>65536:raise ValueError('too_large')
 print(json.dumps({'http_status':response.status,'headers':{k:v for k,v in response.headers.items() if k.lower() in ['content-type','retry-after']},'body_b64':base64.b64encode(body).decode()}))
'''
def acquire_lock(cid):
 code="import sqlite3,time; c=sqlite3.connect('/data/jobs.sqlite',timeout=2); c.execute('BEGIN IMMEDIATE'); print('locked',flush=True); time.sleep(5); c.rollback(); c.close()"
 child=subprocess.Popen(['docker','exec','-i',cid,'python','-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
 if child.stdout.readline().strip()!='locked':raise RuntimeError('lock_failed')
 child.stdout.close();S['lock_until']=time.time()+5
def http(req):
 path=req.get('path','');method=req.get('method','GET');owner=req.get('owner','alpha');key=req.get('key');cursor=req.get('cursor',0)
 if not re.fullmatch(r'/(?:healthz|readyz|jobs(?:/[A-Za-z0-9_-]{1,80}(?:/cancel)?)?|events|artifacts/[A-Za-z0-9_-]{1,80}|control/(?:drain|resume))',path) or method not in ['GET','POST'] or owner not in ['alpha','beta','anonymous']:raise ValueError('invalid_http_scope')
 if key is not None and (not isinstance(key,str) or len(key)>128 or '\r' in key or '\n' in key):raise ValueError('invalid_header')
 if not isinstance(cursor,(int,str)) or len(str(cursor))>20 or '\r' in str(cursor) or '\n' in str(cursor):raise ValueError('invalid_cursor_header')
 headers={'Content-Type':'application/json','Last-Event-ID':str(cursor)}
 if owner!='anonymous':headers['X-Lab-Owner']=owner
 if key is not None:headers['Idempotency-Key']=key
 body=base64.b64encode(json.dumps(req['body'],ensure_ascii=False).encode()).decode() if 'body' in req else None
 if body is not None and len(body)>10000:raise ValueError('too_large')
 cid=S['active'];owned(cid)
 if path=='/jobs' and method=='POST' and S.get('lock_next'):
  # Agent/tool scheduling can exceed the initial fault window. Re-arm under the
  # same controller lock before the first write if the old window has elapsed.
  if time.time()>S.get('lock_until',0)-1:acquire_lock(cid)
  S['lock_next']=False;save()
 p=run(['docker','exec','-i',cid,'python','-c',HTTP],input=json.dumps({'path':path,'method':method,'headers':headers,'body_b64':body}))
 try:value=json.loads(p.stdout) if p.returncode==0 else {'http_status':502,'headers':{},'body_b64':base64.b64encode(b'{"error":"lab_transport_unavailable"}').decode()}
 except ValueError:raise RuntimeError('invalid_transport')
 # Durable mechanical evidence without payload/key/job IDs or raw exception.
 S['http_count']=S.get('http_count',0)+1
 obs={'origin':req.get('origin','agent'),'op':'http','method':method,'route':re.sub(r'(?<=/jobs/)[^/]+|(?<=/artifacts/)[^/]+',':id',path),'status':value['http_status']}
 if path=='/events' and value['http_status']==200:
  ids=[int(x[4:]) for x in base64.b64decode(value['body_b64']).decode().splitlines() if re.fullmatch(r'id: [0-9]+',x)]
  obs.update(cursor=str(cursor),event_count=len(ids),first_event_id=ids[0] if ids else None,last_event_id=ids[-1] if ids else None)
 if value['http_status'] in [429,503]:
  try:code=json.loads(base64.b64decode(value['body_b64'])).get('error')
  except (ValueError,AttributeError):code=None
  if code in ['queue_full','database_busy','draining']:S.setdefault('observed_errors',{}).setdefault(req.get('origin','agent'),{}).setdefault(code,0);S['observed_errors'][req.get('origin','agent')][code]+=1
 S.setdefault('observations',[]).append(obs);S['observations']=S['observations'][-256:];save();return value
def status():
 if not S.get('active'):return {'state':'absent'}
 d=owned(S['active']);h=d['HostConfig']
 S['max_restart_count']=max(S.get('max_restart_count',0),d['RestartCount']);save()
 return {'release':S.get('release'),'image_id':d['Image'],'container_id':d['Id'],'restart_count':d['RestartCount'],'exit_code':d['State']['ExitCode'],'state':d['State']['Status'],'isolation':{'user':d['Config']['User'],'network':h['NetworkMode'],'readonly':h['ReadonlyRootfs'],'memory':h['Memory'],'nano_cpus':h['NanoCpus'],'pids':h['PidsLimit'],'cap_drop':h['CapDrop'],'security_opt':h['SecurityOpt']},'health':http({'path':'/healthz','origin':'controller'}),'ready':http({'path':'/readyz','origin':'controller'})}
def main(r):
 op=r.get('op')
 if op=='ledger':return S
 if S.get('closed') and op!='cleanup':raise ValueError('closed')
 if op=='build':
  name=r['release'];files=r['files']
  if not re.fullmatch(r'queue-v[12](?:-r[1-3])?',name) or set(files)!=FILES:raise ValueError('invalid_build')
  raw={n:base64.b64decode(v,validate=True) for n,v in files.items()}
  if sum(map(len,raw.values()))>262144:raise ValueError('too_large')
  if any(hashlib.sha256(raw[n]).hexdigest()!=h for n,h in META['protected_build'].items()):raise ValueError('protected_changed')
  lines=[x.strip() for x in raw['Dockerfile'].decode().splitlines() if x.strip() and not x.lstrip().startswith('#')]
  if not lines or lines[0]!='FROM '+META['base_ref'] or sum(x.upper().startswith('FROM ') for x in lines)!=1 or any(re.match(r'^(ADD|ONBUILD|VOLUME)\b',x,re.I) or '--mount' in x for x in lines):raise ValueError('dockerfile_policy')
  digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
  if name in S['releases']:
   if S['releases'][name]['source_sha256']!=digest:raise ValueError('immutable_conflict')
   return S['releases'][name]
  folder=ROOT/'contexts'/digest;folder.mkdir(parents=True,exist_ok=True)
  for n,b in raw.items():(folder/n).write_bytes(b)
  tag=LAB+':'+name;p=run(['docker','build','--network','none','--label',LABEL+'='+LAB,'-q','-t',tag,str(folder)])
  if p.returncode:return {'error':'build_failed','diagnostics':p.stderr[-2000:]}
  S['images'].append(tag);save();d=json.loads(docker('image','inspect',tag))[0]
  if d['Config']['User']!='10001:10001' or d['Config']['Cmd']!=['python','/app/server.py']:raise ValueError('image_runtime_policy')
  value={'tag':tag,'image_id':d['Id'],'source_sha256':digest};S['releases'][name]=value;save();return value
 if op=='activate':
  name=r['release'];key=r['request_id']
  if not re.fullmatch(r'stage-v[12](?:-r[1-3])?',key) or name not in S['releases']:raise ValueError('invalid_activation')
  S.setdefault('activation_attempts',{}).setdefault(key,0);S['activation_attempts'][key]+=1;save()
  if key in S['requests']:
   if S['requests'][key]['release']!=name:raise ValueError('conflict')
   return S['requests'][key]
  if S.get('active'):
   d=owned(S['active'])
   if d['State']['Running']:docker('stop','--time','3',S['active'])
  cid=docker('run','-d','--name',LAB+'-'+key,'--label',LABEL+'='+LAB,'--network','none','--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=16777216','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','192m','--cpus','0.5','--pids-limit','32','--restart','on-failure:3','--mount','type=volume,src='+S['volume']+',dst=/data',S['releases'][name]['image_id'])
  S['containers'].append(cid);S['active']=cid;S['release']=name;S['requests'][key]={'release':name,'container_id':cid,'image_id':S['releases'][name]['image_id']};save();return S['requests'][key]
 if op=='status':return status()
 if op=='http':return http(r)
 if op=='parallel':
  cid=S['active'];owned(cid);key=r.get('key','')
  if not re.fullmatch(r'exam-[a-z0-9]{1,40}',key):raise ValueError('invalid_exam_key')
  code="""import threading,urllib.request,urllib.error,json
b=threading.Barrier(6);out=[]
def one():
 b.wait();q=urllib.request.Request('http://127.0.0.1:18081/jobs',data=b'{"kind":"report","text":"parallel synthetic"}',headers={'X-Lab-Owner':'alpha','Content-Type':'application/json','Idempotency-Key':KEY},method='POST')
 try:r=urllib.request.urlopen(q,timeout=3)
 except urllib.error.HTTPError as e:r=e
 with r:out.append({'status':r.status,'body':json.loads(r.read())})
ts=[threading.Thread(target=one) for _ in range(6)]
for t in ts:t.start()
for t in ts:t.join(5)
print(json.dumps({'responses':out}))
""".replace('KEY',repr(key))
  return json.loads(docker('exec',cid,'python','-c',code))
 if op=='test-engine':
  cid=S['active'];owned(cid);phase=r.get('phase')
  if phase not in ['v1','v2']:raise ValueError('invalid_test_phase')
  p=run(['docker','exec','-i','-e','HARMONIKA_EXAM_PROJECT=/app','-e','HARMONIKA_EXAM_PHASE='+phase,cid,'python','-I','-'],input=(ROOT/'independent.py').read_text())
  return {'exit_code':p.returncode,'test_output':(p.stdout+p.stderr)[-14000:]}
 if op=='restart':
  cid=S['active'];owned(cid);docker('restart','--time','3',cid);S.setdefault('manual_restarts',0);S['manual_restarts']+=1;save();return {'restart_requested':True,'container_id':cid}
 if op=='fault':
  kind=r['kind'];cid=S['active'];owned(cid)
  if kind in ['hold','release','crash-after-effect','unknown-ack']:
   code="from pathlib import Path; p=Path('/data/"+('hold' if kind=='release' else kind)+"'); "+('p.unlink(missing_ok=True)' if kind=='release' else "p.write_text('armed')")
   docker('exec',cid,'python','-c',code)
  elif kind=='lock':
   acquire_lock(cid);S['lock_next']=True
  else:raise ValueError('invalid_fault')
  S.setdefault('faults',[]).append({'kind':kind,'origin':r.get('origin','agent'),'at':int(time.time())});save();return {'fault':kind,'armed':True}
 if op=='db-audit':
  cid=S['active'];owned(cid)
  code="""import sqlite3,json,hashlib
out={}
for name in ['jobs','sink']:
 c=sqlite3.connect('file:/data/'+name+'.sqlite?mode=ro',uri=True);c.row_factory=sqlite3.Row
 d={'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'journal_mode':c.execute('PRAGMA journal_mode').fetchone()[0],'tables':{}}
 for t in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\").fetchall():
  rows=[]
  for row in c.execute('SELECT * FROM \"'+t[0].replace('\"','\"\"')+'\" LIMIT 1000'):
   rows.append({k:({'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v) for k,v in dict(row).items()})
  d['tables'][t[0]]=rows
 out[name]=d;c.close()
print(json.dumps(out))
"""
  return json.loads(docker('exec',cid,'python','-c',code))
 if op=='logs':
  owned(S['active']);p=run(['docker','logs','--tail','40',S['active']]);return {'logs':(p.stdout+p.stderr)[-4000:]}
 if op=='events':
  cid=S['active'];owned(cid);raw=docker('events','--since',str(S['created_at']),'--until',str(int(time.time())+1),'--filter','container='+cid,'--format','{{json .}}');events=[]
  for line in raw.splitlines():
   e=json.loads(line)
   if e.get('Action') in ['start','die','restart']:events.append({'action':e['Action'],'time':e.get('time'),'exit_code':e.get('Actor',{}).get('Attributes',{}).get('exitCode')})
  return {'events':events[-100:]}
 if op=='cleanup':
  if S.get('closed'):return {'closed':True,'already_cleaned':True}
  for cid in S['containers']:
   if cid in S.get('removed_containers',[]):continue
   d=owned(cid)
   if d['State']['Running']:docker('stop','--time','3',cid)
   docker('rm',cid);S.setdefault('removed_containers',[]).append(cid);save()
  for tag in set(S['images']):
   if tag in S.get('removed_images',[]):continue
   d=json.loads(docker('image','inspect',tag))[0]
   if d['Config'].get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned_image')
   docker('image','rm',tag);S.setdefault('removed_images',[]).append(tag);save()
  d=json.loads(docker('volume','inspect',S['volume']))[0]
  if d.get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned_volume')
  docker('volume','rm',S['volume']);S['closed']=True;S['active']=None;save();return {'closed':True,'containers_removed':len(S['removed_containers'])}
 raise ValueError('unknown_operation')
if __name__=='__main__':
 with (ROOT/'control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);S=json.loads((ROOT/'ledger.json').read_text())
  try:print(json.dumps(main(json.load(sys.stdin))))
  except (ValueError,KeyError,RuntimeError,subprocess.SubprocessError):print(json.dumps({'error':'lab_operation_rejected'}));raise SystemExit(2)
