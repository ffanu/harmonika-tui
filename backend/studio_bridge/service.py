"""Private owner-scoped Studio adapter. No request/body logging or POST retries."""
import base64,hashlib,hmac,http.client,json,os,re,sqlite3,threading,time,uuid
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(os.environ.get('STUDIO_STATE','/var/lib/harmonika-studio'))
CONFIG=Path(os.environ.get('STUDIO_CONFIG','/etc/harmonika-ai/studio.json'))
MAX_FILE=500*1024*1024; CHUNK=512*1024; MAX_DISK=2*1024**3
MUTATIONS={'render','autocut','subtitles','direct','revise','shorts','track','export_capcut'}
class Failure(Exception):pass
def ident(v,pattern=r'[A-Za-z0-9_-]{1,120}'):
 if not isinstance(v,str) or not re.fullmatch(pattern,v):raise Failure('invalid_identifier')
 return v
def clean(v):
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if not re.search(r'token|secret|authorization|password|file_path|traceback',k,re.I)}
 if isinstance(v,list):return [clean(x) for x in v]
 if isinstance(v,str):return re.sub(r'hst_[\w-]+','[REDACTED]',v)[:16000]
 return v
class Engine:
 def __init__(self,root=ROOT,config=None,start=True):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.cfg=config or json.loads(CONFIG.read_text());self.lock=threading.RLock();self.wake=threading.Event();self.closed=False
  with self.db() as c:
   c.executescript('PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT,request TEXT,digest TEXT,action TEXT,payload TEXT,state TEXT,remote TEXT,result TEXT,error TEXT,created REAL,UNIQUE(owner,request)); CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,owner TEXT,name TEXT,size INTEGER,sha TEXT,offset INTEGER,created REAL);')
   c.execute("UPDATE jobs SET state='unknown',error='studio_submission_uncertain' WHERE state='dispatching' AND remote IS NULL")
   c.execute("UPDATE jobs SET state='polling' WHERE remote IS NOT NULL AND state IN ('dispatching','polling')")
  if start:threading.Thread(target=self.worker,daemon=True).start()
 @contextmanager
 def db(self):
  c=sqlite3.connect(self.root/'state.sqlite',timeout=10);c.row_factory=sqlite3.Row
  try:
   yield c;c.commit()
  except Exception:
   c.rollback();raise
  finally:c.close()
 def used(self):return sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
 def request(self,method,path,payload=None,upload=None,output=None,timeout=1200):
  if not path.startswith('/') or path.startswith('//') or '\r' in path or '\n' in path:raise Failure('invalid_path')
  conn=http.client.HTTPSConnection('studio.harmonika.id',timeout=timeout)
  headers={'Authorization':'Bearer '+self.cfg['api_key'],'User-Agent':'Mozilla/5.0 HarmonikaStudioBridge/1.0','Accept':'application/json'}
  try:
   if upload:
    boundary='harmonika'+uuid.uuid4().hex;head=f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode();tail=f'\r\n--{boundary}--\r\n'.encode()
    headers['Content-Type']='multipart/form-data; boundary='+boundary;headers['Content-Length']=str(len(head)+upload.stat().st_size+len(tail));conn.putrequest(method,path)
    for k,v in headers.items():conn.putheader(k,v)
    conn.endheaders();conn.send(head)
    with upload.open('rb') as f:
     while part:=f.read(CHUNK):conn.send(part)
    conn.send(tail)
   else:
    body=None if payload is None else json.dumps(payload,allow_nan=False).encode();headers['Content-Type']='application/json';conn.request(method,path,body=body,headers=headers)
   r=conn.getresponse()
   if r.status<200 or r.status>=300:
    r.read(1024)
    if method=='POST' and (r.status>=500 or r.status in (408,425)):raise Failure('studio_submission_uncertain')
    raise Failure({401:'studio_auth_failed',403:'studio_forbidden',404:'studio_not_found',409:'studio_conflict',413:'studio_file_too_large',422:'studio_invalid_payload',429:'studio_busy'}.get(r.status,'studio_upstream_failed'))
   if output:
    total=0;sha=hashlib.sha256()
    with output.open('wb') as f:
     while part:=r.read(CHUNK):
      total+=len(part)
      if total>MAX_FILE or self.used()+len(part)>MAX_DISK:raise Failure('studio_storage_limit')
      f.write(part);sha.update(part)
    mime=r.getheader('Content-Type','').split(';')[0]
    with output.open('rb') as probe:prefix=probe.read(12)
    if mime=='video/mp4' and prefix[4:8]!=b'ftyp':raise Failure('studio_invalid_artifact')
    if mime in ('application/zip','application/x-zip-compressed') and not prefix.startswith(b'PK'):raise Failure('studio_invalid_artifact')
    if mime not in ('video/mp4','application/zip','application/x-zip-compressed'):raise Failure('studio_invalid_artifact')
    return {'bytes':total,'sha256':sha.hexdigest(),'mime_type':mime}
   raw=r.read(2*1024*1024+1)
   if len(raw)>2*1024*1024:raise Failure('studio_response_limit')
   data=json.loads(raw)
   if not isinstance(data,(dict,list)):raise Failure('studio_bad_payload')
   return data
  except Failure:raise
  except Exception:raise Failure('studio_submission_uncertain' if method=='POST' else 'studio_unavailable') from None
  finally:conn.close()
 def row(self,owner,jid):
  ident(jid,r'stj_[a-f0-9]{32}')
  with self.db() as c:r=c.execute('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,owner)).fetchone()
  if not r:raise Failure('studio_job_not_found')
  return dict(r)
 def project(self,r):return {'ok':True,'provider':'Harmonika Ai','job_id':r['id'],'state':r['state'],'action':r['action'],'error_code':r['error'],'result':json.loads(r['result']) if r['result'] else None,'retry_submission':False,'requires_reconciliation':r['state']=='unknown' and not r['remote']}
 def enqueue(self,owner,rid,action,payload):
  ident(rid);digest=hashlib.sha256(json.dumps([action,payload],sort_keys=True,allow_nan=False).encode()).hexdigest()
  with self.lock,self.db() as c:
   old=c.execute('SELECT * FROM jobs WHERE owner=? AND request=?',(owner,rid)).fetchone()
   if old:
    if old['digest']!=digest:raise Failure('studio_idempotency_conflict')
    return self.project(old)
   if c.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','dispatching','polling','fetching','unknown')").fetchone()[0]>=8:raise Failure('studio_queue_full')
   jid='stj_'+uuid.uuid4().hex
   c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(jid,owner,rid,digest,action,json.dumps(payload), 'queued',None,None,None,time.time()))
  self.wake.set();return self.project(self.row(owner,jid))
 def route(self,action,p):
  aid=p.get('asset_id')
  if action in {'autocut','subtitles','direct','shorts','track','export_capcut'}:ident(aid,r'ast_[A-Za-z0-9_-]{1,100}')
  params=p.get('params',{})
  if not isinstance(params,dict) or len(json.dumps(params,allow_nan=False))>100000:raise Failure('studio_invalid_params')
  fields={
   'render':{'asset_id','keep','grade','reframe','overlay','fade','crf','audio','burn_subtitles','subtitle_style','transition','zoom','speed','track'},
   'autocut':{'mode','noise_db','min_silence','min_segment'},'subtitles':{'language','translate_to','style'},
   'direct':{'instruction'},'revise':{'timeline','instruction'},'shorts':{'instruction','translate_to','subtitle_style','music'},
   'track':{'t0','t1','box','auto_face'},'export_capcut':{'keep','width','height','text_overlays'},
   'generate':{'prompt','quality','with_audio','size','fps','duration'}}
  if action not in fields or set(params)-fields[action]:raise Failure('studio_invalid_params')
  for key in ('instruction','prompt','music'):
   if key in params and (not isinstance(params[key],str) or not 0<len(params[key])<=8000):raise Failure('studio_invalid_params')
  if 'speed' in params and params['speed'] is not None and (type(params['speed']) not in (int,float) or not 0.75<=params['speed']<=2):raise Failure('studio_invalid_params')
  if 'grade' in params and params['grade'] not in ('none','warm-islamic','cinematic','clean','bw-soft'):raise Failure('studio_invalid_params')
  if 'reframe' in params and params['reframe'] not in (None,'9:16','1:1','16:9','4:5','9:16-follow'):raise Failure('studio_invalid_params')
  if 'zoom' in params and params['zoom'] not in (None,'in','out'):raise Failure('studio_invalid_params')
  if 'transition' in params and params['transition'] not in (None,'crossfade'):raise Failure('studio_invalid_params')
  if action=='generate' and (not params.get('prompt') or type(params.get('duration',5))!=int or not 1<=params.get('duration',5)<=30):raise Failure('studio_invalid_params')
  if action=='revise' and (not isinstance(params.get('timeline'),dict) or not params.get('instruction')):raise Failure('studio_invalid_params')
  if action=='render':
   aid=params.get('asset_id');ident(aid,r'ast_[A-Za-z0-9_-]{1,100}')
   keep=params.get('keep',[])
   if not isinstance(keep,list) or len(keep)>500:raise Failure('studio_invalid_timeline')
   for seg in keep:
    if not isinstance(seg,dict) or type(seg.get('in')) not in (int,float) or type(seg.get('out')) not in (int,float) or not 0<=seg['in']<seg['out']:raise Failure('studio_invalid_timeline')
  routes={'render':'/render','revise':'/revise','generate':'/cloud/video','direct':'/direct?asset_id='+str(aid),'shorts':'/shorts/'+str(aid)}
  return routes.get(action,'/assets/'+str(aid)+'/'+action),params
 def rpc(self,d):
  owner=d.get('owner')
  if not isinstance(owner,str) or owner!=self.cfg['owner']:raise Failure('studio_owner_required')
  action=d.get('action')
  if action=='capabilities':
   h=self.request('GET','/health',timeout=6)
   return {'ok':True,'provider':'Harmonika Ai','online':h.get('ok') is True,'pending_renders':h.get('pending_renders'),'actions':['assets','upload',*sorted(MUTATIONS),'job','download'],'max_upload_bytes':MAX_FILE,'chunk_bytes':CHUNK,'verified_actions':['upload','autocut','export_capcut','render','job','download'],'model_quality_verified':False,'generation_route':'existing_video_tool'}
  if action=='generate':raise Failure('studio_use_existing_video_engine')
  if action=='assets':return {'ok':True,'provider':'Harmonika Ai','result':clean(self.request('GET','/assets',timeout=8))}
  if action=='upload_begin':
   rid=ident(d.get('request_id'));size=d.get('bytes');sha=ident(d.get('sha256'),r'[a-f0-9]{64}');name=d.get('filename','video.mp4')
   if type(size)!=int or not 12<=size<=MAX_FILE or not isinstance(name,str) or len(name)>200 or Path(name).name!=name or not name.lower().endswith('.mp4'):raise Failure('studio_invalid_upload')
   uid='up_'+hashlib.sha256((owner+rid).encode()).hexdigest()[:32]
   with self.lock,self.db() as c:
    old=c.execute('SELECT * FROM uploads WHERE id=?',(uid,)).fetchone()
    if old:
     if old['size']!=size or old['sha']!=sha:raise Failure('studio_idempotency_conflict')
     return {'ok':True,'upload_id':uid,'offset':old['offset'],'chunk_bytes':CHUNK}
    reserved=c.execute('SELECT coalesce(sum(size-offset),0) FROM uploads').fetchone()[0]
    if self.used()+reserved+size>MAX_DISK:raise Failure('studio_storage_limit')
    c.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?,?)',(uid,owner,name,size,sha,0,time.time()))
   return {'ok':True,'upload_id':uid,'offset':0,'chunk_bytes':CHUNK}
  if action in ('upload_chunk','upload_finish'):
   uid=ident(d.get('upload_id'),r'up_[a-f0-9]{32}')
   with self.lock,self.db() as c:
    row=c.execute('SELECT * FROM uploads WHERE id=? AND owner=?',(uid,owner)).fetchone()
    if not row:raise Failure('studio_upload_not_found')
    path=self.root/(uid+'.mp4')
    if action=='upload_chunk':
     try:raw=base64.b64decode(d.get('data',''),validate=True)
     except Exception:raise Failure('studio_invalid_chunk')
     offset=d.get('offset')
     if type(offset)!=int or offset<0 or not 0<len(raw)<=CHUNK or offset+len(raw)>row['size']:raise Failure('studio_invalid_chunk')
     current=path.stat().st_size if path.exists() else 0
     if offset<current:
      with path.open('rb') as f:f.seek(offset);same=f.read(len(raw))==raw
      if not same:raise Failure('studio_chunk_conflict')
     elif offset==current:
      if self.used()+len(raw)>MAX_DISK:raise Failure('studio_storage_limit')
      with path.open('ab') as f:f.write(raw);f.flush();os.fsync(f.fileno())
     else:raise Failure('studio_chunk_offset')
     current=path.stat().st_size;c.execute('UPDATE uploads SET offset=? WHERE id=?',(current,uid));return {'ok':True,'offset':current,'upload_id':uid}
    if not path.exists() or path.stat().st_size!=row['size']:raise Failure('studio_upload_incomplete')
    with path.open('rb') as f:
     if f.read(12)[4:8]!=b'ftyp':raise Failure('studio_invalid_mp4')
    with path.open('rb') as f:
     digest=hashlib.sha256()
     while part:=f.read(CHUNK):digest.update(part)
     actual=digest.hexdigest()
    if actual!=row['sha']:raise Failure('studio_checksum_mismatch')
   return self.enqueue(owner,uid,'upload',{'upload_id':uid})
  if action in MUTATIONS:
   p={'asset_id':d.get('asset_id'),'params':d.get('params',{})};self.route(action,p)
   return self.enqueue(owner,ident(d.get('request_id')),action,p)
  if action=='job':
   if d.get('job_id'):r=self.row(owner,d['job_id'])
   else:
    rid=ident(d.get('request_id'))
    with self.db() as c:r=c.execute('SELECT * FROM jobs WHERE owner=? AND request=?',(owner,rid)).fetchone()
   if not r:raise Failure('studio_job_not_found')
   if r['state']=='unknown' and r['remote'] and r['error']=='studio_poll_timeout':
    with self.db() as c:c.execute("UPDATE jobs SET state='polling',error=NULL WHERE id=?",(r['id'],))
    self.wake.set();r=self.row(owner,r['id'])
   return self.project(r)
  if action=='download':
   r=self.row(owner,d.get('job_id'));out=json.loads(r['result'] or '{}');artifact=out.get('artifact');offset=d.get('offset',0)
   if r['state']!='completed' or not artifact:raise Failure('studio_artifact_not_ready')
   if type(offset)!=int or offset<0 or offset>artifact['bytes']:raise Failure('studio_chunk_offset')
   with (self.root/(r['id']+'.artifact')).open('rb') as f:f.seek(offset);raw=f.read(CHUNK)
   return {'ok':True,**artifact,'offset':offset,'data':base64.b64encode(raw).decode(),'eof':offset+len(raw)==artifact['bytes']}
  raise Failure('studio_unknown_action')
 def finish(self,r,data):
  result=clean(data);url=None
  if isinstance(data,dict):
   result_data=data.get('result')
   url=data.get('url') or data.get('zip') or (result_data.get('url') if isinstance(result_data,dict) else None)
  if url:
   if not isinstance(url,str) or not re.fullmatch(r'/(exports|capcut)/[A-Za-z0-9_.-]+',url) or '..' in url:raise Failure('studio_invalid_artifact_url')
   with self.db() as c:c.execute("UPDATE jobs SET state='fetching',result=? WHERE id=?",(json.dumps(result),r['id']))
   art=self.request('GET',url,output=self.root/(r['id']+'.artifact'));art['filename']=url.rsplit('/',1)[1]
   result={'data':result,'artifact':art}
  with self.db() as c:c.execute("UPDATE jobs SET state='completed',result=?,payload='{}',error=NULL WHERE id=?",(json.dumps(result),r['id']))
 def work(self,r):
  if r['state']=='fetching':self.finish(r,json.loads(r['result']));return
  data=json.loads(r['payload'])
  if r['remote']:reply={'job_id':r['remote']}
  else:
   with self.db() as c:c.execute("UPDATE jobs SET state='dispatching' WHERE id=?",(r['id'],))
   if r['action']=='upload':reply=self.request('POST','/assets',upload=self.root/(data['upload_id']+'.mp4'))
   else:
    path,payload=self.route(r['action'],data);reply=self.request('POST',path,payload)
  if isinstance(reply,dict) and reply.get('job_id'):
   remote=ident(reply['job_id']);
   with self.db() as c:c.execute("UPDATE jobs SET remote=?,state='polling' WHERE id=?",(remote,r['id']))
   deadline=time.monotonic()+3600
   while not self.closed:
    try:reply=self.request('GET','/jobs/'+remote,timeout=15)
    except Failure as e:
     if str(e)=='studio_not_found':raise Failure('studio_remote_job_missing')
     if time.monotonic()>deadline:raise Failure('studio_poll_timeout')
     time.sleep(5);continue
    state=reply.get('state') or reply.get('status')
    if state=='completed':break
    if state in ('failed','error','cancelled'):raise Failure('studio_job_failed')
    if time.monotonic()>deadline:raise Failure('studio_poll_timeout')
    time.sleep(3)
  self.finish(r,reply)
 def cleanup(self):
  cutoff=time.time()-86400
  with self.lock,self.db() as c:
   for r in c.execute("SELECT id FROM jobs WHERE created<? AND state IN ('completed','failed')",(cutoff,)).fetchall():
    (self.root/(r['id']+'.artifact')).unlink(missing_ok=True);c.execute('DELETE FROM jobs WHERE id=?',(r['id'],))
   for r in c.execute('SELECT id FROM uploads WHERE created<?',(cutoff,)).fetchall():
    if c.execute("SELECT 1 FROM jobs WHERE request=? AND state IN ('queued','dispatching','polling','fetching','unknown')",(r['id'],)).fetchone():continue
    (self.root/(r['id']+'.mp4')).unlink(missing_ok=True);c.execute('DELETE FROM uploads WHERE id=?',(r['id'],))
 def worker(self):
  while not self.closed:
   try:
    self.cleanup()
    with self.db() as c:r=c.execute("SELECT * FROM jobs WHERE state IN ('queued','polling','fetching') ORDER BY created LIMIT 1").fetchone()
    if not r:self.wake.wait(3);self.wake.clear();continue
    r=dict(r)
    try:self.work(r)
    except Exception as e:
     code=str(e) if isinstance(e,Failure) else 'studio_adapter_failed'
     current=self.row(r['owner'],r['id'])
     if current['state']=='fetching' and code in ('studio_unavailable','studio_upstream_failed','studio_busy'):
      with self.db() as c:c.execute('UPDATE jobs SET error=? WHERE id=?',(code,r['id']))
      time.sleep(5);continue
     unknown=code in ('studio_submission_uncertain','studio_remote_job_missing','studio_poll_timeout')
     with self.db() as c:c.execute('UPDATE jobs SET state=?,error=?,payload=? WHERE id=?',('unknown' if unknown else 'failed',code,r['payload'] if unknown else '{}',r['id']))
   except Exception:time.sleep(3)
class Handler(BaseHTTPRequestHandler):
 timeout=15
 def log_message(self,*a):pass
 def do_POST(self):
  if self.path!='/rpc':self.send_error(404);return
  if not hmac.compare_digest(self.headers.get('Authorization','').encode(),('Bearer '+engine.cfg['bridge_token']).encode()):self.send_error(401);return
  try:
   n=int(self.headers.get('Content-Length','0'))
   if not 0<n<=1024*1024:raise Failure('studio_payload_limit')
   d=json.loads(self.rfile.read(n));out=engine.rpc(d)
  except Exception as e:out={'ok':False,'provider':'Harmonika Ai','error':{'code':str(e) if isinstance(e,Failure) else 'studio_invalid_request'}}
  raw=json.dumps(out,allow_nan=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
if __name__=='__main__':
 os.umask(0o077);engine=Engine();ThreadingHTTPServer(('127.0.0.1',18275),Handler).serve_forever()
