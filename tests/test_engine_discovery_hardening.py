import asyncio,copy,hashlib,json,os,shutil,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.engine_registry.discovery import Discovery,SanitizedError,_static_validate,_validate_metadata,CODE_METADATA_INVALID
from backend.engine_registry.schema import audit,SchemaError
SAMPLE=Path(__file__).resolve().parents[1]/'backend/engine_registry/skills/text-metrics/skill.py'
SOURCE=SAMPLE.read_text()
CONTEXT={'max_input_tokens':65536,'max_output_tokens':4096,'max_chat_messages':0,'max_tool_calls':0}
IDENTITY={'id':'fixture.test','name':'Test','core_capabilities':['text'],'requirements':[],'input_schema':{'type':'object'}}
class ValidatorMatrix(unittest.TestCase):
 def test_all_metadata_rejection_paths(self):
  pairs=[(None,CONTEXT),(IDENTITY,None),(dict(IDENTITY,name='x'*66000),CONTEXT),(IDENTITY,{'x':'y'*66000}),(dict(IDENTITY,requirements=object()),CONTEXT)]
  for field,values in {'id':[True,'Bad.ID','bad-id','x'*101,'a\n'], 'name':[1,'','\x1bnotice'], 'core_capabilities':[None,[],['x']*33,[4],['']], 'requirements':[None,['x']*33,[4],[''],['external_model']], 'input_schema':[None,{'type':'array','items':{'type':'string'}},{'type':'object','$ref':'http://external'}]}.items():
   for value in values:pairs.append((dict(IDENTITY,**{field:value}),CONTEXT))
  pairs.extend([(dict(IDENTITY,secret='no'),CONTEXT),(IDENTITY,{}),(IDENTITY,dict(CONTEXT,max_input_tokens=True)),(IDENTITY,dict(CONTEXT,max_input_tokens=0)),(IDENTITY,dict(CONTEXT,max_output_tokens=300000)),(IDENTITY,dict(CONTEXT,max_chat_messages=1)),(IDENTITY,dict(CONTEXT,max_tool_calls=1))])
  for identity,context in pairs:
   with self.subTest(identity=repr(identity)[:100],context=repr(context)[:100]):self.assertEqual(_validate_metadata(identity,context),CODE_METADATA_INVALID)
  self.assertIsNone(_validate_metadata(IDENTITY,CONTEXT))
 def test_static_signature_matrix(self):
  self.assertIsNone(_static_validate(SOURCE.encode()))
  variants=[SOURCE+'\n'+SOURCE,SOURCE.replace('async def Execute(payload):','async def Execute(payload, /):'),SOURCE.replace('async def Execute(payload):','async def Execute(payload, extra):'),SOURCE.replace('async def Execute(payload):','async def Execute(payload=None):'),SOURCE.replace('async def Execute(payload):','async def Execute(*args):'),SOURCE.replace('async def Execute(payload):','async def Execute(payload, **kwargs):'),SOURCE.replace('async def Execute(payload):','async def Execute(payload, *, extra):'),SOURCE.replace('def GetSkillIdentity():','def GetSkillIdentity(extra):'),SOURCE+'\ndef GetSkillIdentity():\n return {}\n',SOURCE.replace('async def Execute(payload):','@staticmethod\nasync def Execute(payload):'),SOURCE.replace('def GetSkillIdentity():','@staticmethod\ndef GetSkillIdentity():')]
  for source in variants:
   with self.subTest(source=source[-100:]):self.assertIsNotNone(_static_validate(source.encode()))
 def test_schema_remaining_invalid_keywords(self):
  for schema in ({'type':['object']},{'type':'string','description':3},{'type':'number','required':[]},{'type':'number','maxLength':3},{'type':'object','minimum':'a','maximum':2}):
   with self.subTest(schema=schema),self.assertRaises(SchemaError):audit(schema)
class LifecycleHardening(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name).resolve();self.root=self.base/'plugins';self.root.mkdir();self.db=self.base/'status.db'
 def plugin(self,name='metrics',source=SOURCE):
  folder=self.root/name;folder.mkdir(exist_ok=True);(folder/'skill.py').write_text(source);(folder/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}));return folder
 async def test_metadata_cache_no_repeat_import_and_deepcopy(self):
  self.plugin();engine=Discovery(self.root,self.db);await engine.refresh()
  with patch.object(engine,'_run_child_sync',side_effect=AssertionError('must reuse immutable metadata')):await engine.refresh()
  row=engine.descriptors()[0];row['context']['max_chat_messages']=999;row['input_schema'].clear()
  self.assertEqual(engine.descriptors()[0]['context']['max_chat_messages'],0)
  self.assertIn('type',engine.descriptors()[0]['input_schema'])
 async def test_changed_unsupported_schema_and_context_never_registered(self):
  for name,source in [('schema',SOURCE.replace("'minLength':1","'pattern':'evil'")),('context',SOURCE.replace("'max_chat_messages':0","'max_chat_messages':1")),('requirement',SOURCE.replace("'requirements':[]","'requirements':['missing']"))]:self.plugin(name,source)
  engine=Discovery(self.root,self.db);summary=await engine.refresh();self.assertEqual(summary['registered'],0);self.assertEqual(summary['rejected'],3)
 async def test_metadata_runtime_overwrite_not_async_is_rejected(self):
  self.plugin(source=SOURCE+'\nExecute=3\n');engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_PLUGIN':1})
 async def test_cancel_cleans_owned_child_and_records_latency(self):
  self.plugin(source=SOURCE.replace("text=payload['text']","import asyncio\n await asyncio.sleep(60)\n text=payload['text']"))
  engine=Discovery(self.root,self.db);await engine.refresh()
  task=asyncio.create_task(engine.execute('text.metrics',{'text':'private-canary'}));await asyncio.sleep(.15);task.cancel()
  with self.assertRaises(asyncio.CancelledError):await task
  with sqlite3.connect(self.db) as db:
   row=db.execute('SELECT latency_ms,error_code FROM engine_discovery_execution').fetchone();self.assertGreater(row[0],0);self.assertEqual(row[1],'E_CANCELLED')
  self.assertNotIn(b'private-canary',self.db.read_bytes())
 async def test_output_and_input_caps_fail_closed(self):
  self.plugin(source=SOURCE.replace("return {'characters':len(text)","return {'large':'x'*10000,'characters':len(text)"))
  engine=Discovery(self.root,self.db);await engine.refresh()
  with self.assertRaises(SanitizedError) as e:await engine.execute('text.metrics',{'text':'ok'})
  self.assertEqual(e.exception.code,'E_OUTPUT')
  with self.assertRaises(SanitizedError) as e:await engine.execute('text.metrics',{'text':'x'*66000})
  self.assertEqual(e.exception.code,'E_PAYLOAD')
 async def test_policy_blocks_restricted_new_id(self):
  self.plugin(source=SOURCE.replace('text.metrics','exploit.future'))
  engine=Discovery(self.root,self.db,identity_policy=lambda id:not id.startswith('exploit.'));v=await engine.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_RESERVED_ID':1})
 async def test_db_failure_never_publishes_unaudited_module(self):
  self.plugin();engine=Discovery(self.root,self.db)
  with patch.object(engine,'_persist',return_value='E_DATABASE'):v=await engine.refresh()
  self.assertEqual(v['status'],'degraded');self.assertEqual(v['registered'],0);self.assertEqual(engine.descriptors(),[])
 async def test_scan_wallclock_and_file_total_bounds_fail_closed(self):
  self.plugin();engine=Discovery(self.root,self.db,scan_timeout=-1);v=await engine.refresh();self.assertTrue(v['truncated']);self.assertEqual(v['registered'],0)
  engine=Discovery(self.root,self.db,max_total_bytes=10);v=await engine.refresh();self.assertEqual(v['registered'],0);self.assertTrue(v['truncated'])
 async def test_symlink_root_directory_and_manifest(self):
  folder=self.plugin();(folder/'manifest.json').unlink();(folder/'manifest.json').symlink_to(folder/'skill.py')
  (self.root/'linked').symlink_to(folder,target_is_directory=True)
  engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_SYMLINK':2})
  link=self.base/'rootlink';link.symlink_to(self.root,target_is_directory=True)
  engine=Discovery(link,self.base/'other.db');v=await engine.refresh();self.assertEqual(v['result'],'E_SYMLINK')
 async def test_untrusted_writable_file_root_and_directory(self):
  folder=self.plugin();(folder/'skill.py').chmod(0o666);engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['error_codes'],{'E_UNTRUSTED_OWNER':1})
  (folder/'skill.py').chmod(0o644);folder.chmod(0o777);v=await engine.refresh();self.assertEqual(v['registered'],0)
  folder.chmod(0o755);self.root.chmod(0o777);v=await engine.refresh();self.assertEqual(v['result'],'E_UNTRUSTED_OWNER');self.root.chmod(0o755)
 async def test_database_symlink_rejected_without_touching_target(self):
  target=self.base/'unrelated';target.write_bytes(b'preserve');self.db.symlink_to(target)
  with self.assertRaises(SanitizedError):Discovery(self.root,self.db)
  self.assertEqual(target.read_bytes(),b'preserve')
 async def test_missing_root_empty_file_and_unsafe_name_isolated(self):
  self.plugin('bad name');(self.root/'notes.txt').write_text('ignore');engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['rejected'],1)
  shutil.rmtree(self.root);v=await engine.refresh();self.assertEqual(v['result'],'E_ROOT_UNREADABLE');self.assertEqual(v['registered'],0)
 async def test_real_execution_is_what_latency_measures(self):
  self.plugin();engine=Discovery(self.root,self.db);await engine.refresh();result=await engine.execute('text.metrics',{'text':'Halo Kak!'});self.assertEqual(result['words'],2)
  with sqlite3.connect(self.db) as db:
   row=db.execute('SELECT latency_ms,error_code FROM engine_discovery_execution').fetchone();self.assertGreater(row[0],0);self.assertEqual(row[1],'OK')
 async def test_deleted_database_is_not_silently_recreated(self):
  self.plugin();engine=Discovery(self.root,self.db);await engine.refresh();self.db.unlink()
  with self.assertRaises(SanitizedError) as e:await engine.execute('text.metrics',{'text':'test'})
  self.assertEqual(e.exception.code,'E_DATABASE');self.assertFalse(self.db.exists())
 async def test_module_runtime_cannot_change_execute_signature(self):
  self.plugin(source=SOURCE+'\nasync def alternate(first,second):\n return {}\nExecute=alternate\n')
  engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_PLUGIN':1})
 async def test_nested_top_level_control_flow_rejected_statically(self):
  for name,tail in [('if','\nif True:\n while True:pass\n'),('try','\ntry:\n while True:pass\nexcept:pass\n'),('with','\nwith open("/dev/null"):\n while True:pass\n')]:self.plugin(name,SOURCE+tail)
  engine=Discovery(self.root,self.db);v=await engine.refresh();self.assertEqual(v['error_codes'],{'E_CONTRACT_STATIC':3})
 async def test_replaced_foreign_database_never_receives_records(self):
  self.plugin();engine=Discovery(self.root,self.db);await engine.refresh();self.db.rename(self.base/'old.db')
  with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE foreign_data(value TEXT)')
  before=self.db.read_bytes()
  with self.assertRaises(SanitizedError):await engine.execute('text.metrics',{'text':'test'})
  self.assertEqual(self.db.read_bytes(),before)
 async def test_symlink_ancestor_database_rejected_explicitly(self):
  target=self.base/'target';target.mkdir();link=self.base/'link';link.symlink_to(target,target_is_directory=True)
  with self.assertRaises(SanitizedError):Discovery(self.root,link/'new.db')
  self.assertFalse((target/'new.db').exists())
