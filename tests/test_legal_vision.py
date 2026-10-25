"""Vision contracts and least privilege, without pretending fixtures prove legal truth."""
import io,json,os,tempfile,unittest
from pathlib import Path
try:
 from backend.legal_engine.runtime import Runtime
 from backend.legal_engine.vault import Vault
 from backend.legal_engine.common import DomainError
 from test_legal_core import FakeSources
 from PIL import Image
 ENABLED=True
except ImportError:ENABLED=False
class Observer:
 ready=True
 def __init__(self):self.calls=0
 def analyze(self,raw,mime):
  self.calls+=1
  if not mime.startswith('image/'):raise DomainError('vision_media_unsupported',415)
  return {'status':'observed','analysis_performed':True,'observations':{'text_detected':'PRIVATE_OCR_SENTINEL','description':'Fixture only','warnings':[]},'legal_fact_verified':False,'requires_human_confirmation':True}
@unittest.skipUnless(ENABLED,'dedicated legal dependencies required')
class LegalVision(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.observer=Observer();self.rt=Runtime(self.root,os.urandom(32),FakeSources(),self.observer);self.a='api-token:890';self.case=self.rt.case_create(self.a,{'topic':'fixture','facts':'fixture','occurred_on':'2026-01-01'},'case')['result']['case']['id']
  out=io.BytesIO();Image.new('RGB',(3,4)).save(out,format='PNG');self.f=Vault(self.rt.store,self.root/'files',self.a).upload(self.case,'image.png',out.getvalue());self.args={'case_id':self.case,'file_id':self.f['file_id']}
 def tearDown(self):self.tmp.cleanup()
 def test_actual_vault_bytes_observed_once_with_encrypted_cache(self):
  r=self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,'read');self.assertEqual(r['status'],'completed');self.assertTrue(r['result']['analysis_performed']);self.assertFalse(r['result']['legal_fact_verified']);self.assertEqual(self.observer.calls,1)
  self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,'read');self.rt.invoke(self.a,'evidence.ocr.extract',self.args,'read2');self.assertEqual(self.observer.calls,1)
  for p in self.root.glob('state.sqlite*'):self.assertNotIn(b'PRIVATE_OCR_SENTINEL',p.read_bytes())
 def test_no_cross_case_or_token_observation(self):
  case2=self.rt.case_create(self.a,{'topic':'other','facts':'other'},'other')['result']['case']['id']
  with self.assertRaises(DomainError):self.rt.invoke(self.a,'evidence.vlm.analyze',{**self.args,'case_id':case2},'bad')
  with self.assertRaises(DomainError):self.rt.invoke('api-token:891','evidence.vlm.analyze',self.args,'bad')
  self.assertEqual(self.observer.calls,0)
 def test_adapter_failure_does_not_make_up_contents(self):
  self.observer.analyze=lambda *a:{'status':'degraded','analysis_performed':False,'observations':None}
  r=self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,'offline');self.assertEqual(r['status'],'degraded');self.assertFalse(r['result']['analysis_performed']);self.assertIsNone(r['result']['observations'])
 def test_prepare_evidence_runs_before_case_chat_and_keeps_facts_unverified(self):
  from backend.legal_engine.chat import answer
  self.rt.prepare_evidence(self.a,self.case);r=answer(self.rt,self.a,{'case_id':self.case,'message':'Lihat bukti ini'},'chat');self.assertIn('PRIVATE_OCR_SENTINEL',r['result']['text']);self.assertFalse(r['result']['applicability_determined']);self.assertTrue(r['result']['media_observations'])
 def test_missing_key_never_spends_observer_call(self):
  with self.assertRaises(DomainError):self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,None)
  self.assertEqual(self.observer.calls,0)
 def test_tampered_original_does_not_reuse_cached_observation(self):
  self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,'first')
  p=next((self.root/'files').rglob('*.enc'));p.write_bytes(b'corrupt')
  with self.assertRaises(DomainError):self.rt.invoke(self.a,'evidence.vlm.analyze',self.args,'first')
  from backend.legal_engine.chat import answer
  r=answer(self.rt,self.a,{'case_id':self.case,'message':'Lihat bukti'},'chatbad');self.assertNotIn('PRIVATE_OCR_SENTINEL',r['result']['text'])
 def test_missing_configuration_not_advertised_ready(self):
  from unittest.mock import patch
  from backend.legal_engine.vision import VisionAdapter
  with patch('backend.legal_engine.vision.Path.read_text',side_effect=FileNotFoundError('private path')):
   adapter=VisionAdapter(True);self.assertFalse(adapter.ready)
   adapter.ready=True
   with self.assertRaises(DomainError) as e:adapter.analyze(b'bytes','image/png')
   self.assertEqual(e.exception.code,'vision_unavailable');self.assertNotIn('private path',str(e.exception))
 def test_auto_observer_is_one_uncached_image_per_chat_and_does_not_rebill_cache(self):
  original=self.f;out=io.BytesIO();Image.new('RGB',(5,4)).save(out,format='PNG');Vault(self.rt.store,self.root/'files',self.a).upload(self.case,'second.png',out.getvalue())
  self.rt.prepare_evidence(self.a,self.case);self.assertEqual(self.observer.calls,1)
  self.rt.prepare_evidence(self.a,self.case);self.assertEqual(self.observer.calls,2)
  self.rt.prepare_evidence(self.a,self.case);self.assertEqual(self.observer.calls,2)
