"""Local isolated media contracts; fakes never count as live inference proof."""
import io,os,tempfile,unittest,wave
from pathlib import Path
try:
 from backend.legal_engine.runtime import Runtime
 from backend.legal_engine.vault import Vault
 from backend.legal_engine.common import DomainError
 from test_legal_core import FakeSources
 from test_legal_vision import Observer
 READY=True
except ImportError:READY=False
class MediaObserver:
 ready=True;asr_ready=True;public_safe=True;video_ready=True
 def __init__(self):self.calls=0
 def transcribe(self,raw,mime,language=None):
  self.calls+=1;return {'status':'transcribed','analysis_performed':True,'transcript':'PRIVATE_TRANSCRIPT_FIXTURE','language':language or 'id','confidence':None,'legal_fact_verified':False,'speaker_identity_verified':False}
 def analyze(self,raw,mime):return {'status':'observed','analysis_performed':True,'observations':{'text_detected':'fixture','description':'fixture','warnings':[]},'legal_fact_verified':False}
@unittest.skipUnless(READY,'dedicated legal venv')
class LocalMediaTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.media=MediaObserver();self.rt=Runtime(self.root,os.urandom(32),FakeSources(),self.media);self.actor='api-token:911';self.case=self.rt.case_create(self.actor,{'topic':'synthetic','facts':'synthetic'},'create')['result']['case']['id'];out=io.BytesIO()
  with wave.open(out,'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*1600)
  self.file=Vault(self.rt.store,self.root/'files',self.actor).upload(self.case,'fixture.wav',out.getvalue());self.args={'case_id':self.case,'file_id':self.file['file_id']}
 def test_real_asr_handler_contract_and_language_cache_boundary(self):
  v=self.rt.invoke(self.actor,'evidence.asr.transcribe',{**self.args,'language':'id'},'id');self.assertEqual(v['status'],'completed');self.assertIsNone(v['result']['confidence']);self.assertFalse(v['result']['speaker_identity_verified'])
  self.rt.invoke(self.actor,'evidence.asr.transcribe',{**self.args,'language':'id'},'id2');self.assertEqual(self.media.calls,1)
  self.rt.invoke(self.actor,'evidence.asr.transcribe',{**self.args,'language':'en'},'en');self.assertEqual(self.media.calls,2)
  for p in self.root.glob('state.sqlite*'):self.assertNotIn(b'PRIVATE_TRANSCRIPT_FIXTURE',p.read_bytes())
 def test_other_tenant_and_case_cannot_transcribe(self):
  with self.assertRaises(DomainError):self.rt.invoke('api-token:912','evidence.asr.transcribe',self.args,'bad')
  second=self.rt.case_create(self.actor,{'topic':'other','facts':'other'},'other')['result']['case']['id']
  with self.assertRaises(DomainError):self.rt.invoke(self.actor,'evidence.asr.transcribe',{**self.args,'case_id':second},'badcase')
  self.assertEqual(self.media.calls,0)
 def test_auto_audio_observation_is_real_adapter_output_not_metadata(self):
  from backend.legal_engine.chat import answer
  self.rt.prepare_evidence(self.actor,self.case);v=answer(self.rt,self.actor,{'case_id':self.case,'message':'Baca lampiran'},'chat');self.assertIn('PRIVATE_TRANSCRIPT_FIXTURE',v['result']['text']);self.assertFalse(v['result']['unanalysed_media_file_ids'])
 def test_media_sandbox_has_no_ip_network_or_host_credentials(self):
  p=Path(__file__).resolve().parents[1]/'backend/legal_engine/deploy/harmonika-legal-media.service';text=p.read_text();self.assertIn('DynamicUser=yes',text);self.assertIn('RestrictAddressFamilies=AF_UNIX',text);self.assertIn('PrivateDevices=yes',text);self.assertNotIn('internal-agent.token',text)
 def test_local_media_failure_does_not_become_fake_ready(self):
  from unittest.mock import patch
  from backend.legal_engine.local_media import LocalMedia
  with patch.object(LocalMedia,'_call',side_effect=DomainError('media_unavailable',503)):
   self.assertFalse(LocalMedia().ready)
 def test_adapter_revision_change_invalidates_derived_cache(self):
  self.media.revision='revision-one';self.rt.invoke(self.actor,'evidence.asr.transcribe',self.args,'first');self.assertEqual(self.media.calls,1)
  self.media.revision='revision-two';self.rt.invoke(self.actor,'evidence.asr.transcribe',self.args,'next');self.assertEqual(self.media.calls,2)
 def test_auto_observer_uses_a_new_idempotency_key_after_adapter_change(self):
  self.media.revision='one';self.rt.prepare_evidence(self.actor,self.case);self.assertEqual(self.media.calls,1)
  self.media.revision='two';self.rt.prepare_evidence(self.actor,self.case);self.assertEqual(self.media.calls,2)

@unittest.skipUnless(READY,'dedicated legal venv')
class MediaProcessCleanup(unittest.IsolatedAsyncioTestCase):
 async def test_success_and_timeout_remove_plaintext_job_temp(self):
  import asyncio
  from backend.legal_engine.media.service import execute_child
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);jobs=root/'jobs';jobs.mkdir();worker=root/'fake.py';worker.write_text("import os,time,json\nfrom pathlib import Path\nPath(os.environ['TMPDIR'],'plaintext').write_text('SYNTHETIC ONLY')\ntime.sleep(0.05)\nprint(json.dumps({'ok':True}))")
   code,out=await execute_child(b'{}','vision',temp_root=str(jobs),worker_path=worker,timeout=2);self.assertEqual(code,0);self.assertFalse(list(jobs.iterdir()))
   worker.write_text("import os,time\nfrom pathlib import Path\nPath(os.environ['TMPDIR'],'plaintext').write_text('SYNTHETIC ONLY')\ntime.sleep(4)")
   with self.assertRaises(asyncio.TimeoutError):await execute_child(b'{}','vision',temp_root=str(jobs),worker_path=worker,timeout=.15)
   self.assertFalse(list(jobs.iterdir()))

@unittest.skipUnless(READY,'dedicated legal venv')
class ModelIntegrityGate(unittest.TestCase):
 def test_empty_or_partial_model_manifest_cannot_be_ready(self):
  import json
  from unittest.mock import patch
  from backend.legal_engine.media import service
  with tempfile.TemporaryDirectory() as tmp,patch.object(service,'ROOT',Path(tmp)):
   for manifest in ({},{'vision':{}},{'vision':{'revision':'a'*40,'files':{}},'asr':{'revision':'b'*40,'files':{}}}):
    Path(tmp,'manifest.json').write_text(json.dumps(manifest));self.assertIsNone(service.verified_models())
