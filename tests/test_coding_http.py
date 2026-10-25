"""Private HTTP contract tests; no provider calls or production data."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from aiohttp.test_utils import TestClient,TestServer
    from backend.coding_runtime.service import make_app
    HAS_HTTP=True
except ImportError:HAS_HTTP=False
from backend.coding_runtime.core import Runtime
from backend.coding_runtime.files import FileStore
class Sandbox:
    async def capabilities(self):return {'ready':True}
    async def stop(self,*a):return False
@unittest.skipUnless(HAS_HTTP,'aiohttp is installed on backend runtime')
class HTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.runtime=Runtime(Path(self.tmp.name),Sandbox(),FileStore)
        self.client=TestClient(TestServer(make_app(self.runtime,'x'*64)));await self.client.start_server()
        self.headers={'X-Harmonika-Coding-Secret':'x'*64,'X-Harmonika-Principal':'api-token:1'}
    async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
    async def test_unauthenticated_denied(self):
        r=await self.client.get('/capabilities');self.assertEqual(r.status,403)
    async def test_health_contains_no_actor(self):
        r=await self.client.get('/healthz');self.assertEqual(r.status,200);self.assertNotIn('token',await r.text())
    async def test_actor_is_required(self):
        r=await self.client.get('/capabilities',headers={'X-Harmonika-Coding-Secret':'x'*64});self.assertEqual(r.status,403)
    async def test_cross_actor_workspace_404(self):
        r=await self.client.post('/workspaces',headers=self.headers,json={'name':'Test','permissions':{'write':True}});w=(await r.json())['workspace']
        r=await self.client.get('/workspaces/'+w['id'],headers={**self.headers,'X-Harmonika-Principal':'api-token:2'});self.assertEqual(r.status,404)
    async def test_strict_request_and_permission(self):
        r=await self.client.post('/workspaces',headers=self.headers,json={'name':'x','actor':'api-token:2'});self.assertEqual(r.status,400)
        r=await self.client.post('/workspaces',headers=self.headers,json={'name':'x'});w=(await r.json())['workspace']
        r=await self.client.post('/workspaces/'+w['id']+'/invoke',headers=self.headers,json={'skill_id':'fs.write','input':{'path':'x','content':'x'},'idempotency_key':'key'})
        self.assertEqual(r.status,403)

    async def test_sse_drains_more_than_one_page_before_terminal_done(self):
        w=await self.runtime.create('api-token:1','Paged',{'write':True})
        j=await self.runtime.submit('api-token:1',w['id'],'fs.write',{'path':'x.txt','content':'x'},'paged')
        for n in range(140):await self.runtime.event(j['id'],'process.output',{'text':str(n)})
        await self.runtime.finish(j['id'],'completed',{'done':True})
        r=await self.client.get('/jobs/'+j['id']+'/events',headers={**self.headers,'Accept':'text/event-stream'})
        text=await r.text()
        self.assertIn('job.completed',text);self.assertIn('139',text);self.assertTrue(text.endswith('data: [DONE]\n\n'))
    async def test_approval_pending_stream_waits_until_stop(self):
        import asyncio
        w=await self.runtime.create('api-token:1','Pending',{'execute':True})
        j=await self.runtime.submit('api-token:1',w['id'],'exec.shell',{'command':'echo x'},'pending')
        r=await self.client.get('/jobs/'+j['id']+'/events',headers={**self.headers,'Accept':'text/event-stream'})
        first=await r.content.readline();self.assertTrue(first.startswith(b'id: '))
        await self.runtime.stop('api-token:1',j['id'])
        text=await asyncio.wait_for(r.text(),3)
        self.assertIn('job.stopped',text);self.assertIn('[DONE]',text)
