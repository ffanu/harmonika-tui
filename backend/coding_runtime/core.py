"""Tenant-scoped coding workspaces, copy-on-write generations and approval jobs."""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from uuid import uuid4
from .catalog import SKILLS,FILE_SKILLS,EXEC_SKILLS,WRITES,APPROVAL,BASE_SKILLS,PHASE2_SKILLS,FOUNDATION_SKILLS,TOOL_SKILLS,DEBUG_SKILLS,needs_approval,writes_requested,catalog

SCHEMAS={x['id']:x['input_schema'] for x in catalog()}
TERMINAL={'completed','failed','stopped','timeout','expired','interrupted'}

class CodingError(Exception):
    def __init__(self,code,status=400):super().__init__(code);self.code=code;self.status=status

def actor(value):
    if not isinstance(value,str) or not re.fullmatch(r'api-token:[1-9][0-9]{0,18}',value):raise CodingError('invalid_principal',403)
    return value

def identifier(value):
    try:
        if str(__import__('uuid').UUID(str(value)))!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise CodingError('invalid_identifier',404)
    return value

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def validate(skill,args):
    if skill not in SKILLS:raise CodingError('unknown_skill')
    if type(args) is not dict:raise CodingError('invalid_input')
    schema=SCHEMAS[skill]
    if set(args)-set(schema['properties']) or not set(schema['required'])<=set(args):raise CodingError('invalid_input')
    def check(value,spec,depth=0):
        if depth>8:raise CodingError('invalid_input')
        kind=spec['type']
        if kind=='string':
            if type(value) is not str or len(value)>spec.get('maxLength',524288) or len(value)<spec.get('minLength',0) or '\0' in value:raise CodingError('invalid_input')
            if 'enum' in spec and value not in spec['enum']:raise CodingError('invalid_input')
        elif kind=='integer':
            if type(value) is not int or not spec['minimum']<=value<=spec['maximum']:raise CodingError('invalid_input')
        elif kind=='boolean':
            if type(value) is not bool:raise CodingError('invalid_input')
        elif kind=='array':
            if type(value) is not list or not spec.get('minItems',0)<=len(value)<=spec.get('maxItems',32):raise CodingError('invalid_input')
            for item in value:check(item,spec['items'],depth+1)
        else:raise CodingError('invalid_input')
    for k,v in args.items():check(v,schema['properties'][k])
    try:raw=json.dumps(args,ensure_ascii=False,allow_nan=False).encode()
    except (ValueError,UnicodeError):raise CodingError('invalid_input') from None
    if len(raw)>768*1024:raise CodingError('input_too_large',413)
    if skill in ('exec.python','exec.node') and (bool(args.get('code'))==bool(args.get('path'))):raise CodingError('code_or_path_required')
    if skill in ('exec.shell','exec.background') and not args.get('command','').strip():raise CodingError('command_required')
    if skill=='debug.attach':
        if args.get('action','start')=='start':
            if not args.get('path') or args.get('session_id'):raise CodingError('debug_path_required')
        else:
            identifier(args.get('session_id'))
            if 'path' in args or 'timeout_seconds' in args:raise CodingError('invalid_debug_action')

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.path.is_symlink():raise CodingError('invalid_store')
        with self.db() as d:
            d.execute('PRAGMA journal_mode=WAL')
            d.executescript("""
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY, actor TEXT NOT NULL, name TEXT NOT NULL, permissions TEXT NOT NULL, generation TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, actor TEXT NOT NULL, workspace TEXT NOT NULL, skill TEXT NOT NULL, payload TEXT NOT NULL, input_hash TEXT NOT NULL, state TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, expires REAL NOT NULL, idem TEXT, version INTEGER NOT NULL, cancel INTEGER NOT NULL DEFAULT 0, UNIQUE(actor,idem));
CREATE TABLE IF NOT EXISTS events(job TEXT NOT NULL, sequence INTEGER NOT NULL, created REAL NOT NULL, type TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(job,sequence));
CREATE TABLE IF NOT EXISTS turns(actor TEXT NOT NULL, idem TEXT NOT NULL, workspace TEXT NOT NULL, digest TEXT NOT NULL, state TEXT NOT NULL, result TEXT, created REAL NOT NULL, PRIMARY KEY(actor,idem));
CREATE INDEX IF NOT EXISTS idx_jobs_actor_created ON jobs(actor,created);
""")
        os.chmod(self.path,0o600)
    @contextmanager
    def db(self):
        d=sqlite3.connect(self.path,timeout=10);d.row_factory=sqlite3.Row
        try:
            with d:yield d
        finally:d.close()
    async def run(self,fn):
        def work():
            with self.db() as d:return fn(d)
        return await asyncio.to_thread(work)

class Runtime:
    def __init__(self,root,sandbox,files_class,redact=lambda x:x,phase2_enabled=None,debug_adapter=None):
        self.root=Path(root).resolve();self.root.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(self.root,0o700)
        self.phase2_enabled=(os.environ.get('HARMONIKA_CODING_PHASE2_ENABLED','false').lower()=='true') if phase2_enabled is None else bool(phase2_enabled)
        self.debug_adapter=debug_adapter;self.debug=None
        self.store=Store(self.root/'state.sqlite');self.sandbox=sandbox;self.files_class=files_class;self.redact=redact
        self.locks={};self.tasks={};self.protected=set();self.slots=asyncio.Semaphore(2);self.file_slots=asyncio.Semaphore(2);self._caps_lock=asyncio.Lock();self._caps=None;self._caps_at=0
    def enabled_skills(self):return SKILLS if self.phase2_enabled else BASE_SKILLS
    def lock(self,wid):return self.locks.setdefault(wid,asyncio.Lock())
    def directory(self,w,gen=None):
        # Actor directories and generations are server-derived, never caller paths.
        return self.root/'workspaces'/hashlib.sha256(w['actor'].encode()).hexdigest()[:32]/w['id']/(gen or w['generation'])
    def files(self,w):return self.files_class(self.directory(w))
    async def workspace(self,a,wid):
        actor(a);identifier(wid)
        row=await self.store.run(lambda d:d.execute('SELECT * FROM workspaces WHERE id=? AND actor=?',(wid,a)).fetchone())
        if row is None:raise CodingError('workspace_not_found',404)
        return dict(row)
    def view_workspace(self,w):
        return {k:(json.loads(w[k]) if k=='permissions' else w[k]) for k in ('id','name','permissions','version','created')}
    async def create(self,a,name,permissions):
        actor(a)
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise CodingError('invalid_name')
        if type(permissions) is not dict or set(permissions)-{'write','execute','git_commit'} or any(type(x) is not bool for x in permissions.values()):raise CodingError('invalid_permissions')
        perms={x:permissions.get(x,False) for x in ('write','execute','git_commit')}
        if perms['git_commit'] and not perms['write']:raise CodingError('commit_requires_write')
        wid=str(uuid4());gen='g-'+uuid4().hex
        w={'id':wid,'actor':a,'name':name.strip(),'permissions':json.dumps(perms),'generation':gen,'version':0,'created':time.time()}
        directory=self.directory(w);directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        try:
            def insert(d):
                d.execute('BEGIN IMMEDIATE')
                if d.execute('SELECT count(*) FROM workspaces WHERE actor=?',(a,)).fetchone()[0]>=3:raise CodingError('workspace_quota',429)
                if d.execute('SELECT count(*) FROM workspaces').fetchone()[0]>=50:raise CodingError('workspace_capacity',429)
                d.execute('INSERT INTO workspaces VALUES(?,?,?,?,?,?,?)',tuple(w[x] for x in ('id','actor','name','permissions','generation','version','created')))
            await self.store.run(insert)
        except BaseException:
            shutil.rmtree(directory.parent,ignore_errors=True);raise
        return self.view_workspace(w)
    async def list_workspaces(self,a):
        actor(a)
        rows=await self.store.run(lambda d:d.execute('SELECT * FROM workspaces WHERE actor=? ORDER BY created DESC',(a,)).fetchall())
        return [self.view_workspace(dict(w)) for w in rows]
    async def initialize(self):
        # Restarts never replay execution or approvals. Container PID1 watchdogs
        # bound orphan lifetime, while records explicitly require a new request.
        await self.store.run(lambda d:d.execute("UPDATE jobs SET state='interrupted',error='service_restarted' WHERE state IN ('queued','running')"))
        await self.store.run(lambda d:d.execute("UPDATE turns SET state='interrupted' WHERE state='running'"))
        if self.debug:await self.debug.initialize()
        await self.prune()
    async def prune(self):
        def clean(d):
            now=time.time()
            d.execute("UPDATE jobs SET state='interrupted',error='stale_job' WHERE state IN ('queued','running') AND created<?",(now-300,))
            d.execute("UPDATE turns SET state='interrupted' WHERE state='running' AND created<?",(now-300,))
            d.execute("UPDATE jobs SET state='expired' WHERE state='approval_required' AND expires<?",(now,))
            d.execute("DELETE FROM events WHERE job IN (SELECT id FROM jobs WHERE created<? AND state NOT IN ('queued','running','approval_required'))",(now-86400,))
            d.execute("DELETE FROM jobs WHERE created<? AND state NOT IN ('queued','running','approval_required')",(now-86400,))
            d.execute("DELETE FROM turns WHERE created<? AND state!='running'",(now-86400,))
        await self.store.run(clean)
    async def agent_turn(self,a,wid,key,messages,run):
        await self.workspace(a,wid)
        if type(key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key):raise CodingError('idempotency_required')
        digest=fingerprint({'workspace':wid,'messages':messages})
        def reserve(d):
            d.execute('BEGIN IMMEDIATE')
            old=d.execute('SELECT * FROM turns WHERE actor=? AND idem=?',(a,key)).fetchone()
            if old:
                if old['digest']!=digest:raise CodingError('idempotency_conflict',409)
                if old['state'] in ('completed','interrupted'):
                    if old['result']:return json.loads(old['result'])
                    prefix='agent-'+hashlib.sha256((a+'\0'+wid+'\0'+key).encode()).hexdigest()[:32]+'-'
                    jobs=[self.view_job(dict(x)) for x in d.execute('SELECT * FROM jobs WHERE actor=? AND idem LIKE ? ORDER BY created',(a,prefix+'%'))]
                    return {'text':'Pengerjaan sebelumnya terputus. Periksa langkah dan workspace tersimpan; tidak dijalankan ulang otomatis.','workspace_id':wid,'status':'interrupted','steps':jobs}
                raise CodingError('agent_in_progress' if old['state']=='running' else 'agent_interrupted',409)
            if d.execute("SELECT count(*) FROM turns WHERE actor=? AND state='running'",(a,)).fetchone()[0]:raise CodingError('agent_in_progress',409)
            d.execute("INSERT INTO turns VALUES(?,?,?,?,?,?,?)",(a,key,wid,digest,'running',None,time.time()))
        cached=await self.store.run(reserve)
        if cached:
            cached['replayed']=True
            if cached.get('job'):
                latest=self.view_job(await self.job(a,cached['job']['id']));cached['job']=latest
                cached['approval_required']=latest['status']=='approval_required'
                cached['steps']=[({'skill_id':latest['skill_id'],**latest} if item.get('id')==latest['id'] else item) for item in cached.get('steps',[])]
                if latest['status']!='approval_required':
                    cached['status']=latest['status'];cached['text']='Status job terbaru: '+latest['status']+'. Periksa hasil job sebelum melanjutkan.'
            return cached
        try:
            result=await run()
            await self.store.run(lambda d:d.execute("UPDATE turns SET state='completed',result=? WHERE actor=? AND idem=?",(json.dumps(result,ensure_ascii=False),a,key)))
            return result
        except BaseException:
            await self.store.run(lambda d:d.execute("UPDATE turns SET state='interrupted' WHERE actor=? AND idem=?",(a,key)))
            raise
    async def sandbox_capabilities(self):
        async with self._caps_lock:
            if self._caps is None or time.monotonic()-self._caps_at>5:
                self._caps=await self.sandbox.capabilities();self._caps_at=time.monotonic()
            return dict(self._caps)
    async def capabilities(self):
        c=await self.sandbox_capabilities()
        phase2_ready=self.phase2_enabled and bool(c.get('phase2_ready',False))
        ready=bool(c.get('available',c.get('ready',False)))
        debug_ready=phase2_ready and self.debug is not None and bool((await self.debug.capabilities()).get('ready'))
        entries=[]
        for x in catalog():
            if x['id'] not in self.enabled_skills():continue
            available=(x['id'] in FILE_SKILLS or ready) and (x['id'] not in PHASE2_SKILLS or phase2_ready)
            if x['id'] in DEBUG_SKILLS:available=available and debug_ready
            entries.append(dict(x,readiness={'status':'available' if available else 'unavailable','reason':None if available else 'phase2_dependency_unavailable' if x['id'] in PHASE2_SKILLS else 'sandbox_unavailable'}))
        return {'schema_version':1,'ready':ready,'skills':entries,'sandbox':c,'features':{'phase2':phase2_ready,'debug_sessions':debug_ready},
          'limits':{'workspace_bytes':16*1024*1024,'file_bytes':512*1024,'files':256,'workspaces_per_token':3,'executions_per_hour':30,'operations_per_minute':120,'active_per_token':1,'active_global':2,'job_seconds_max':120,'agent_steps':8,'debug_session_seconds_max':300,'debug_idle_seconds':60,'debug_active_global':1},
          'offline_packages':{'python':{'humanize':['4.12.2']},'node':{'decimal.js':['10.5.0']}} if phase2_ready else {},
          'permissions':{'workspace_only':True,'execution_approval_required':True,'git_readonly_approval_required':False,'git_commit_approval_required':True,'network':False,'host_shell':False,'host_filesystem':False}}
    async def job(self,a,jid):
        actor(a);identifier(jid)
        row=await self.store.run(lambda d:d.execute('SELECT * FROM jobs WHERE id=? AND actor=?',(jid,a)).fetchone())
        if row is None:raise CodingError('job_not_found',404)
        return dict(row)
    def view_job(self,j):
        result=json.loads(j['result']) if j.get('result') else None
        value={'id':j['id'],'workspace_id':j['workspace'],'skill_id':j['skill'],'status':j['state'],'input_hash':j['input_hash'],'created_at':j['created'],'approval_expires_at':j['expires'] if j['state']=='approval_required' else None,
          'requires_approval':j['state']=='approval_required','result':result,'error':j.get('error'),'stop_requested':bool(j['cancel'])}
        if value['requires_approval']:
            raw=self.redact(j['payload'])
            try:value['preview']=json.loads(raw)
            except ValueError:value['preview']={'text':raw,'redacted':True}
        return value
    async def events(self,a,jid,after=0):
        await self.job(a,jid)
        if type(after) is not int or after<0:raise CodingError('invalid_sequence')
        rows=await self.store.run(lambda d:d.execute('SELECT * FROM events WHERE job=? AND sequence>? ORDER BY sequence LIMIT 128',(jid,after)).fetchall())
        return [{'event_id':jid+':'+str(x['sequence']),'job_id':jid,'sequence':x['sequence'],'timestamp':x['created'],'type':x['type'],'data':json.loads(x['data'])} for x in rows]
    async def event(self,jid,kind,data):
        # Only sanitized, bounded output is persisted. No raw prompt, key or host env.
        raw=self.redact(json.dumps(data,ensure_ascii=False,default=str))
        # redact may remove quote delimiters; preserve a valid event representation.
        try:clean=json.loads(raw)
        except ValueError:clean={'text':self.redact(str(data))[:4096],'redacted':True}
        encoded=json.dumps(clean,ensure_ascii=False)
        if len(encoded.encode())>8192:encoded=json.dumps({'text':self.redact(str(data))[:2048],'truncated':True})
        def add(d):
            d.execute('BEGIN IMMEDIATE');seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM events WHERE job=?',(jid,)).fetchone()[0]
            if seq>256 or (kind=='process.output' and seq>=250):return False
            d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(jid,seq,time.time(),kind,encoded));return True
        return await self.store.run(add)
    async def submit(self,a,wid,skill,args,key):
        actor(a);validate(skill,args)
        if skill not in self.enabled_skills():raise CodingError('skill_unavailable',503)
        if skill in PHASE2_SKILLS and not (await self.sandbox_capabilities()).get('phase2_ready',False):raise CodingError('phase2_dependency_unavailable',503)
        if key is not None and (type(key) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key)):raise CodingError('invalid_idempotency_key')
        if (skill in WRITES or skill in EXEC_SKILLS) and not key:raise CodingError('idempotency_required')
        await self.workspace(a,wid)
        async with self.lock(wid):
            w=await self.workspace(a,wid);perms=json.loads(w['permissions'])
            if writes_requested(skill,args) and not perms['write']:raise CodingError('write_not_allowed',403)
            if skill in EXEC_SKILLS and not perms['execute']:raise CodingError('execution_not_allowed',403)
            if skill=='git.commit' and not perms['git_commit']:raise CodingError('commit_not_allowed',403)
            digest=fingerprint({'workspace':wid,'skill':skill,'input':args})
            if key:
                previous=await self.store.run(lambda d:d.execute('SELECT * FROM jobs WHERE actor=? AND idem=?',(a,key)).fetchone())
                if previous:
                    if previous['input_hash']!=digest:raise CodingError('idempotency_conflict',409)
                    out=self.view_job(dict(previous));out['replayed']=True;return out
            if self.debug and not (skill=='debug.attach' and args.get('action','start')!='start') and skill not in ('debug.breakpoint','fs.read','code.read','fs.list','fs.tree','fs.glob','code.search','code.grep','code.diff') and await self.debug.workspace_busy(a,wid):raise CodingError('debug_workspace_busy',409)
            if skill in EXEC_SKILLS:
                caps=await self.sandbox_capabilities()
                if not caps.get('available',caps.get('ready',False)):raise CodingError('sandbox_unavailable',503)
            jid=str(uuid4());now=time.time();state='approval_required' if needs_approval(skill,args) else 'queued'
            def reserve(d):
                d.execute('BEGIN IMMEDIATE')
                if d.execute('SELECT count(*) FROM jobs WHERE actor=? AND created>?',(a,now-60)).fetchone()[0]>=120:raise CodingError('skill_rate_limit',429)
                if skill in EXEC_SKILLS and d.execute("SELECT count(*) FROM jobs WHERE actor=? AND created>? AND skill NOT IN ("+','.join('?' for _ in FILE_SKILLS)+")",(a,now-3600,*sorted(FILE_SKILLS))).fetchone()[0]>=30:raise CodingError('execution_quota',429)
                if d.execute('SELECT count(*) FROM jobs').fetchone()[0]>=5000:raise CodingError('job_capacity',429)
                if d.execute("SELECT count(*) FROM jobs WHERE actor=? AND state IN ('queued','running')",(a,)).fetchone()[0]:raise CodingError('job_in_progress',409)
                if state=='queued' and d.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=4:raise CodingError('queue_full',429)
                if state=='approval_required' and d.execute("SELECT count(*) FROM jobs WHERE actor=? AND state='approval_required' AND expires>?",(a,now)).fetchone()[0]>=3:raise CodingError('approval_capacity',429)
                d.execute('INSERT INTO jobs(id,actor,workspace,skill,payload,input_hash,state,created,expires,idem,version) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(jid,a,wid,skill,json.dumps(args,ensure_ascii=False),digest,state,now,now+300,key,w['version']))
            try:await self.store.run(reserve)
            except sqlite3.IntegrityError:raise CodingError('idempotency_conflict',409) from None
            await self.event(jid,'approval.required' if state=='approval_required' else 'job.queued',{'skill_id':skill,'input_hash':digest,'workspace_version':w['version']})
        if state=='approval_required':
            out=self.view_job(await self.job(a,jid));return out
        if skill in FILE_SKILLS:
            try:
                async with self.file_slots:
                    result=await self._run_file(a,jid)
            except asyncio.CancelledError:
                await self.finish(jid,'interrupted',error='request_interrupted');raise
            out=self.view_job(await self.job(a,jid));out['result']=result;return out
        else:self.start(a,jid)
        return self.view_job(await self.job(a,jid))
    async def approve(self,a,jid,digest):
        j=await self.job(a,jid)
        async with self.lock(j['workspace']):
            j=await self.job(a,jid);w=await self.workspace(a,j['workspace'])
            if j['input_hash']!=digest:raise CodingError('approval_hash_mismatch',409)
            if j['state']!='approval_required':return self.view_job(j)
            if time.time()>j['expires']:
                await self.store.run(lambda d:d.execute("UPDATE jobs SET state='expired' WHERE id=?",(jid,)));raise CodingError('approval_expired',409)
            if w['version']!=j['version']:raise CodingError('stale_approval',409)
            if self.debug and j['skill']!='debug.breakpoint' and await self.debug.workspace_busy(a,j['workspace']):raise CodingError('debug_workspace_busy',409)
            def queue(d):
                d.execute('BEGIN IMMEDIATE')
                if d.execute("SELECT count(*) FROM jobs WHERE actor=? AND state IN ('queued','running')",(a,)).fetchone()[0]:raise CodingError('job_in_progress',409)
                if d.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]>=4:raise CodingError('queue_full',429)
                d.execute("UPDATE jobs SET state='queued' WHERE id=? AND state='approval_required'",(jid,))
            await self.store.run(queue)
        await self.event(jid,'approval.accepted',{'input_hash':digest});self.start(a,jid)
        return self.view_job(await self.job(a,jid))
    def start(self,a,jid):
        if jid in self.tasks:return
        task=asyncio.create_task(self._run_exec(a,jid));self.tasks[jid]=task
        task.add_done_callback(lambda _:self.tasks.pop(jid,None))
    async def finish(self,jid,state,result=None,error=None,only_states=None):
        # Terminal state and its event commit together; SSE cannot observe one
        # without the other. Conditional transitions preserve completed output.
        stamp=time.time();encoded=json.dumps({'status':state,'error':error})
        def update(d):
            d.execute('BEGIN IMMEDIATE')
            if only_states:
                old=d.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
                if old is None or old['state'] not in only_states:return
            d.execute('UPDATE jobs SET state=?,result=?,error=? WHERE id=?',(state,json.dumps(result,ensure_ascii=False) if result is not None else None,error,jid))
            seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM events WHERE job=?',(jid,)).fetchone()[0]
            if seq<=256:d.execute('INSERT INTO events VALUES(?,?,?,?,?)',(jid,seq,stamp,'job.'+state,encoded))
        await self.store.run(update)
    async def publish(self,w,snapshot,operation=None,args=None):
        task=asyncio.create_task(self._publish_atomic(w,snapshot,operation,args))
        self.protected.add(task);task.add_done_callback(self.protected.discard)
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise
    async def _publish_atomic(self,w,snapshot,operation=None,args=None):
        gen='g-'+uuid4().hex;path=self.directory(w,gen);path.mkdir(mode=0o700,parents=True,exist_ok=False)
        try:
            fs=self.files_class(path);await asyncio.to_thread(fs.replace_snapshot,snapshot)
            result=await asyncio.to_thread(fs.execute,operation,args) if operation else None
            def swap(d):
                cur=d.execute('UPDATE workspaces SET generation=?,version=version+1 WHERE id=? AND actor=? AND version=?',(gen,w['id'],w['actor'],w['version']))
                if cur.rowcount!=1:raise CodingError('workspace_changed',409)
            await self.store.run(swap)
        except BaseException:
            shutil.rmtree(path,ignore_errors=True);raise
        # Old data is only a service-owned generation with validated regular files.
        await asyncio.to_thread(shutil.rmtree,self.directory(w),True)
        return result
    async def _run_file(self,a,jid):
        j=await self.job(a,jid)
        async with self.lock(j['workspace']):
            try:
                j=await self.job(a,jid)
                if j['cancel'] or j['state']!='queued':raise CodingError('job_stopped',409)
                w=await self.workspace(a,j['workspace']);args=json.loads(j['payload'])
                if j['skill'] in WRITES and self.debug and await self.debug.workspace_busy(a,j['workspace']):raise CodingError('debug_workspace_busy',409)
                await self.store.run(lambda d:d.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,)))
                if j['skill'] in WRITES:
                    snap=await asyncio.to_thread(self.files(w).snapshot)
                    result=await self.publish(w,snap,j['skill'],args)
                else:result=await asyncio.to_thread(self.files(w).execute,j['skill'],args)
                # Read contents are returned once and not cached in job SQLite.
                await self.finish(jid,'completed',result if j['skill'] in WRITES else {'read_result_not_persisted':True})
                return result
            except Exception as exc:
                code=getattr(exc,'code','file_operation_failed')
                if not isinstance(code,str) or not re.fullmatch('[a-z_]{1,64}',code):code='file_operation_failed'
                status=409 if code in ('hash_mismatch','conflict','file_exists','already_exists','ambiguous_match') else 404 if code in ('not_found','file_not_found') else getattr(exc,'status',400)
                await self.finish(jid,'failed',error=code)
                raise CodingError(code,status) from None
    async def invoke_result(self,a,wid,skill,args,key):
        return await self.submit(a,wid,skill,args,key)
    async def _run_exec(self,a,jid):
        try:
            j=await self.job(a,jid)
            if j['skill']=='debug.breakpoint' or (j['skill']=='debug.attach' and json.loads(j['payload']).get('action','start')!='start'):
                await self._run_debug_control(a,j);return
            async with self.slots:
                j=await self.job(a,jid)
                async with self.lock(j['workspace']):
                    j=await self.job(a,jid)
                    if j['cancel'] or j['state']!='queued':return
                    w=await self.workspace(a,j['workspace'])
                    if self.debug and j['skill']!='debug.attach' and await self.debug.workspace_busy(a,j['workspace']):
                        await self.finish(jid,'failed',error='debug_workspace_busy');return
                    if w['version']!=j['version']:await self.finish(jid,'failed',error='stale_job');return
                    await self.store.run(lambda d:d.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,)));await self.event(jid,'job.started',{'skill_id':j['skill']})
                    snap=await asyncio.to_thread(self.files(w).snapshot)
                    output_dropped=False;event_limit_reached=False
                    async def output(*parts):
                        nonlocal output_dropped,event_limit_reached
                        if event_limit_reached:return
                        if len(parts)==1 and isinstance(parts[0],dict):
                            stream=parts[0].get('stream','stdout');chunk=parts[0].get('data','')
                        else:stream,chunk=parts
                        if isinstance(chunk,bytes):chunk=chunk.decode('utf-8','replace')
                        text=self.redact(str(chunk))
                        for offset in range(0,len(text),1000):
                            try:
                                stored=await self.event(jid,'process.output',{'stream':stream,'text':text[offset:offset+1000]})
                                if stored is False:
                                    output_dropped=True;event_limit_reached=True;break
                            except sqlite3.OperationalError:output_dropped=True
                    if j['skill']=='debug.attach':
                        if not self.debug:raise CodingError('debug_unavailable',503)
                        result=await asyncio.wait_for(self.debug.start(a,w,snap,json.loads(j['payload']),jid),145)
                    else:
                        result=await asyncio.wait_for(self.sandbox.run(snap,'exec.shell' if j['skill']=='exec.background' else j['skill'],json.loads(j['payload']),jid,output,deadline_seconds=140),150)
                    if not isinstance(result,dict):raise CodingError('invalid_sandbox_result',502)
                    if j['skill'] in TOOL_SKILLS:
                        try:
                            structured=json.loads(result.get('stdout',''))
                            if type(structured) is not dict:
                                raise ValueError('envelope required')
                            if type(structured) is dict:
                                detail=structured.get('result')
                                if structured.get('ok') is False:
                                    result['status']='failed';result['error']=(structured.get('error') or {}).get('code','tool_failed')
                                elif isinstance(detail,dict) and (detail.get('passed') is False or detail.get('clean') is False or detail.get('exit_code',0)!=0):
                                    result['status']='failed';result['error']='quality_check_failed'
                                    result['exit_code']=detail.get('exit_code') or 1
                                cleaned=self.redact(json.dumps(structured,ensure_ascii=False))
                                try:result['tool_result']=json.loads(cleaned) if len(cleaned.encode())<=65536 else {'truncated':True,'text':cleaned[:16000]}
                                except ValueError:result['tool_result']={'redacted':True,'text':cleaned[:16000]}
                        except (ValueError,TypeError):
                            result['status']='failed';result['error']='invalid_tool_output'
                    exported=result.pop('snapshot',None);updated=False
                    read_only=j['skill'] in ('git.status','git.diff','lint.run','type.check','pkg.list') or (j['skill']=='refactor.dead_code' and not json.loads(j['payload']).get('apply',False))
                    atomic_tool=j['skill'] in ('code.format','format.run','build.run','pkg.install','code.rename','refactor.rename','refactor.extract','refactor.dead_code')
                    if atomic_tool and result.get('status')!='completed' and exported!=snap:
                        result['changes_discarded']=True;read_only=True
                    if exported is not None and not read_only and json.loads(w['permissions'])['write'] and exported!=snap:
                        await self.publish(w,exported);updated=True
                    if result.get('status')=='completed' and not read_only and json.loads(w['permissions'])['write'] and exported is None:
                        result['status']='failed';result['error']='workspace_export_unavailable'
                    for key in ('stdout','stderr'):
                        if key in result:
                            full=self.redact(str(result[key]));result[key]=full[:65536]
                            if len(full)>65536:result[key+'_truncated']=True
                    result['workspace_updated']=updated;result['code_executed']=True if result.get('exit_code') is not None else None
                    if output_dropped:result['event_output_dropped']=True
                    if exported is not None and exported!=snap and not updated:result['changes_discarded']=True
                    final=await self.job(a,jid);state='stopped' if final['cancel'] else result.get('status','failed')
                    if state not in ('completed','failed','timeout','stopped'):state='failed'
                    await self.finish(jid,state,result)
        except asyncio.TimeoutError:
            await self.sandbox.stop(jid);await self.finish(jid,'timeout',error='execution_timeout')
        except asyncio.CancelledError:
            await self.sandbox.stop(jid);await self.finish(jid,'stopped',error='cancelled');raise
        except CodingError as e:
            await self.sandbox.stop(jid);await self.finish(jid,'failed',error=e.code)
        except Exception:
            await self.sandbox.stop(jid);await self.finish(jid,'failed',error='execution_failed')
    async def _run_debug_control(self,a,j):
        if not self.debug:raise CodingError('debug_unavailable',503)
        j=await self.job(a,j['id'])
        if j['cancel'] or j['state']!='queued':return
        args=json.loads(j['payload']);w=await self.workspace(a,j['workspace'])
        if w['version']!=j['version']:await self.finish(j['id'],'failed',error='stale_job');return
        await self.store.run(lambda d:d.execute("UPDATE jobs SET state='running' WHERE id=?",(j['id'],)))
        session=await self.debug.get(a,args['session_id'])
        if session['workspace']!=j['workspace']:raise CodingError('debug_session_not_found',404)
        if j['skill']=='debug.breakpoint':
            result=await self.debug.breakpoint(a,j['workspace'],args['session_id'],args['path'],args['lines'])
        elif args['action']=='stop':result=await self.debug.stop(a,args['session_id'])
        else:result=await self.debug.action_once(a,args['session_id'],args['action'],'job-'+j['id'])
        await self.finish(j['id'],'completed',{'exit_code':0,'code_executed':args.get('action') in ('continue','next'),'tool_result':result,'workspace_updated':bool((result.get('result') or {}).get('workspace_updated'))})
    async def stop(self,a,jid):
        j=await self.job(a,jid)
        if j['state'] in TERMINAL:return self.view_job(j)
        await self.store.run(lambda d:d.execute("UPDATE jobs SET cancel=1 WHERE id=? AND actor=? AND state IN ('approval_required','queued','running')",(jid,a)))
        await self.sandbox.stop(jid)
        await self.finish(jid,'stopped',only_states=('approval_required','queued'))
        return self.view_job(await self.job(a,jid))
    async def close(self):
        if self.protected:await asyncio.gather(*list(self.protected),return_exceptions=True)
        for jid in list(self.tasks):await self.sandbox.stop(jid)
        if self.tasks:
            done,pending=await asyncio.wait(list(self.tasks.values()),timeout=8)
            for task in pending:task.cancel()
            if pending:await asyncio.gather(*pending,return_exceptions=True)
        if self.debug:await self.debug.close()
