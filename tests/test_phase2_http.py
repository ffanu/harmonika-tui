"""Debugger HTTP ownership, replay, strict payload and terminal SSE contracts."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_phase2_runtime import Sandbox,Debug
from backend.coding_runtime.core import Runtime
from backend.coding_runtime.phase2_files import EngineeringFileStore
from backend.coding_runtime.debug_sessions import DebugSessions
try:
    from aiohttp.test_utils import TestClient,TestServer
    from backend.coding_runtime.service import make_app
    HAS_HTTP=True
except ImportError:HAS_HTTP=False
@unittest.skipUnless(HAS_HTTP,'aiohttp tested on deployed Linux runtime')
class DebugHTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name),Sandbox(),EngineeringFileStore,phase2_enabled=True)
        self.adapter=Debug();self.rt.debug=DebugSessions(self.rt,self.adapter)
        self.client=TestClient(TestServer(make_app(self.rt,'x'*64)));await self.client.start_server()
        self.h={'X-Harmonika-Coding-Secret':'x'*64,'X-Harmonika-Principal':'api-token:800'}
        w=await self.rt.create('api-token:800','HTTP debug',{'write':True,'execute':True});self.wid=w['id']
        await self.rt.submit('api-token:800',self.wid,'code.write',{'path':'app.py','content':'value=42\nprint(value)\n'},'write')
        j=await self.rt.submit('api-token:800',self.wid,'debug.attach',{'path':'app.py'},'launch')
        await self.rt.approve('api-token:800',j['id'],j['input_hash'])
        for _ in range(200):
            row=await self.rt.job('api-token:800',j['id'])
            if row['state']=='completed':break
            await asyncio.sleep(.01)
        self.sid=json.loads(row['result'])['debug_session']['id'];self.path='/debug/sessions/'+self.sid
    async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
    async def test_auth_and_cross_actor_denied(self):
        for suffix in ('','/events','/stop'):
            method='post' if suffix=='/stop' else 'get'
            r=await getattr(self.client,method)(self.path+suffix);self.assertEqual(r.status,403)
            r=await getattr(self.client,method)(self.path+suffix,headers={**self.h,'X-Harmonika-Principal':'api-token:801'});self.assertEqual(r.status,404)
    async def test_action_requires_key_and_cannot_evaluate(self):
        r=await self.client.post(self.path+'/actions',headers=self.h,json={'action':'continue'});self.assertEqual(r.status,400)
        r=await self.client.post(self.path+'/actions',headers={**self.h,'Idempotency-Key':'invalid'},json={'action':'evaluate','expression':'danger()'});self.assertEqual(r.status,400)
        self.assertNotIn('continue',self.adapter.calls)
    async def test_action_replay_does_not_step_twice(self):
        for _ in range(2):
            r=await self.client.post(self.path+'/actions',headers={**self.h,'Idempotency-Key':'same-step'},json={'action':'next'});self.assertEqual(r.status,200)
        self.assertEqual(self.adapter.calls.count('next'),1)
    async def test_terminal_sse_replay_and_stop_idempotency(self):
        for n in range(140):await self.rt.debug.event(self.sid,{'event':'output','body':{'text':str(n)}})
        for _ in range(2):
            r=await self.client.post(self.path+'/stop',headers=self.h);self.assertEqual(r.status,200)
        self.assertEqual(self.adapter.calls.count('stop'),1)
        r=await self.client.get(self.path+'/events',headers={**self.h,'Accept':'text/event-stream','Last-Event-ID':self.sid+':2'})
        txt=await asyncio.wait_for(r.text(),4)
        self.assertIn('139',txt);self.assertIn('debug.closed',txt);self.assertTrue(txt.endswith('data: [DONE]\n\n'))
        sequences=[json.loads(line[6:])['sequence'] for line in txt.splitlines() if line.startswith('data: {')]
        self.assertEqual(sequences,sorted(set(sequences)));self.assertTrue(all(x>2 for x in sequences))
    async def test_event_header_cannot_switch_session(self):
        r=await self.client.get(self.path+'/events',headers={**self.h,'Last-Event-ID':'00000000-0000-0000-0000-000000000000:1'});self.assertEqual(r.status,400)
