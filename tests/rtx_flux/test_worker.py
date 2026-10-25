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
  self.temp=tempfile.TemporaryDirectory();return w.create_app(Path(self.temp.name))
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
if __name__=='__main__':unittest.main()
