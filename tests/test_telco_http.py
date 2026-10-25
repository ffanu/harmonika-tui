import asyncio,json,tempfile,unittest
from pathlib import Path
from backend.telco_engine.runtime import Runtime
try:
 from aiohttp.test_utils import TestClient,TestServer
 from backend.telco_engine.service import make_app
 HTTP=True
except ImportError:HTTP=False
@unittest.skipUnless(HTTP,'aiohttp contract tested on Linux service venv')
class HTTPTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.a='api-token:730'
  self.rt.mutations.add('report.arpu');self.client=TestClient(TestServer(make_app(self.rt,'s'*64)));await self.client.start_server()
  self.h={'X-Harmonika-Telco-Secret':'s'*64,'X-Harmonika-Principal':self.a}
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def make(self):
  r=await self.client.post('/invoke',headers={**self.h,'Idempotency-Key':'one'},json={'skill_id':'report.arpu','input':{'period':'2026-10'}});self.assertEqual(r.status,200);return await r.json()
 async def test_secret_and_actor_required(self):
  r=await self.client.get('/capabilities');self.assertEqual(r.status,403)
  r=await self.client.get('/capabilities',headers={'X-Harmonika-Telco-Secret':'s'*64});self.assertEqual(r.status,403)
 async def test_strict_body_cannot_spoof_tenant(self):
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'report.arpu','input':{'period':'2026-10'},'tenant':'other'});self.assertEqual(r.status,400)
 async def test_ownership_and_approval_hash(self):
  j=await self.make();p='/requests/'+j['id'];r=await self.client.get(p,headers={**self.h,'X-Harmonika-Principal':'api-token:731'});self.assertEqual(r.status,404)
  r=await self.client.post(p+'/approve',headers=self.h,json={'decision':'approve','input_hash':'incorrect'});self.assertEqual(r.status,409)
 async def test_sse_waits_for_approval_then_terminal_replay(self):
  j=await self.make();p='/requests/'+j['id'];r=await self.client.get(p+'/events',headers={**self.h,'Accept':'text/event-stream'})
  self.assertTrue((await r.content.readline()).startswith(b'id: '))
  a=await self.client.post(p+'/approve',headers=self.h,json={'decision':'approve','input_hash':j['input_hash']});self.assertEqual(a.status,200)
  txt=await asyncio.wait_for(r.text(),4);self.assertIn('response.completed',txt);self.assertTrue(txt.endswith('data: [DONE]\n\n'))
  replay=await self.client.get(p+'/events',headers={**self.h,'Last-Event-ID':j['id']+':1'});data=await replay.json();self.assertTrue(all(x['sequence']>1 for x in data['events']))
 async def test_stop_and_disabled_connector(self):
  j=await self.make();r=await self.client.post('/requests/'+j['id']+'/stop',headers=self.h,json={});self.assertEqual((await r.json())['status'],'stopped')
  r=await self.client.post('/invoke',headers={**self.h,'Idempotency-Key':'blocked'},json={'skill_id':'olt.firmware.upgrade','input':{}});self.assertEqual(r.status,503);self.assertEqual((await r.json())['error']['code'],'skill_unavailable')
