import unittest
from backend.mobile_engine import native_modules as n
from backend.mobile_engine.registry import dispatch
class Native(unittest.TestCase):
 def test_seven_real_source_kits(self):
  for id in n.IDS:
   out=dispatch(id,{})
   self.assertEqual(out['status'],'completed');self.assertTrue(out['files']);self.assertFalse(out['build_performed']);self.assertFalse(out['runtime_verified'])
 def test_identifier_and_framework_guards(self):
  for v in ({'identifier':'id.class.app'},{'identifier':'id.when.app'},{'identifier':'../app'},{'identifier':'id.my_app.test'},{'framework':'expo'},{'secret':'secret'}):
   with self.assertRaises(ValueError):n.dispatch('bridge.kotlin.module',v)
 def test_real_channel_contract_has_cancel(self):
  out=n.dispatch('bridge.event_channel',{});text='\n'.join(x['content'] for x in out['files'])
  self.assertIn('onCancel',text);self.assertIn('timer?.invalidate()',text);self.assertIn('handler.removeCallbacks',text)
 def test_unknown_calls_not_fabricated(self):
  text='\n'.join(x['content'] for x in n.dispatch('bridge.method_channel',{})['files'])
  self.assertIn('result.notImplemented()',text);self.assertIn('FlutterMethodNotImplemented',text)
if __name__=='__main__':unittest.main()
