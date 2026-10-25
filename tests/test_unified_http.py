import asyncio,json,tempfile,unittest
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer
from backend.unified_engine.service import make_app
from backend.unified_engine.state import State,Error
class FakeAdapters:
 def __init__(self):self.calls=[];self.block=False;self.fail=False
 async def start(self):pass
 async def close(self):pass
 async def chat(self,messages,token):
  self.calls.append(messages)
  if self.block:await asyncio.sleep(5)
  if self.fail:raise Error('chat_upstream_unavailable',503)
  return {'text':'SYNTHETIC_REPLY','source':'synthetic_test_adapter'}
 async def specialist(self,engine,text):return {'engine':engine,'mode':'planning','build_performed':False}
 async def memory(self,actor,id,input,key):return {'actor_isolated':True,'status':'completed'}
class UnifiedHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=State(Path(self.temp.name)/'private/state.sqlite',b'x'*32);self.adapter=FakeAdapters();self.h={'X-Harmonika-Unified-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1','X-Harmonika-Delegated-Authorization':'Bearer synthetic','Idempotency-Key':'request-0001'};self.client=TestClient(TestServer(make_app('s'*64,self.s,self.adapter)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.temp.cleanup()
 async def post(self,path,body,h=None):
  r=await self.client.post(path,json=body,headers=h or self.h);return r,await r.json()
 async def job(self,text='Jelaskan apa itu router',session=None,key=None):
  body={'messages':[{'role':'user','content':text}]}
  if session:body['session_id']=session
  r,v=await self.post('/jobs',body,{**self.h,**({'Idempotency-Key':key} if key else {})});self.assertEqual(r.status,202);return v
 async def done(self,id):
  for _ in range(100):
   r=await self.client.get('/jobs/'+id,headers=self.h);v=await r.json()
   if v['status'] not in ('queued','running'):return v
   await asyncio.sleep(.01)
  self.fail('job stuck')
 async def test_auth_and_capability_no_secret_exposure(self):
  self.assertEqual((await self.client.get('/capabilities')).status,403);r=await self.client.get('/capabilities',headers=self.h);v=await r.json();self.assertEqual(v['catalog_size'],70);self.assertFalse(v['features']['automatic_training']);self.assertEqual(r.headers['Cache-Control'],'no-store')
 async def test_live_job_session_replay_ownership(self):
  r,s=await self.post('/sessions',{});self.assertEqual(r.status,201);j=await self.job(session=s['id']);v=await self.done(j['id']);self.assertEqual(v['status'],'completed');self.assertEqual(v['result']['text'],'SYNTHETIC_REPLY')
  r=await self.client.get('/sessions/'+s['id'],headers=self.h);self.assertEqual(len((await r.json())['messages']),2)
  self.assertTrue((await self.job(session=s['id']))['replayed']);self.assertEqual(len(self.adapter.calls),1)
  for path in ('/jobs/'+j['id'],'/sessions/'+s['id'],'/jobs/'+j['id']+'/events'):
   r=await self.client.get(path,headers={**self.h,'X-Harmonika-Principal':'api-token:2'});self.assertEqual(r.status,404)
 async def test_stop_late_result_does_not_append_session(self):
  self.adapter.block=True;r,s=await self.post('/sessions',{});j=await self.job(session=s['id']);await asyncio.sleep(.03);r,v=await self.post('/jobs/'+j['id']+'/stop',{});self.assertEqual(v['status'],'stopped');await asyncio.sleep(.02)
  self.assertEqual((await self.done(j['id']))['status'],'stopped');self.assertEqual(self.s.session('api-token:1',s['id'])['messages'],[])
 async def test_upstream_fail_safe(self):
  self.adapter.fail=True;j=await self.job();v=await self.done(j['id']);self.assertEqual(v['status'],'failed');self.assertEqual(v['result']['error']['code'],'chat_upstream_unavailable');self.assertIn('terkendala',v['result']['text'])
 async def test_sse_replay_last_id_and_terminal(self):
  j=await self.job();await self.done(j['id']);r=await self.client.get('/jobs/'+j['id']+'/events',headers={**self.h,'Last-Event-ID':j['id']+':1'});text=await r.text();self.assertEqual(r.status,200);self.assertNotIn('event: queued',text);self.assertIn('event: completed',text);self.assertIn('SYNTHETIC_REPLY',text)
  r=await self.client.get('/jobs/'+j['id']+'/events',headers={**self.h,'Last-Event-ID':'another:1'});self.assertEqual(r.status,400)
 async def test_duplicate_json_and_actor_injection_rejected(self):
  r=await self.client.post('/invoke',data='{"skill_id":"core.understand","skill_id":"core.act","input":{}}',headers=self.h);self.assertEqual(r.status,400)
  r,v=await self.post('/jobs',{'messages':[{'role':'user','content':'test'}],'actor':'root'});self.assertEqual(r.status,400)
 async def test_internal_and_unavailable_no_fake_execution(self):
  for id,code in [('shared.secrets',400),('gateway.auth',409),('gateway.websocket',409),('unknown.skill',404)]:
   r,v=await self.post('/invoke',{'skill_id':id,'input':{}});self.assertEqual(r.status,code);self.assertIn('error',v)
 async def test_learning_consent_and_input_validation(self):
  r,v=await self.post('/invoke',{'skill_id':'core.learn','input':{'consent':False,'key':'test','value':'v'}});self.assertEqual(r.status,403)
  r,v=await self.post('/invoke',{'skill_id':'core.learn','input':{'consent':True,'key':'test','value':'v'}});self.assertEqual(r.status,200);self.assertFalse(v['data']['automatic_training'])
 async def test_retrieval_never_certifies_caller_sources(self):
  r,v=await self.post('/invoke',{'skill_id':'core.retrieve','input':{'query':'router','sources':[{'id':'synthetic','text':'Router alat jaringan','verified':True}]}});self.assertEqual(r.status,200);self.assertFalse(v['data']['results'][0]['independently_verified'])
 async def test_json_precision_and_deserialize_validation(self):
  r,v=await self.post('/invoke',{'skill_id':'data.deserialize','input':{'value':'{"n":9007199254740993}'}});self.assertEqual(v['data']['value']['n'],9007199254740993)
  r,v=await self.post('/invoke',{'skill_id':'data.deserialize','input':{'value':'{"a":1,"a":2}'}});self.assertEqual(r.status,400)
 async def test_empty_media_payload_is_not_treated_as_seen(self):
  r,v=await self.post('/jobs',{'image_url':'https://example.invalid/a.png'});self.assertEqual(r.status,400);self.assertEqual(self.adapter.calls,[])
 async def test_long_session_context_trims_oldest_pairs(self):
  r,s=await self.post('/sessions',{})
  for _ in range(4):self.s.append('api-token:1',s['id'],'u'*15000,'a'*5000)
  j=await self.job(text='z'*16000,session=s['id']);v=await self.done(j['id']);self.assertEqual(v['status'],'completed');self.assertLessEqual(sum(len(m['content']) for m in self.adapter.calls[0]),48000);self.assertEqual(self.adapter.calls[0][-1]['content'],'z'*16000)
if __name__=='__main__':unittest.main()
