import base64,copy,hashlib,importlib.util,json,os,stat,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
s=importlib.util.spec_from_file_location('scanner_cli_test',ROOT/'runtime/tools/scanner_cli.py');cli=importlib.util.module_from_spec(s);s.loader.exec_module(cli)
from debug_scanner.reports import report
class ArtifactTransport(unittest.TestCase):
 def artifact(self):return report('audit.report.generate',{'findings':[],'format':'pdf'})['artifact']
 def test_pdf_saved_privately_and_base64_removed(self):
  with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'HARMONIKA_DOWNLOADS_DIR':str(Path(tmp).resolve())}):
   result=cli.process_artifacts({'report':{'artifact':self.artifact()}});a=result['report']['artifact'];self.assertNotIn('base64',json.dumps(result));p=Path(a['artifact_path']);self.assertEqual(stat.S_IMODE(p.stat().st_mode),0o600);self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),a['sha256'])
 def test_bad_sha_and_active_pdf_are_rejected_before_save(self):
  a=self.artifact();a['sha256']='0'*64
  with self.assertRaisesRegex(ValueError,'pdf_integrity_failed'):cli.save_pdf(a)
  a=self.artifact();raw=base64.b64decode(a['base64']).replace(b'HARMONIKA',b'/JavaScript HARMONIKA');a.update(base64=base64.b64encode(raw).decode(),sha256=hashlib.sha256(raw).hexdigest())
  with self.assertRaisesRegex(ValueError,'active_pdf_rejected'):cli.save_pdf(a)
 def test_symlink_directory_refused(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp).resolve();(base/'real').mkdir();(base/'link').symlink_to(base/'real',target_is_directory=True)
   with patch.dict(os.environ,{'HARMONIKA_DOWNLOADS_DIR':str(base/'link')}),self.assertRaisesRegex(ValueError,'unsafe_artifact_directory'):cli.save_pdf(self.artifact())
 def test_catalog_summary_does_not_walk_nested_skill_schemas_as_artifacts(self):
  catalog={'engine':'harmonika-debug-security-scanner','catalog_size':290,'callable_entrypoints':55,'readiness':{'available':53,'unavailable':237},'features':{'automatic_fix_or_deploy':False},'all_290_operational':False,'source_sha256':'a'*64,'skills':[]};deep={};cursor=deep
  for _ in range(16):cursor['properties']={};cursor=cursor['properties']
  catalog['skills'].append(deep)
  with patch.object(sys,'argv',['scanner_cli.py','catalog']),patch.object(cli.client,'request',return_value={'result':catalog}),patch.object(cli,'process_artifacts',side_effect=AssertionError('catalog should not traverse artifacts')):
   result=cli.main()
  self.assertEqual(result['callable_entrypoints'],55);self.assertEqual(result['readiness']['available'],53);self.assertNotIn('skills',result)
