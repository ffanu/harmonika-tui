import unittest,json
from backend.mobile_engine import registry as r
from backend.mobile_engine.configuration import IDS,dispatch
class Configuration(unittest.TestCase):
 def test_all_configs_generate_without_execution(self):
  for id in IDS:
   out=r.dispatch(id,{});self.assertEqual(out['status'],'completed');self.assertFalse(out['tests_performed']);self.assertTrue(out['files'])
 def test_url_injection_rejected(self):
  for value in ('http://example.org','https://a.invalid/a\nFOO=bar','https://a.invalid/?key=private','https://user:pass@a.invalid','https://a.invalid/#token'):
   with self.assertRaises(ValueError):dispatch(IDS[0],{'api_base_url':value})
 def test_no_extra_fields_or_identifiers(self):
  for value in ({'actor':'root'},{'identifier':'../root'},{'identifier':'id.a;run()'}):
   with self.assertRaises(ValueError):dispatch(IDS[0],value)
 def test_ci_uses_locked_source_only_commands(self):
  text=json.dumps(dispatch(IDS[1],{}));self.assertIn('npm ci --ignore-scripts',text);self.assertNotIn('npm publish',text);self.assertNotIn('secrets.',text)
if __name__=='__main__':unittest.main()
