import unittest
from backend.web_builder.planning import dispatch
class Negation(unittest.TestCase):
 def parse(self,text):return dispatch('spec.parse.features',{'text':text})['data']
 def test_later_exclusion_does_not_get_lost(self):
  v=self.parse('Butuh payment.\nTidak perlu payment.');self.assertNotIn('payments',v['feature_keys']);self.assertIn('payments',v['excluded_keys'])
 def test_contrast_begins_new_scope(self):
  v=self.parse('Tanpa payment, tapi perlu blog dan login.');self.assertEqual(set(v['feature_keys']),{'auth','blog'});self.assertEqual(v['excluded_keys'],['payments'])
 def test_feature_name_is_not_a_negative_marker(self):
  v=self.parse('Buat notebook dan login.');self.assertIn('auth',v['feature_keys'])

 def test_affirmative_clause_after_comma(self):
  v=self.parse('Tanpa payment, perlu blog dan login.');self.assertEqual(set(v['feature_keys']),{'auth','blog'});self.assertEqual(v['excluded_keys'],['payments'])
 def test_negation_binds_to_following_feature_only(self):
  v=self.parse('Katalog produk tanpa filter.');self.assertEqual(v['feature_keys'],['catalog']);self.assertEqual(v['excluded_keys'],['search'])
 def test_conflicting_mentions_are_visible_for_clarification(self):
  text='Butuh payment.\nTidak perlu payment.';v=self.parse(text);self.assertIn('payments',v['conflicting_features'])
  out=dispatch('spec.clarify',{'text':text})['data'];self.assertIn('conflicting_spec',[q['topic'] for q in out['questions']])

 def test_explicit_api_and_erp_need_server_even_without_feature_synonyms(self):
  for project_type in ('api','erp_crm'):
   v=dispatch('stack.select.backend',{'project_type':project_type})['data'];self.assertNotIn('none',v['choice'])
