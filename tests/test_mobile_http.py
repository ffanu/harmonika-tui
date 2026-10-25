import json,unittest,asyncio,threading
from unittest.mock import patch
from aiohttp.test_utils import TestClient,TestServer
from backend.master_engine.service import make_app
from backend.mobile_engine.registry import descriptors
try:
 from mobile_engine import registry as runtime_registry
except ImportError:
 from backend.mobile_engine import registry as runtime_registry
class MobileHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.h={'X-Harmonika-Master-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1'}
  self.client=TestClient(TestServer(make_app('s'*64)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close()
 async def test_auth_and_capabilities(self):
  self.assertEqual((await self.client.get('/mobile/capabilities')).status,403)
  res=await self.client.get('/mobile/capabilities',headers=self.h);caps=await res.json()
  self.assertEqual(caps['catalog_size'],214);self.assertFalse(caps['features']['store_publish']);self.assertEqual(res.headers['Cache-Control'],'no-store')
 async def test_all_unready_ids_reject(self):
  for row in descriptors():
   if row['status']=='available':continue
   res=await self.client.post('/mobile/invoke',headers=self.h,json={'skill_id':row['id'],'input':{}})
   self.assertEqual(res.status,503,row['id']);self.assertEqual((await res.json())['error']['code'],row['reason'])
 async def test_source_not_host_path_or_actor(self):
  for body in ({'skill_id':'spec.read.mobile','input':{'path':'/etc/passwd'}},{'skill_id':'spec.read.mobile','input':{'spec':'Android'},'actor':'root'},{'skill_id':'spec.read.mobile','input':{'spec':'Android'},'context':{'engine':'coding'}}):
   res=await self.client.post('/mobile/invoke',headers=self.h,json=body);self.assertEqual(res.status,400)
 async def test_duplicate_keys_and_nonfinite(self):
  for raw in ('{"skill_id":"spec.read.mobile","input":{"spec":"ios","spec":"android"}}','{"skill_id":"spec.read.mobile","input":{"spec":"Android","n":NaN}}'):
   res=await self.client.post('/mobile/invoke',headers=self.h,data=raw);self.assertEqual(res.status,400)
 async def test_planning_and_unknown(self):
  res=await self.client.post('/mobile/invoke',headers=self.h,json={'skill_id':'spec.read.mobile','input':{'spec':'Android Kotlin offline'}})
  out=await res.json();self.assertEqual(res.status,200);self.assertFalse(out['build_performed']);self.assertEqual(out['mode'],'planning')
  res=await self.client.post('/mobile/invoke',headers=self.h,json={'skill_id':'mobile.gen.screen.login','input':{}});self.assertEqual(res.status,404)
 async def test_same_actor_cannot_overlap_and_capacity_is_shared(self):
  gate=threading.Event();started=threading.Event()
  def delayed(id,v):
   started.set();gate.wait(4)
   return {'status':'completed','mode':'planning','runtime_verified':False}
  with patch.object(runtime_registry,'dispatch',side_effect=delayed):
   pending=asyncio.create_task(self.client.post('/mobile/invoke',headers=self.h,json={'skill_id':'spec.read.mobile','input':{'spec':'Android'}}))
   try:
    for _ in range(100):
     if started.is_set():break
     await asyncio.sleep(.005)
    self.assertTrue(started.is_set())
    second=await self.client.post('/mobile/invoke',headers=self.h,json={'skill_id':'spec.read.mobile','input':{'spec':'iOS'}})
    self.assertEqual(second.status,429);self.assertEqual((await second.json())['error']['code'],'analysis_busy')
   finally:gate.set()
   self.assertEqual((await pending).status,200)
 async def test_pagination_and_injection(self):
  for suffix in ('/mobile/capabilities?url=http://localhost','/mobile/skills?limit=215','/mobile/skills/spec.read.mobile?url=x'):
   self.assertEqual((await self.client.get(suffix,headers=self.h)).status,400)
  res=await self.client.get('/mobile/skills?offset=200&limit=20',headers=self.h);self.assertEqual(len((await res.json())['skills']),14)
if __name__=='__main__':unittest.main()
