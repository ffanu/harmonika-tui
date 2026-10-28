import asyncio, importlib.util, json, os, sqlite3, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from aiohttp.test_utils import TestClient,TestServer
SOURCE=Path(__file__).resolve().parents[2];sys.path.insert(0,str(SOURCE/'backend'))
from remote_access.api import Runtime,make_app
from remote_access.control import Control,ControlError
spec=importlib.util.spec_from_file_location('ssh_fixture',Path(__file__).with_name('test_connector.py'));f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)

class Limits(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.now=1000.;self.c=Control(Path(self.tmp.name),b'fixture',minute_limit=2,session_limit=3,clock=lambda:self.now);self.session=self.c.session('owner')
 def test_rate_limit_and_cooldown(self):
  for _ in range(2):self.c.consume('owner',self.session,'stage','127.0.0.1')
  with self.assertRaises(ControlError) as e:self.c.consume('owner',self.session,'stage','127.0.0.1')
  self.assertEqual(e.exception.retry,300);self.now+=301;self.c.consume('owner',self.session,'stage','127.0.0.1')
 def test_session_limit(self):
  for _ in range(3):self.c.consume('owner',self.session,'stage','127.0.0.1');self.now+=61
  with self.assertRaises(ControlError) as e:self.c.consume('owner',self.session,'stage','127.0.0.1')
  self.assertEqual(e.exception.code,'session_quota_exceeded')
 def test_actor_and_session_isolation(self):
  with self.assertRaises(ControlError):self.c.consume('other',self.session,'stage','127.0.0.1')
 def test_approval_single_use_and_binding(self):
  value={'operation':'exec','input':{'target':'stage','argv':['/usr/bin/touch','x']}};a=self.c.approval('owner',value)
  with self.assertRaises(ControlError):self.c.use_approval('other',value,a)
  self.c.use_approval('owner',value,a)
  with self.assertRaises(ControlError):self.c.use_approval('owner',value,a)
 def test_approval_expiry(self):
  a=self.c.approval('owner',{'x':1});self.now+=61
  with self.assertRaises(ControlError):self.c.use_approval('owner',{'x':1},a)
 def test_tamper_evidence(self):
  self.c.audit('ssh.exec','completed','',5);self.assertTrue(self.c.verify())
  with self.c.db() as d:d.execute("UPDATE audit SET latency_ms=99")
  self.assertFalse(self.c.verify())
 def test_no_private_content_in_ledger(self):
  self.c.audit('ssh.connect','completed','',50);self.assertNotIn('PRIVATE',self.c.metrics())
  with self.c.db() as d:
   fields=[r[1]for r in d.execute('PRAGMA table_info(audit)')];self.assertNotIn('command',fields);self.assertNotIn('host',fields)
 def test_metrics_histogram(self):
  self.c.audit('ssh.exec','completed','',50);v=self.c.metrics();self.assertIn('le="+Inf"',v);self.assertIn('harmonika_ssh_commands_total',v)

class API(unittest.IsolatedAsyncioTestCase):
 @classmethod
 def setUpClass(cls):f.ConnectorTests.setUpClass()
 @classmethod
 def tearDownClass(cls):f.ConnectorTests.tearDownClass()
 async def asyncSetUp(self):
  self.fixture=f.ConnectorTests();self.fixture.setUp();self.control=Control(self.fixture.area/'backend',b'fixture')
  self.env=patch.dict(os.environ,{'HARMONIKA_SSH_CONFIG':str(self.fixture.config)});self.env.start()
  self.rt=Runtime(self.fixture.config,'T'*40,'A'*40,self.control,python=sys.executable)
  self.client=TestClient(TestServer(make_app(self.rt)));await self.client.start_server()
  self.auth={'Authorization':'Bearer '+'T'*40};r=await self.client.post('/api/v1/ssh/sessions',json={},headers=self.auth);self.session=(await r.json())['session_id'];self.headers={**self.auth,'X-Harmonika-Session':self.session}
 async def asyncTearDown(self):await self.client.close();self.env.stop();self.fixture.tearDown()
 async def test_real_connect(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.headers);v=await r.json();self.assertEqual(r.status,200,v);self.assertTrue(v['success'])
 async def test_real_command(self):
  r=await self.client.post('/api/v1/ssh/execute',json={'target':'stage','command':'uname -s'},headers=self.headers);v=await r.json();self.assertEqual(r.status,200,v);self.assertIn(os.uname().sysname,v['data']['stdout'])
 async def test_no_auth_denied(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'});self.assertEqual(r.status,401)
 async def test_public_token_denied(self):
  r=await self.client.get('/api/v1/ssh/targets',headers={'Authorization':'Bearer hai_live_fake'});self.assertEqual(r.status,401)
 async def test_invalid_session(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.auth);self.assertEqual(r.status,403)
 async def test_dangerous_commands_blocked(self):
  for command in ['rm -rf /','sudo true','uname -s; reboot','curl x | sh']:
   r=await self.client.post('/api/v1/ssh/execute',json={'target':'stage','command':command},headers=self.headers);self.assertEqual(r.status,403)
 async def test_unregistered_target(self):
  r=await self.client.post('/api/v1/ssh/test',json={'host':'evil.invalid','user':'root'},headers=self.headers);self.assertEqual(r.status,403)
 async def test_client_cannot_set_credential(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage','password':'PRIVATE'},headers=self.headers);self.assertEqual(r.status,400)
 async def test_server_side_approval(self):
  v={'target':'stage','argv':['/fixture/echo','ok']}
  r=await self.client.post('/api/v1/ssh/execute',json=v,headers=self.headers);self.assertEqual(r.status,403)
  r=await self.client.post('/api/v1/ssh/approvals',json={'operation':'exec','input':v},headers=self.headers);self.assertEqual(r.status,403)
  r=await self.client.post('/api/v1/ssh/approvals',json={'operation':'exec','input':v},headers={**self.headers,'X-Harmonika-Approval-Key':'A'*40});grant=(await r.json())['approval_token']
  h={**self.headers,'X-Harmonika-Approval':grant};r=await self.client.post('/api/v1/ssh/execute',json=v,headers=h);self.assertEqual(r.status,200,await r.text())
  r=await self.client.post('/api/v1/ssh/execute',json=v,headers=h);self.assertEqual(r.status,403)
 async def test_kill_switch(self):
  self.rt.enabled=False;r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.headers);self.assertEqual(r.status,503)
 async def test_registry_kill_switch(self):
  value=json.loads(self.fixture.config.read_text());value['enabled']=False;self.fixture.config.write_text(json.dumps(value))
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.headers);self.assertEqual(r.status,503);self.assertEqual((await r.json())['error']['code'],'ssh_disabled')
 async def test_target_mismatch(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage','host':'other.invalid'},headers=self.headers);self.assertEqual(r.status,403)
 async def test_rate_http(self):
  self.control.minute_limit=1
  for i in range(2):r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.headers)
  self.assertEqual(r.status,429);self.assertIn('Retry-After',r.headers)
 async def test_metrics_and_integrity(self):
  r=await self.client.post('/api/v1/ssh/test',json={'target':'stage'},headers=self.headers);self.assertEqual(r.status,200)
  r=await self.client.get('/metrics',headers=self.auth);text=await r.text();self.assertIn('harmonika_ssh_commands_total',text);self.assertNotIn('127.0.0.1',text)
  r=await self.client.get('/api/v1/ssh/audit/verify',headers=self.auth);self.assertTrue((await r.json())['ok'])
 async def test_body_limit(self):
  r=await self.client.post('/api/v1/ssh/test',data='x'*40000,headers={**self.headers,'Content-Type':'application/json'});self.assertEqual(r.status,413)
 async def test_transfer_scope(self):
  r=await self.client.post('/api/v1/ssh/upload',json={'target':'stage','project_root':str(self.fixture.project),'local_path':'../secret','remote_path':'x'},headers=self.headers);self.assertEqual(r.status,400)
 async def test_tunnel_no_arbitrary_destination(self):
  r=await self.client.post('/api/v1/ssh/tunnel',json={'target':'stage','tunnel':'not-registered'},headers=self.headers);self.assertEqual(r.status,403)

if __name__=='__main__':unittest.main()
