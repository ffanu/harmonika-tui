import os,sqlite3,tempfile,unittest
from pathlib import Path
from backend.engine_registry.failure_log import FailureLog
class SchemaGuard(unittest.TestCase):
 def test_no_insert_into_extended_logging_schema(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'native.sqlite3'
   with sqlite3.connect(p) as c:c.execute("CREATE TABLE skill_failures(timestamp TEXT,skill_id TEXT,error_type TEXT,latency REAL,prompt TEXT DEFAULT 'PRIVATE_SENTINEL')")
   p.chmod(0o600);log=FailureLog(p)
   self.assertEqual(log.record('image.generate','graphics_busy',1),'logging_unavailable')
   with sqlite3.connect(p) as c:self.assertEqual(c.execute('SELECT count(*) FROM skill_failures').fetchone()[0],0)
 def test_unsafe_sidecar_rejected_without_modification(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'native.sqlite3';p.touch(mode=0o600);target=Path(d)/'keep';target.write_text('KEEP');Path(str(p)+'-wal').symlink_to(target)
   log=FailureLog(p);self.assertEqual(log.record('image.generate','graphics_busy',1),'logging_unavailable');self.assertEqual(target.read_text(),'KEEP')
if __name__=='__main__':unittest.main()
