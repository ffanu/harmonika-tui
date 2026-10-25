"""Inert shared registry and remote client boundaries for telecom domains."""
import importlib.util,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime/tools'))
from skill_registry import Registry,SkillError
class TelcoTUI(unittest.TestCase):
 def test_canonical_276_remote_descriptors(self):
  rows=[r for r in Registry().list() if r.get('worker')=='telco'];self.assertEqual(len(rows),276);self.assertEqual(len({r['id'] for r in rows}),276)
  for r in rows:self.assertEqual(r['execution_target'],'telco_tenant');self.assertFalse(r['company_data_connected']);self.assertEqual(r['permissions']['filesystem'],[])
 def test_remote_schema_does_not_route_to_local_document_worker(self):
  Registry().validate_input('sub.create',{'name':'Fixture'})
  self.assertEqual(Registry().route('sub.create'),('telco',''))
  with self.assertRaises(SkillError):Registry().validate_input('sub.create',['bad'])
 def test_cli_help_works_in_isolated_python(self):
  r=subprocess.run([sys.executable,'-I',str(ROOT/'runtime/tools/telco_cli.py'),'--help'],capture_output=True,text=True,timeout=5)
  self.assertEqual(r.returncode,0,r.stderr);self.assertIn('capabilities',r.stdout);self.assertIn('approve',r.stdout)
 def test_release_includes_remote_client_and_catalog(self):
  source=(ROOT/'runtime/package-release.py').read_text();self.assertIn("'telco_cli.py'",source);self.assertIn("'telco_catalog.json'",source)
