import asyncio,base64,json,sys,types,unittest
from unittest.mock import patch
from backend.master_engine.browser import execute,SCHEMA
from backend.web_builder.common import BuilderError
class Sandbox:
 async def run(self,files,skill,args,job,**kw):
  self.files=files;self.args=args
  return {'status':'completed','exit_code':0,'snapshot':{'result.json':base64.b64encode(json.dumps({'actions':[],'screenshot_sha256':'a'*64,'horizontal_overflow':False}).encode()).decode()}}
class Browser(unittest.IsolatedAsyncioTestCase):
 async def test_fixed_script_no_payload_interpolation_host_url_or_gpu(self):
  sandbox=Sandbox();payload={'html':'<p>malicious user text</p>','actions':[{'type':'dom.text','selector':'p'}]}
  with patch.dict(sys.modules,{'web_builder':__import__('backend.web_builder',fromlist=['']), 'web_builder.common':__import__('backend.web_builder.common',fromlist=[''])}):out=await execute(payload,sandbox)
  self.assertTrue(out['analysis_performed']);self.assertEqual(sandbox.args['command'],'node runner.cjs');self.assertEqual(json.loads(base64.b64decode(sandbox.files['request.json'])),payload)
  code=base64.b64decode(sandbox.files['runner.cjs']).decode();self.assertIn('--disable-gpu',code);self.assertIn("route.abort()",code);self.assertNotIn('malicious user text',code)
 async def test_reject_arbitrary_urls_eval_and_unknown_actions(self):
  with patch.dict(sys.modules,{'web_builder':__import__('backend.web_builder',fromlist=['']), 'web_builder.common':__import__('backend.web_builder.common',fromlist=[''])}):
   for payload in ({'url':'https://billing.example','actions':[]},{'html':'<p>x</p>','actions':[{'type':'eval','value':'fetch("http://evil")'}]},{'html':'<p>x</p>','actions':[{'type':'dom.click'}]}):
    with self.assertRaises((ValueError,BuilderError)):await execute(payload,Sandbox())
