import asyncio,hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.engine_registry.discovery import Discovery,SanitizedError
from backend.engine_registry import scanning
SOURCE=(Path(__file__).resolve().parents[1]/'backend/engine_registry/skills/text-metrics/skill.py').read_text()
class ScaleGuards(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name).resolve();self.root=self.base/'plugins';self.root.mkdir();self.db=self.base/'state.db'
 def put(self,name,source):
  p=self.root/name;p.mkdir();(p/'skill.py').write_text(source);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}));return p
 async def test_all_snapshot_metadata_bounded_not_only_cache(self):
  for i in range(5):self.put('mod'+str(i),SOURCE.replace('text.metrics','fixture.mod'+str(i)))
  d=Discovery(self.root,self.db)
  with patch.object(scanning,'METADATA_CACHE_BUDGET',1000):v=await d.refresh()
  self.assertEqual(v['registered'],0);self.assertTrue(v['truncated']);self.assertEqual(v['status'],'degraded')
 async def test_disk_budget_enforced_before_next_blob(self):
  for i in range(4):self.put('mod'+str(i),SOURCE.replace('text.metrics','fixture.mod'+str(i)))
  d=Discovery(self.root,self.db)
  with patch.object(scanning,'SPOOL_DISK_BUDGET',len(SOURCE.encode())+100):v=await d.refresh()
  self.assertEqual(v['registered'],0);self.assertTrue(v['truncated'])
  self.assertLessEqual(sum(f.stat().st_size for f in (self.base/'discovery-source').iterdir()),len(SOURCE.encode())+100)
 async def test_default_capacity_and_cache_does_not_double_count(self):
  self.put('one',SOURCE);d=Discovery(self.root,self.db);self.assertEqual(d._max_plugins,3072)
  first=await d.refresh()
  for _ in range(3):
   v=await d.refresh();self.assertEqual(v['metadata_bytes'],first['metadata_bytes']);self.assertEqual(v['metadata_cached'],1)
 async def test_tampered_own_blob_repaired_using_live_pinned_bytes(self):
  self.put('one',SOURCE);d=Discovery(self.root,self.db);await d.refresh();entry=d._snapshot['text.metrics'];p=Path(entry.source.path);p.write_bytes(b'x'*len(SOURCE.encode()))
  with self.assertRaises(SanitizedError):entry.source.load_checked()
  await d.refresh();self.assertEqual((await d.execute('text.metrics',{'text':'hello world'}))['words'],2)
 async def test_foreign_hardlink_or_permissions_not_replaced_or_pruned(self):
  self.put('one',SOURCE);d=Discovery(self.root,self.db);await d.refresh();p=Path(d._snapshot['text.metrics'].source.path);p.chmod(0o644);before=p.read_bytes()
  v=await d.refresh();self.assertEqual(v['registered'],0);self.assertEqual(p.read_bytes(),before);self.assertEqual(p.stat().st_mode&0o777,0o644)
 async def test_metadata_fast_path_never_accepts_decorator_side_effect(self):
  source=SOURCE+'\ndef mutate(f):\n globals()["Execute"]=3\n return f\n@mutate\ndef helper():pass\n'
  self.put('one',source);d=Discovery(self.root,self.db);v=await d.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_PLUGIN':1})
 async def test_bad_submodule_import_is_child_audited(self):
  self.put('one',SOURCE.replace('import re','import re.does_not_exist'));d=Discovery(self.root,self.db);v=await d.refresh();self.assertEqual(v['registered'],0);self.assertEqual(v['error_codes'],{'E_PLUGIN':1})
