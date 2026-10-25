"""Signed TUI legal descriptors never become host execution privileges."""
import asyncio,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];TOOLS=ROOT/'runtime/tools'
sys.path.insert(0,str(TOOLS))
from skill_registry import Registry,SkillError
from skill_executor import SkillExecutor,ExecutionContext
class LegalTerminal(unittest.IsolatedAsyncioTestCase):
 def test_catalog_contains_99_unique_tenant_descriptors(self):
  entries=[x for x in Registry().list() if x.get('worker')=='legal'];self.assertEqual(len(entries),99);self.assertEqual(len({x['id'] for x in entries}),99)
  self.assertTrue(all(x['execution_target']=='legal_tenant' for x in entries));self.assertEqual(len(Registry().list()),601)
 async def test_no_fallthrough_to_local_shell_or_document_worker(self):
  with tempfile.TemporaryDirectory() as tmp:
   ctx=ExecutionContext('fixture',str(Path(tmp).resolve()),frozenset({'intent.detect.legal'}),True);ex=SkillExecutor(Path(tmp).resolve()/'state')
   with self.assertRaises(SkillError) as e:await ex.execute('intent.detect.legal',{'text':'fixture'},ctx)
   self.assertEqual(e.exception.code,'legal_tenant_required')
 def test_cli_help_has_no_private_keys_or_provider_endpoint(self):
  p=subprocess.run([sys.executable,'-I',str(TOOLS/'legal_cli.py'),'--help'],capture_output=True,text=True,timeout=5)
  self.assertEqual(p.returncode,0,p.stderr);self.assertIn('create-case',p.stdout);self.assertNotIn('hai_live_',p.stdout);self.assertNotIn('127.0.0.1:17890',p.stdout)
 def test_release_includes_legal_client_and_catalog(self):
  src=(ROOT/'runtime/package-release.py').read_text();self.assertIn("'legal_cli.py'",src);self.assertIn("'legal_catalog.json'",src)
