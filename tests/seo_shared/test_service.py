import sys,unittest,tempfile,importlib.util,sqlite3,secrets,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'runtime/tools'))
s=importlib.util.spec_from_file_location('seo_service',ROOT/'backend/seo_shared/service.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class ServiceTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);p=patch.object(m,'STATE',Path(self.tmp.name).resolve());p.start();self.addCleanup(p.stop)
 def test_paths_cannot_escape_actor_project(self):
  root=m.scoped_project('api-token:4')
  for value in ['../secret','/etc/passwd','a/../b','.env','a//b']:
   with self.assertRaises(m.Failure):m.relative(root,value)
  other=Path(self.tmp.name)/'outside';other.mkdir();(root/'escape').symlink_to(other)
  with self.assertRaises(m.Failure):m.relative(root,'escape/x')
 def test_isolation_and_artifact_integrity(self):
  service=m.Service();a='api-token:5';b='api-token:6';root=m.scoped_project(a);f=root/'proof.json';f.write_text('{}')
  import hashlib,json
  aid='a'*32;index=root/'artifact-index';index.mkdir();(index/(aid+'.json')).write_text(json.dumps({'path':'proof.json','sha256':hashlib.sha256(b'{}').hexdigest(),'bytes':2,'mime_type':'application/json'}))
  self.assertEqual(service.artifact(a,{'artifact_id':aid})['bytes'],2)
  with self.assertRaisesRegex(m.Failure,'not_found'):service.artifact(b,{'artifact_id':aid})
  f.write_text('changed')
  with self.assertRaisesRegex(m.Failure,'artifact_changed'):service.artifact(a,{'artifact_id':aid})
 def test_visual_is_blocked_until_cpu_isolation_qualified(self):
  service=m.Service()
  with patch.dict(m.os.environ,{'HARMONIKA_SEO_VISUAL_ENABLED':'0'}):
   with self.assertRaisesRegex(m.Failure,'visual_isolation_qualification_pending'):service.submit('api-token:7',{'skill_id':'seo.visual','input':{'url':'https://harmonika.id','output_dir':'x'},'request_id':'b'*32})
if __name__=='__main__':unittest.main()
