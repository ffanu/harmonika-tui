"""WAL qualification against the actual discovery engine, isolated SQLite only."""
import asyncio,contextlib,hashlib,importlib.util,io,json,sqlite3,tempfile,unittest
from pathlib import Path
from backend.engine_registry.discovery import Discovery
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'backend/engine_registry/skills/text-metrics/skill.py').read_text()
spec=importlib.util.spec_from_file_location('wal_maintenance',ROOT/'scripts/sqlite-maintenance.py');maintenance=importlib.util.module_from_spec(spec);spec.loader.exec_module(maintenance)
class DiscoveryWal(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve();self.db=self.base/'state.db';self.plugins=self.base/'plugins';self.plugins.mkdir();p=self.plugins/'metrics';p.mkdir();(p/'skill.py').write_text(SOURCE);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(SOURCE.encode()).hexdigest()}));self.d=Discovery(self.plugins,self.db);await self.d.refresh();self.addCleanup(self.temp.cleanup)
 async def asyncTearDown(self):self.d.close()
 def migrate(self,action):
  out=io.StringIO()
  with contextlib.redirect_stdout(out):code=maintenance.main([action,str(self.db),'--busy-timeout-ms','500'])
  self.assertEqual(code,0,out.getvalue());return json.loads(out.getvalue())
 async def test_migrate_preserves_live_engine_file_identity_and_real_invoke(self):
  before=self.db.stat().st_ino;report=self.migrate('migrate-wal');self.assertEqual(report['after'],'wal');self.assertEqual(self.db.stat().st_ino,before)
  self.assertEqual((await self.d.refresh())['registered'],1);self.assertEqual((await self.d.execute('text.metrics',{'text':'one two'}))['words'],2)
  self.migrate('revert-delete');self.assertEqual((await self.d.execute('text.metrics',{'text':'one two three'}))['words'],3)
 async def test_held_readonly_snapshot_does_not_block_engine_audit_write(self):
  self.migrate('migrate-wal')
  with contextlib.closing(sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True)) as reader:
   reader.execute('BEGIN');self.assertEqual(reader.execute('SELECT count(*) FROM engine_discovery_execution').fetchone()[0],0)
   result=await asyncio.wait_for(self.d.execute('text.metrics',{'text':'live snapshot'}),3);self.assertEqual(result['words'],2)
   self.assertEqual(reader.execute('SELECT count(*) FROM engine_discovery_execution').fetchone()[0],0);reader.commit()
   self.assertEqual(reader.execute('SELECT count(*) FROM engine_discovery_execution').fetchone()[0],1)
 async def test_cold_service_reopen_preserves_wal_and_integrity(self):
  self.migrate('migrate-wal');self.d.close();self.d=Discovery(self.plugins,self.db);self.assertEqual((await self.d.refresh())['registered'],1)
  with contextlib.closing(sqlite3.connect(self.db)) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
 async def test_execution_audit_releases_its_sqlite_connection(self):
  from unittest.mock import patch
  captured=[];original=self.d._connect_existing
  class Tracked:
   def __init__(self,connection):self.connection=connection;self.closed=False
   def __getattr__(self,name):return getattr(self.connection,name)
   def __enter__(self):self.connection.__enter__();return self
   def __exit__(self,*args):return self.connection.__exit__(*args)
   def close(self):self.connection.close();self.closed=True
  def tracked(*args,**kwargs):
   connection=Tracked(original(*args,**kwargs));captured.append(connection);return connection
  with patch.object(self.d,'_connect_existing',side_effect=tracked):await self.d.execute('text.metrics',{'text':'closed audit connection'})
  self.assertEqual(len(captured),1);self.assertTrue(captured[0].closed)
if __name__=='__main__':unittest.main()
