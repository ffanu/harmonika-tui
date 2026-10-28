import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
s=importlib.util.spec_from_file_location('av_broker',Path(__file__).resolve().parents[2]/'backend/video_toolkit/broker_av.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Profiles(unittest.TestCase):
 def test_default_normalized(self):self.assertEqual(m.validate({'kind':'video.generate','prompt':'fixture'})['duration_seconds'],1)
 def test_ten_second_requires_verified_inference(self):
  with patch.object(m,'read',return_value={'phase':'running'}):
   with self.assertRaisesRegex(ValueError,'not_qualified'):m.validate({'kind':'video.generate','prompt':'fixture','duration_seconds':10})
  with patch.object(m,'read',return_value={'phase':'verified'}):self.assertEqual(m.validate({'kind':'video.generate','prompt':'fixture','duration_seconds':10})['duration_seconds'],10)
 def test_framepack_ten_rejected(self):
  with self.assertRaisesRegex(ValueError,'unsupported_profile'):m.validate({'kind':'video.animate_image','prompt':'fixture','duration_seconds':10})
 def test_soundtrack_restriction_preserved(self):
  with self.assertRaisesRegex(ValueError,'license_restricted'):m.validate({'kind':'audio.soundtrack','prompt':'fixture'})
 def test_request_recovery_is_owner_scoped(self):
  import sqlite3,threading
  b=object.__new__(m.Broker);b.lock=threading.RLock();b.db=sqlite3.connect(':memory:');b.db.row_factory=sqlite3.Row
  b.db.execute('CREATE TABLE jobs(id TEXT,owner TEXT,idem TEXT)');b.db.execute('INSERT INTO jobs VALUES(?,?,?)',('job','owner-a','request'))
  self.assertEqual(b.by_request('owner-a','request')['id'],'job');self.assertIsNone(b.by_request('owner-b','request'));b.db.close()
if __name__=='__main__':unittest.main()

