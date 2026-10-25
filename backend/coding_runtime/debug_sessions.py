"""Durable actor-owned debugger sessions; DAP/container authority stays server-side."""
import asyncio
import hashlib
import json
import re
import time
from uuid import uuid4
from .core import CodingError,actor,identifier

ACTIVE=('starting','paused','running','terminated','stopping')
FINAL=('closed','expired','interrupted','failed')
class DebugSessions:
    def __init__(self,runtime,adapter,idle_seconds=60,max_seconds=300):
        self.rt=runtime;self.adapter=adapter;self.idle_seconds=min(60,max(1,idle_seconds));self.max_seconds=min(300,max(2,max_seconds))
        self._locks={};self._leases=set();self._reaper=None;self._caps=None;self._caps_at=0;self._caps_lock=asyncio.Lock()
        with self.rt.store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS debug_sessions(id TEXT PRIMARY KEY,actor TEXT NOT NULL,workspace TEXT NOT NULL,job TEXT NOT NULL,version INTEGER NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,touched REAL NOT NULL,expires REAL NOT NULL,result TEXT);
CREATE TABLE IF NOT EXISTS debug_events(session TEXT NOT NULL,sequence INTEGER NOT NULL,created REAL NOT NULL,type TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(session,sequence));
CREATE TABLE IF NOT EXISTS debug_actions(session TEXT NOT NULL,idem TEXT NOT NULL,digest TEXT NOT NULL,state TEXT NOT NULL,result TEXT,created REAL NOT NULL,PRIMARY KEY(session,idem));
CREATE INDEX IF NOT EXISTS idx_debug_actor ON debug_sessions(actor,created);''')
    async def capabilities(self):
        async with self._caps_lock:
            if self._caps is None or time.monotonic()-self._caps_at>30:
                try:
                    self._caps=await asyncio.wait_for(self.adapter.capabilities(),15) if hasattr(self.adapter,'capabilities') else {'ready':True}
                except Exception:self._caps={'ready':False,'error':'debug_dependency_unavailable'}
                self._caps_at=time.monotonic()
            return dict(self._caps)
    @staticmethod
    def require_result(result):
        if type(result) is not dict:raise CodingError('invalid_debug_result',502)
        if result.get('ok') is False or result.get('status')=='error':raise CodingError('debug_operation_failed',503)
        return result
    def lock(self,sid):return self._locks.setdefault(sid,asyncio.Lock())
    async def initialize(self):
        if self._reaper:
            self._reaper.cancel();await asyncio.gather(self._reaper,return_exceptions=True)
        rows=await self.rt.store.run(lambda d:d.execute('SELECT id FROM debug_sessions WHERE state IN (?,?,?,?,?)',ACTIVE).fetchall())
        # A new service process never reattaches a user process or silently resumes
        # code. UUID-owned resource cleanup is optional; hard watchdog bounds life.
        for row in rows:
            if hasattr(self.adapter,'cleanup_names'):
                try:await self.adapter.cleanup_names(row['id'])
                except Exception:pass
        await self.rt.store.run(lambda d:d.execute("UPDATE debug_sessions SET state='interrupted',result=? WHERE state IN (?,?,?,?,?)",(json.dumps({'error':'service_restarted','changed_state_unknown':True}),*ACTIVE)))
        self._reaper=asyncio.create_task(self._watch())
    async def get(self,a,sid):
        actor(a);identifier(sid)
        row=await self.rt.store.run(lambda d:d.execute('SELECT * FROM debug_sessions WHERE id=? AND actor=?',(sid,a)).fetchone())
        if row is None:raise CodingError('debug_session_not_found',404)
        return dict(row)
    def view(self,s):
        return {'id':s['id'],'workspace_id':s['workspace'],'job_id':s['job'],'state':s['state'],
                'created_at':s['created'],'last_activity_at':s['touched'],'expires_at':s['expires'],
                'idle_timeout_seconds':self.idle_seconds,'result':json.loads(s['result']) if s['result'] else None}
    async def workspace_busy(self,a,wid):
        return bool(await self.rt.store.run(lambda d:d.execute('SELECT id FROM debug_sessions WHERE actor=? AND workspace=? AND state IN (?,?,?,?,?)',(a,wid,*ACTIVE)).fetchone()))
    def clean(self,value,limit=65536):
        raw=self.rt.redact(json.dumps(value,ensure_ascii=False,default=str))
        if len(raw.encode())>limit:return {'truncated':True,'text':raw[:min(2000,limit//4)]}
        try:return json.loads(raw)
        except ValueError:return {'redacted':True,'text':self.rt.redact(str(value))[:2000]}
    async def event(self,sid,event):
        name=event.get('event',event.get('type','event')) if type(event) is dict else 'event'
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_.]{1,64}',name):name='event'
        data=self.clean(event,4096)
        state={'stopped':'paused','terminated':'terminated','exited':'terminated','continued':'running'}.get(name.split('.')[-1])
        if state:await self.rt.store.run(lambda d:d.execute('UPDATE debug_sessions SET state=? WHERE id=? AND state IN (?,?,?,?,?)',(state,sid,*ACTIVE)))
        def insert(d):
            d.execute('BEGIN IMMEDIATE');seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM debug_events WHERE session=?',(sid,)).fetchone()[0]
            if seq>504:return
            d.execute('INSERT INTO debug_events VALUES(?,?,?,?,?)',(sid,seq,time.time(),'debug.'+name,json.dumps(data,ensure_ascii=False)))
        await self.rt.store.run(insert)
    async def finish(self,sid,state,result):
        stamp=time.time();clean=self.clean(result)
        def update(d):
            d.execute('BEGIN IMMEDIATE')
            d.execute('UPDATE debug_sessions SET state=?,result=?,touched=? WHERE id=?',(state,json.dumps(clean),stamp,sid))
            seq=d.execute('SELECT coalesce(max(sequence),0)+1 FROM debug_events WHERE session=?',(sid,)).fetchone()[0]
            if seq<=512:d.execute('INSERT INTO debug_events VALUES(?,?,?,?,?)',(sid,seq,stamp,'debug.'+state,json.dumps({'state':state,'workspace_updated':bool(clean.get('workspace_updated'))})))
        await self.rt.store.run(update)
    async def events(self,a,sid,after=0):
        await self.get(a,sid)
        if type(after) is not int or after<0:raise CodingError('invalid_sequence')
        rows=await self.rt.store.run(lambda d:d.execute('SELECT * FROM debug_events WHERE session=? AND sequence>? ORDER BY sequence LIMIT 128',(sid,after)).fetchall())
        return [{'event_id':sid+':'+str(x['sequence']),'session_id':sid,'sequence':x['sequence'],'timestamp':x['created'],'type':x['type'],'data':json.loads(x['data'])} for x in rows]
    def release(self,sid):
        if sid in self._leases:self._leases.remove(sid);self.rt.slots.release()
    async def start(self,a,w,snapshot,args,job_id):
        sid=str(uuid4());now=time.time();expires=now+min(self.max_seconds,args.get('timeout_seconds',self.max_seconds))
        # Existing FileStore validates actual source ownership before a process
        # is created; .py code remains data until the approved sandbox launch.
        if not args.get('path','').endswith('.py'):raise CodingError('debug_python_required')
        await asyncio.to_thread(self.rt.files(w).execute,'code.read',{'path':args['path']})
        def reserve(d):
            d.execute('BEGIN IMMEDIATE')
            if d.execute('SELECT count(*) FROM debug_sessions WHERE state IN (?,?,?,?,?)',ACTIVE).fetchone()[0]:raise CodingError('debug_capacity',429)
            if d.execute('SELECT count(*) FROM debug_sessions').fetchone()[0]>=500:raise CodingError('debug_metadata_capacity',429)
            d.execute('INSERT INTO debug_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,a,w['id'],job_id,w['version'],'starting',now,now,expires,None))
        await self.rt.store.run(reserve)
        async with self.lock(sid):
            try:
                # Retain one execution slot for the persistent debug process.
                await asyncio.wait_for(self.rt.slots.acquire(),120);self._leases.add(sid)
                async def emit(value):await self.event(sid,value)
                launch_args={k:v for k,v in args.items() if k!='action'}
                result=await asyncio.wait_for(self.adapter.start(snapshot,launch_args,sid,emit),45)
                self.require_result(result)
                state=result.get('state','paused')
                if state not in ACTIVE:raise CodingError('debug_start_failed',503)
                clean=self.clean(result);stamp=time.time()
                await self.rt.store.run(lambda d:d.execute('UPDATE debug_sessions SET state=?,touched=?,result=? WHERE id=?',(state,stamp,json.dumps(clean),sid)))
                await self.event(sid,{'type':'ready','state':state})
                return {'status':'completed','exit_code':0,'stdout':'','stderr':'','snapshot':snapshot,'debug_session':self.view(await self.get(a,sid)),'code_executed':True}
            except BaseException:
                try:await asyncio.wait_for(self.adapter.stop(sid),12)
                except BaseException:pass
                self.release(sid)
                await self.finish(sid,'failed',{'error':'debug_start_failed','changed_state_unknown':True})
                raise
    async def breakpoint(self,a,wid,sid,path,lines):
        s=await self.get(a,sid)
        if s['workspace']!=wid:raise CodingError('debug_session_not_found',404)
        w=await self.rt.workspace(a,wid)
        await asyncio.to_thread(self.rt.files(w).execute,'code.read',{'path':path})
        if type(lines) is not list or len(lines)>32 or any(type(x) is not int or not 1<=x<=10000 for x in lines):raise CodingError('invalid_breakpoints')
        async with self.lock(sid):
            s=await self.get(a,sid)
            if s['state'] not in ACTIVE or s['state']=='starting':raise CodingError('debug_not_active',409)
            result=self.clean(self.require_result(await asyncio.wait_for(self.adapter.set_breakpoints(sid,path,lines),15)))
            await self.rt.store.run(lambda d:d.execute('UPDATE debug_sessions SET touched=? WHERE id=?',(time.time(),sid)))
            await self.event(sid,{'type':'breakpoints','data':result})
            return result
    async def action(self,a,sid,action):
        if action not in ('status','continue','next','inspect'):raise CodingError('invalid_debug_action')
        s=await self.get(a,sid)
        if action=='status' and s['state'] in FINAL:return self.view(s)
        async with self.lock(sid):
            s=await self.get(a,sid)
            if s['state'] not in ACTIVE or s['state'] in ('starting','stopping'):raise CodingError('debug_not_active',409)
            result=await asyncio.wait_for(self.adapter.action(sid,action),20)
            self.require_result(result)
            state=result.get('state',s['state'])
            if state not in ACTIVE:state=s['state']
            clean=self.clean(result)
            await self.rt.store.run(lambda d:d.execute('UPDATE debug_sessions SET state=?,touched=?,result=? WHERE id=?',(state,time.time(),json.dumps(clean),sid)))
            await self.event(sid,{'type':action,'data':clean})
            return self.view(await self.get(a,sid))
    async def action_once(self,a,sid,action,key):
        await self.get(a,sid)
        if action=='status':return await self.action(a,sid,action)
        if action not in ('continue','next','inspect'):raise CodingError('invalid_debug_action')
        if type(key) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}',key):raise CodingError('idempotency_required')
        digest=hashlib.sha256(action.encode()).hexdigest()
        def reserve(d):
            d.execute('BEGIN IMMEDIATE')
            old=d.execute('SELECT * FROM debug_actions WHERE session=? AND idem=?',(sid,key)).fetchone()
            if old:
                if old['digest']!=digest:raise CodingError('idempotency_conflict',409)
                if old['state']=='completed':return json.loads(old['result'])
                raise CodingError('debug_action_'+old['state'],409)
            if d.execute('SELECT count(*) FROM debug_actions WHERE session=?',(sid,)).fetchone()[0]>=256:raise CodingError('debug_action_limit',429)
            d.execute('INSERT INTO debug_actions VALUES(?,?,?,?,?,?)',(sid,key,digest,'running',None,time.time()))
        cached=await self.rt.store.run(reserve)
        if cached:return {**cached,'replayed':True}
        try:
            result=await self.action(a,sid,action)
            await self.rt.store.run(lambda d:d.execute("UPDATE debug_actions SET state='completed',result=? WHERE session=? AND idem=?",(json.dumps(result),sid,key)))
            return result
        except BaseException:
            await self.rt.store.run(lambda d:d.execute("UPDATE debug_actions SET state='interrupted' WHERE session=? AND idem=?",(sid,key)))
            raise
    async def stop(self,a,sid,reason='closed'):
        await self.get(a,sid)
        task=asyncio.create_task(self._stop(a,sid,reason))
        self.rt.protected.add(task);task.add_done_callback(self.rt.protected.discard)
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task);raise
    async def _stop(self,a,sid,reason='closed'):

        s=await self.get(a,sid)
        async with self.lock(sid):
            s=await self.get(a,sid)
            if s['state'] in FINAL:return self.view(s)
            if reason=='expired' and time.time()<s['expires'] and (s['state']=='starting' or time.time()-s['touched']<self.idle_seconds):return self.view(s)
            await self.rt.store.run(lambda d:d.execute("UPDATE debug_sessions SET state='stopping' WHERE id=?",(sid,)))
            try:
                result=await asyncio.wait_for(self.adapter.stop(sid),20)
                if type(result) is not dict:result={'changed_state_unknown':True}
                exported=result.pop('snapshot',None);updated=False
                async with self.rt.lock(s['workspace']):
                    w=await self.rt.workspace(a,s['workspace'])
                    if exported is not None:
                        if w['version']!=s['version']:result['changes_discarded']=True;result['error']='workspace_changed'
                        elif json.loads(w['permissions'])['write']:
                            original=await asyncio.to_thread(self.rt.files(w).snapshot)
                            if original!=exported:await self.rt.publish(w,exported);updated=True
                        else:result['changes_discarded']=True
                    else:result['changed_state_unknown']=True
                result['workspace_updated']=updated;clean=self.clean(result)
                await self.finish(sid,reason,clean)
                return self.view(await self.get(a,sid))
            except Exception:
                await self.finish(sid,'failed',{'error':'debug_stop_failed','changed_state_unknown':True})
                raise CodingError('debug_stop_failed',503) from None
            finally:self.release(sid)
    async def prune(self):
        now=time.time()
        rows=await self.rt.store.run(lambda d:d.execute('SELECT * FROM debug_sessions WHERE state IN (?,?,?,?,?)',ACTIVE).fetchall())
        for row in rows:
            s=dict(row)
            if now>=s['expires'] or (s['state']!='starting' and now-s['touched']>=self.idle_seconds):
                try:await self.stop(s['actor'],s['id'],'expired')
                except Exception:pass
        def clean(d):
            d.execute('DELETE FROM debug_actions WHERE session IN (SELECT id FROM debug_sessions WHERE created<? AND state IN (?,?,?,?))',(now-86400,*FINAL))
            d.execute('DELETE FROM debug_events WHERE session IN (SELECT id FROM debug_sessions WHERE created<? AND state IN (?,?,?,?))',(now-86400,*FINAL))
            d.execute('DELETE FROM debug_sessions WHERE created<? AND state IN (?,?,?,?)',(now-86400,*FINAL))
        await self.rt.store.run(clean)
    async def _watch(self):
        while True:
            await asyncio.sleep(2)
            try:await self.prune()
            except Exception:pass
    async def close(self):
        if self._reaper:self._reaper.cancel();await asyncio.gather(self._reaper,return_exceptions=True)
        rows=await self.rt.store.run(lambda d:d.execute('SELECT id,actor FROM debug_sessions WHERE state IN (?,?,?,?,?)',ACTIVE).fetchall())
        for row in rows:
            try:await self.stop(row['actor'],row['id'],'interrupted')
            except Exception:pass
