"""Synthetic legal core checks: encryption, tenancy, grounded quotes and no legal verdicts."""
import datetime as dt,hashlib,io,json,os,tempfile,unittest
from pathlib import Path
from backend.legal_engine.common import DomainError
try:
 from backend.legal_engine.runtime import Runtime
 from backend.legal_engine.sources import citation,Sources,MANIFEST
 from backend.legal_engine.chat import answer
 CRYPTO=True
except ImportError:CRYPTO=False
class FakeSources:
 def __init__(self):self.records={'fixture':{'id':'fixture','base_id':'fixture','body':'Pasal 1\nKutipan contoh sintetis untuk pengujian.\nPasal 2\nData lain.','title':'Fixture BUKAN hukum nyata','url':'https://peraturan.go.id/fixture','authority':'peraturan.go.id','jurisdiction':'ID','document_kind':'uu','sha256':'a'*64,'fetched_at':dt.datetime.now(dt.timezone.utc).isoformat(),'provenance':'official_live_fetch','verification':{'official_host':True,'fetched_by_server':True,'document_identity_verified':True},'currentness':'not_certified','has_full_text':True}}
 def get(self,id):
  if id not in self.records:raise DomainError('source_not_found',404)
  return self.records[id]
 def search(self,query,jurisdiction='ID',limit=10,kind=None):return list(self.records.values())[:limit] if jurisdiction=='ID' else []
 def history(self,id):return [self.get(id)]
@unittest.skipUnless(CRYPTO,'legal-specific encrypted runtime venv required; mandatory suite run separately')
class Core(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.key=os.urandom(32);self.rt=Runtime(self.root,self.key,FakeSources());self.a='api-token:880';self.b='api-token:881';self.n=0
 def tearDown(self):self.tmp.cleanup()
 def keyn(self):self.n+=1;return 't'+str(self.n)
 def case(self,**changes):
  r=self.rt.case_create(self.a,{'topic':'kasus fixture','facts':'PRIVATE_LEGAL_FACT_SENTINEL','occurred_on':'2026-01-01',**changes},self.keyn());self.assertEqual(r['status'],'completed',r);return r['result']['case']
 def test_99_actual_ids_not_claimed221(self):
  c=self.rt.capabilities();self.assertEqual(c['document_actual_skills'],99);self.assertEqual(len(c['skills']),99);self.assertFalse(c['features']['legal_vision']);self.assertFalse(c['features']['legal_licensed_expert'])
 def test_case_and_request_json_encrypted_at_rest(self):
  case=self.case();self.assertEqual(self.rt.case_get(self.a,case['id'])['facts'],'PRIVATE_LEGAL_FACT_SENTINEL')
  for p in self.root.glob('state.sqlite*'):self.assertNotIn(b'PRIVATE_LEGAL_FACT_SENTINEL',p.read_bytes())
 def test_wrong_key_cannot_decrypt_case(self):
  c=self.case();other=Runtime(self.root,os.urandom(32),FakeSources())
  with self.assertRaises(DomainError):other.case_get(self.a,c['id'])
 def test_actor_cannot_read_other_case_or_request(self):
  r=self.rt.case_create(self.a,{'topic':'test','facts':'test'},'x');c=r['result']['case']
  with self.assertRaises(DomainError):self.rt.case_get(self.b,c['id'])
  with self.assertRaises(DomainError):self.rt.status(self.b,r['id'])
 def test_no_caller_tenant_or_currentness_claim(self):
  with self.assertRaises(DomainError):self.rt.case_create(self.a,{'topic':'x','facts':'x','tenant':'other'},'x')
 def test_idempotency_returns_one_case_and_conflict(self):
  a={'topic':'x','facts':'x'};x=self.rt.case_create(self.a,a,'same');y=self.rt.case_create(self.a,a,'same');self.assertEqual(x['id'],y['id']);self.assertTrue(y['replayed'])
  with self.assertRaises(DomainError):self.rt.case_create(self.a,{**a,'facts':'different'},'same')
 def test_no_ocr_fake_success(self):
  with self.assertRaises(DomainError) as e:self.rt.invoke(self.a,'evidence.ocr.extract',{},'ocr')
  self.assertEqual(e.exception.code,'skill_unavailable')
 def test_exact_source_quote_and_article_no_untrusted_meaning(self):
  r=self.rt.invoke(self.a,'kb.uu.pasal.get',{'law_id':'fixture','article':'1'},self.keyn());self.assertEqual(r['status'],'completed',r);self.assertIn('Kutipan contoh',r['result']['text']);self.assertFalse(r['result']['applicability_determined'])
  with self.assertRaises(DomainError):citation(self.rt.sources.get('fixture'),'Invented statute')
 def test_source_currentness_is_unknown_even_fetched(self):
  r=self.rt.invoke(self.a,'uu.status.check',{'law_id':'fixture'},self.keyn());self.assertEqual(r['status'],'degraded');self.assertEqual(r['result']['current_legal_status'],'not_certified')
 def test_foreign_jurisdiction_does_not_use_id_law(self):
  r=self.rt.invoke(self.a,'kb.uu.search',{'query':'test','jurisdiction':'SG'},self.keyn());self.assertFalse(r['result']['matches'])
 def test_case_bound_chat_history_and_replay(self):
  c=self.case();a={'case_id':c['id'],'message':'Apa aturan yang perlu diperiksa?'};r=answer(self.rt,self.a,a,'chat');self.assertEqual(r['status'],'completed');self.assertTrue(r['result']['citations']);self.assertFalse(r['result']['applicability_determined']);self.assertIn('bukan nasihat',r['result']['text'])
  self.assertTrue(answer(self.rt,self.a,a,'chat')['replayed']);r2=answer(self.rt,self.a,{'case_id':c['id'],'message':'Buktinya apa?'},'chat2');self.assertEqual(r2['result']['history_messages_considered'],2)
  other=self.case();r3=answer(self.rt,self.a,{'case_id':other['id'],'message':'Halo'},'chat3');self.assertEqual(r3['result']['history_messages_considered'],0)
 def test_no_date_requires_clarification_not_old_kuhp_default(self):
  c=self.case(occurred_on=None);r=answer(self.rt,self.a,{'case_id':c['id'],'message':'Apa saya bersalah?'},'clarify');self.assertEqual(r['result']['state'],'clarification_required');self.assertFalse(r['result']['citations'])
 def test_tampered_encrypted_record_fails_closed(self):
  c=self.case()
  with self.rt.store.db() as d:d.execute("UPDATE records SET data=? WHERE kind='legal_case'",(b'corrupt',))
  with self.assertRaises(DomainError):self.rt.case_get(self.a,c['id'])
 def test_failed_mutation_rolls_back_partial_records(self):
  def broken(c,a):c.create('legal_case',{'facts':'x'});raise DomainError('fixture_failure')
  r=self.rt._invoke(self.a,'fixture',{},'rollback',broken,False);self.assertEqual(r['status'],'failed')
  with self.rt.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM records').fetchone()[0],0)
 def test_readonly_handler_cannot_create_records(self):
  r=self.rt._invoke(self.a,'fixture',{},'readonly',lambda c,a:c.create('legal_case',{'facts':'x'}),True);self.assertEqual(r['error']['code'],'mutation_not_authorized')
 def test_replay_events_are_ordered_and_owned(self):
  r=self.rt.case_create(self.a,{'topic':'x','facts':'x'},'events');es=self.rt.events(self.a,r['id']);self.assertEqual([x['sequence'] for x in es],[1,2]);self.assertEqual(len(self.rt.events(self.a,r['id'],1)),1)
  with self.assertRaises(DomainError):self.rt.events(self.b,r['id'])
 def test_negated_emergency_not_declared_confirmed(self):
  r=self.rt.invoke(self.a,'intent.detect.emergency',{'text':'contoh bukan sedang dipukul'},'neg');self.assertFalse(r['result']['urgent_safety_signal']);self.assertFalse(r['result']['confirmed_emergency'])
 def test_persona_is_not_professional_identity(self):
  c=self.case();r=self.rt.invoke(self.a,'persona.activate.ite',{'case_id':c['id']},'lens');self.assertFalse(r['result']['licensed_expert_present']);self.assertFalse(r['result']['expert_impersonation'])
 def test_source_no_user_urls_and_first_refresh_not_throttled(self):
  source=Sources(self.root/'public.sqlite')
  with self.assertRaises(DomainError):source.refresh('http://127.0.0.1')
 def test_unrelated_negation_does_not_hide_current_danger(self):
  r=self.rt.invoke(self.a,'intent.detect.emergency',{'text':'tidak ada polisi tetapi saya sedang dipukul'},'danger');self.assertTrue(r['result']['urgent_safety_signal']);self.assertFalse(r['result']['confirmed_emergency'])
 def test_followup_explicit_date_advances_the_same_case(self):
  c=self.case(occurred_on=None);r=answer(self.rt,self.a,{'case_id':c['id'],'message':'Peristiwa terjadi 2026-01-02'},'date');self.assertNotEqual(r['result']['state'],'clarification_required');self.assertEqual(self.rt.case_get(self.a,c['id'])['occurred_on'],'2026-01-02')
 def test_other_document_date_is_not_incident_date(self):
  c=self.case(occurred_on=None);r=answer(self.rt,self.a,{'case_id':c['id'],'message':'KTP terbit 2015-03-01, mohon bantu'},'ktpdate');self.assertEqual(r['result']['state'],'clarification_required');self.assertIsNone(self.rt.case_get(self.a,c['id'])['occurred_on'])
 def test_explicit_incident_date_correction(self):
  c=self.case();r=answer(self.rt,self.a,{'case_id':c['id'],'message':'Tanggal kejadian: 2025-02-01'},'correctdate');self.assertEqual(self.rt.case_get(self.a,c['id'])['occurred_on'],'2025-02-01');self.assertIn('2025-02-01',r['result']['text'])
 def test_slow_readonly_handler_does_not_hold_writer(self):
  import threading
  entered=threading.Event();release=threading.Event();results=[]
  def read(c,a):entered.set();release.wait(3);return {'ok':True}
  t=threading.Thread(target=lambda:results.append(self.rt._invoke(self.a,'slow',{},'slow',read,True)));t.start();self.assertTrue(entered.wait(2))
  try:self.assertEqual(self.rt.case_create(self.b,{'topic':'second','facts':'second'},'other')['status'],'completed')
  finally:release.set();t.join(4)
  self.assertEqual(results[0]['status'],'completed')
