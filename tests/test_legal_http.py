"""Actual legal HTTP boundary, encrypted upload/download and honest source failures."""
import asyncio,io,json,os,tempfile,unittest
from pathlib import Path
try:
 from aiohttp import FormData
 from aiohttp.test_utils import TestClient,TestServer
 from backend.legal_engine.runtime import Runtime
 from backend.legal_engine.service import make_app
 from test_legal_core import FakeSources
 from test_legal_vision import Observer
 from PIL import Image
 ENABLED=True
except ImportError:ENABLED=False
@unittest.skipUnless(ENABLED,'dedicated legal venv required; run mandatory suite separately')
class LegalHTTP(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.rt=Runtime(self.root,os.urandom(32),FakeSources(),Observer());self.h={'X-Harmonika-Legal-Secret':'s'*64,'X-Harmonika-Principal':'api-token:888','Idempotency-Key':'create'};self.client=TestClient(TestServer(make_app(self.rt,'s'*64)));await self.client.start_server()
  r=await self.client.post('/cases',headers=self.h,json={'topic':'Fixture','facts':'PRIVATE_CASE_HTTP','occurred_on':'2026-01-01'});self.assertEqual(r.status,200);self.case=(await r.json())['result']['case']['id']
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def upload(self):
  out=io.BytesIO();Image.new('RGB',(4,3),(80,90,100)).save(out,format='PNG');raw=out.getvalue();data=FormData();data.add_field('case_id',self.case);data.add_field('file',raw,filename='bukti.png',content_type='text/plain');r=await self.client.post('/files',headers=self.h,data=data);self.assertEqual(r.status,201,await r.text());return (await r.json())['file'],raw
 async def test_private_secret_and_principal_required(self):
  r=await self.client.get('/capabilities');self.assertEqual(r.status,403)
  r=await self.client.get('/capabilities',headers={'X-Harmonika-Legal-Secret':'s'*64});self.assertEqual(r.status,403)
 async def test_source_identity_cannot_be_supplied_by_caller(self):
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'kb.uu.get','input':{'law_id':'fixture'},'verified_source':True});self.assertEqual(r.status,400)
 async def test_files_magic_bytes_encryption_ownership_and_download(self):
  file,raw=await self.upload();self.assertEqual(file['mime_type'],'image/png');self.assertEqual(file['width'],4);self.assertEqual(file['encryption'],'AES-256-GCM')
  p=next((self.root/'files').rglob('*.enc'));self.assertNotIn(raw,p.read_bytes())
  r=await self.client.get('/files/'+file['id']+'/download',headers=self.h);self.assertEqual(r.status,200);self.assertEqual(await r.read(),raw)
  r=await self.client.get('/files/'+file['id']+'/download',headers={**self.h,'X-Harmonika-Principal':'api-token:889'});self.assertEqual(r.status,404)
 async def test_corrupt_image_and_svg_rejected(self):
  for raw in (b'\x89PNG\r\n\x1a\ninvalid',b'<svg><script>1</script></svg>'):
   data=FormData();data.add_field('case_id',self.case);data.add_field('file',raw,filename='fake.png',content_type='image/png');r=await self.client.post('/files',headers=self.h,data=data);self.assertIn(r.status,(400,415))
 async def test_other_case_upload_rejected(self):
  out=io.BytesIO();Image.new('RGB',(1,1)).save(out,format='PNG');data=FormData();data.add_field('case_id',self.case);data.add_field('file',out.getvalue(),filename='x.png');r=await self.client.post('/files',headers={**self.h,'X-Harmonika-Principal':'api-token:889'},data=data);self.assertEqual(r.status,404);self.assertFalse(list((self.root/'files').rglob('*.enc')))
 async def test_case_stream_and_persisted_event_replay(self):
  r=await self.client.post('/chat',headers={**self.h,'Idempotency-Key':'chat'},json={'case_id':self.case,'message':'pasal mana perlu diperiksa?','stream':True});self.assertEqual(r.status,200);text=await r.text();self.assertIn('response.created',text);self.assertIn('response.completed',text);self.assertTrue(text.endswith('data: [DONE]\n\n'))
  result=json.loads(next(x[6:] for x in text.splitlines() if 'response.completed' in x))['data'];id=result['id']
  r=await self.client.get('/requests/'+id+'/events',headers={**self.h,'Accept':'text/event-stream','Last-Event-ID':id+':1'});replay=await r.text();self.assertNotIn('id: '+id+':1\n',replay);self.assertIn('[DONE]',replay)
 async def test_evidence_tamper_detected(self):
  f,raw=await self.upload();p=next((self.root/'files').rglob('*.enc'));p.write_bytes(p.read_bytes()[:-1]+b'x');r=await self.client.get('/files/'+f['id']+'/download',headers=self.h);self.assertEqual(r.status,503)

 async def test_public_caller_cannot_enable_internal_observer(self):
  r=await self.client.get('/capabilities',headers=self.h);v=await r.json();self.assertFalse(v['features']['legal_vision'])
  r=await self.client.post('/invoke',headers=self.h,json={'skill_id':'evidence.vlm.analyze','input':{'case_id':self.case,'file_id':'fake'}});self.assertEqual(r.status,503)
  r=await self.client.get('/capabilities',headers={**self.h,'X-Harmonika-Legal-Observer':'1'});v=await r.json();self.assertTrue(v['features']['legal_vision'])

 async def test_stream_preserves_structured_domain_error(self):
  from unittest.mock import patch
  from backend.legal_engine.common import DomainError
  with patch('backend.legal_engine.service.answer',side_effect=DomainError('rate_limited',429)):
   r=await self.client.post('/chat',headers={**self.h,'Idempotency-Key':'limitedstream'},json={'case_id':self.case,'message':'fixture','stream':True});text=await r.text()
   self.assertIn('response.error',text);self.assertIn('rate_limited',text);self.assertIn('[DONE]',text)
