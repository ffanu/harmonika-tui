"""Actual authenticated HTTP/SSE/agent-loop contract, synthetic runtime only."""
import asyncio,json,tempfile,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));sys.path.insert(0,str(Path(__file__).parent))
from aiohttp.test_utils import TestClient,TestServer
from backend.coding_runtime.core import Runtime
from backend.coding_runtime.files import FileStore
from backend.coding_runtime.service import make_app
from test_level14_evidence import LocalSandbox

class HTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.runtime=Runtime(Path(self.tmp.name),LocalSandbox(),FileStore);self.answers=[];self.requests=[]
  async def decide(payload):self.requests.append(payload);return self.answers.pop(0)
  self.client=TestClient(TestServer(make_app(self.runtime,'x'*64,decide)));await self.client.start_server();self.headers={'X-Harmonika-Coding-Secret':'x'*64,'X-Harmonika-Principal':'api-token:14001'};self.w=await self.runtime.create('api-token:14001','Synthetic',{'write':True,'execute':True});self.path='/workspaces/'+self.w['id']+'/agent';self.i=0
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def call(self,verification=None,stream=False,headers=None):
  self.i+=1;b={'messages':[{'role':'user','content':'Do the authorized synthetic task.'}],'stream':stream}
  if verification is not None:b['verification']=verification
  return await self.client.post(self.path,headers={**self.headers,'Idempotency-Key':'http-'+str(self.i),**(headers or {})},json=b)
 async def test_model_no_tool_success_is_not_completion(self):
  self.answers=[{'content':'Everything deployed successfully. All tests are green.','tool_calls':[]}];r=await self.call();self.assertEqual(r.status,200);v=await r.json();self.assertEqual(v['status'],'unverified');self.assertNotIn('deployed',v['text']);self.assertEqual(v['proof']['operation_count'],0)
 async def test_forged_owner_in_contract_rejected(self):
  r=await self.call({'profile':'artifact','paths':['x'],'owner':'api-token:2'});self.assertEqual(r.status,400);self.assertEqual(self.requests,[])
 async def test_bridge_and_principal_required(self):
  r=await self.call(headers={'X-Harmonika-Coding-Secret':'wrong'});self.assertEqual(r.status,403)
  r=await self.call(headers={'X-Harmonika-Principal':'owner-from-body'});self.assertEqual(r.status,403)
 async def test_question_and_plan_do_not_run_tools(self):
  for profile in ['conversation','plan_only']:
   self.answers=[{'content':'DNS maps names to records.','tool_calls':[]}];r=await self.call({'profile':profile});self.assertEqual(r.status,200);v=await r.json();self.assertEqual(v['status'],'answered');self.assertEqual(v['text'],'DNS maps names to records.')
  self.assertEqual(self.runtime.sandbox.runs,0)
 async def test_question_cannot_select_mutation(self):
  self.answers=[{'tool_calls':[{'id':'bad','function':{'name':'fs_write','arguments':'{"path":"x","content":"wrong"}'}}]}];r=await self.call({'profile':'conversation'});self.assertEqual(r.status,403);self.assertEqual((await self.runtime.workspace('api-token:14001',self.w['id']))['version'],0)
 async def test_read_content_is_real_not_model_fiction_or_cache(self):
  await self.runtime.invoke_result('api-token:14001',self.w['id'],'fs.write',{'path':'x.txt','content':'ACTUAL_SYNTHETIC_SENTINEL'},'seed')
  self.answers=[{'tool_calls':[{'id':'one','function':{'name':'fs_read','arguments':'{"path":"x.txt"}'}}]},{'content':'Invented content is BLUE.','tool_calls':[]}]
  r=await self.call({'profile':'read','paths':['x.txt']});self.assertEqual(r.status,200);v=await r.json();self.assertEqual(v['status'],'completed');self.assertIn('ACTUAL_SYNTHETIC_SENTINEL',v['text']);self.assertNotIn('BLUE',v['text'])
  rows=await self.runtime.store.run(lambda d:d.execute('SELECT result FROM turns').fetchall());self.assertNotIn('ACTUAL_SYNTHETIC_SENTINEL',str([tuple(x) for x in rows]))
 async def test_sse_has_honest_final_text_and_done(self):
  self.answers=[{'content':'Everything done.','tool_calls':[]}];r=await self.call(stream=True);self.assertEqual(r.status,200);text=await r.text();self.assertTrue(text.endswith('data: [DONE]\n\n'));events=[json.loads(l[6:]) for l in text.splitlines() if l.startswith('data: {')];last=events[-1];self.assertEqual(last['data']['status'],'unverified');self.assertNotIn('task_id',last['data']['text']);self.assertNotIn('Everything done',last['data']['text'])
 async def test_profile_fields_not_silently_discarded(self):
  r=await self.call({'profile':'read','paths':['x'],'tests':['test_app.py']});self.assertEqual(r.status,400);self.assertEqual(self.requests,[])
 async def test_resume_receives_actual_failure_diagnostics(self):
  a='api-token:14001';wid=self.w['id'];await self.runtime.invoke_result(a,wid,'fs.write',{'path':'x.txt','content':'data'},'seed')
  t=await self.runtime.evidence.admit(a,wid,'first-turn',{'profile':'read','paths':['x.txt']})
  key='failed-read';await self.runtime.evidence.intent(a,wid,t['id'],key,'fs.read',{'path':'absent.txt'})
  from backend.coding_runtime.core import CodingError
  with self.assertRaisesRegex(CodingError,'not_found'):
   await self.runtime.invoke_result(a,wid,'fs.read',{'path':'absent.txt'},key)
  await self.runtime.evidence.link(a,wid,t['id'],key)
  self.answers=[{'content':'I cannot verify this task yet.','tool_calls':[]}]
  r=await self.call({'task_id':t['id']});self.assertEqual(r.status,200)
  tool_messages=[m for m in self.requests[-1]['messages'] if m.get('role')=='tool']
  self.assertTrue(tool_messages);self.assertIn('failed',tool_messages[-1]['content'])
 async def test_sse_conflict_keeps_typed_error_and_done(self):
  self.answers=[{'content':'A conceptual answer.','tool_calls':[]}]
  headers={'Idempotency-Key':'fixed-sse-conflict'}
  r=await self.call({'profile':'conversation'},stream=True,headers=headers);self.assertEqual(r.status,200);await r.read()
  r=await self.call({'profile':'plan_only'},stream=True,headers=headers);self.assertEqual(r.status,200);text=await r.text()
  self.assertTrue(text.endswith('data: [DONE]\n\n'));events=[json.loads(l[6:]) for l in text.splitlines() if l.startswith('data: {')]
  self.assertEqual(events[-1]['type'],'error');self.assertEqual(events[-1]['data'],{'code':'idempotency_conflict','http_status':409})

if __name__=='__main__':unittest.main()
