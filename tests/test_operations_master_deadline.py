import asyncio,time,unittest
from aiohttp.test_utils import TestClient,TestServer
from backend.master_engine.service import make_app
class Binding:
 def supports(self,sid):return sid=='operations.engine.diagnose'
 def rows(self,base):return base
 def invoke(self,principal,sid,value):time.sleep(20.1);return {'ok':True,'result':{'status':'completed'}}
class DeadlineTests(unittest.IsolatedAsyncioTestCase):
 async def test_actual_master_http_preserves_a_valid_operations_result_after_twenty_seconds(self):
  secret='fixture-service-secret-'+'x'*32
  client=TestClient(TestServer(make_app(secret,shared_bindings=Binding())));await client.start_server()
  try:
   response=await client.post('/invoke',json={'skill_id':'operations.engine.diagnose','input':{}},headers={'X-Harmonika-Master-Secret':secret,'X-Harmonika-Principal':'api-token:1'})
   self.assertEqual(response.status,200);self.assertTrue((await response.json())['ok'])
  finally:await client.close()
if __name__=='__main__':unittest.main()
