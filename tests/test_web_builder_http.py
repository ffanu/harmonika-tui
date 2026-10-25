import asyncio,base64,json,os,tempfile,unittest
try:
 from aiohttp.test_utils import TestServer,TestClient
 from backend.web_builder.core import Runtime
 from backend.web_builder.service import make_app
 READY=True
except ModuleNotFoundError as e:
 if not e.name.startswith(('aiohttp','cryptography')):raise
 READY=False
@unittest.skipUnless(READY,'dedicated builder venv required; mandatory suite run separately')
class HTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(self.tmp.name,os.urandom(32));self.client=TestClient(TestServer(make_app(self.rt,'s'*64)));await self.client.start_server();self.headers={'X-Harmonika-Builder-Secret':'s'*64,'X-Harmonika-Principal':'api-token:1','Idempotency-Key':'create'}
  r=await self.client.post('/projects',json={'name':'fixture'},headers=self.headers);self.assertEqual(r.status,201);self.p=(await r.json())['project']['id']
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def test_private_secret_principal_and_health(self):
  r=await self.client.get('/projects');self.assertEqual(r.status,403)
  r=await self.client.get('/projects',headers={**self.headers,'X-Harmonika-Principal':'root'});self.assertEqual(r.status,403)
  r=await self.client.get('/healthz');self.assertEqual((await r.json())['service'],'harmonika-web-builder')
 async def test_other_actor_project_artifact_hidden(self):
  for suffix in ('','/artifact'):
   r=await self.client.get('/projects/'+self.p+suffix,headers={**self.headers,'X-Harmonika-Principal':'api-token:2'});self.assertEqual(r.status,404)
 async def test_upload_revision_paths_private_zip_and_no_overwrite(self):
  route='/projects/'+self.p+'/files';data={'path':'spec.md','content':'PRIVATE_HTTP_FIXTURE','expected_revision':0}
  r=await self.client.post(route,json=data,headers=self.headers);self.assertEqual(r.status,201)
  r=await self.client.post(route,json=data,headers=self.headers);self.assertEqual(r.status,409)
  for path in ('/etc/passwd','../secret','.git/hooks/x'):
   r=await self.client.post(route,json={**data,'path':path,'expected_revision':1},headers=self.headers);self.assertIn(r.status,(400,403))
  r=await self.client.get('/projects/'+self.p+'/artifact',headers=self.headers);self.assertEqual(r.headers['Content-Type'],'application/zip');self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertNotIn('PRIVATE_HTTP_FIXTURE',r.headers.get('Content-Disposition',''))
 async def test_no_actor_or_host_paths_allowed_in_json(self):
  r=await self.client.post('/projects',json={'name':'fixture','actor':'api-token:2'},headers={**self.headers,'Idempotency-Key':'evil'});self.assertEqual(r.status,400)
 async def test_expected_revision_is_required_for_upload(self):
  r=await self.client.post('/projects/'+self.p+'/files',json={'path':'x','content':'data'},headers=self.headers);self.assertEqual(r.status,400)
 async def test_no_invoke_without_idempotency(self):
  r=await self.client.post('/projects/'+self.p+'/invoke',json={'skill_id':'fe.gen.page','input':{}},headers={k:v for k,v in self.headers.items() if k!='Idempotency-Key'});self.assertEqual(r.status,400);self.assertEqual((await r.json())['error']['code'],'idempotency_required')
