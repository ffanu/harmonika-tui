"""End-to-end static reports using the actual redaction and analyzer modules."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'));sys.path.insert(0,str(ROOT/'skills/harmonika-engineering/scripts'))
from engineering_analyzers import analyze

class EngineeringReportTests(unittest.TestCase):
    def review(self,source,path='app.py'):
        return analyze('code_review',{'request':'Review security and correctness', 'files':[{'path':path,'content':source}]})
    def test_source_line_citations_and_no_execution(self):
        result=self.review('import subprocess\ndef run(command):\n    subprocess.call(command, shell=True)\n')
        self.assertTrue(result['findings'])
        ids={v['id']:v for v in result['evidence']}
        for finding in result['findings']:
            self.assertIn(finding['evidence_id'],ids)
            self.assertEqual(finding['path'],'app.py')
            self.assertEqual(finding['line'],3)
        self.assertFalse(result['method']['code_executed'])
        self.assertFalse(result['method']['repository_fetched'])
    def test_analysis_never_executes_supplied_python(self):
        result=self.review('raise RuntimeError("DO_NOT_EXECUTE_FIXTURE")')
        self.assertIn(result['status'],('completed','degraded'))
    def test_empty_review_requests_actual_source(self):
        result=analyze('code_review',{'request':'Review PR https://github.com/example/project/pull/123'})
        self.assertEqual(result['status'],'clarification_required')
        self.assertEqual(result['findings'],[])
    def test_no_findings_does_not_mean_security_approval(self):
        result=self.review('def greet():\n    return "hello"\n')
        self.assertTrue(any('not' in x.lower() and ('secure' in x.lower() or 'approval' in x.lower()) for x in result['limitations']))
    def test_secret_not_reflected_in_report(self):
        result=self.review('API_KEY="fixture-private-value-8921"\npassword="fixture-private-password-7821"')
        encoded=json.dumps(result)
        self.assertNotIn('fixture-private-value-8921',encoded)
        self.assertNotIn('fixture-private-password-7821',encoded)
    def test_debug_requires_log_or_reproduction(self):
        result=analyze('debug',{'request':'Kenapa rusak?'})
        self.assertEqual(result['status'],'clarification_required')
    def test_debug_has_evidence_but_no_confirmed_execution(self):
        result=analyze('debug',{'request':'Debug missing name','logs':'Traceback (most recent call last):\n  File "app.py", line 2, in get_name\nKeyError: name', 'files':[{'path':'app.py','content':'def get_name(data):\n    return data["name"]'}]})
        self.assertTrue(result['evidence'])
        self.assertFalse(result['method']['code_executed'])
        self.assertIn('root_cause',result['report'])
        self.assertTrue(result['report']['hypotheses'])
    def test_plan_never_claims_coverage(self):
        result=analyze('testing_strategy',{'request':'Test plan untuk idempotency dan ownership invoice'})
        self.assertFalse(result['report']['coverage']['measured'])
        self.assertFalse(result['method']['code_executed'])
    def test_documentation_stays_in_scope(self):
        result=analyze('documentation',{'request':'README for supplied function','files':[{'path':'maths.py','content':'def add(a, b):\n    return a+b\n'}]})
        blob=json.dumps(result)
        self.assertIn('add',blob)
        self.assertNotIn('localhost:3000',blob)
        self.assertFalse(result['method']['repository_fetched'])
    def test_unified_diff_uses_new_side_line(self):
        result=analyze('code_review',{'request':'Review diff','diff':'--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,2 @@\n import subprocess\n+subprocess.call(command, shell=True)\n'})
        self.assertTrue(result['findings'])
        for finding in result['findings']:
            self.assertEqual(finding['line'],2)
            self.assertEqual(finding['source'],'diff')
    def test_reports_and_evidence_stay_bounded(self):
        source=('subprocess.call(command, shell=True)\n'*250)
        result=analyze('code_review',{'request':'Review','files':[{'path':'sample%d.py'%i,'content':source} for i in range(4)]})
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),65536)
        self.assertLessEqual(len(result['findings']),40)
        self.assertLessEqual(len(result['evidence']),200)
    def test_unknown_operation_is_rejected(self):
        with self.assertRaises(ValueError):analyze('shell.exec',{'request':'No'})

if __name__=='__main__':unittest.main()
