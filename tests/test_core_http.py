import asyncio,json,tempfile,unittest
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer
from backend.core_engine.service import make_app
from backend.core_engine.state import Store
from backend.core_engine.registry import descriptors
class CoreHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'private/core.sqlite',b'x'*32)
  self.h={'X-Harmonika-Core-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1'}
  self.client=TestClient(TestServer(make_app('s'*64,self.store)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.store.close();self.tmp.cleanup()
 async def invoke(self,id,input,key='request-0001',actor=1):
  h={**self.h,'X-Harmonika-Principal':'api-token:'+str(actor)}
  if key is not None:h['Idempotency-Key']=key
  res=await self.client.post('/invoke',headers=h,json={'skill_id':id,'input':input});return res,await res.json()
 async def test_health_auth_no_store(self):
  self.assertEqual((await self.client.get('/healthz')).status,200)
  self.assertEqual((await self.client.get('/capabilities')).status,403)
  r=await self.client.get('/capabilities',headers=self.h);v=await r.json();self.assertEqual(v['catalog_size'],99);self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertFalse(v['features']['host_shell_access'])
 async def test_every_delegated_local_unavailable_not_executed(self):
  for row in descriptors():
   if row['status']=='available':continue
   r,v=await self.invoke(row['id'],{})
   self.assertEqual(r.status,409 if row['status'] in ('context_required','native_endpoint','local_only') else 503,row['id'])
 async def test_data_success_and_customer_safe_errors(self):
  r,v=await self.invoke('data.read_csv',{'text':'a,b\n1,2\n'});self.assertEqual(r.status,200);self.assertEqual(v['data']['row_count'],1);self.assertEqual(v['skill_id'],'data.read_csv')
  r,v=await self.invoke('data.transform',{'rows':[{'a':1}],'operations':[{'type':[]}]});self.assertEqual(r.status,400);self.assertNotIn('message',v['error'])
 async def test_memory_two_token_precision_idempotency(self):
  payload={'key':'n','value':9007199254740993}
  r,a=await self.invoke('memory.set',payload);self.assertEqual(r.status,200)
  _,b=await self.invoke('memory.set',payload);self.assertTrue(b['replayed'])
  r,_=await self.invoke('memory.set',{'key':'n','value':9007199254740992});self.assertEqual(r.status,409)
  r,v=await self.invoke('memory.get',{'key':'n'},key=None);self.assertEqual(v['data']['value'],9007199254740993)
  _,v=await self.invoke('memory.get',{'key':'n'},actor=2);self.assertFalse(v['data']['found'])
 async def test_scoped_clear_does_not_affect_other_actor(self):
  await self.invoke('memory.set',{'key':'x','value':'one'})
  await self.invoke('memory.set',{'key':'x','value':'two'},actor=2)
  await self.invoke('memory.clear',{'scope':'default','all':True},key='clear-0001')
  _,v=await self.invoke('memory.get',{'key':'x'},actor=2);self.assertEqual(v['data']['value'],'two')
 async def test_workflow_replay_and_no_cross_actor(self):
  r,v=await self.invoke('workflow.define',{'name':'synthetic','steps':[{'id':'parse','skill_id':'data.read_json','input':{'text':'{"a":1}'}},{'id':'calc','skill_id':'data.analyze','input':{'rows':[{'n':1},{'n':3}]},'depends_on':['parse']}]})
  self.assertEqual(r.status,200);wid=v['data']['workflow_id']
  r,v=await self.invoke('workflow.run',{'workflow_id':wid},key='run-00001');self.assertEqual(r.status,200);self.assertEqual(v['data']['status'],'completed')
  _,again=await self.invoke('workflow.run',{'workflow_id':wid},key='run-00001');self.assertTrue(again['replayed']);self.assertEqual(v['data']['run_id'],again['data']['run_id'])
  r,_=await self.invoke('workflow.run',{'workflow_id':wid},key='run-00001',actor=2);self.assertEqual(r.status,404)
 async def test_unknown_duplicate_nan_depth_and_identity(self):
  for raw in ('{"skill_id":"memory.get","input":{"key":"a","key":"b"}}','{"skill_id":"memory.set","input":{"key":"a","value":NaN}}','{"skill_id":"memory.get","input":{"key":"a"},"actor":"root"}'):
   r=await self.client.post('/invoke',headers=self.h,data=raw);self.assertEqual(r.status,400)
  for v in ({'key':'a','path':'/etc/passwd'},{'key':'a','principal':'root'}):
   r,_=await self.invoke('memory.get',v);self.assertEqual(r.status,400)
  r,_=await self.invoke('not.a.skill',{});self.assertEqual(r.status,404)
 async def test_mutating_calls_require_idempotency(self):
  r,v=await self.invoke('memory.set',{'key':'a','value':'b'},key=None);self.assertEqual(r.status,400);self.assertEqual(v['error']['code'],'idempotency_required')
 async def test_workflow_failure_not_fake_completion(self):
  _,v=await self.invoke('workflow.define',{'name':'bad','steps':[{'id':'parse','skill_id':'data.read_json','input':{'text':'invalid'}}]})
  r,out=await self.invoke('workflow.run',{'workflow_id':v['data']['workflow_id']},key='run-bad01');self.assertEqual(r.status,200);self.assertEqual(out['data']['status'],'failed');self.assertEqual(out['data']['steps'][0]['status'],'failed')
 async def test_queries_bounded(self):
  for path in ('/capabilities?url=http://localhost','/skills?limit=201','/skills/memory.get?actor=root'):
   self.assertEqual((await self.client.get(path,headers=self.h)).status,400)
if __name__=='__main__':unittest.main()
