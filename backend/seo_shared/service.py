"""Tenant-isolated async SEO adapter. Logs/SQLite contain mechanics, not inputs."""
import asyncio,base64,concurrent.futures,hashlib,hmac,json,os,queue,re,secrets,sqlite3,sys,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path('/opt/harmonika-ai/seo-shared');STATE=Path('/var/lib/harmonika-seo');TOOLS=ROOT/'runtime/tools';sys.path.insert(0,str(TOOLS))
from skill_registry import Registry,SkillError
from skill_executor import SkillExecutor,ExecutionContext
REGISTRY=Registry();DESCRIPTORS={x['id']:x for x in REGISTRY.list() if x.get('worker')=='seo'}
ID=re.compile('^[a-f0-9]{32}$');ACTOR=re.compile(r'^(?:api-token:[1-9][0-9]{0,18}|owner:[a-f0-9]{64})$')
class Failure(Exception):pass

def scoped_project(principal):
 if not isinstance(principal,str) or not ACTOR.fullmatch(principal):raise Failure('invalid_principal')
 p=STATE/'actors'/hashlib.sha256(principal.encode()).hexdigest()/'project';p.mkdir(parents=True,exist_ok=True,mode=0o700);return p

def relative(root,value):
 if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_./-]{0,180}',value) or any(x in ['','..','.'] or x.startswith('.') for x in value.split('/')):raise Failure('invalid_project_path')
 p=root/value
 if p.is_symlink() or any(x.is_symlink() for x in p.parents) or not p.resolve().is_relative_to(root.resolve()):raise Failure('invalid_project_path')
 return p

def capabilities(principal):
 # Run the existing capability probe without executing a user skill.
 import subprocess
 p=subprocess.run([sys.executable,'-I',str(TOOLS/'seo_skill_worker.py'),'--capabilities'],capture_output=True,timeout=12)
 if p.returncode or len(p.stdout)>65536:raise Failure('seo_capabilities_unavailable')
 raw=json.loads(p.stdout);operations=raw['operations']
 if os.environ.get('HARMONIKA_SEO_VISUAL_ENABLED')!='1':operations['seo.visual']={'status':'unavailable','reason':'cpu_browser_isolation_qualification_pending'}
 # Google settings are principal-specific; no owner credentials are inherited by API users.
 from seo_extra import google_config
 _,reason=google_config(principal)
 for name in ['seo.search_console','seo.ga4']:operations[name]={'status':'unavailable' if reason else 'degraded','reason':reason or 'configured_connection_not_verified'}
 return {'ok':True,'provider':'Harmonika Ai','operations':operations,'async_jobs':True,'artifact_chunks':True,'shared_backend':True}

class Service:
 def __init__(self):
  STATE.mkdir(parents=True,exist_ok=True,mode=0o700);self.lock=threading.RLock();self.pending=queue.Queue(8);self.active=set()
  self.db=sqlite3.connect(STATE/'jobs.sqlite3',check_same_thread=False);self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,actor TEXT,rid TEXT,digest TEXT,skill TEXT,state TEXT,updated REAL,error TEXT,UNIQUE(actor,rid))');self.db.execute("UPDATE jobs SET state='unknown',error='service_restarted' WHERE state IN ('queued','running')");self.db.commit()
  for _ in range(2):threading.Thread(target=self.worker,daemon=True).start()
 def view(self,principal,jid):
  with self.lock:row=self.db.execute('SELECT * FROM jobs WHERE actor=? AND id=?',(principal,jid)).fetchone()
  if not row:raise Failure('not_found')
  out={'ok':True,'job_id':jid,'request_id':row['rid'],'skill_id':row['skill'],'state':row['state'],'error_code':row['error'],'provider':'Harmonika Ai','poll_after_seconds':2}
  if row['state'] in ['completed','degraded','unavailable']:
   p=scoped_project(principal)/'job-results'/(jid+'.json')
   if not p.is_file() or p.stat().st_size>230000:raise Failure('result_unavailable')
   out['result']=json.loads(p.read_text())
   for artifact in out['result'].get('artifacts',[]):
    if 'id' in artifact and 'artifact_id' not in artifact:artifact['artifact_id']=artifact.pop('id')
  return out
 def submit(self,principal,body):
  if type(body) is not dict or set(body)-{'skill_id','input','request_id'}:raise Failure('invalid_request')
  sid=body.get('skill_id');value=body.get('input');rid=body.get('request_id')
  if not isinstance(sid,str) or sid not in DESCRIPTORS or type(value) is not dict or not isinstance(rid,str) or not ID.fullmatch(rid):raise Failure('invalid_request')
  if sid=='seo.visual' and os.environ.get('HARMONIKA_SEO_VISUAL_ENABLED')!='1':raise Failure('visual_isolation_qualification_pending')
  root=scoped_project(principal);payload=json.loads(json.dumps(value));descriptor=DESCRIPTORS[sid]
  for name in descriptor.get('path_fields',[]):
   if name in payload:payload[name]=str(relative(root,payload[name]))
  REGISTRY.validate_input(sid,payload)
  digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  with self.lock:
   old=self.db.execute('SELECT id,digest FROM jobs WHERE actor=? AND rid=?',(principal,rid)).fetchone()
   if old:
    if old['digest']!=digest:raise Failure('idempotency_conflict')
    return self.view(principal,old['id'])
   if principal in self.active or self.pending.full():raise Failure('seo_busy')
   if sum(p.stat().st_size for p in STATE.rglob('*') if p.is_file() and not p.is_symlink())>480*1024*1024:raise Failure('seo_storage_limit')
   if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=1000:raise Failure('seo_storage_limit')
   jid=secrets.token_hex(16);self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,NULL)',(jid,principal,rid,digest,sid,'queued',time.time()));self.db.commit();self.active.add(principal);self.pending.put_nowait((jid,principal,rid,sid,payload));return self.view(principal,jid)
 def worker(self):
  while True:
   jid,principal,rid,sid,value=self.pending.get();root=scoped_project(principal);start=time.monotonic();error=None
   with self.lock:self.db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?",(time.time(),jid));self.db.commit()
   try:
    context=ExecutionContext(principal,str(root),frozenset(DESCRIPTORS),True,'internal');executor=SkillExecutor(STATE/'executor-state',python=sys.executable)
    # The outer journal owns idempotency; never persist report content/path in executor SQLite.
    original_finish=executor.store.finish
    executor.store.finish=lambda job,status,result=None:original_finish(job,status,None)
    response=asyncio.run(executor.execute(sid,value,context,idempotency_key=rid));state=response.get('status','completed');artifacts=[]
    candidates=[]
    if sid=='seo.visual':candidates=list(Path(value['output_dir']).glob('*.png'))
    elif sid=='seo.drift' and value.get('action')=='capture' and Path(value['baseline_path']).is_file():candidates=[Path(value['baseline_path'])]
    for file in candidates:
     if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root) or file.stat().st_size>8*1024*1024:raise Failure('invalid_artifact')
     data=file.read_bytes();digest=hashlib.sha256(data).hexdigest();rel=str(file.relative_to(root));aid=hashlib.sha256((principal+'\x00'+rel+'\x00'+digest).encode()).hexdigest()[:32];index=root/'artifact-index';index.mkdir(exist_ok=True,mode=0o700)
     meta={'id':aid,'path':rel,'sha256':digest,'bytes':len(data),'mime_type':'image/png' if file.suffix=='.png' else 'application/json'};(index/(aid+'.json')).write_text(json.dumps(meta));artifacts.append({'artifact_id':aid,**{k:v for k,v in meta.items() if k not in ['path','id']}})
    def sanitize(x):
     if isinstance(x,str):return x.replace(str(root)+'/','') if x.startswith(str(root)+'/') else x
     if isinstance(x,list):return [sanitize(y) for y in x]
     if isinstance(x,dict):return {k:sanitize(v) for k,v in x.items()}
     return x
    result={'analysis':sanitize(response),'artifacts':artifacts};encoded=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
    if len(encoded)>230000:raise Failure('seo_output_limit')
    target=root/'job-results';target.mkdir(exist_ok=True,mode=0o700);temp=target/(jid+'.next');temp.write_bytes(encoded);temp.chmod(0o600);temp.replace(target/(jid+'.json'))
    if state not in ['completed','degraded','unavailable']:raise Failure('seo_analysis_failed')
   except Exception as e:state='failed';candidate=getattr(e,'code',str(e) if isinstance(e,Failure) else 'seo_failed');error=candidate if isinstance(candidate,str) and re.fullmatch('[a-z_]{1,80}',candidate) else 'seo_failed'
   finally:
    with self.lock:self.db.execute('UPDATE jobs SET state=?,error=?,updated=? WHERE id=?',(state,error,time.time(),jid));self.db.commit();self.active.discard(principal)
    print(json.dumps({'event':'seo_terminal','state':state,'error_type':error,'latency_ms':round((time.monotonic()-start)*1000)}),flush=True);self.pending.task_done()
 def artifact(self,principal,value):
  if type(value) is not dict or set(value)-{'artifact_id','offset','limit'}:raise Failure('invalid_request')
  aid=value.get('artifact_id');offset=value.get('offset',0);limit=value.get('limit',65536)
  if not isinstance(aid,str) or not ID.fullmatch(aid) or type(offset) is not int or not 0<=offset<=8*1024*1024 or type(limit) is not int or not 1<=limit<=65536:raise Failure('invalid_request')
  root=scoped_project(principal);index=root/'artifact-index'/(aid+'.json')
  if not index.is_file():raise Failure('not_found')
  meta=json.loads(index.read_text());p=relative(root,meta['path'])
  if not p.is_file() or p.stat().st_size!=meta['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=meta['sha256']:raise Failure('artifact_changed')
  with p.open('rb') as f:f.seek(offset);chunk=f.read(limit)
  return {'ok':True,'artifact_id':aid,'offset':offset,'next_offset':offset+len(chunk),'done':offset+len(chunk)>=meta['bytes'],'data_base64':base64.b64encode(chunk).decode(),**{k:v for k,v in meta.items() if k not in ['path','id']}}

def main():
 token=Path('/etc/harmonika-ai/seo-shared.token').read_text().strip();service=Service()
 class Handler(BaseHTTPRequestHandler):
  timeout=15
  def log_message(self,*args):pass
  def response(self,status,data):
   raw=json.dumps(data,separators=(',',':')).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def do_POST(self):
   actor=self.headers.get('X-Harmonika-Principal','')
   if not hmac.compare_digest(self.headers.get('X-Harmonika-SEO-Secret',''),token) or not ACTOR.fullmatch(actor):return self.response(401,{'ok':False,'error':{'code':'unauthorized'}})
   try:
    n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=131072:raise Failure('input_limit')
    data=json.loads(self.rfile.read(n))
    if self.path=='/capabilities':out=capabilities(actor)
    elif self.path=='/invoke':out=service.submit(actor,data)
    elif self.path=='/request':
     if set(data)!={'request_id'} or not ID.fullmatch(str(data.get('request_id',''))):raise Failure('invalid_request')
     with service.lock:row=service.db.execute('SELECT id FROM jobs WHERE actor=? AND rid=?',(actor,data['request_id'])).fetchone()
     if not row:raise Failure('not_found')
     out=service.view(actor,row['id'])
    elif self.path=='/job':
     if set(data)!={'job_id'} or not ID.fullmatch(str(data.get('job_id',''))):raise Failure('invalid_request')
     out=service.view(actor,data['job_id'])
    elif self.path=='/artifact':out=service.artifact(actor,data)
    else:raise Failure('not_found')
    return self.response(200,out)
   except Exception as e:
    code=getattr(e,'code',str(e) if isinstance(e,Failure) else 'seo_unavailable');code=code if isinstance(code,str) and re.fullmatch('[a-z_]{1,80}',code) else 'seo_unavailable'
    return self.response(404 if code=='not_found' else 429 if code=='seo_busy' else 400,{'ok':False,'error':{'code':code}})
 class Server(ThreadingHTTPServer):
  daemon_threads=True
  def handle_error(self,*args):print('{"event":"seo_transport_error"}',flush=True)
 Server(('127.0.0.1',18260),Handler).serve_forever()
if __name__=='__main__':main()
