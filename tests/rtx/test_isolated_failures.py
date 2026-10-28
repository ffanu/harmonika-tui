"""Synthetic PC2 faults; no production interruption, real GPU allocation or user data."""
import asyncio,base64,io,json,sqlite3,tempfile,unittest
from pathlib import Path
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase,TestServer
from PIL import Image
from backend.rtx_queue.adapter import RTXAdapter
from backend.rtx_queue.core import RTXError,Store
from backend.rtx_queue.service import create_app,QUEUE
SENTINEL='PRIVATE_CUSTOMER_CHAT_PHONE_TOKEN_DO_NOT_LOG'
TOKEN='fixture-key-do-not-use'
class Faults(AioHTTPTestCase):
 async def get_application(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.mode='busy';self.post_count=0
  async def health(req):return web.json_response({'ok':True,'cuda':True,'device':'cuda','model':'fixture'})
  async def generate(req):
   self.post_count+=1
   if self.mode=='busy':return web.json_response({'detail':SENTINEL},status=429)
   if self.mode=='oom':return web.json_response({'ok':False,'error':'gpu_memory_full','detail':SENTINEL})
   if self.mode=='bad':return web.json_response({'ok':True,'device':'cuda','b64_png':SENTINEL,'detail':SENTINEL})
   if self.mode=='timeout':await asyncio.sleep(.6)
   return web.json_response({'ok':False,'detail':SENTINEL})
  upstream=web.Application();upstream.add_routes([web.get('/health',health),web.post('/generate',generate)])
  self.pc=TestServer(upstream);await self.pc.start_server()
  self.adapter=RTXAdapter(str(self.pc.make_url('')).rstrip('/'),'legacy',connect_timeout=.1,job_timeout=.2)
  import hashlib
  return create_app(self.root/'queue',self.adapter,{'fixture':hashlib.sha256(TOKEN.encode()).hexdigest()})
 async def tearDownAsync(self):
  await super().tearDownAsync();await self.pc.close();self.temp.cleanup()
 async def submit_and_wait(self):
  r=await self.client.post('/v1/rtx/jobs',headers={'Authorization':'Bearer '+TOKEN,'Idempotency-Key':'fixture-'+self.mode},json={'kind':'generate','prompt':SENTINEL,'size':'512x512','reference_file_ids':[]});self.assertEqual(r.status,202)
  job=(await r.json())['job']
  for _ in range(120):
   r=await self.client.get('/v1/rtx/jobs/'+job['job_id'],headers={'Authorization':'Bearer '+TOKEN});out=(await r.json())['job']
   if out['status'] in ('failed','unknown','completed'):return out
   await asyncio.sleep(.01)
  self.fail('queue did not reach explicit terminal or unknown state')
 def check_log(self,error):
  db=self.root/'queue/queue.sqlite3'
  with sqlite3.connect(db) as c:
   cols=[r[1] for r in c.execute('PRAGMA table_info(skill_failures)')];rows=c.execute('SELECT timestamp,skill_id,error_type,latency FROM skill_failures').fetchall()
  self.assertEqual(cols,['timestamp','skill_id','error_type','latency']);self.assertTrue(rows);self.assertEqual(rows[-1][1],'image.generate');self.assertEqual(rows[-1][2],error)
  self.assertNotIn(SENTINEL,json.dumps(rows));self.assertGreaterEqual(rows[-1][3],0)
 async def test_busy_definitive_rejection_no_unknown_or_retry(self):
  out=await self.submit_and_wait();self.assertEqual(out['status'],'failed');self.assertEqual(out['error']['code'],'worker_busy');self.assertEqual(self.post_count,1);self.check_log('graphics_busy');self.assertFalse(self.app[QUEUE].task.done())
 async def test_vram_full_safe_failure(self):
  self.mode='oom';out=await self.submit_and_wait();self.assertEqual(out['status'],'failed');self.assertEqual(out['error']['code'],'worker_vram_full');self.check_log('graphics_vram_full');self.assertEqual(self.post_count,1)
 async def test_bad_payload_isolated(self):
  self.mode='bad';out=await self.submit_and_wait();self.assertEqual(out['status'],'failed');self.check_log('graphics_bad_payload');self.assertNotIn(SENTINEL,json.dumps(out));self.assertFalse(self.app[QUEUE].task.done())
 async def test_timeout_remains_uncertain_no_second_dispatch(self):
  self.mode='timeout';out=await self.submit_and_wait();self.assertEqual(out['status'],'unknown');self.check_log('graphics_timeout');self.assertEqual(self.post_count,1);self.assertFalse(self.app[QUEUE].task.done())
 async def test_offline_is_safe_and_logged(self):
  await self.pc.close();out=await self.submit_and_wait();self.assertEqual(out['status'],'failed');self.check_log('graphics_offline');self.assertEqual(self.post_count,0)
 async def test_logging_failure_does_not_crash_gateway(self):
  class Unavailable:
   def record(self,*args):raise OSError(SENTINEL)
   def summary(self):raise OSError(SENTINEL)
  self.app[QUEUE].failures=Unavailable();out=await self.submit_and_wait();self.assertEqual(out['status'],'failed');self.assertFalse(self.app[QUEUE].task.done())
  r=await self.client.get('/health',headers={'Authorization':'Bearer '+TOKEN});v=await r.json();self.assertEqual(r.status,200);self.assertTrue(v['diagnostics']['logging_degraded']);self.assertNotIn(SENTINEL,json.dumps(v))
if __name__=='__main__':unittest.main()
