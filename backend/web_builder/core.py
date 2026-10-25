"""Encrypted projects and bounded async jobs. Generated code executes only in Docker."""
import asyncio,base64,contextlib,hashlib,io,json,os,re,sqlite3,time,zipfile
from pathlib import Path
from uuid import uuid4
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .common import BuilderError,require,validate,principal,uuid_id,safe_path,canonical,digest,now_iso
TERMINAL={'completed','failed','stopped','interrupted','timeout'}
from .constants import MAX_FILES,MAX_BYTES,MAX_FILE,TENANT_BYTES,READERS,EXEC_IDS,snapshot_check
class Store:
 def __init__(self,root,key):
  require(len(key)==32,'encryption_key_required');self.root=Path(root);require(not self.root.is_symlink(),'unsafe_storage_root');self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700);self.path=self.root/'state.sqlite';require(not self.path.is_symlink(),'unsafe_database');self.aes=AESGCM(key)
  with self.db() as d:
   d.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,actor TEXT,revision INTEGER,data BLOB,created TEXT);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,actor TEXT,project TEXT,skill TEXT,idem TEXT,digest TEXT,revision INTEGER,state TEXT,payload BLOB,result BLOB,error TEXT,created TEXT,UNIQUE(actor,idem));
CREATE TABLE IF NOT EXISTS events(job TEXT,sequence INTEGER,data BLOB,PRIMARY KEY(job,sequence));''')
   if 'meta' not in {r[1] for r in d.execute('PRAGMA table_info(projects)')}:d.execute('ALTER TABLE projects ADD COLUMN meta BLOB')
  self.path.chmod(0o600)
 @contextlib.contextmanager
 def db(self):
  d=sqlite3.connect(self.path,timeout=5);d.row_factory=sqlite3.Row
  try:
   with d:yield d
  except sqlite3.OperationalError as e:
   if 'locked' in str(e).lower() or 'busy' in str(e).lower():raise BuilderError('builder_busy',429) from None
   raise
  finally:d.close()
 def quota(self,d,a,extra=0):
  used=d.execute('SELECT coalesce(sum(length(data)+coalesce(length(meta),0)),0) FROM projects WHERE actor=?',(a,)).fetchone()[0]+d.execute('SELECT coalesce(sum(length(payload)+coalesce(length(result),0)),0) FROM jobs WHERE actor=?',(a,)).fetchone()[0]
  require(used+extra<=TENANT_BYTES,'tenant_storage_quota',429)
 def seal(self,v,aad):
  raw=canonical(v).encode();require(len(raw)<=24*1024*1024,'payload_too_large',413);nonce=os.urandom(12);return nonce+self.aes.encrypt(nonce,raw,aad.encode())
 def open(self,v,aad):
  try:return json.loads(self.aes.decrypt(v[:12],v[12:],aad.encode()))
  except Exception:raise BuilderError('encrypted_record_invalid',503) from None
class Runtime:
 def __init__(self,root,key,sandbox=None,connectors=None):
  self.store=Store(root,key);self.sandbox=sandbox;self.connectors=connectors;self.tasks={};self.locks={};self.slots=asyncio.Semaphore(2);self._caps=None
 def lock(self,p):return self.locks.setdefault(p,asyncio.Lock())
 def project(self,a,p):
  principal(a);uuid_id(p)
  with self.store.db() as d:r=d.execute('SELECT * FROM projects WHERE id=? AND actor=?',(p,a)).fetchone()
  require(r is not None,'project_not_found',404);return {**dict(r),'_stored_bytes':len(r['data']),'data':self.store.open(r['data'],'project:'+a+':'+p)}
 def project_view(self,a,p):
  r=self.project(a,p);return {'id':p,'name':r['data']['name'],'revision':r['revision'],'files':[{'path':k,'size_bytes':len(base64.b64decode(v)),'sha256':hashlib.sha256(base64.b64decode(v)).hexdigest()} for k,v in sorted(r['data']['files'].items())],'created_at':r['created']}
 def projects(self,a):
  principal(a)
  with self.store.db() as d:rows=d.execute('SELECT id,revision,created,meta FROM projects WHERE actor=? ORDER BY created DESC LIMIT 20',(a,)).fetchall()
  out=[]
  for r in rows:
   if r['meta']:name=self.store.open(r['meta'],'project-meta:'+a+':'+r['id'])['name']
   else:name=self.project(a,r['id'])['data']['name']
   out.append({'id':r['id'],'name':name,'revision':r['revision'],'created_at':r['created']})
  return out
 def create(self,a,name,idem):
  principal(a);require(type(name) is str and 1<=len(name)<=100,'invalid_name');self.idem(idem);fp=digest({'name':name,'skill':'project.create'})
  with self.store.db() as d:
   d.execute('BEGIN IMMEDIATE');old=d.execute('SELECT * FROM jobs WHERE actor=? AND idem=?',(a,idem)).fetchone()
   if old:
    require(old['digest']==fp,'idempotency_conflict',409);return self.project_view(a,old['project'])
   require(d.execute('SELECT count(*) FROM projects WHERE actor=?',(a,)).fetchone()[0]<20,'project_quota',429)
   p=str(uuid4());j=str(uuid4());now=now_iso();data={'name':name,'files':{}}
   d.execute('INSERT INTO projects(id,actor,revision,data,created,meta) VALUES(?,?,?,?,?,?)',(p,a,0,self.store.seal(data,'project:'+a+':'+p),now,self.store.seal({'name':name},'project-meta:'+a+':'+p)));d.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(j,a,p,'project.create',idem,fp,0,'completed',self.store.seal({},'job:'+a+':'+j),None,None,now));return {'id':p,'name':name,'revision':0,'files':[],'created_at':now}
 @staticmethod
 def idem(value):require(type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value) is not None,'idempotency_required')
 def job(self,a,j):
  principal(a);uuid_id(j)
  with self.store.db() as d:r=d.execute('SELECT * FROM jobs WHERE id=? AND actor=?',(j,a)).fetchone()
  require(r is not None,'job_not_found',404);out={k:r[k] for k in ('id','project','skill','revision','state','created')};out['input_hash']=r['digest']
  if r['result']:out['result']=self.store.open(r['result'],'result:'+a+':'+j)
  if r['error']:out['error']={'code':r['error']}
  return out
 def events(self,a,j,after=0):
  self.job(a,j);require(type(after) is int and after>=0,'invalid_sequence')
  with self.store.db() as d:return [self.store.open(r['data'],'event:'+j+':'+str(r['sequence'])) for r in d.execute('SELECT * FROM events WHERE job=? AND sequence>? ORDER BY sequence LIMIT 128',(j,after))]
 def event(self,d,j,kind,data):
  n=d.execute('SELECT coalesce(max(sequence),0)+1 FROM events WHERE job=?',(j,)).fetchone()[0];v={'event_id':j+':'+str(n),'job_id':j,'sequence':n,'timestamp':now_iso(),'type':kind,'data':data};d.execute('INSERT INTO events VALUES(?,?,?)',(j,n,self.store.seal(v,'event:'+j+':'+str(n))))
 async def capabilities(self,a=None):
  from .registry import catalog
  if self._caps is None:self._caps=await self.sandbox.capabilities() if self.sandbox else {'ready':False}
  ssh_ready=bool(a and self.connectors and any(a in t.get('principals',[]) for t in self.connectors.targets.values()))
  try:
   from legal_engine.local_media import LocalMedia
   media=await asyncio.to_thread(LocalMedia);asr_ready=media.asr_ready
  except ImportError:asr_ready=False
  return {'schema_version':1,'features':{'web_builder':True,'builder_projects':True,'builder_sse':True,'builder_sandbox':self._caps.get('ready',False),'builder_production_autodeploy':False,'builder_deployment_submission_configured':bool(a and self.connectors and any(a in t.get('principals',[]) and t.get('cost_authorized') is True for t in self.connectors.deployments.values())),'builder_ssh':ssh_ready},'catalog_size':161,'implemented_handlers':sum(s['readiness']['reason']!='generator_pending' for s in catalog(self._caps.get('ready',False),ssh_ready,asr_ready,bool(a and self.connectors and any(a in t.get('principals',[]) for t in self.connectors.notion.values())))),'skills':catalog(self._caps.get('ready',False),ssh_ready,asr_ready,bool(a and self.connectors and any(a in t.get('principals',[]) for t in self.connectors.notion.values()))),'limits':{'projects_per_token':20,'project_files':MAX_FILES,'file_bytes':MAX_FILE,'project_bytes':MAX_BYTES,'active_jobs':2,'global_queued_running_jobs':32,'tenant_storage_bytes':TENANT_BYTES,'input_bytes':1048576},'execution_contract':'Generated artifacts are not installed, tested, deployed or monitored until an actual execution result proves it.'}
 async def invoke(self,a,p,skill,args,idem,expected_revision=None,overwrite=False,_workflow=False):
  from .registry import schema_for,IDS
  self.idem(idem);require(skill in IDS or _workflow and skill in ('builder.workflow','builder.deploy'),'unknown_skill',404);require(type(overwrite) is bool,'invalid_overwrite');
  from .workflow import SCHEMA as WORKFLOW_SCHEMA
  from .deployment import SCHEMA as DEPLOY_SCHEMA
  validate(args,(DEPLOY_SCHEMA if skill=='builder.deploy' else WORKFLOW_SCHEMA) if _workflow else schema_for(skill));w=self.project(a,p)
  if overwrite:require(expected_revision is not None,'expected_revision_required')
  if expected_revision is not None:require(type(expected_revision) is int and expected_revision==w['revision'],'revision_conflict',409)
  fp=digest({'project':p,'skill':skill,'input':args,'expected_revision':expected_revision,'overwrite':overwrite})
  with self.store.db() as d:
   d.execute('BEGIN IMMEDIATE');old=d.execute('SELECT * FROM jobs WHERE actor=? AND idem=?',(a,idem)).fetchone()
   if old:require(old['digest']==fp,'idempotency_conflict',409);return self.job(a,old['id'])
   self.store.quota(d,a,len(canonical(args).encode())+256)
   require(d.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]<32,'builder_capacity',429)
   require(d.execute("SELECT count(*) FROM jobs WHERE actor=? AND state IN ('queued','running','awaiting_approval')",(a,)).fetchone()[0]<2,'active_job_limit',429)
   require(d.execute('SELECT count(*) FROM jobs WHERE actor=?',(a,)).fetchone()[0]<2000,'job_quota',429)
   j=str(uuid4());state='awaiting_approval' if skill in EXEC_IDS or skill in ('ssh.connect','ssh.exec','ssh.upload','ssh.download','ssh.tunnel') or skill=='builder.deploy' or _workflow and args.get('run_checks') else 'queued';payload={'input':args,'overwrite':overwrite};d.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(j,a,p,skill,idem,fp,w['revision'],state,self.store.seal(payload,'job:'+a+':'+j),None,None,now_iso()));self.event(d,j,'job.'+state,{'status':state})
  if state=='queued':self.start(a,j)
  return self.job(a,j)
 def start(self,a,j):
  task=asyncio.create_task(self.run(a,j));self.tasks[j]=task;task.add_done_callback(lambda _:self.tasks.pop(j,None))
 async def approve(self,a,j,input_hash):
  state=self.job(a,j);require(state['input_hash']==input_hash,'approval_hash_mismatch',409);w=self.project(a,state['project'])
  with self.store.db() as d:
   d.execute('BEGIN IMMEDIATE');require(state['revision']==w['revision'],'stale_approval',409);n=d.execute("UPDATE jobs SET state='queued' WHERE id=? AND actor=? AND state='awaiting_approval'",(j,a)).rowcount;require(n==1,'job_not_awaiting_approval',409);self.event(d,j,'job.queued',{'approved':True})
  self.start(a,j);return self.job(a,j)
 async def stop(self,a,j):
  self.job(a,j)
  with self.store.db() as d:
   n=d.execute("UPDATE jobs SET state='stopped' WHERE id=? AND actor=? AND state NOT IN ('completed','failed','stopped','interrupted','timeout')",(j,a)).rowcount
   if n:self.event(d,j,'job.stopped',{})
  if n and self.sandbox:await self.sandbox.stop(j)
  return self.job(a,j)
 async def run(self,a,j):
  try:
   async with self.slots:
    job=self.job(a,j)
    async with self.lock(job['project']):
     if self.job(a,j)['state']!='queued':return
     w=self.project(a,job['project']);require(w['revision']==job['revision'],'revision_conflict',409)
     with self.store.db() as d:
      r=d.execute('SELECT payload FROM jobs WHERE id=?',(j,)).fetchone();payload=self.store.open(r[0],'job:'+a+':'+j);d.execute("UPDATE jobs SET state='running' WHERE id=? AND state='queued'",(j,));self.event(d,j,'job.progress',{'stage':'processing'})
     files=dict(w['data']['files']);out=await self.dispatch(job['skill'],payload['input'],files,j,a)
     require(type(out) is dict and out.get('status')=='completed','operation_failed',422)
     changed=False
     if '_snapshot' in out:files=snapshot_check(out.pop('_snapshot'));changed=files!=w['data']['files']
     for item in out.get('writes',[]):
      p=safe_path(item['path']);require(type(item['content']) is str,'invalid_artifact');b=base64.b64encode(item['content'].encode()).decode()
      if p in files and files[p]!=b:require(payload['overwrite'],'file_exists',409)
      changed|=files.get(p)!=b;files[p]=b
     snapshot_check(files);written_paths=[item['path'] for item in out.get('writes',[])];out={k:v for k,v in out.items() if k!='writes'};out['project_id']=w['id'];out['files_written']=written_paths;out['revision']=w['revision']+int(changed)
     with self.store.db() as d:
      d.execute('BEGIN IMMEDIATE');current=d.execute('SELECT state FROM jobs WHERE id=?',(j,)).fetchone()[0]
      if current!='running':
       if out.get('data',{}).get('remote_submission_started') or out.get('data',{}).get('remote_mutation_started'):
        out['local_stop_did_not_cancel_remote']=True;d.execute('UPDATE jobs SET result=? WHERE id=?',(self.store.seal(out,'result:'+a+':'+j),j));self.event(d,j,'deployment.submitted_after_local_stop',{'remote_submission_started':True})
       return
      self.store.quota(d,a,max(0,len(canonical({**w['data'],'files':files}).encode())+28-w['_stored_bytes'])+len(canonical(out).encode())+28)
      if changed:
       n=d.execute('UPDATE projects SET revision=revision+1,data=? WHERE id=? AND actor=? AND revision=?',(self.store.seal({**w['data'],'files':files},'project:'+a+':'+w['id']),w['id'],a,w['revision'])).rowcount;require(n==1,'revision_conflict',409)
      d.execute("UPDATE jobs SET state='completed',result=? WHERE id=?",(self.store.seal(out,'result:'+a+':'+j),j));self.event(d,j,'job.completed',{'status':'completed','revision':out['revision']})
  except asyncio.CancelledError:raise
  except Exception as e:
   code=e.code if isinstance(e,BuilderError) else 'builder_operation_failed'
   with self.store.db() as d:
    n=d.execute("UPDATE jobs SET state='failed',error=? WHERE id=? AND state NOT IN ('stopped','completed')",(code,j)).rowcount
    if n:self.event(d,j,'job.error',{'code':code})
 async def dispatch(self,skill,args,files,j,actor):
  if skill=='builder.deploy':
   from .deployment import submit
   return await submit(self,args,files,j,actor)
  if skill=='builder.workflow':
   from .workflow import build
   return await build(self,args,files,j,actor)
  from .operations import dispatch
  return await dispatch(self,skill,args,files,j,actor)
 def artifact(self,a,p):
  w=self.project(a,p);buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
   for name,b in sorted(w['data']['files'].items()):z.writestr(safe_path(name),base64.b64decode(b))
  return buf.getvalue()
 async def recover(self):
  with self.store.db() as d:
   for r in d.execute("SELECT id FROM jobs WHERE state IN ('queued','running')").fetchall():d.execute("UPDATE jobs SET state='interrupted' WHERE id=?",(r[0],));self.event(d,r[0],'job.error',{'code':'service_restarted'})
 async def close(self):
  tasks=dict(self.tasks)
  with self.store.db() as d:
   for j in tasks:
    r=d.execute('SELECT skill,state FROM jobs WHERE id=?',(j,)).fetchone()
    if r and r['state'] not in TERMINAL:
     code='remote_state_unknown_after_shutdown' if r['skill'] in ('builder.deploy','ssh.upload') and r['state']=='running' else 'service_shutdown'
     d.execute("UPDATE jobs SET state='interrupted',error=? WHERE id=?",(code,j));self.event(d,j,'job.error',{'code':code})
  for j,task in tasks.items():
   if self.sandbox:await self.sandbox.stop(j)
   task.cancel()
  if tasks:await asyncio.gather(*tasks.values(),return_exceptions=True)
