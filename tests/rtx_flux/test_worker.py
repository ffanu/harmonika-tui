import asyncio, importlib.util, json, tempfile, unittest
from pathlib import Path
from aiohttp.test_utils import AioHTTPTestCase
SPEC=importlib.util.spec_from_file_location('flux_worker',Path(__file__).resolve().parents[2]/'backend/rtx_flux/worker.py')
w=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(w)
class ContractTests(unittest.TestCase):
 def test_preserves_long_prompt(self):
  prompt='A detailed futuristic city with Indonesian architecture. '*65
  p,x,y,s=w.validate({'prompt':prompt,'width':512,'height':512,'seed':1})
  self.assertEqual(p,prompt);self.assertEqual((x,y),(1024,1024))
  g=w.workflow(p,x,y,s,'test');self.assertEqual(g['4']['inputs']['text'],prompt)
  self.assertEqual(g['10']['inputs']['steps'],4);self.assertEqual(g['8']['inputs']['cfg'],1)
 def test_invalid_payload(self):
  for data in [None,{}, {'prompt':''}, {'prompt':'x'*12001},{'prompt':'hello','width':2048},{'prompt':'hello','seed':True},{'prompt':'hello','width':True},{'prompt':'hello','reference_files':['a']}]:
   with self.subTest(data=str(data)[:30]),self.assertRaises(ValueError):w.validate(data)
 def test_dimensions_preserve_aspect_ratio(self):
  self.assertEqual(w.validate({'prompt':'a panorama','width':1024,'height':768})[1:3],(1024,768))
class ServerTests(AioHTTPTestCase):
 async def get_application(self):
  self.temp=tempfile.TemporaryDirectory();app=w.create_app(Path(self.temp.name));self.worker=app[w.WORKER_KEY];return app
 async def tearDownAsync(self):
  await super().tearDownAsync();self.temp.cleanup()
 async def test_not_ready_until_approved(self):
  r=await self.client.post('/generate',json={'prompt':'make a city'})
  self.assertEqual(r.status,503);self.assertEqual((await r.json())['error'],'model_not_approved')
 async def test_invalid_no_dispatch(self):
  r=await self.client.post('/generate',json={'prompt':'x','width':4096})
  self.assertEqual(r.status,422)
 async def test_corrupt_json(self):
  r=await self.client.post('/generate',data='bad-json');self.assertEqual(r.status,422)
 async def test_busy_rejected(self):
  self.worker.approved=True;self.worker.warmed=True;self.worker.refresh_approval=lambda:None;self.worker.active=True
  r=await self.client.post('/generate',json={'prompt':'a city'})
  self.assertEqual(r.status,429)
 async def test_uncertain_gpu_rejected(self):
  self.worker.approved=True;self.worker.warmed=True;self.worker.refresh_approval=lambda:None;self.worker.uncertain_pid='old-uncertain-id'
  r=await self.client.post('/generate',json={'prompt':'a city'})
  self.assertEqual(r.status,503);self.assertEqual((await r.json())['error'],'gpu_job_recovery_required')
 async def test_health_is_honest(self):
  async def fake(*args,**kw):return {'devices':[{'type':'cuda'}]}
  self.worker.request=fake
  r=await self.client.get('/health');v=await r.json()
  self.assertFalse(v['ok']);self.assertFalse(v['loaded']);self.assertTrue(v['cuda'])
 async def test_async_render_does_not_block_health_and_no_overlap(self):
  release=asyncio.Event()
  async def fake_request(*args,**kw):return {'devices':[{'type':'cuda'}]}
  async def fake_render(data):
   try:await release.wait();return {'ok':True,'model':w.MODEL}
   finally:self.worker.active=False
  self.worker.request=fake_request;self.worker.render=fake_render
  r=await self.client.post('/generate',json={'prompt':'a city'},headers={'X-Harmonika-Qualification':'local-test'})
  try:
   health=await asyncio.wait_for(self.client.get('/health'),.5)
   self.assertEqual(health.status,200)
   busy=await self.client.post('/generate',json={'prompt':'another city'},headers={'X-Harmonika-Qualification':'local-test'})
   self.assertEqual(busy.status,429)
  finally:release.set()
  self.assertTrue((await r.json())['ok'])

class TechnicalErrorFirewall(unittest.TestCase):
 def test_oom_type_classified_without_exception_text(self):
  sentinel='PRIVATE_CUSTOMER_TEXT_TOKEN'
  value={'status':{'messages':[['execution_error',{'exception_type':'torch.OutOfMemoryError','exception_message':sentinel,'inputs':sentinel}]]}}
  self.assertEqual(w.comfy_failure(value),'gpu_memory_full');self.assertNotIn(sentinel,w.comfy_failure(value))
 def test_unknown_exception_type_not_echoed(self):
  self.assertEqual(w.comfy_failure({'status':{'messages':[['execution_error',{'exception_type':'PRIVATE_SENTINEL'}]]}}),'image_render_failed')

if __name__=='__main__':unittest.main()
