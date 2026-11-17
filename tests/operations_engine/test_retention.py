import json,tempfile,unittest
from pathlib import Path
from backend.operations_engine.retention import prune
class RetentionTests(unittest.TestCase):
 def test_only_expired_owned_traces_removed_without_following_links(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();root.chmod(0o700);base=root/'artifacts';base.mkdir(mode=0o700);scope=base/('a'*64);scope.mkdir(mode=0o700)
   def create(n,start):
    p=scope/('browser_'+n*32);p.mkdir(mode=0o700)
    for name,value in [('retention.json',json.dumps({'created_at':start,'expires_at':start+86400})),('trace.zip','PRIVATE_TRACE')]:
     f=p/name;f.write_text(value);f.chmod(0o600)
    return p
   expired=create('1',0);current=create('2',90000);unsafe=create('3',0)
   victim=root/'victim';victim.write_text('keep');(unsafe/'screenshot.png').symlink_to(victim)
   result=prune(root,100000)
   self.assertEqual(result['removed'],1);self.assertFalse(expired.exists());self.assertTrue(current.exists());self.assertTrue(unsafe.exists());self.assertEqual(victim.read_text(),'keep')
if __name__=='__main__':unittest.main()
