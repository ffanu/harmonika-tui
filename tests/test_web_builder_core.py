import asyncio,base64,io,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
try:
 from backend.web_builder.core import Runtime,snapshot_check
 READY=True
except ModuleNotFoundError as e:
 if not e.name.startswith('cryptography'):raise
 READY=False
from backend.web_builder.common import BuilderError
class FakeSandbox:
 async def capabilities(self):return {'ready':True}
 async def stop(self,j):return True
 async def run(self,files,skill,args,j,callback,deadline_seconds):return {'status':'completed','exit_code':0,'snapshot':files,'stdout':'fixture','stderr':''}
@unittest.skipUnless(READY,'dedicated builder venv required; mandatory suite run separately')
class Core(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.rt=Runtime(self.tmp.name,os.urandom(32),FakeSandbox());self.actor='api-token:1';self.p=self.rt.create(self.actor,'PRIVATE_BUILD_FIXTURE','create')['id'];self.addAsyncCleanup(self.rt.close)
 async def invoke(self,skill='test.run.unit',args=None,idem='one'):
  schema={'type':'object','properties':{},'additionalProperties':False}
  with patch('backend.web_builder.registry.schema_for',return_value=schema):return await self.rt.invoke(self.actor,self.p,skill,args or {},idem)
 async def test_ownership_and_create_replay(self):
  self.assertEqual(self.rt.create(self.actor,'PRIVATE_BUILD_FIXTURE','create')['id'],self.p)
  for fn in (lambda:self.rt.project('api-token:2',self.p),lambda:self.rt.artifact('api-token:2',self.p)):
   with self.assertRaises(BuilderError) as e:fn()
   self.assertEqual(e.exception.status,404)
 async def test_idempotency_and_approval_binding(self):
  j=await self.invoke();self.assertEqual(j['state'],'awaiting_approval');self.assertEqual((await self.invoke())['id'],j['id'])
  with self.assertRaises(BuilderError):await self.rt.approve(self.actor,j['id'],'wrong')
  await self.rt.stop(self.actor,j['id'])
  with self.assertRaises(BuilderError):await self.rt.approve(self.actor,j['id'],j['input_hash'])
 async def test_atomic_generation_private_encryption_and_artifact(self):
  async def dispatch(*args):return {'status':'completed','execution_kind':'artifact_generation','writes':[{'path':'index.html','content':'PRIVATE_SOURCE_MARKER'}],'data':{}}
  self.rt.dispatch=dispatch;j=await self.invoke('fe.gen.page');await asyncio.gather(*list(self.rt.tasks.values()));j=self.rt.job(self.actor,j['id']);self.assertEqual(j['state'],'completed');self.assertEqual(j['result']['files_written'],['index.html'])
  with zipfile.ZipFile(io.BytesIO(self.rt.artifact(self.actor,self.p))) as z:self.assertEqual(z.read('index.html'),b'PRIVATE_SOURCE_MARKER')
  for file in Path(self.tmp.name).glob('state.sqlite*'):self.assertNotIn(b'PRIVATE_SOURCE_MARKER',file.read_bytes());self.assertNotIn(b'PRIVATE_BUILD_FIXTURE',file.read_bytes())
 async def test_failed_generation_publishes_nothing(self):
  async def dispatch(*args):return {'status':'completed','writes':[{'path':'good.txt','content':'fixture'},{'path':'../outside','content':'bad'}]}
  self.rt.dispatch=dispatch;j=await self.invoke('fe.gen.page');await asyncio.gather(*list(self.rt.tasks.values()));self.assertEqual(self.rt.job(self.actor,j['id'])['state'],'failed');self.assertFalse(self.rt.project_view(self.actor,self.p)['files'])
 async def test_stopped_job_does_not_publish_and_sse_recovery(self):
  entered=asyncio.Event();release=asyncio.Event()
  async def dispatch(*args):entered.set();await release.wait();return {'status':'completed','writes':[{'path':'x','content':'late'}]}
  self.rt.dispatch=dispatch;j=await self.invoke('fe.gen.page');await entered.wait();await self.rt.stop(self.actor,j['id']);release.set();await asyncio.gather(*list(self.rt.tasks.values()));self.assertEqual(self.rt.job(self.actor,j['id'])['state'],'stopped');self.assertFalse(self.rt.project_view(self.actor,self.p)['files']);rows=self.rt.events(self.actor,j['id']);self.assertEqual(rows[-1]['type'],'job.stopped');self.assertEqual(self.rt.events(self.actor,j['id'],rows[-1]['sequence']),[])
 async def test_cross_tenant_job_and_injected_secret_fields(self):
  j=await self.invoke()
  with self.assertRaises(BuilderError):self.rt.job('api-token:2',j['id'])
  with self.assertRaises(BuilderError):await self.rt.invoke(self.actor,self.p,'test.run.unit',{'actor':'api-token:2'},'inject')
 def test_paths_and_binary_bounds(self):
  for p in ('../secret','/etc/passwd','a/../b','.ssh/id_rsa','a\\b','./x'):
   with self.assertRaises(BuilderError):snapshot_check({p:'eA=='})
  snapshot_check({'assets/x.txt':base64.b64encode(b'hello').decode()})
 async def test_recovery_marks_only_running_queued_interrupted(self):
  j=await self.invoke();await self.rt.recover();self.assertEqual(self.rt.job(self.actor,j['id'])['state'],'awaiting_approval')

 async def test_shutdown_prevents_late_pure_generation_publish(self):
  entered=asyncio.Event()
  async def dispatch(*args):entered.set();await asyncio.sleep(30);return {'status':'completed','writes':[{'path':'late.txt','content':'bad'}]}
  self.rt.dispatch=dispatch;j=await self.invoke('fe.gen.page');await entered.wait();await self.rt.close();self.assertEqual(self.rt.job(self.actor,j['id'])['state'],'interrupted');self.assertFalse(self.rt.project_view(self.actor,self.p)['files'])

 async def test_storage_root_permissions_and_symlink(self):
  root=Path(self.tmp.name)/'existing';root.mkdir(mode=0o755);r=Runtime(root,os.urandom(32),FakeSandbox());self.assertEqual(root.stat().st_mode&0o777,0o700);await r.close()
  link=Path(self.tmp.name)/'alias';link.symlink_to(root,target_is_directory=True)
  with self.assertRaises(BuilderError):Runtime(link,os.urandom(32),FakeSandbox())
