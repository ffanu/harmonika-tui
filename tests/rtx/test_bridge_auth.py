import asyncio,hashlib,io,json,tempfile,unittest
from pathlib import Path
from aiohttp import FormData
from aiohttp.test_utils import TestClient,TestServer
from PIL import Image
from backend.rtx_queue.service import create_app
class Worker:
 async def start(self):pass
 async def close(self):pass
 async def health(self):return {'ready':True,'generate':True,'analyze':True,'references':True}
 async def run(self,job,store):
  data=io.BytesIO();Image.new('RGB',(16,12),'blue').save(data,format='PNG');return {'text':'observed fixture','images':[data.getvalue()]}
class BridgeAuth(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.secret='b'*64
  self.client=TestClient(TestServer(create_app(Path(self.temp.name).resolve(),Worker(),{'legacy':hashlib.sha256(b'legacy-token').hexdigest()},self.secret)));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.temp.cleanup()
 def h(self,id=1):return {'Authorization':'Bearer '+self.secret,'X-Harmonika-Media-Principal':'api_'+str(id),'Idempotency-Key':'fixture-unique-'+str(id)}
 async def test_bridge_needs_secret_and_valid_server_principal(self):
  for headers in ({},{'Authorization':'Bearer '+self.secret},{**self.h(),'X-Harmonika-Media-Principal':'root'},{**self.h(),'Authorization':'Bearer bad'}):self.assertEqual((await self.client.get('/health',headers=headers)).status,401)
  self.assertEqual((await self.client.get('/health',headers=self.h())).status,200)
 async def test_two_api_tokens_job_ownership_and_body_cannot_override(self):
  response=await self.client.post('/v1/rtx/jobs',headers=self.h(),json={'kind':'generate','prompt':'safe fixture'});self.assertEqual(response.status,202);job=(await response.json())['job']['job_id']
  self.assertEqual((await self.client.get('/v1/rtx/jobs/'+job,headers=self.h(2))).status,404)
  self.assertEqual((await self.client.get('/v1/rtx/jobs/'+job,headers=self.h())).status,200)
  response=await self.client.post('/v1/rtx/jobs',headers=self.h(2),json={'kind':'generate','prompt':'safe fixture','owner':'api_1'});self.assertEqual(response.status,400)
 async def test_legacy_token_ignores_claimed_principal(self):
  response=await self.client.post('/v1/rtx/jobs',headers=self.h(),json={'kind':'generate','prompt':'safe fixture'});job=(await response.json())['job']['job_id']
  forged={'Authorization':'Bearer legacy-token','X-Harmonika-Media-Principal':'api_1'}
  self.assertEqual((await self.client.get('/v1/rtx/jobs/'+job,headers=forged)).status,404)
  self.assertEqual((await self.client.get('/health',headers=forged)).status,200)
 async def test_image_file_ownership_has_no_public_url(self):
  data=io.BytesIO();Image.new('RGB',(16,12),'green').save(data,format='PNG');form=FormData();form.add_field('file',data.getvalue(),filename='fixture.png',content_type='image/png')
  response=await self.client.post('/v1/rtx/files',headers=self.h(),data=form);self.assertEqual(response.status,201);meta=(await response.json())['file'];self.assertNotIn('url',meta)
  path='/v1/rtx/files/'+meta['file_id'];self.assertEqual((await self.client.get(path,headers=self.h(2))).status,404)
  response=await self.client.get(path,headers=self.h());self.assertEqual(response.status,200);self.assertEqual(await response.read(),data.getvalue())
