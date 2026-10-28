import asyncio,contextlib,io,json,logging,sqlite3,tempfile,unittest
from pathlib import Path
from aiohttp.test_utils import AioHTTPTestCase
from backend.engine_registry.failure_log import FailureLog
from backend.master_engine.service import make_app
from backend.master_engine.diagnostics import errors_view,graphics_view
SENTINEL='PRIVATE_CUSTOMER_CHAT_AND_TOKEN'
class MechanicalHTTP(AioHTTPTestCase):
 async def get_application(self):
  self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'registry.sqlite3';self.log=FailureLog(self.db)
  def dispatch(id,data):
   if data.get('fail'):raise RuntimeError(SENTINEL)
   return {'status':'completed','analysis_performed':True}
  async def probe():return {'graphics':{'status':'online','latency_ms':2},'errors':{'status':'ok','count':0,'recent':[]}}
  self.headers={'X-Harmonika-Master-Secret':'s'*40,'X-Harmonika-Principal':'api-token:1'}
  return make_app('s'*40,skills={'safe.check':{'input_schema':{'type':'object'},'description':'Safe fixture'}},dispatcher=dispatch,mobile_enabled=False,failure_log=self.log,graphics_probe=probe)
 async def tearDownAsync(self):await super().tearDownAsync();self.temp.cleanup()
 async def test_private_failure_logged_without_identity_or_message(self):
  output=io.StringIO();handler=logging.StreamHandler(output);logger=logging.getLogger('harmonika.master.audit');logger.addHandler(handler);before=logger.level;logger.setLevel(logging.INFO)
  try:r=await self.client.post('/invoke',headers=self.headers,json={'skill_id':'safe.check','input':{'fail':True,'text':SENTINEL}})
  finally:logger.removeHandler(handler);logger.setLevel(before)
  self.assertEqual(r.status,503);self.assertNotIn(SENTINEL,await r.text());self.assertNotIn('actor',output.getvalue());self.assertNotIn('api-token:1',output.getvalue());self.assertNotIn(SENTINEL,output.getvalue())
  summary=self.log.summary();self.assertEqual(summary['count'],1);self.assertEqual(summary['recent'][0]['error_type'],'skill_exception');self.assertNotIn(SENTINEL,json.dumps(summary))
 async def test_diagnostics_endpoint_is_authenticated_and_mechanical(self):
  r=await self.client.get('/skills/discovery');self.assertEqual(r.status,403)
  self.log.record('safe.check','skill_timeout',1000)
  r=await self.client.get('/skills/discovery',headers=self.headers);v=await r.json();self.assertEqual(r.status,200);self.assertEqual(v['diagnostics']['graphics']['status'],'online');self.assertEqual(v['diagnostics']['registry_errors']['count'],1)
 async def test_unknown_user_id_not_recorded(self):
  r=await self.client.post('/invoke',headers=self.headers,json={'skill_id':'private.customer_name','input':{'text':SENTINEL}})
  self.assertEqual(r.status,404);self.assertEqual(self.log.summary()['count'],0)
class Filters(unittest.TestCase):
 def test_bad_types_are_isolated(self):
  for value in [None,{}, {'status':{}},{'recent':[{'error_type':{}}]}]:
   self.assertIsInstance(graphics_view(value),dict);self.assertIsInstance(errors_view(value),dict)
if __name__=='__main__':unittest.main()
