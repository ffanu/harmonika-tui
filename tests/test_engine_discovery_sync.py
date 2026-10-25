import asyncio,hashlib,importlib.util,json,threading,unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).parent))
from test_engine_discovery_real_http import RealHTTP,SOURCE
from backend.engine_registry.discovery import SanitizedError
spec=importlib.util.spec_from_file_location('master_sync_client',Path(__file__).resolve().parents[1]/'runtime/tools/master_cli.py');client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)

class SyncHTTP(RealHTTP):
 """Real parent gateway + actual scanner, no remote services or user data."""
 async def hold_scan(self,source):
  folder=self.root/'metric';folder.mkdir(exist_ok=True);(folder/'skill.py').write_text(source);(folder/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}))
  entered=threading.Event();release=threading.Event();original=self.d._scan_sync
  def held():entered.set();release.wait(2);return original()
  wrapper=patch.object(self.d,'_scan_sync',side_effect=held);wrapper.start()
  task=asyncio.create_task(self.d.refresh())
  for _ in range(100):
   if entered.is_set():break
   await asyncio.sleep(.005)
  self.assertTrue(entered.is_set())
  return release,task,wrapper
 async def test_invoke_waits_then_executes_new_snapshot_once(self):
  await self.plugin(SOURCE.replace("text=payload['text']","raise RuntimeError('OLD')\n text=payload['text']"))
  release,scan,wrapper=await self.hold_scan(SOURCE)
  try:
   self.assertEqual(self.d.summary()['result'],'E_SCAN_BUSY')
   request=asyncio.create_task(self.call());await asyncio.sleep(.05);self.assertFalse(request.done())
   release.set();await scan;code,data=await request
   self.assertEqual(code,200);self.assertEqual(data['result']['words'],2)
  finally:release.set();await scan;wrapper.stop()
 async def test_refresh_wait_true_blocks_until_publication(self):
  release,scan,wrapper=await self.hold_scan(SOURCE)
  try:
   pending=asyncio.create_task(self.d.refresh(wait=True));await asyncio.sleep(.03);self.assertFalse(pending.done())
   release.set();await scan;result=await pending
   self.assertEqual(result['registered'],1);self.assertFalse(result['scanning']);self.assertEqual(result['result'],'OK')
  finally:release.set();await scan;wrapper.stop()
 async def test_wait_timeout_cancel_and_waiter_cap_are_bounded(self):
  release,scan,wrapper=await self.hold_scan(SOURCE)
  tasks=[]
  try:
   with self.assertRaises(SanitizedError) as error:await self.d.wait_ready(timeout=.02)
   self.assertEqual(error.exception.code,'E_SCAN_BUSY');self.assertEqual(self.d._waiters,0)
   tasks=[asyncio.create_task(self.d.wait_ready(timeout=1)) for _ in range(16)];await asyncio.sleep(.03)
   with self.assertRaises(SanitizedError):await self.d.wait_ready(timeout=.02)
   for task in tasks:task.cancel()
   await asyncio.gather(*tasks,return_exceptions=True);self.assertEqual(self.d._waiters,0)
  finally:release.set();await scan;wrapper.stop()
 async def test_removed_module_not_executed_from_stale_snapshot(self):
  import shutil
  await self.plugin();shutil.rmtree(self.root/'metric')
  entered=threading.Event();release=threading.Event();original=self.d._scan_sync
  def held():entered.set();release.wait(2);return original()
  with patch.object(self.d,'_scan_sync',side_effect=held):
   scan=asyncio.create_task(self.d.refresh())
   while not entered.is_set():await asyncio.sleep(.005)
   pending=asyncio.create_task(self.call());await asyncio.sleep(.03);self.assertFalse(pending.done());release.set();await scan
   self.assertEqual((await pending)[0],404)
 async def test_scan_busy_error_has_retry_after(self):
  with patch.object(self.d,'wait_ready',side_effect=SanitizedError('E_SCAN_BUSY')):
   r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'text.metrics','input':{'text':'x'}})
   self.assertEqual(r.status,503);self.assertEqual(r.headers['Retry-After'],'1');self.assertEqual((await r.json())['error']['code'],'discovery_scan_busy')

class ClientSync(unittest.TestCase):
 def test_get_poll_retries_before_returning_ready(self):
  items=iter([{'result':'E_SCAN_BUSY','retry_after_ms':20},{'scanning':True},{'status':'ready'}]);sleeps=[]
  value=client.wait_for_discovery(lambda:next(items),sleep=sleeps.append)
  self.assertEqual(value['status'],'ready');self.assertEqual(sleeps,[.02,.1])
 def test_timeout_is_bounded_and_never_retries_post(self):
  now=[0]
  def sleep(x):now[0]+=x
  with self.assertRaisesRegex(ValueError,'discovery_scan_busy'):client.wait_for_discovery(lambda:{'result':'E_SCAN_BUSY'},timeout=.05,clock=lambda:now[0],sleep=sleep)
  self.assertLessEqual(now[0],.05)
 def test_auth_or_invalid_status_fails_without_retry(self):
  for value in ({'error':{'code':'forbidden'}},None):
   with self.assertRaisesRegex(ValueError,'discovery_status_unavailable'):client.wait_for_discovery(lambda:value,sleep=lambda _:self.fail('must not retry'))
 def test_rejected_other_module_does_not_block_valid_registry(self):
  value={'status':'degraded','scanning':False,'result':'OK','registered':1}
  self.assertEqual(client.wait_for_discovery(lambda:value),value)
