import unittest
from backend.unified_engine.understanding import normalize,analyze,aggregate
from backend.unified_engine.registry import descriptors,capabilities
from backend.unified_engine.pipeline import Adapters
def chat(text,prior=None):return analyze(([{'role':'assistant','content':prior}] if prior else [])+[{'role':'user','content':text}])
class Routing(unittest.TestCase):
 def test_normalization_protects_urls_ids_money_code(self):
  text='sy bkn minta kode `gk bkn 123` https://example.test/yg?q=tdk Rp170.940 5075 -6.409272,106.740922';n=normalize(text);self.assertTrue(n['protected_tokens_preserved']);self.assertIn('saya bukan',n['text']);self.assertIn('`gk bkn 123`',n['text']);self.assertIn('Rp170.940',n['text'])
 def test_negation_does_not_route_forbidden_task(self):
  r=chat('Jangan buat aplikasi Android. Jelaskan router saja.');self.assertNotIn('mobile_builder',r['intents']);self.assertIn('mobile_builder',r['negated_intents'])
 def test_two_specialists_detected(self):self.assertEqual(set(chat('Buat website toko lalu buatkan aplikasi Android')['intents']),{'web_builder','mobile_builder'})
 def test_blueprint_web_and_legal_example(self):self.assertEqual(set(chat('Buatkan website toko online, lalu ceritakan apa hukumnya kalau customer complain')['intents']),{'web_builder','legal'})
 def test_negated_clause_then_positive_command(self):self.assertEqual(chat('gk perlu web, bkin aplikasi android')['intents'],['mobile_builder'])
 def test_old_user_request_does_not_leak(self):
  r=analyze([{'role':'user','content':'Buat aplikasi Android'},{'role':'assistant','content':'Baik.'},{'role':'user','content':'Sekarang jelaskan apa itu router'}]);self.assertEqual(r['intents'],['chat'])
 def test_quoted_code_and_url_not_action(self):self.assertEqual(chat('Jelaskan kode `buat aplikasi Android` di https://example.test/buat-website')['intents'],['chat'])
 def test_short_platform_followup(self):self.assertIn('mobile_builder',chat('Android','Platform Android atau iOS?')['intents'])
 def test_ambiguous_reference_requests_clarification(self):self.assertTrue(chat('ubah itu')['needs_clarification'])
 def test_greeting_not_ambiguous(self):self.assertFalse(chat('Halo Kak')['needs_clarification'])
 def test_crisis_not_negated_or_fiction(self):
  self.assertEqual(chat('Saya mau bunuh diri')['urgency'],'crisis');self.assertNotEqual(chat('Saya tidak mau bunuh diri')['urgency'],'crisis');self.assertNotEqual(chat('Contoh mau bunuh diri dalam cerita fiksi')['urgency'],'crisis')
 def test_confidence_not_fabricated(self):self.assertIsNone(chat('Apa hukum kasus ini')['confidence'])
 def test_provenance_conflicts_not_resolved_by_guess(self):
  out=aggregate([{'engine':'a','data':{'price':100}},{'engine':'b','data':{'price':200}}]);self.assertEqual(out['status'],'needs_review');self.assertFalse(out['facts_independently_verified']);self.assertEqual(len(out['results']),2)
 def test_internal_reasoning_not_in_aggregate(self):
  import json
  out=aggregate([{'engine':'a','reasoning':'private-value','data':{'thinking':'private-value','answer':'ok'}}]);self.assertNotIn('private-value',json.dumps(out));self.assertIn('ok',json.dumps(out))
 def test_validation_and_70_catalog_unique(self):
  self.assertEqual(len({r['id'] for r in descriptors()}),70);self.assertEqual(sum(capabilities()['readiness'].values()),70)
  for bad in (None,[],[{'role':'root','content':'x'}]):
   with self.assertRaises(ValueError):analyze(bad)
  with self.assertRaises(ValueError):aggregate([{'engine':'x'}]*9)
class Specialists(unittest.IsolatedAsyncioTestCase):
 async def test_real_existing_planners(self):
  a=Adapters()
  for engine in ('web_builder','mobile_builder','legal'):
   r=await a.specialist(engine,'Buat website dan aplikasi Android untuk katalog produk');self.assertEqual(r['engine'],engine);self.assertIn('data',r);self.assertNotEqual(r.get('build_performed'),True)
 async def test_browser_requires_owned_context(self):
  r=await Adapters().specialist('browser_auto','buka browser');self.assertEqual(r['status'],'context_required');self.assertFalse(r['external_actions_performed'])
 async def test_invalid_media_no_outbound_call(self):
  for media in ({'type':'image','image_url':'http://localhost/secret'},{'type':'image','image_base64':'not-valid$'}):
   with self.assertRaises(ValueError):await Adapters().media(media)
if __name__=='__main__':unittest.main()
