import asyncio,base64,hashlib,importlib.util,json,os,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]/'backend/media_suite'
sys.path.insert(0,str(ROOT))
from contracts import Failure,binary,validate,url
from process import execute
from service import Suite
from client import MediaAdapter,AdapterError
from aiohttp.test_utils import TestClient,TestServer

def resolver_public(*a,**k):return [(2,1,6,'',('93.184.216.34',443))]
class Contracts(unittest.TestCase):
 def test_invalid_base64(self):
  for x in ['',None,'data:image/png;base64,abc','%%%']:
   with self.assertRaises(Failure):binary(x)
 def test_binary_limit(self):
  with self.assertRaises(Failure):binary(base64.b64encode(b'123').decode(),2)
 def test_paths_and_extra_fields_denied(self):
  with self.assertRaises(Failure):validate('audio.transcribe',{'audio_base64':'YQ==','path':'/etc/passwd'}, {})
 def test_caller_cannot_enable_f5(self):
  with self.assertRaisesRegex(Failure,'license_restricted'):validate('audio.synthesize',{'text':'secret'}, {})
 def test_unqualified_tts_language(self):
  with self.assertRaisesRegex(Failure,'language_unqualified'):validate('audio.synthesize',{'text':'Hi','reference_text':'Hello','language':'id','reference_audio_base64':'YQ=='},{'tts_research_enabled':True})
 def test_browser_blocks_unregistered_and_credential_urls(self):
  for value in ['http://example.com','https://localhost','https://example.com@127.0.0.1','https://user:password@example.com','https://example.com:8443']:
   with self.assertRaises(Failure):url(value,['example.com'],resolver_public)
 def test_browser_blocks_mixed_dns_private(self):
  with self.assertRaisesRegex(Failure,'private_target_denied'):url('https://example.com',['example.com'],lambda *a,**k:resolver_public()+[(2,1,6,'',('127.0.0.1',443))])
 def test_operator_public_host(self):self.assertEqual(url('https://example.com',['example.com'],resolver_public),'https://example.com')
 def test_bounded_child_timeout_and_invalid_json(self):
  if os.name=='nt':self.skipTest('target native test separate')
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'child.py';p.write_text('import time;time.sleep(30)')
   with self.assertRaisesRegex(Failure,'engine_timeout'):execute(sys.executable,p,{},.1,d)
   p.write_text('print("not json")')
   with self.assertRaisesRegex(Failure,'invalid_engine_payload'):execute(sys.executable,p,{},2,d)
 def test_adapter_only_loopback(self):
  for v in ['http://192.168.1.5:1000','https://127.0.0.1:1000','http://127.0.0.1:3000','http://localhost:18231','http://127.0.0.1:18231/?key=secret']:
   with self.assertRaises((AdapterError,ValueError)):MediaAdapter(v,'not-read')
 def test_model_manifest_pinned(self):
  rows=json.loads((ROOT/'models.json').read_text());self.assertEqual(len(rows),10)
  for row in rows:
   self.assertRegex(row['revision'],'^[a-f0-9]{40}$');self.assertRegex(row['sha256'],'^[a-f0-9]{64}$');self.assertGreater(row['bytes'],0);self.assertIn(row['revision'],row['url'])
class API(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  (self.root/'config.json').write_text(json.dumps({'role':'windows','browser_allowed_hosts':[],'tts_research_enabled':False}))
  (self.root/'service.token').write_text('t'*48);(self.root/'qualification.json').write_text(json.dumps({'audio.transcribe':{'real_test_passed':True}}))
  env=self.root/'.asr'/('Scripts' if os.name=='nt' else 'bin');env.mkdir(parents=True);(env/('python.exe' if os.name=='nt' else 'python')).touch()
  self.calls=0
  def runner(*a):self.calls+=1;return {'status':'completed','result':{'transcript':'PRIVATE_TRANSCRIPT','language':'id'}}
  self.suite=Suite(self.root,runner);self.client=TestClient(TestServer(self.suite.app()));await self.client.start_server()
  self.headers={'Authorization':'Bearer '+'t'*48,'Idempotency-Key':'test_request_one'}
  self.body={'skill_id':'audio.transcribe','input':{'audio_base64':base64.b64encode(b'PRIVATE_AUDIO').decode(),'language':'id'}}
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def submit(self,body=None,headers=None):return await self.client.post('/v1/jobs',json=body or self.body,headers=headers or self.headers)
 async def test_auth_required(self):
  r=await self.client.get('/health');self.assertEqual(r.status,401)
 async def test_health_does_not_claim_all_ready(self):
  r=await self.client.get('/health',headers=self.headers);j=await r.json();self.assertTrue(j['capabilities']['audio.transcribe']['ready']);self.assertFalse(j['capabilities']['image.upscale']['ready'])
 async def test_idempotency_and_no_private_data_in_sqlite(self):
  a=await self.submit();self.assertEqual(a.status,202);jid=(await a.json())['job_id']
  for _ in range(50):
   result=await (await self.client.get('/v1/jobs/'+jid,headers=self.headers)).json()
   if result['state']=='completed':break
   await asyncio.sleep(.01)
  self.assertEqual(result['result']['transcript'],'PRIVATE_TRANSCRIPT')
  b=await self.submit();self.assertEqual((await b.json())['job_id'],jid);self.assertEqual(self.calls,1)
  sql=''.join(self.suite.db.iterdump());self.assertNotIn('PRIVATE',sql);self.assertNotIn(self.body['input']['audio_base64'],sql);self.assertNotIn('t'*48,sql)
 async def test_conflict(self):
  await self.submit();changed=json.loads(json.dumps(self.body));changed['input']['language']='en';r=await self.submit(changed);self.assertEqual(r.status,409)
 async def test_unqualified_operation_rejected(self):
  r=await self.submit({'skill_id':'image.upscale','input':{'image_base64':'YQ=='}});self.assertEqual(r.status,422)
 async def test_f5_cannot_be_enabled_by_body(self):
  r=await self.submit({'skill_id':'audio.synthesize','input':{'text':'say secret'}});self.assertEqual((await r.json())['error']['code'],'license_restricted')
 async def test_bad_idempotency(self):
  r=await self.submit(headers={'Authorization':self.headers['Authorization']});self.assertEqual(r.status,400)
 async def test_unknown_job(self):
  r=await self.client.get('/v1/jobs/'+'0'*32,headers=self.headers);self.assertEqual(r.status,404)
 async def test_restart_marks_inflight_unknown(self):
  self.suite.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",('x','owner','idem','hash','audio.transcribe','running',None,None,time.time()));self.suite.db.commit()
  other=Suite(self.root,lambda *a:None)
  self.assertEqual(other.db.execute("SELECT state FROM jobs WHERE id='x'").fetchone()[0],'unknown');other.db.close()
 async def test_raw_exception_redacted(self):
  def fail(*a):raise RuntimeError('PRIVATE_PROMPT_SECRET_TOKEN')
  self.suite.runner=fail;a=await self.submit();jid=(await a.json())['job_id']
  for _ in range(50):
   r=await (await self.client.get('/v1/jobs/'+jid,headers=self.headers)).json()
   if r['state']=='failed':break
   await asyncio.sleep(.01)
  self.assertEqual(r['error_type'],'engine_unavailable');self.assertNotIn('PRIVATE_PROMPT',''.join(self.suite.db.iterdump()))
 async def test_malformed_success_rejected(self):
  self.suite.runner=lambda *a:{'status':'completed','result':{'transcript':123}}
  a=await self.submit();jid=(await a.json())['job_id']
  for _ in range(50):
   r=await (await self.client.get('/v1/jobs/'+jid,headers=self.headers)).json()
   if r['state']=='failed':break
   await asyncio.sleep(.01)
  self.assertEqual(r['error_type'],'invalid_engine_payload');self.assertIsNone(r['result'])
 async def test_expired_result(self):
  self.suite.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)",('a',''+hashlib.sha256(('t'*48).encode()).hexdigest(),'old','hash','audio.transcribe','completed',None,1,time.time()));self.suite.db.commit();self.suite.results['a']=(time.time()-1801,{'transcript':'secret'})
  r=await (await self.client.get('/v1/jobs/a',headers=self.headers)).json();self.assertTrue(r['result_expired']);self.assertIsNone(r['result'])
if __name__=='__main__':unittest.main()
