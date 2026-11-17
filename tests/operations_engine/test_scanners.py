import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from backend.operations_engine import scanners
from backend.operations_engine._adapter import execute, environment

class Scanners(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.base=Path(self.temp.name).resolve();self.project=self.base/'project';self.project.mkdir();self.artifacts=self.base/'artifacts';self.artifacts.mkdir(mode=0o700)
  (self.project/'app.py').write_text('eval(input())\n',encoding='utf-8')
  self.config={'project_root':str(self.project),'artifacts_root':str(self.artifacts),'tools':{'semgrep':sys.executable,'trivy':sys.executable},'scanner_policy':{'semgrep':True,'trivy':['vuln','secret','misconfig']}}
 def fake(self,argv,**kwargs):
  if argv[-1]=='--version':return 0,b'1.185.0\n'
  self.assertEqual(argv[1],'scan');self.assertIn('--metrics=off',argv);self.assertNotIn('auto',argv)
  stage=Path(argv[-1]);self.assertFalse((stage/'.env').exists());self.assertFalse((stage/'.semgrepignore').exists())
  return 0,json.dumps({'results':[{'check_id':'harmonika-python-eval','path':str(stage/'app.py'),'start':{'line':1},'extra':{'severity':'WARNING','lines':'PRIVATE_MATCH','metavars':{'value':'PRIVATE_SECRET'},'message':'PRIVATE_MESSAGE'}}],'paths':{'scanned':[str(stage/'app.py')]},'errors':[]}).encode()
 def test_fixed_semgrep_adapter_sanitizes_and_excludes_private_files(self):
  (self.project/'.env').write_text('PRIVATE_CREDENTIAL',encoding='utf-8');(self.project/'.semgrepignore').write_text('*',encoding='utf-8')
  with patch.object(scanners,'execute',side_effect=self.fake):result=scanners.scan(self.config,{'scanner':'semgrep','path':'.'})
  self.assertEqual(result['status'],'completed');self.assertFalse(result['passed'])
  self.assertEqual(result['findings'],[{'rule':'harmonika-python-eval','file':'app.py','line':1,'severity':'MEDIUM'}]);self.assertNotIn('PRIVATE_',json.dumps(result));self.assertEqual(list(self.artifacts.iterdir()),[])
 def test_missing_executable_or_vuln_database_is_not_a_pass(self):
  self.config['tools']={};self.assertEqual(scanners.scan(self.config,{'scanner':'semgrep'})['status'],'needs_toolchain')
  self.config['tools']={'trivy':sys.executable}
  with patch.object(scanners,'_version',return_value=('0.76.2',None)):result=scanners.scan(self.config,{'scanner':'trivy','mode':'vuln'})
  self.assertEqual(result['reason'],'trivy_vulnerability_database_missing');self.assertNotIn('passed',result)
 def test_bad_paths_policy_and_custom_arguments_are_rejected(self):
  for payload in ({'scanner':'semgrep','path':'../outside'},{'scanner':'semgrep','path':str(self.project)},{'scanner':'semgrep','path':'.env'},{'scanner':'semgrep','argv':['--config','remote']},{'scanner':'remote'}):
   with self.assertRaises(ValueError):scanners.scan(self.config,payload)
  self.config['scanner_policy']={}
  with self.assertRaisesRegex(ValueError,'scanner_not_allowed'):scanners.scan(self.config,{'scanner':'semgrep'})
 def test_symlink_content_is_not_scanned(self):
  try:(self.project/'linked.py').symlink_to(self.project/'app.py')
  except OSError:self.skipTest('symlink unavailable')
  with patch.object(scanners,'_version',return_value=('1.185.0',None)):
   with self.assertRaisesRegex(ValueError,'symlink_path_denied'):scanners.scan(self.config,{'scanner':'semgrep'})
 def test_trivy_parser_returns_only_mechanics_and_rejects_foreign_paths(self):
  stage=self.base/'stage';stage.mkdir();files={'app.py'}
  data={'SchemaVersion':2,'Results':[{'Target':'app.py','Secrets':[{'RuleID':'fixture-rule','Severity':'HIGH','StartLine':2,'Match':'PRIVATE_MATCH','Code':{'Lines':['PRIVATE_LINE']}}]}]}
  rows,count,partial=scanners._parse('trivy','secret',data,stage,files)
  self.assertEqual(rows,[{'rule':'fixture-rule','file':'app.py','line':2,'severity':'HIGH'}]);self.assertEqual(count,1);self.assertFalse(partial)
  data['Results'][0]['Target']='../private.py'
  with self.assertRaisesRegex(ValueError,'scanner_report_outside_snapshot'):scanners._parse('trivy','secret',data,stage,files)
 def test_empty_partial_reports_cannot_claim_clean(self):
  for data,status in [({'results':[],'paths':{'scanned':[]},'errors':[]},'inconclusive'),({'results':[],'paths':{'scanned':['app.py']},'errors':[{'message':'PRIVATE_ERROR'}]},'partial')]:
   with patch.object(scanners,'_version',return_value=('1.185.0',None)),patch.object(scanners,'execute',return_value=(0,json.dumps(data).encode())):result=scanners.scan(self.config,{'scanner':'semgrep'})
   self.assertEqual(result['status'],status);self.assertIsNone(result['passed']);self.assertNotIn('PRIVATE_ERROR',json.dumps(result))
 def test_rule_asset_hash_and_version_pins(self):
  self.assertTrue(scanners._rule('semgrep.json').is_file())
  with patch.object(scanners,'execute',return_value=(0,b'1.1.1')):self.assertEqual(scanners._version(self.config,'semgrep'),(None,'scanner_version_mismatch'))
  rules=self.base/'rules';rules.mkdir();(rules/'semgrep.json').write_text('{}',encoding='utf-8')
  with patch.object(scanners,'HERE',self.base):
   with self.assertRaisesRegex(ValueError,'scanner_rules_integrity_failed'):scanners._rule('semgrep.json')
 def test_subprocess_output_timeout_and_environment_are_bounded(self):
  with patch.dict(os.environ,{'HARMONIKA_API_KEY':'PRIVATE_KEY','SEMGREP_APP_TOKEN':'PRIVATE_KEY'}):self.assertNotIn('SEMGREP_APP_TOKEN',environment());self.assertNotIn('HARMONIKA_API_KEY',environment())
  with self.assertRaisesRegex(ValueError,'adapter_output_limit'):execute([sys.executable,'-c','print("x"*10000)'],cwd=self.base,maximum=256,timeout=5)
  with self.assertRaisesRegex(ValueError,'adapter_timeout'):execute([sys.executable,'-c','import time;time.sleep(2)'],cwd=self.base,timeout=.1)

if __name__=='__main__':unittest.main()
