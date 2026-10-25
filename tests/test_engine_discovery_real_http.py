import asyncio,hashlib,json,os,sys,tempfile,unittest
from pathlib import Path
from aiohttp.test_utils import TestClient,TestServer
from backend.engine_registry.discovery import Discovery
from backend.master_engine.service import make_app
SOURCE=(Path(__file__).resolve().parents[1]/'backend/engine_registry/skills/text-metrics/skill.py').read_text()
class RealHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.root=self.base/'plugins';self.root.mkdir()
  self.d=Discovery(self.root,self.base/'status.db',execute_timeout=.7)
  self.h={'X-Harmonika-Master-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1'}
  self.client=TestClient(TestServer(make_app('s'*64,skills={},dispatcher=lambda *a:None,mobile_enabled=False,discovery=self.d,discovery_poll_seconds=.02)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.temp.cleanup()
 async def plugin(self,source=SOURCE):
  p=self.root/'metric';p.mkdir(exist_ok=True);(p/'skill.py').write_text(source);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}))
  target=hashlib.sha256(source.encode()).hexdigest();deadline=asyncio.get_running_loop().time()+5
  while True:
   await self.d.refresh(wait=True)
   rows=self.d.descriptors()
   if rows and rows[0]['sha256']==target:break
   if asyncio.get_running_loop().time()>deadline:self.fail('target snapshot not published')
   await asyncio.sleep(.01)
 async def call(self,text='Halo Kak!',headers=None):
  r=await self.client.post('/invoke',headers=headers or self.h,json={'skill_id':'text.metrics','input':{'text':text}});return r.status,await r.json()
 async def test_actual_end_to_end_hot_register(self):
  await self.plugin();code,result=await self.call();self.assertEqual(code,200);self.assertEqual(result['result']['words'],2)
 async def test_model_free_timeout_does_not_hang_next_request(self):
  await self.plugin(SOURCE.replace("text=payload['text']","import asyncio\n await asyncio.sleep(60)\n text=payload['text']"))
  code,result=await self.call();self.assertEqual(code,504);self.assertEqual(result['error']['code'],'skill_timeout')
  await self.plugin();self.assertEqual((await self.call())[0],200)
 async def test_one_actor_one_active_and_global_limit(self):
  self.d._execute_timeout=2
  await self.plugin(SOURCE.replace("text=payload['text']","import asyncio\n await asyncio.sleep(.4)\n text=payload['text']"))
  first=asyncio.create_task(self.call());await asyncio.sleep(.1)
  self.assertEqual((await self.call())[0],429)
  second=asyncio.create_task(self.call(headers={**self.h,'X-Harmonika-Principal':'api-token:2'}));await asyncio.sleep(.1)
  self.assertEqual((await self.call(headers={**self.h,'X-Harmonika-Principal':'api-token:3'}))[0],429)
  self.assertEqual((await first)[0],200);self.assertEqual((await second)[0],200)
 async def test_runtime_error_is_safe_and_service_survives(self):
  await self.plugin(SOURCE.replace("text=payload['text']","raise RuntimeError('PRIVATE')\n text=payload['text']"));code,result=await self.call();self.assertEqual(code,422);self.assertNotIn('PRIVATE',str(result))
  await self.plugin();self.assertEqual((await self.call())[0],200)
 @unittest.skipUnless(sys.platform.startswith('linux') and os.geteuid()==0,'Linux service root privilege-drop check')
 async def test_child_drops_root_before_plugin_code(self):
  await self.plugin(SOURCE.replace("text=payload['text']","import os\n return {'root':os.geteuid()==0}\n text=payload['text']"));code,result=await self.call();self.assertEqual(code,200);self.assertFalse(result['result']['root'])
