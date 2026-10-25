import asyncio,copy,unittest
from aiohttp.test_utils import TestClient,TestServer
from backend.master_engine.service import make_app

ROW={'id':'text.metrics','status':'available','engine':'discovery','description':'Bounded text metrics','execution_target':'operator_plugin','directly_invocable':True,'input_schema':{'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':100}},'required':['text'],'additionalProperties':False}}
class FakeDiscovery:
 def __init__(self):self.rows=[];self.pending=[];self.calls=0;self.executions=0
 async def refresh(self):self.calls+=1;self.rows=copy.deepcopy(self.pending)
 def descriptors(self):return copy.deepcopy(self.rows)
 def summary(self):return {'status':'ready','registered':len(self.rows),'generation':self.calls}
 async def execute(self,id,payload):
  self.executions+=1
  if payload['text']=='fail':raise ValueError('PRIVATE_CONTENT')
  return {'characters':len(payload['text'])}
class DiscoveryGateway(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.d=FakeDiscovery();self.h={'X-Harmonika-Master-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1'}
  self.client=TestClient(TestServer(make_app('s'*64,skills={},dispatcher=lambda *a:None,mobile_enabled=False,discovery=self.d,discovery_poll_seconds=.01)))
  await self.client.start_server()
 async def asyncTearDown(self):await self.client.close()
 async def register(self):
  self.d.pending=[ROW];await asyncio.sleep(.04)
 async def test_hot_add_detail_invoke_remove_without_restart(self):
  r=await self.client.get('/skills/text.metrics',headers=self.h);self.assertEqual(r.status,404)
  await self.register()
  r=await self.client.get('/skills/text.metrics',headers=self.h);self.assertEqual((await r.json())['skill']['id'],'text.metrics')
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'text.metrics','input':{'text':'Halo'}})
  self.assertEqual(r.status,200);self.assertEqual((await r.json())['result']['characters'],4)
  self.d.pending=[];await asyncio.sleep(.04)
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'text.metrics','input':{'text':'Halo'}});self.assertEqual(r.status,404)
 async def test_invalid_input_never_executes(self):
  await self.register()
  for body in ({},{'text':4},{'text':'ok','url':'http://private'},{'text':'x'*101}):
   r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'text.metrics','input':body});self.assertEqual(r.status,400)
  self.assertEqual(self.d.executions,0)
 async def test_runtime_failure_sanitized(self):
  await self.register()
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'text.metrics','input':{'text':'fail'}})
  self.assertEqual(r.status,422);self.assertNotIn('PRIVATE',await r.text())
 async def test_capability_and_summary_update_no_auth_bypass(self):
  await self.register()
  r=await self.client.get('/capabilities',headers=self.h);v=await r.json();self.assertTrue(v['features']['runtime_discovery']);self.assertEqual(v['discovery']['registered'],1)
  self.assertEqual((await self.client.get('/skills/discovery')).status,403)
  r=await self.client.get('/skills/discovery',headers=self.h);self.assertEqual((await r.json())['registered'],1)
  self.assertEqual((await self.client.get('/skills/discovery?refresh=1',headers=self.h)).status,400)
 async def test_listing_filter_includes_discovered(self):
  await self.register()
  r=await self.client.get('/skills?engine=discovery',headers=self.h);v=await r.json();self.assertEqual(v['total'],1);self.assertEqual(v['skills'][0]['id'],'text.metrics')
