import asyncio,json,tempfile,unittest
from pathlib import Path
from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient,TestServer
from backend.unified_engine.service import make_app
from backend.unified_engine.state import State
from backend.unified_engine.extensions import Extensions
from tests.test_unified_http import FakeAdapters

class CompletionHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.state=State(Path(self.temp.name)/'private/state.sqlite',b'x'*32);self.valid=True;self.adapters=FakeAdapters()
  async def auth_check(bearer):return self.valid and bearer=='Bearer hai_synthetic'
  self.adapters.auth_check=auth_check;self.h={'X-Harmonika-Unified-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1','X-Harmonika-Delegated-Authorization':'Bearer hai_synthetic','Idempotency-Key':'request-0001'}
  self.client=TestClient(TestServer(make_app('s'*64,self.state,self.adapters)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.temp.cleanup()
 async def post(self,path,value,extra=None):
  r=await self.client.post(path,json=value,headers={**self.h,**(extra or {})});return r,await r.json()
 async def test_no_missing_declared_skills_and_knowledge_hashes(self):
  r=await self.client.get('/capabilities',headers=self.h);caps=await r.json();self.assertEqual(caps['readiness'],{'available':57,'internal_only':12,'native_endpoint':1,'unavailable':0});self.assertTrue(caps['features']['websocket_ticket_auth'])
  r,v=await self.post('/knowledge/query',{'query':'idempotency status stopped'});self.assertEqual(r.status,200);self.assertGreater(len(v['results']),0);self.assertEqual(len(v['results'][0]['citation']['sha256']),64);self.assertFalse(v['independently_verified'])
 async def test_core_retrieve_uses_operator_corpus(self):
  r,v=await self.post('/invoke',{'skill_id':'core.retrieve','input':{'query':'idempotency status queued'}});self.assertEqual(r.status,200);self.assertEqual(v['data']['method'],'local_lexical_bm25')
 async def test_real_graphql_and_mutation_guard(self):
  r,v=await self.post('/graphql',{'query':'{ capabilities skills { id status } knowledge(query:"idempotency") }'});self.assertEqual(r.status,200);self.assertEqual(len(v['data']['skills']),70);self.assertEqual(v['data']['capabilities']['readiness']['unavailable'],0)
  r,v=await self.post('/graphql',{'query':'mutation{deleteEverything}'});self.assertEqual(r.status,400)
 async def test_events_publish_replay_ownership_and_idempotency(self):
  body={'topic':'synthetic.topic','payload':{'text':'synthetic event'}};r,a=await self.post('/bus/events',body);self.assertEqual(r.status,200)
  r,b=await self.post('/bus/events',body);self.assertTrue(b['replayed']);self.assertEqual(a['sequence'],b['sequence'])
  r,p=await self.post('/bus/replay',{'topic':'synthetic.topic'});self.assertEqual(len(p['events']),1)
  r,p=await self.post('/bus/replay',{'topic':'synthetic.topic'}, {'X-Harmonika-Principal':'api-token:2'});self.assertEqual(p['events'],[])
 async def test_secret_write_scope_metadata_only_and_rotation_replay(self):
  body={'operation':'set','name':'synthetic.connector','value':'SYNTHETIC_VALUE_NOT_REAL'};r,v=await self.post('/secrets',body);self.assertEqual(r.status,403)
  scope={'X-Harmonika-Unified-Scopes':'chat,secrets:write,secrets:read'};r,v=await self.post('/secrets',body,scope);self.assertEqual(r.status,200);self.assertNotIn('SYNTHETIC_VALUE',json.dumps(v));self.assertNotIn('value',v)
  r,v=await self.post('/secrets',{'operation':'metadata','name':'synthetic.connector'},scope);self.assertEqual(r.status,200);self.assertNotIn('value',v)
  r,v=await self.post('/secrets',{'operation':'metadata','name':'synthetic.connector'}, {**scope,'X-Harmonika-Principal':'api-token:2'});self.assertEqual(r.status,404)
  r,v=await self.post('/secrets',{'operation':'get','name':'synthetic.connector'},scope);self.assertEqual(r.status,400)
 async def test_native_websocket_ping_and_read_only(self):
  ws=await self.client.ws_connect('/ws',headers=self.h);v=await ws.receive_json();self.assertEqual(v['type'],'connected');self.assertTrue(v['read_only'])
  await ws.send_json({'type':'ping'});self.assertEqual((await ws.receive_json())['type'],'pong')
  await ws.send_json({'type':'create_job','messages':[]});self.assertEqual((await ws.receive_json())['error']['code'],'read_only_websocket');await ws.close()
 async def test_ticket_single_use_and_revocation(self):
  r,t=await self.post('/ticket',{});self.assertEqual(r.status,200)
  ws=await self.client.ws_connect('/ws?ticket='+t['ticket'],headers={'X-Harmonika-Unified-Secret':'s'*64});self.assertEqual((await ws.receive_json())['type'],'connected');await ws.close()
  with self.assertRaises(WSServerHandshakeError) as e:await self.client.ws_connect('/ws?ticket='+t['ticket'],headers={'X-Harmonika-Unified-Secret':'s'*64})
  self.assertEqual(e.exception.status,401)
  r,t=await self.post('/ticket',{});self.valid=False
  with self.assertRaises(WSServerHandshakeError) as e:await self.client.ws_connect('/ws?ticket='+t['ticket'],headers={'X-Harmonika-Unified-Secret':'s'*64})
  self.assertEqual(e.exception.status,401)
 async def test_websocket_topic_live_events_and_other_actor_job(self):
  ws=await self.client.ws_connect('/ws',headers=self.h);await ws.receive_json();await ws.send_json({'type':'resume','topic':'live.topic','after_sequence':0});self.assertEqual((await ws.receive_json())['type'],'resume.accepted')
  await self.post('/bus/events',{'topic':'live.topic','payload':{'n':42}});event=await ws.receive_json(timeout=2);self.assertEqual(event['data']['payload']['n'],42)
  other=self.state.create('api-token:2',{'messages':[{'role':'user','content':'test'}]},'other-0001');await ws.send_json({'type':'resume','job_id':other['id'],'after_sequence':0});self.assertEqual((await ws.receive_json())['error']['code'],'not_found');await ws.close()
 async def test_second_service_cannot_recover_active_instances_jobs(self):
  from backend.unified_engine.state import Error
  j=self.state.create('api-token:1',{'messages':[{'role':'user','content':'owned pending'}]},'pending-0001')
  other=TestClient(TestServer(make_app('s'*64,self.state,FakeAdapters())))
  try:
   with self.assertRaisesRegex(Error,'service_already_active'):await other.start_server()
   self.assertEqual(self.state.get('api-token:1',j['id'])['status'],'queued')
  finally:await other.close()

if __name__=='__main__':unittest.main()
