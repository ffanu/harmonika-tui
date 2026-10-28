import json,tempfile,unittest
from pathlib import Path
from backend.master_engine.shared_bindings import Bindings
class BindingsTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)/'bindings.json';self.path.write_text(json.dumps({'schema_version':1,'seo_enabled':True,'owner_media':{'audio.synthesize':{},'video.generate':{'qualified_duration_seconds':[1,10]}}}));self.path.chmod(0o600);self.bindings=Bindings(self.path)
 def test_overlay_does_not_grant_public_owner_media(self):
  base=[{'id':'audio.synthesize','status':'unavailable'},{'id':'seo.page_audit','status':'local_only'},{'id':'seo.ga4','status':'local_only'}];rows={x['id']:x for x in self.bindings.rows(base)}
  self.assertEqual(base[0]['status'],'unavailable');self.assertEqual(rows['audio.synthesize']['status'],'context_required');self.assertFalse(rows['audio.synthesize']['public_invocation_enabled']);self.assertFalse(self.bindings.supports('audio.synthesize'));self.assertEqual(rows['seo.ga4']['status'],'unavailable');self.assertEqual(rows['seo.page_audit']['status'],'available')
 def test_root_config_cannot_register_arbitrary_handler(self):
  self.assertFalse(self.bindings.supports('shell.exec'));self.assertTrue(self.bindings.supports('seo.page_audit'));self.path.chmod(0o666);self.assertFalse(self.bindings.supports('seo.page_audit'))
 def test_request_ids_required_before_transport(self):
  self.assertEqual(self.bindings.invoke('api-token:1','seo.page_audit',{'url':'https://example.com'})['error']['code'],'seo_request_id_required')
