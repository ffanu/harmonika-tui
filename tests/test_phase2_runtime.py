import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.coding_runtime.core import Runtime,CodingError,validate
from backend.coding_runtime.phase2_files import EngineeringFileStore
from backend.coding_runtime.phase2_foundation import unified
from backend.coding_runtime.debug_sessions import DebugSessions

class Sandbox:
    async def capabilities(self):return {'ready':True,'phase2_ready':True}
    async def stop(self,*a):return False
    async def run(self,snapshot,skill,args,jid,*a,**kw):return {'status':'completed','exit_code':0,'stdout':json.dumps({'status':'completed','skill_id':skill}),'stderr':'','snapshot':snapshot}
class Debug:
    def __init__(self):self.sessions={};self.calls=[]
    async def start(self,snapshot,args,sid,emit):
        self.sessions[sid]=dict(snapshot);self.calls.append('start');await emit({'event':'stopped','body':{'reason':'entry'}});return {'state':'paused','ok':True}
    async def set_breakpoints(self,sid,path,lines):self.calls.append('breakpoint');return {'ok':True,'breakpoints':[{'line':x,'verified':True} for x in lines]}
    async def action(self,sid,action):self.calls.append(action);return {'ok':True,'state':'paused','location':{'line':2},'variables':[{'name':'value','value':'42'}]}
    async def stop(self,sid):self.calls.append('stop');return {'ok':True,'snapshot':self.sessions.pop(sid,None)}
    async def cleanup_names(self,sid):self.calls.append('cleanup')

class Phase2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name),Sandbox(),EngineeringFileStore,phase2_enabled=True)
        self.adapter=Debug();self.rt.debug=DebugSessions(self.rt,self.adapter,idle_seconds=60,max_seconds=300)
        self.a='api-token:700';self.b='api-token:701';self.w=await self.rt.create(self.a,'Phase2',{'write':True,'execute':True,'git_commit':True});self.seq=0
    async def asyncTearDown(self):await self.rt.close();self.tmp.cleanup()
    async def invoke(self,skill,args,key=None):
        self.seq+=1;return await self.rt.submit(self.a,self.w['id'],skill,args,key or 'phase2-'+str(self.seq))
    async def wait(self,j):
        for _ in range(300):
            row=await self.rt.job(self.a,j['id'])
            if row['state'] not in ('queued','running'):return self.rt.view_job(row)
            await asyncio.sleep(.01)
        self.fail('job hung')
    async def start_debug(self):
        await self.invoke('code.write',{'path':'app.py','content':'value=42\nprint(value)\n'})
        j=await self.invoke('debug.attach',{'path':'app.py'})
        self.assertEqual(j['status'],'approval_required');self.assertEqual(self.adapter.calls,[])
        await self.rt.approve(self.a,j['id'],j['input_hash']);j=await self.wait(j)
        self.assertEqual(j['status'],'completed',j);return j['result']['debug_session']['id']
    async def test_disabled_phase2_remains18_and_rejects_new(self):
        self.rt.phase2_enabled=False;self.assertEqual(len((await self.rt.capabilities())['skills']),18)
        with self.assertRaises(CodingError) as e:await self.invoke('code.diff',{'path':'app.py','new_content':'x'})
        self.assertEqual(e.exception.code,'skill_unavailable')
    async def test_dependency_missing_does_not_advertise_ready(self):
        self.rt._caps={'ready':True,'phase2_ready':False};self.rt._caps_at=__import__('time').monotonic()
        c=await self.rt.capabilities();self.assertFalse(c['features']['phase2'])
        self.assertTrue(all(x['readiness']['status']=='unavailable' for x in c['skills'] if x['phase']==2))
    async def test_phase2_exposes_exact38_with_schemas(self):
        c=await self.rt.capabilities();self.assertEqual(len(c['skills']),38);self.assertTrue(c['features']['phase2'])
    async def test_scaffold_publishes_one_generation_and_replay_does_not_write(self):
        j=await self.invoke('scaffold.project',{'path':'starter','template':'python_http','name':'Demo'},'stable')
        self.assertEqual(j['status'],'completed');self.assertEqual(j['result']['files_changed'],3)
        self.assertEqual((await self.rt.workspace(self.a,self.w['id']))['version'],1)
        replay=await self.invoke('scaffold.project',{'path':'starter','template':'python_http','name':'Demo'},'stable')
        self.assertEqual(j['id'],replay['id']);self.assertEqual((await self.rt.workspace(self.a,self.w['id']))['version'],1)
    async def test_scaffold_conflict_preserves_whole_generation(self):
        await self.invoke('code.write',{'path':'starter/test_app.py','content':'existing'})
        w=await self.rt.workspace(self.a,self.w['id']);snapshot=self.rt.files(w).snapshot()
        with self.assertRaises(CodingError):await self.invoke('scaffold.project',{'path':'starter','template':'python_http','name':'Demo'})
        later=await self.rt.workspace(self.a,self.w['id']);self.assertEqual(later['generation'],w['generation']);self.assertEqual(self.rt.files(later).snapshot(),snapshot)
    async def test_diff_patch_hash_and_context(self):
        await self.invoke('code.write',{'path':'app.py','content':'value=1\n'})
        before=await self.invoke('code.read',{'path':'app.py'})
        p=await self.invoke('code.diff',{'path':'app.py','new_content':'value=2\n'})
        await self.invoke('code.patch',{'path':'app.py','patch':p['result']['diff'],'expected_sha256':before['result']['sha256']})
        after=await self.invoke('code.read',{'path':'app.py'});self.assertEqual(after['result']['content'],'value=2\n')
        with self.assertRaises(CodingError):await self.invoke('code.patch',{'path':'app.py','patch':p['result']['diff'],'expected_sha256':before['result']['sha256']})
    async def test_breakpoint_integer_arrays_strict(self):
        validate('debug.breakpoint',{'session_id':'x','path':'app.py','lines':[1,2]})
        for lines in ([True],['1'],[0],list(range(1,34))):
            with self.assertRaises(CodingError):validate('debug.breakpoint',{'session_id':'x','path':'app.py','lines':lines})
        validate('code.rename',{'path':'app.py','line':1,'column':0,'new_name':'other'})
    async def test_debug_ownership_and_source_write_lease(self):
        sid=await self.start_debug()
        with self.assertRaises(CodingError) as e:await self.rt.debug.get(self.b,sid)
        self.assertEqual(e.exception.status,404)
        with self.assertRaises(CodingError) as e:await self.invoke('code.write',{'path':'other.py','content':'blocked'})
        self.assertEqual(e.exception.code,'debug_workspace_busy')
        read=await self.invoke('code.read',{'path':'app.py'});self.assertIn('42',read['result']['content'])
        await self.rt.debug.stop(self.a,sid)
        self.assertFalse(await self.rt.debug.workspace_busy(self.a,self.w['id']))
    async def test_breakpoint_job_and_action_replay(self):
        sid=await self.start_debug();j=await self.invoke('debug.breakpoint',{'session_id':sid,'path':'app.py','lines':[2]});j=await self.wait(j)
        self.assertEqual(j['status'],'completed',j);self.assertEqual(self.adapter.calls.count('breakpoint'),1)
        x=await self.rt.debug.action_once(self.a,sid,'inspect','inspect-key')
        y=await self.rt.debug.action_once(self.a,sid,'inspect','inspect-key')
        self.assertEqual(self.adapter.calls.count('inspect'),1);self.assertTrue(y['replayed']);self.assertEqual(x['result']['variables'][0]['value'],'42')
        with self.assertRaises(CodingError):await self.rt.debug.action_once(self.a,sid,'continue','inspect-key')
    async def test_debug_stop_exports_owned_changes_and_releases_slot(self):
        sid=await self.start_debug();self.adapter.sessions[sid]['generated.txt']=base64.b64encode(b'result').decode()
        value=await self.rt.debug.stop(self.a,sid)
        self.assertEqual(value['state'],'closed');self.assertTrue(value['result']['workspace_updated']);self.assertEqual(self.rt.slots._value,2)
        read=await self.invoke('fs.read',{'path':'generated.txt'});self.assertEqual(read['result']['content'],'result')
    async def test_debug_stop_idempotent_cross_token_denied(self):
        sid=await self.start_debug()
        with self.assertRaises(CodingError):await self.rt.debug.stop(self.b,sid)
        await self.rt.debug.stop(self.a,sid);await self.rt.debug.stop(self.a,sid)
        self.assertEqual(self.adapter.calls.count('stop'),1)
    async def test_debug_ttl_expires_and_cleanup(self):
        import time
        sid=await self.start_debug()
        await self.rt.store.run(lambda d:d.execute('UPDATE debug_sessions SET expires=? WHERE id=?',(time.time()-1,sid)))
        await self.rt.debug.prune();self.assertEqual((await self.rt.debug.get(self.a,sid))['state'],'expired');self.assertEqual(self.rt.slots._value,2)
    async def test_debug_action_requires_explicit_key(self):
        sid=await self.start_debug()
        with self.assertRaises(CodingError):await self.rt.debug.action_once(self.a,sid,'continue',None)
        self.assertNotIn('continue',self.adapter.calls)

    async def test_failed_refactor_never_publishes_partial_source(self):
        await self.invoke('code.write',{'path':'app.py','content':'value=1\n'})
        before=await self.rt.workspace(self.a,self.w['id'])
        async def broken(snapshot,skill,args,*a,**kw):
            changed=dict(snapshot);changed['app.py']=base64.b64encode(b'BROKEN PARTIAL').decode()
            return {'status':'failed','exit_code':1,'stdout':json.dumps({'ok':False,'error':{'code':'rename_failed'}}),'stderr':'','snapshot':changed}
        self.rt.sandbox.run=broken
        j=await self.invoke('code.rename',{'path':'app.py','line':1,'column':0,'new_name':'other'})
        await self.rt.approve(self.a,j['id'],j['input_hash']);done=await self.wait(j)
        self.assertEqual(done['status'],'failed');self.assertTrue(done['result']['changes_discarded'])
        after=await self.rt.workspace(self.a,self.w['id']);self.assertEqual(before['version'],after['version'])
        self.assertEqual(self.rt.files(after).execute('code.read',{'path':'app.py'})['content'],'value=1\n')
    async def test_model_can_control_owned_debug_session_without_new_launch(self):
        sid=await self.start_debug()
        j=await self.invoke('debug.attach',{'action':'inspect','session_id':sid})
        self.assertNotEqual(j['status'],'approval_required');done=await self.wait(j)
        self.assertEqual(done['status'],'completed');self.assertIn('inspect',self.adapter.calls);self.assertEqual(self.adapter.calls.count('start'),1)
        j=await self.invoke('debug.attach',{'action':'stop','session_id':sid});self.assertEqual((await self.wait(j))['status'],'completed')

    async def test_nested_test_failure_is_not_false_success(self):
        async def failing(snapshot,*args,**kw):
            return {'status':'completed','exit_code':0,'stdout':json.dumps({'ok':True,'result':{'passed':False,'exit_code':1,'ran':1}}),'stderr':'','snapshot':snapshot}
        self.rt.sandbox.run=failing
        j=await self.invoke('test.single',{'runner':'python_unittest','path':'test_app.py','name':'test_app.Example.test_bad'})
        await self.rt.approve(self.a,j['id'],j['input_hash']);done=await self.wait(j)
        self.assertEqual(done['status'],'failed');self.assertEqual(done['result']['error'],'quality_check_failed')
        self.assertFalse(done['result']['tool_result']['result']['passed'])
    async def test_lint_findings_are_failed_quality_gate(self):
        async def failing(snapshot,*args,**kw):
            return {'status':'completed','exit_code':0,'stdout':json.dumps({'ok':True,'result':{'clean':False,'count':1}}),'stderr':'','snapshot':snapshot}
        self.rt.sandbox.run=failing
        done=await self.wait(await self.invoke('lint.run',{'path':'app.py'}))
        self.assertEqual(done['status'],'failed');self.assertEqual(done['result']['error'],'quality_check_failed')
    async def test_restart_marks_debug_interrupted_without_replaying_code(self):
        import time
        from uuid import uuid4
        sid=str(uuid4());stamp=time.time()
        await self.rt.store.run(lambda d:d.execute('INSERT INTO debug_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',(sid,self.a,self.w['id'],str(uuid4()),0,'paused',stamp,stamp,stamp+300,None)))
        await self.rt.debug.initialize()
        value=await self.rt.debug.get(self.a,sid)
        self.assertEqual(value['state'],'interrupted');self.assertEqual(self.adapter.calls,['cleanup'])
        self.assertEqual((await self.rt.workspace(self.a,self.w['id']))['version'],0)
    async def test_cancelled_stop_still_finishes_publication_and_releases_lease(self):
        sid=await self.start_debug();self.adapter.sessions[sid]['result.txt']=base64.b64encode(b'owned result').decode()
        original=self.adapter.stop;entered=asyncio.Event();proceed=asyncio.Event()
        async def slow_stop(s):entered.set();await proceed.wait();return await original(s)
        self.adapter.stop=slow_stop
        task=asyncio.create_task(self.rt.debug.stop(self.a,sid));await entered.wait();task.cancel();proceed.set()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((await self.rt.debug.get(self.a,sid))['state'],'closed');self.assertEqual(self.rt.slots._value,2)
        self.assertEqual(self.rt.files(await self.rt.workspace(self.a,self.w['id'])).execute('code.read',{'path':'result.txt'})['content'],'owned result')

    async def test_explicit_debug_start_action_is_orchestration_only(self):
        await self.invoke('code.write',{'path':'app.py','content':'value=42\n'})
        original=self.adapter.start
        async def strict(snapshot,args,*rest):
            self.assertNotIn('action',args);return await original(snapshot,args,*rest)
        self.adapter.start=strict
        j=await self.invoke('debug.attach',{'action':'start','path':'app.py'})
        await self.rt.approve(self.a,j['id'],j['input_hash']);self.assertEqual((await self.wait(j))['status'],'completed')
