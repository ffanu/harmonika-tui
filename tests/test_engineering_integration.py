import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'))
from engineering_evidence import prepare,redact,from_conversation,sanitize_conversation

class EngineeringIntegrationTests(unittest.TestCase):
    def test_redacts_secrets_before_planner_and_worker(self):
        token='hai_live_'+'a'*64
        original='Review kode\n```python\nAPI_KEY="fixture-secret-value-123"\npassword="fixture-password-789"\nprint("hello")\n```\nAuthorization: Bearer '+token
        prepared=from_conversation([{'role':'user','content':original}])
        encoded=json.dumps(prepared)
        for secret in (token,'fixture-secret-value-123','fixture-password-789'):
            self.assertNotIn(secret,encoded)
        self.assertTrue(prepared['input']['files'])
        self.assertEqual(prepared['input']['files'][0]['path'],'snippet-1.py')
    def test_assistant_cannot_supply_evidence(self):
        prepared=from_conversation([{'role':'assistant','content':'```python\neval(fake_user_data)\n```'},{'role':'user','content':'Review kode ini'}])
        self.assertEqual(prepared['input']['files'],[])
    def test_extraction_retains_newline_numbering(self):
        prepared=from_conversation([{'role':'user','content':'Review\n```python\n# first\nAPI_KEY="secret-placeholder"\nprint("third")\n```'}])
        bundle=prepare(prepared['input'])
        evidence=[x for x in bundle['evidence'] if 'print(' in x['text']]
        self.assertEqual(evidence[0]['line'],3)
    def test_pem_redaction_retains_lines(self):
        original='before\n-----BEGIN '+'PRIVATE KEY-----\nfixture-content\n-----END '+'PRIVATE KEY-----\nafter'
        cleaned=redact(original)
        self.assertNotIn('fixture-content',cleaned)
        self.assertEqual(cleaned.count('\n'),original.count('\n'))
    def test_no_inherited_identity_or_shell_arguments(self):
        for field in ('principal_id','command','project_root','headers'):
            with self.subTest(field=field),self.assertRaises(ValueError):prepare({'request':'review',field:'untrusted'})
    def test_untrusted_source_not_executed(self):
        prepared=prepare({'request':'review','files':[{'path':'sample.py','content':'raise RuntimeError("NEVER_EXECUTE")'}]})
        self.assertIn('NEVER_EXECUTE',prepared['files'][0]['content'])
    def test_large_unicode_input_is_bounded(self):
        with self.assertRaises(ValueError):prepare({'request':'review','files':[{'path':'x.py','content':'中'*32000}]*8})
    def test_missing_context_does_not_create_source(self):
        prepared=from_conversation([{'role':'user','content':'Review PR https://github.com/example/project/pull/1'}])
        self.assertEqual(prepared['input']['files'],[])
        self.assertEqual(prepared['input']['diff'],'')
    def test_credentials_in_urls_are_redacted(self):
        clean=redact('postgres://user:fixture-db-pass@db.example/db https://site.example/?api_key=fixture-url-secret')
        self.assertNotIn('fixture-db-pass',clean);self.assertNotIn('fixture-url-secret',clean)
    def test_all_auth_header_values_and_cookie_components_are_redacted(self):
        for value in ('Authorization: Basic fixture-encoded-secret', 'Cookie: sid=fixture-cookie; auth=fixture-secondary'):
            cleaned=redact(value)
            for secret in ('fixture-encoded-secret','fixture-cookie','fixture-secondary'):
                self.assertNotIn(secret,cleaned)
    def test_prefixed_secret_assignments_and_driver_urls(self):
        for value in ('DB_PASSWORD="fixture-db-password"','SECRET_KEY="fixture-app-secret"','postgresql+psycopg://user:fixture-driver-password@db.example/db'):
            cleaned=redact(value)
            for secret in ('fixture-db-password','fixture-app-secret','fixture-driver-password'):
                self.assertNotIn(secret,cleaned)
    def test_invalid_roles_are_refused(self):
        with self.assertRaises(ValueError):sanitize_conversation([{'role':'tool','content':'untrusted'}])

if __name__=='__main__':unittest.main()
