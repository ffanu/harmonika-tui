"""Shared internal local executor. Authority comes from trusted context, never tool input."""
import asyncio
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import signal
import time
from uuid import uuid4
from skill_registry import Registry, SkillError

MAX_INPUT = 1024 * 1024
MAX_OUTPUT = 1024 * 1024

@dataclass(frozen=True)
class ExecutionContext:
    principal_id: str
    project_root: str
    allowed_skills: frozenset
    can_write: bool = False
    interface: str = 'internal'

    def scope(self):
        if not isinstance(self.principal_id,str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,128}',self.principal_id):
            raise SkillError('invalid_context','Invalid principal')
        if type(self.can_write) is not bool or not isinstance(self.allowed_skills,frozenset):
            raise SkillError('invalid_context','Invalid permissions')
        if self.interface not in ('internal','tui'):
            raise SkillError('interface_unavailable','This phase only exposes trusted internal/local interfaces')
        root = Path(self.project_root)
        if not root.is_absolute() or not root.is_dir() or '..' in root.parts:
            raise SkillError('invalid_project','Select an existing absolute project directory')
        for item in (root,*root.parents):
            alias=(str(item),str(item.resolve())) in {('/tmp','/private/tmp'),('/var','/private/var')}
            if item.is_symlink() and not alias:raise SkillError('invalid_project','Symlinked project root refused')
        root=root.resolve()
        if root in (Path('/'),Path.home(),Path.home().parent,Path.home()/'Projects',Path.home()/'Downloads') or any(p.startswith('.') for p in root.parts):
            raise SkillError('invalid_project','Select a specific project, not a broad/private directory')
        identity=root.stat()
        return hashlib.sha256((self.principal_id+'\0'+str(identity.st_dev)+':'+str(identity.st_ino)).encode()).hexdigest(),root

def scoped(root, value):
    if not isinstance(value,str):raise SkillError('invalid_path','Expected an absolute project file path')
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts:raise SkillError('invalid_path','Expected an absolute project file path')
    for item in (path,*path.parents):
        alias=(str(item),str(item.resolve())) in {('/tmp','/private/tmp'),('/var','/private/var')}
        if item.is_symlink() and not alias:raise SkillError('invalid_path','Symlinks refused')
    path=path.resolve()
    blocked={'secrets','credentials','node_modules','venv','library'}
    if not path.is_relative_to(root) or path==root or any(p.startswith('.') or p.casefold() in blocked for p in path.parts):
        raise SkillError('scope_denied','File is outside allowed project scope')
    if path.suffix.lower() in ('.pem','.key','.p12','.pfx','.keystore') or path.name.lower().startswith(('credentials','service-account','service_account')):
        raise SkillError('scope_denied','Credential files are not document inputs')
    return path

class JobStore:
    """Private metadata journal; never persists extracted document text or content specs."""
    def __init__(self, directory):
        directory=Path(directory)
        if not directory.is_absolute():raise SkillError('invalid_state_dir','State directory must be absolute')
        if any(p.is_symlink() for p in (directory,*directory.parents)):
            raise SkillError('invalid_state_dir','State directory must be canonical without symlinks')
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(directory,0o700)
        self.path=directory/'skills.sqlite'
        if self.path.is_symlink():raise SkillError('invalid_state_dir','State database cannot be a symlink')
        fd=os.open(str(self.path),os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(self.path,0o600)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, scope TEXT NOT NULL, key TEXT, fingerprint TEXT NOT NULL, skill TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, cancel INTEGER NOT NULL DEFAULT 0, result TEXT, UNIQUE(scope,key))')
            db.execute('CREATE TABLE IF NOT EXISTS events (job TEXT NOT NULL, sequence INTEGER NOT NULL, timestamp REAL NOT NULL, type TEXT NOT NULL, PRIMARY KEY(job,sequence))')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def begin(self,scope,key,fingerprint,skill):
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='unknown' WHERE status='running' AND created<?",(now-120,))
            stale=[r[0] for r in db.execute("SELECT id FROM jobs WHERE status!='running' AND created<?",(now-86400,))]
            for job in stale:
                db.execute('DELETE FROM events WHERE job=?',(job,));db.execute('DELETE FROM jobs WHERE id=?',(job,))
            if key:
                row=db.execute('SELECT * FROM jobs WHERE scope=? AND key=?',(scope,key)).fetchone()
                if row:
                    if row['fingerprint']!=fingerprint:raise SkillError('idempotency_conflict','Key was already used for a different request')
                    if row['status'] in ('completed','degraded') and row['result']:return dict(row)
                    raise SkillError('in_progress' if row['status']=='running' else 'recovery_required','Existing job must be reviewed before retry')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE scope=? AND status='running'",(scope,)).fetchone()[0]:
                raise SkillError('in_progress','A skill job is already running in this project')
            if db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]>=10000:
                raise SkillError('job_capacity','Local job metadata capacity reached')
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]>=4:
                raise SkillError('busy','Local worker concurrency limit reached')
            job=str(uuid4());db.execute('INSERT INTO jobs(id,scope,key,fingerprint,skill,status,created) VALUES(?,?,?,?,?,?,?)',(job,scope,key,fingerprint,skill,'running',now))
            return {'id':job,'status':'running','result':None}
    def event(self,job,kind):
        with self.connect() as db:
            n=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE job=?',(job,)).fetchone()[0]
            stamp=time.time();db.execute('INSERT INTO events VALUES(?,?,?,?)',(job,n,stamp,kind))
            return {'schema_version':1,'event_id':job+':'+str(n),'job_id':job,'sequence':n,'timestamp':stamp,'type':kind}
    def finish(self,job,status,result=None):
        with self.connect() as db:db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',(status,json.dumps(result) if result else None,job))
    def cancelled(self,job):
        with self.connect() as db:return bool(db.execute('SELECT cancel FROM jobs WHERE id=?',(job,)).fetchone()[0])
    def cancel(self,scope,job):
        with self.connect() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=? AND scope=?',(job,scope)).fetchone()
            if not row:raise SkillError('job_not_found','Job not found in this account/project')
            if row['status']=='running':db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(job,))
            return {'job_id':job,'status':row['status'],'stop_requested':row['status']=='running'}
    def status(self,scope,job,after_sequence=0):
        with self.connect() as db:
            row=db.execute('SELECT id,skill,status,cancel FROM jobs WHERE id=? AND scope=?',(job,scope)).fetchone()
            if not row:raise SkillError('job_not_found','Job not found in this account/project')
            events=[dict(e) for e in db.execute('SELECT sequence,timestamp,type FROM events WHERE job=? AND sequence>? ORDER BY sequence LIMIT 100',(job,after_sequence))]
            return {'job_id':row['id'],'skill_id':row['skill'],'status':row['status'],'stop_requested':bool(row['cancel']),'events':events}

class SkillExecutor:
    def __init__(self,state_dir,registry=None,python=None):
        self.registry=registry or Registry();self.store=JobStore(state_dir)
        runtime=Path(os.environ.get('HARMONIKA_TERMINAL_HOME',str(Path.home()/'.config/harmonika-terminal')))/'document-runtime/bin/python'
        self.python=python or (str(runtime) if runtime.is_file() else sys.executable)
        self.worker=Path(__file__).resolve().with_name('document_skill_worker.py')
    async def capabilities(self):
        try:
            worker=self.worker
            proc=await asyncio.create_subprocess_exec(self.python,'-I',str(worker),'--capabilities',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:raw,_=await asyncio.wait_for(proc.communicate(),10)
            except asyncio.TimeoutError:
                proc.kill();await proc.wait();raise ValueError()
            if proc.returncode or len(raw)>65536:raise ValueError()
            features=json.loads(raw)['formats']
        except (OSError,ValueError,KeyError):features={}
        try:
            seo_worker=Path(__file__).resolve().with_name('seo_skill_worker.py')
            proc=await asyncio.create_subprocess_exec(self.python,'-I',str(seo_worker),'--capabilities',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:raw,_=await asyncio.wait_for(proc.communicate(),10)
            except asyncio.TimeoutError:
                proc.kill();await proc.wait();raise ValueError()
            seo_features=json.loads(raw)['operations'] if proc.returncode==0 and len(raw)<65536 else {}
        except (OSError,ValueError,KeyError):seo_features={}
        try:
            engineering_worker=Path(__file__).resolve().with_name('engineering_skill_worker.py')
            proc=await asyncio.create_subprocess_exec(self.python,'-I',str(engineering_worker),'--capabilities',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            try:raw,_=await asyncio.wait_for(proc.communicate(),10)
            except asyncio.TimeoutError:
                proc.kill();await proc.wait();raise ValueError()
            engineering_features=json.loads(raw)['operations'] if proc.returncode==0 and len(raw)<65536 else {}
        except (OSError,ValueError,KeyError):engineering_features={}
        entries=[]
        for item in self.registry.list():
            if item.get('worker')=='builder':
                item['readiness']={'status':'requires_builder_project','reason':'Use harmonika builder capabilities; generation is not deployment'}
                entries.append(item);continue
            if item.get('worker')=='legal':
                item['readiness']={'status':'requires_authenticated_legal_tenant','reason':'Use harmonika legal capabilities; no legal validity or expert status implied'}
                entries.append(item);continue
            if item.get('worker')=='telco':
                item['readiness']={'status':'requires_authenticated_tenant','reason':'Use harmonika telco capabilities for live readiness; no company/network access implied'}
                entries.append(item);continue
            if item.get('worker')=='coding':
                item['readiness']={'status':'requires_workspace','reason':'Use harmonika coding with an authenticated private workspace'}
                entries.append(item);continue
            action,ext=self.registry.route(item['id'])
            ready=features.get(ext,{}).get('operations',{}).get(action) is True
            if item.get('worker')=='engineering':
                item['readiness']=engineering_features.get(item['id'],{'status':'unavailable','reason':'worker_unavailable'})
                entries.append(item);continue
            if item.get('worker')=='seo':
                item['readiness']=seo_features.get(item['id'],{'status':'unavailable','reason':'worker_unavailable'})
                entries.append(item);continue
            item['readiness']={'status':'available' if ready else 'unavailable',
                               'reason':None if ready else 'worker_or_dependency_unavailable'}
            entries.append(item)
        return {'schema_version':1,'execution_target':'local_worker','skills':entries,
                'public_api':False,'http_transport':False,'mcp_transport':False}
    async def stop(self,job_id,context):
        scope,_=context.scope();return await asyncio.to_thread(self.store.cancel,scope,job_id)
    async def status(self,job_id,context,after_sequence=0):
        scope,_=context.scope()
        if type(after_sequence) is not int or after_sequence<0:raise SkillError('invalid_sequence','Sequence must be nonnegative')
        return await asyncio.to_thread(self.store.status,scope,job_id,after_sequence)
    async def execute(self,skill_id,payload,context,idempotency_key=None,on_event=None):
        scope,root=context.scope()
        if skill_id not in context.allowed_skills:raise SkillError('permission_denied','Skill is not granted to this principal')
        descriptor=self.registry.get(skill_id)
        if descriptor.get('worker')=='builder':raise SkillError('builder_project_required','Use the authenticated Harmonika builder project API')
        if descriptor.get('worker')=='legal':raise SkillError('legal_tenant_required','Use the authenticated legal API, not the local document worker')
        if descriptor.get('worker')=='telco':raise SkillError('tenant_required','Use the authenticated harmonika telco API, not the local worker')
        if descriptor.get('worker')=='coding':raise SkillError('workspace_required','Use the authenticated coding workspace API, not the local document worker')
        self.registry.validate_input(skill_id,payload)
        payload=json.loads(json.dumps(payload,allow_nan=False))  # Snapshot before the first await.
        action,extension=self.registry.route(skill_id)
        writing='write' in descriptor['permissions']['filesystem']
        condition=descriptor.get('write_condition')
        if condition:writing=writing and payload.get(condition['field'])==condition['equals']
        output_field=descriptor.get('output_field') or 'output_path'
        output_path=payload.get(output_field) if writing else None
        if writing and not context.can_write:raise SkillError('permission_denied','Project write access is not granted')
        if writing and not idempotency_key:raise SkillError('idempotency_required','Writes require an idempotency key')
        if idempotency_key is not None and (not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',idempotency_key)):
            raise SkillError('invalid_idempotency_key','Key must contain 1..128 letters/digits/underscore/hyphen')
        for field in tuple(dict.fromkeys(['path','output_path']+descriptor.get('path_fields',[]))):
            if field in payload:
                path=scoped(root,payload[field])
                if extension and path.suffix.lower()!=extension:raise SkillError('invalid_format','File extension does not match skill')
        if descriptor.get('reference_fields'):
            references=payload.get('changes',{}).get('append_files',[])
            if not isinstance(references,list) or len(references)>10:raise SkillError('invalid_input','References must be a bounded array')
            for item in references:
                extra=scoped(root,item)
                if extra.suffix.lower()!='.pdf':raise SkillError('invalid_format','Reference must be a project PDF')
        envelope=json.dumps({'skill_id':skill_id,'project_root':str(root),'principal_id':context.principal_id,'input':payload},allow_nan=False).encode()
        if len(envelope)>MAX_INPUT:raise SkillError('input_too_large','Input exceeds 1 MiB')
        raw=json.dumps({'skill_id':skill_id,'input':payload},sort_keys=True,allow_nan=False).encode()
        if len(raw)>MAX_INPUT:raise SkillError('input_too_large','Input exceeds 1 MiB')
        key=hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key and writing else None
        reservation=asyncio.create_task(asyncio.to_thread(self.store.begin,scope,key,hashlib.sha256(raw).hexdigest(),skill_id))
        try:record=await asyncio.shield(reservation)
        except asyncio.CancelledError:
            record=await reservation
            if record['result']:
                result=json.loads(record['result']);result['replayed']=True;return result
            await asyncio.to_thread(self.store.finish,record['id'],'stopped')
            await asyncio.to_thread(self.store.event,record['id'],'job.stopped')
            return {'schema_version':1,'request_id':str(uuid4()),'job_id':record['id'],'skill_id':skill_id,'status':'stopped','error':{'code':'stopped','message':'Cancelled before worker start'}}
        if record['result']:
            result=json.loads(record['result']);result['replayed']=True;return result
        job=record['id'];request=str(uuid4())
        async def event(kind):
            value=await asyncio.to_thread(self.store.event,job,kind)
            if on_event:
                async def notify():
                    try:
                        response=on_event(value)
                        if hasattr(response,'__await__'):await response
                    except (Exception,asyncio.CancelledError):pass
                try:await asyncio.wait_for(asyncio.create_task(notify()),1)
                except asyncio.TimeoutError:pass
        proc=None
        terminal_committed=False
        try:
            await event('job.queued')
            if writing and output_path and Path(output_path).exists():raise SkillError('output_exists','Choose a new output filename')
            env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','RL_'))}
            worker_name={'seo':'seo_skill_worker.py','engineering':'engineering_skill_worker.py'}.get(descriptor.get('worker'))
            worker=Path(__file__).resolve().with_name(worker_name) if worker_name else self.worker
            proc=await asyncio.create_subprocess_exec(self.python,'-I',str(worker),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=env,start_new_session=True)
            deadline=asyncio.get_running_loop().time()+min(descriptor.get('timeout_seconds',30),90)
            proc.stdin.write(envelope)
            try:await asyncio.wait_for(proc.stdin.drain(),max(.01,deadline-asyncio.get_running_loop().time()))
            except asyncio.TimeoutError:raise SkillError('timeout','Worker stdin timed out') from None
            proc.stdin.close()
            await event('job.started')
            async def bounded(stream,limit):
                chunks=[];size=0
                while True:
                    part=await stream.read(16384)
                    if not part:return b''.join(chunks)
                    size+=len(part)
                    if size>limit:raise SkillError('output_too_large','Worker output exceeded limit')
                    chunks.append(part)
            output=asyncio.gather(bounded(proc.stdout,MAX_OUTPUT),bounded(proc.stderr,8192),proc.wait())
            while not output.done():
                if await asyncio.to_thread(self.store.cancelled,job):raise SkillError('stopped','Stop requested')
                if asyncio.get_running_loop().time()>=deadline:raise SkillError('timeout','Worker timed out')
                await asyncio.wait({output},timeout=.05)
            stdout,stderr,code=await output
            if code:
                try:worker_code=json.loads(stderr).get('code','worker_failed')
                except (ValueError,AttributeError):worker_code='worker_failed'
                if not isinstance(worker_code,str) or not re.fullmatch('[a-z_]{1,64}',worker_code):worker_code='worker_failed'
                raise SkillError(worker_code,'Skill worker failed; inspect input and capability readiness')
            result=json.loads(stdout)
            if not isinstance(result,dict) or result.get('ok') is not True or not isinstance(result.get('result'),dict):raise SkillError('invalid_worker_response','Worker response is invalid')
            state=result['result'].get('status','completed')
            if state not in ('completed','degraded','unavailable','clarification_required'):state='completed'
            response={'schema_version':1,'request_id':request,'job_id':job,'skill_id':skill_id,'status':state,'result':result['result'],'replayed':False}
            cached=response if writing and state in ('completed','degraded') else None
            if cached is not None and descriptor.get('worker')=='seo':
                cached=dict(response,result={'status':state,'artifact_path':output_path,'artifact_exists':bool(output_path and Path(output_path).exists()),'artifact_kind':'directory' if skill_id=='seo.visual' else 'json_baseline'},replay_scope='artifact_metadata')
                reason=result['result'].get('reason')
                if isinstance(reason,str) and re.fullmatch('[a-z0-9_]{1,64}',reason):cached['result']['reason']=reason
            commit=asyncio.create_task(asyncio.to_thread(self.store.finish,job,state,cached))
            try:await asyncio.shield(commit)
            except asyncio.CancelledError:await commit
            terminal_committed=True
            await event('job.'+state)
            return response
        except (Exception,asyncio.CancelledError) as error:
            if terminal_committed:return response
            if proc and proc.returncode is None:
                try:os.killpg(proc.pid,signal.SIGTERM)  # Dedicated session contains only this job and its browser children.
                except ProcessLookupError:pass
                try:await asyncio.wait_for(proc.wait(),5 if descriptor.get('worker')=='seo' else 2)
                except asyncio.TimeoutError:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    await proc.wait()
            if 'output' in locals():
                if not output.done():output.cancel()
                try:await output
                except BaseException:pass
            state='stopped' if isinstance(error,asyncio.CancelledError) or getattr(error,'code',None)=='stopped' else 'failed'
            # A write may have atomically committed just before stop/timeout; never claim rollback.
            if writing and proc is not None and output_path and Path(output_path).exists():state='unknown'
            response={'schema_version':1,'request_id':request,'job_id':job,'skill_id':skill_id,'status':state,'error':{'code':getattr(error,'code','execution_failed'),'message':'Review existing output before retry' if state=='unknown' else 'Skill execution did not complete'}}
            await asyncio.to_thread(self.store.finish,job,state)
            await event('job.'+state)
            return response
