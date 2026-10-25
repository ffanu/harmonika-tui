import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.coding_runtime.core import Runtime,CodingError
from backend.coding_runtime.files import FileStore

class Sandbox:
    def __init__(self):self.runs=0;self.stops=[];self.release=asyncio.Event()
    async def capabilities(self):return {'ready':True}
    async def run(self,snapshot,skill,args,jid,on_output,**kw):
        self.runs+=1
        await on_output({'stream':'stdout','data':'REAL RUN\n'})
        if args.get('command')=='wait':await self.release.wait()
        changed=dict(snapshot);changed['generated.txt']=base64.b64encode(b'generated').decode()
        return {'status':'completed','exit_code':0,'stdout':'REAL RUN\n','stderr':'','snapshot':changed}
    async def stop(self,jid):self.stops.append(jid);self.release.set();return True

class Core(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='coding-core-');self.sb=Sandbox()
        self.rt=Runtime(Path(self.temp.name).resolve(),self.sb,FileStore)
        self.a='api-token:1';self.b='api-token:2'
        self.w=await self.rt.create(self.a,'Demo',{'write':True,'execute':True,'git_commit':True})
    async def asyncTearDown(self):await self.rt.close();self.temp.cleanup()
    async def invoke(self,skill,args,key='test-key'):return await self.rt.invoke_result(self.a,self.w['id'],skill,args,key)
    async def wait(self,j):
        for _ in range(100):
            item=await self.rt.job(self.a,j['id'])
            if item['state'] not in ('running','queued'):return self.rt.view_job(item)
            await asyncio.sleep(.01)
        self.fail('job hung')
    async def test_tenant_isolation(self):
        with self.assertRaises(CodingError) as e:await self.rt.workspace(self.b,self.w['id'])
        self.assertEqual(e.exception.status,404)
    async def test_read_write_edit_and_generation(self):
        await self.invoke('code.write',{'path':'app.py','content':'value = 1\n'})
        read=await self.invoke('code.read',{'path':'app.py'},None)
        self.assertEqual(read['result']['content'],'value = 1\n')
        sha=read['result']['sha256']
        await self.invoke('code.edit',{'path':'app.py','old':'1','new':'2','expected_sha256':sha},'edit')
        w=await self.rt.workspace(self.a,self.w['id']);self.assertEqual(w['version'],2)
    async def test_replay_and_conflict(self):
        x=await self.invoke('fs.write',{'path':'a.py','content':'a'})
        y=await self.invoke('fs.write',{'path':'a.py','content':'a'})
        self.assertEqual(x['id'],y['id']);self.assertTrue(y['replayed'])
        with self.assertRaises(CodingError) as e:await self.invoke('fs.write',{'path':'b.py','content':'b'})
        self.assertEqual(e.exception.status,409)
    async def test_approval_required_before_any_execution(self):
        j=await self.invoke('exec.python',{'code':'print(42)'})
        self.assertEqual(j['status'],'approval_required');self.assertEqual(self.sb.runs,0)
        self.assertIn('code',j['preview'])
        await self.rt.approve(self.a,j['id'],j['input_hash'])
        result=await self.wait(j)
        self.assertEqual(result['status'],'completed');self.assertEqual(self.sb.runs,1)
        read=await self.invoke('fs.read',{'path':'generated.txt'},None);self.assertEqual(read['result']['content'],'generated')
    async def test_cross_tenant_approval_and_stop_denied(self):
        j=await self.invoke('exec.shell',{'command':'echo hello'})
        with self.assertRaises(CodingError):await self.rt.approve(self.b,j['id'],j['input_hash'])
        with self.assertRaises(CodingError):await self.rt.stop(self.b,j['id'])
        self.assertEqual(self.sb.runs,0)
    async def test_stale_approval_is_not_executed(self):
        j=await self.invoke('exec.shell',{'command':'echo hello'})
        await self.invoke('fs.write',{'path':'changed.py','content':'changed'},'changed')
        with self.assertRaises(CodingError) as e:await self.rt.approve(self.a,j['id'],j['input_hash'])
        self.assertEqual(e.exception.code,'stale_approval');self.assertEqual(self.sb.runs,0)
    async def test_wrong_approval_hash_rejected(self):
        j=await self.invoke('test.run',{'runner':'python_unittest'})
        with self.assertRaises(CodingError):await self.rt.approve(self.a,j['id'],'incorrect')
        self.assertEqual(self.sb.runs,0)
    async def test_stop_pending_never_executes(self):
        j=await self.invoke('exec.background',{'command':'wait'})
        result=await self.rt.stop(self.a,j['id'])
        self.assertEqual(result['status'],'stopped');self.assertEqual(self.sb.runs,0)
    async def test_git_commit_requires_commit_permission(self):
        w=await self.rt.create(self.a,'NoCommit',{'write':True,'execute':True})
        with self.assertRaises(CodingError):await self.rt.submit(self.a,w['id'],'git.commit',{'message':'commit'},'git')
    async def test_read_only_workspace(self):
        w=await self.rt.create(self.a,'ReadOnly',{})
        with self.assertRaises(CodingError):await self.rt.submit(self.a,w['id'],'fs.write',{'path':'x','content':'x'},'w')
    async def test_no_source_in_read_journal(self):
        await self.invoke('fs.write',{'path':'private_fixture.py','content':'PRIVATE_WORKSPACE_CONTENT_123'})
        await self.invoke('fs.read',{'path':'private_fixture.py'},None)
        rows=await self.rt.store.run(lambda d:d.execute("SELECT result FROM jobs WHERE skill='fs.read'").fetchall())
        self.assertNotIn('PRIVATE_WORKSPACE_CONTENT_123',str([tuple(x) for x in rows]))
    async def test_restart_does_not_replay_jobs(self):
        j=await self.invoke('exec.shell',{'command':'wait'})
        await self.rt.initialize()
        self.assertEqual((await self.rt.job(self.a,j['id']))['state'],'approval_required')
        self.assertEqual(self.sb.runs,0)
    async def test_capabilities_do_not_hang_or_recurse(self):
        c=await asyncio.wait_for(self.rt.capabilities(),1)
        self.assertEqual(len(c['skills']),18)

    async def test_cancelled_publication_keeps_live_generation(self):
        import threading,time
        original=self.rt.store.run;entered=threading.Event();release=threading.Event()
        async def delayed(fn):
            if getattr(fn,'__name__','')=='swap':
                def held(d):entered.set();release.wait(2);return fn(d)
                return await original(held)
            return await original(fn)
        self.rt.store.run=delayed
        task=asyncio.create_task(self.invoke('fs.write',{'path':'kept.py','content':'kept'}))
        for _ in range(100):
            if entered.is_set():break
            await asyncio.sleep(.01)
        self.assertTrue(entered.is_set());task.cancel();release.set()
        with self.assertRaises(asyncio.CancelledError):await task
        w=await self.rt.workspace(self.a,self.w['id'])
        self.assertTrue(self.rt.directory(w).is_dir())
        self.assertEqual((self.rt.directory(w)/'kept.py').read_text(),'kept')
        self.assertFalse(await self.rt.store.run(lambda d:d.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone()))
    async def test_interrupted_turn_returns_recovery_without_reexecution(self):
        calls=[]
        async def run():calls.append(1);raise CodingError('planner_unavailable',503)
        with self.assertRaises(CodingError):await self.rt.agent_turn(self.a,self.w['id'],'turn-key',[{'role':'user','content':'work'}],run)
        value=await self.rt.agent_turn(self.a,self.w['id'],'turn-key',[{'role':'user','content':'work'}],run)
        self.assertEqual(value['status'],'interrupted');self.assertTrue(value['replayed']);self.assertEqual(calls,[1])
    async def test_periodic_prune_recovers_stale_rows(self):
        import time
        j=await self.invoke('exec.shell',{'command':'echo hi'})
        await self.rt.store.run(lambda d:d.execute("UPDATE jobs SET state='running',created=? WHERE id=?",(time.time()-400,j['id'])))
        await self.rt.prune();self.assertEqual((await self.rt.job(self.a,j['id']))['state'],'interrupted')

    async def test_missing_export_does_not_claim_saved_workspace(self):
        await self.invoke('fs.write',{'path':'keep.txt','content':'original'})
        async def missing(*a,**kw):return {'status':'completed','exit_code':0,'stdout':'42','stderr':'','changed_state_unknown':True}
        self.sb.run=missing
        j=await self.invoke('exec.python',{'code':'print(42)'},'missing-export')
        await self.rt.approve(self.a,j['id'],j['input_hash']);result=await self.wait(j)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['result']['error'],'workspace_export_unavailable')
        self.assertEqual((await self.invoke('fs.read',{'path':'keep.txt'},None))['result']['content'],'original')

    async def test_stop_race_preserves_completed_result(self):
        j=await self.invoke('exec.shell',{'command':'echo finished'})
        async def finishes_during_stop(jid):
            await self.rt.finish(jid,'completed',{'stdout':'finished','exit_code':0,'workspace_updated':True});return False
        self.sb.stop=finishes_during_stop
        result=await self.rt.stop(self.a,j['id'])
        self.assertEqual(result['status'],'completed');self.assertEqual(result['result']['stdout'],'finished');self.assertTrue(result['result']['workspace_updated'])
    async def test_terminal_state_and_event_are_atomic(self):
        j=await self.invoke('fs.write',{'path':'terminal.txt','content':'terminal'})
        events=await self.rt.events(self.a,j['id'])
        self.assertEqual(events[-1]['type'],'job.completed')

    async def test_large_output_chunk_is_split_without_silent_loss(self):
        async def emit(snapshot,skill,args,jid,callback,**kw):
            await callback({'stream':'stdout','data':'x'*10000})
            return {'status':'completed','exit_code':0,'stdout':'x'*10000,'stderr':'','snapshot':snapshot}
        self.sb.run=emit
        j=await self.invoke('exec.python',{'code':'print(42)'})
        await self.rt.approve(self.a,j['id'],j['input_hash']);self.assertEqual((await self.wait(j))['status'],'completed')
        events=await self.rt.events(self.a,j['id'])
        text=''.join(x['data']['text'] for x in events if x['type']=='process.output')
        self.assertEqual(text,'x'*10000)
