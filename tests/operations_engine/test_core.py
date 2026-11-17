import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.operations_engine.common import OpsError,decode,authorization
from backend.operations_engine.database import audit
from backend.operations_engine.licenses import usage
from backend.operations_engine.telemetry import Telemetry
from backend.operations_engine.engine import Engine
from backend.operations_engine.catalog import descriptors
class CoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.root.chmod(0o700)
  self.cfg={'state_root':str(self.root/'state'),'principals':{'api-token:1':{'role':'admin','projects':['fixture']},'api-token:2':{'role':'user','projects':[]}},'projects':{'fixture':{'root':str(self.root/'project')}}}
 def tearDown(self):self.tmp.cleanup()
 def test_strict_json_duplicate_and_budget(self):
  for raw in [b'{"x":1,"x":2}',b'{"v":NaN}',b'['*26+b'0'+b']'*26,b'x'*1048577]:
   with self.assertRaises(OpsError):decode(raw)
 def test_principal_project_and_roles(self):
  for args in [('api-token:9',None,False),('api-token:2','fixture',False),('api-token:2',None,True),('evil',None,False)]:
   with self.assertRaises(OpsError):authorization(self.cfg,args[0],args[1],args[2])
  self.assertEqual(authorization(self.cfg,'api-token:1','fixture',True)['role'],'admin')
 def test_global_operations_cannot_select_hidden_projects(self):
  engine=Engine(self.cfg,b'a'*32)
  with self.assertRaisesRegex(OpsError,'unexpected_project'):engine.invoke('api-token:2','operations.engine.diagnose',{'project':'fixture'})
 def test_every_project_tool_rejects_missing_or_null_project(self):
  from backend.operations_engine.catalog import SPECS
  engine=Engine(self.cfg,b'a'*32)
  for sid,(_,project,_) in SPECS.items():
   if not project:continue
   for payload in ({},{'project':None},{'project':''}):
    with self.assertRaisesRegex(OpsError,'project_required'):engine.invoke('api-token:1',sid,payload)
 def test_database_reads_schema_and_plan_without_rows_or_mutations(self):
  p=self.root/'audit.sqlite3'
  with sqlite3.connect(p) as c:c.execute('CREATE TABLE fixture(id INTEGER PRIMARY KEY, private TEXT)');c.execute("INSERT INTO fixture VALUES(1,'PRIVATE_CUSTOMER_SENTINEL')")
  before=hashlib.sha256(p.read_bytes()).hexdigest();cfg={'databases':{'local':{'type':'sqlite','path':str(p),'queries':{'one':'SELECT private FROM fixture WHERE id=1'}}}}
  result=audit(cfg,{'database':'local','query':'one'})
  self.assertTrue(result['read_only']);self.assertTrue(result['integrity_ok']);self.assertEqual(result['rows_returned'],0);self.assertTrue(result['query_plan'])
  self.assertNotIn('PRIVATE_CUSTOMER_SENTINEL',json.dumps(result));self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest())
 def test_database_rejects_arbitrary_sql_alias_and_symlink(self):
  p=self.root/'x.sqlite3';sqlite3.connect(p).close();q=self.root/'link.sqlite3';q.symlink_to(p)
  for cfg,data in [({'databases':{}},{'database':'notregistered'}),({'databases':{'a':{'type':'sqlite','path':str(q)}}},{'database':'a'}),({'databases':{'a':{'type':'sqlite','path':str(p),'queries':{'bad':'DELETE FROM secret'}}}},{'database':'a','query':'bad'})]:
   with self.assertRaises(OpsError):audit(cfg,data)
 def test_project_database_aliases_do_not_inherit_global_connectors(self):
  self.cfg['databases']={'private':{'type':'sqlite','path':'/never/read'}}
  self.cfg['principals']['api-token:2']['projects']=['fixture']
  engine=Engine(self.cfg,b'a'*32)
  with self.assertRaisesRegex(OpsError,'database_not_registered'):engine.invoke('api-token:2','operations.database.audit',{'project':'fixture','database':'private'})
 def test_wide_schema_is_explicitly_partial(self):
  p=self.root/'wide.sqlite3'
  with sqlite3.connect(p) as c:c.execute('CREATE TABLE wide('+','.join('c'+str(i)+' INTEGER' for i in range(129))+')')
  value=audit({'databases':{'wide':{'type':'sqlite','path':str(p)}}},{'database':'wide'})
  self.assertEqual(value['status'],'partial');self.assertFalse(value['schema_complete']);self.assertTrue(value['schema'][0]['columns_truncated'])
 def test_license_scope_self_and_admin_all(self):
  good={k:0 for k in ['issued','active','ever_used','used_24h','used_window','inactive','bound_devices']}
  with patch('backend.operations_engine.licenses.postgres',return_value=good) as pg:
   result=usage({'license_source':{}},'api-token:2',{});self.assertEqual(result['scope'],'self');self.assertIn('id=2',pg.call_args[0][1])
   with self.assertRaisesRegex(OpsError,'admin_required'):usage({'license_source':{}},'api-token:2',{'scope':'all'})
   usage({'license_source':{}},'api-token:1',{'scope':'all'},admin=True);self.assertIn('WHERE TRUE',pg.call_args[0][1])
 def test_wal_telemetry_is_scoped_and_contains_no_raw_values(self):
  t=Telemetry(self.root/'telemetry',b'b'*32);t.record('api-token:1','operations.engine.diagnose','completed',12);t.record('api-token:2','operations.engine.diagnose','PRIVATE_SENTINEL',14,'PRIVATE_SENTINEL')
  self.assertEqual(t.summary('api-token:1')[0]['count'],1);self.assertEqual(t.summary('api-token:2')[0]['status'],'failed')
  with sqlite3.connect(t.path) as c:
   self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');blob=json.dumps(c.execute('SELECT * FROM events').fetchall())
  for secret in ['PRIVATE_SENTINEL','api-token:1','api-token:2']:self.assertNotIn(secret,blob)
 def test_public_descriptors_match_packaged_catalog(self):
  source=Path(__file__).resolve().parents[2]/'runtime/tools/operations_catalog.json'
  self.assertEqual(json.loads(source.read_text())['skills'],descriptors())
if __name__=='__main__':unittest.main()
