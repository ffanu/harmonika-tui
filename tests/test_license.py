import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from urllib.error import HTTPError

spec=importlib.util.spec_from_file_location('license_client',Path(__file__).resolve().parent.parent/'runtime/license.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
KEY='hai_fixture_'+('a'*32)
def data(coding=True,unlimited=False):return {'ok':True,'features':{'chat':True,'function_calling':coding,'tool_calls':coding},'limits':{'unlimited':unlimited,'requests_per_day':1000,'max_message_bytes':102400}}
class Opener:
    def __init__(self,payload):self.payload=payload
    def open(self,req,timeout):
        assert req.full_url==m.ENDPOINT
        assert req.get_header('Authorization')=='Bearer '+KEY
        return io.BytesIO(json.dumps(self.payload).encode())
class License(unittest.TestCase):
    def test_permissions_come_from_server_not_key_prefix(self):
        result=m.check(KEY,Opener(data(False)))
        self.assertTrue(result['valid']);self.assertFalse(result['coding']);self.assertFalse(result['unlimited'])
    def test_unlimited_requires_true_boolean(self):
        response=data();response['limits']['unlimited']='true';self.assertFalse(m.summary(response)['unlimited'])
        response['limits']['unlimited']=True;self.assertTrue(m.summary(response)['unlimited'])
    def test_activation_private_key_only_and_no_key_output(self):
        with tempfile.TemporaryDirectory() as directory:
            result=m.activate(KEY,Path(directory),lambda key:m.summary(data(unlimited=True)))
            file=Path(directory)/'harmonika-api-key'
            self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
            self.assertEqual(file.read_text().strip(),KEY);self.assertNotIn(KEY,json.dumps(result))
    def test_failed_permission_does_not_replace_existing_key(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'harmonika-api-key';file.write_text('KEEP')
            with self.assertRaisesRegex(m.LicenseError,'not_enabled'):m.activate(KEY,Path(directory),lambda key:m.summary(data(False)))
            self.assertEqual(file.read_text(),'KEEP')
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'existing';target.write_text('KEEP');(root/'harmonika-api-key').symlink_to(target)
            with self.assertRaisesRegex(m.LicenseError,'unsafe_key'):m.activate(KEY,root,lambda key:m.summary(data()))
            self.assertEqual(target.read_text(),'KEEP')
    def test_auth_error_redacted(self):
        class Bad:
            def open(self,*args,**kwargs):raise HTTPError(m.ENDPOINT,401,'do not print '+KEY,{},None)
        with self.assertRaisesRegex(m.LicenseError,'invalid_or_revoked_license') as caught:m.check(KEY,Bad())
        self.assertNotIn(KEY,str(caught.exception))
    def test_redirect_never_forwards_key(self):
        with self.assertRaisesRegex(m.LicenseError,'redirect_refused'):m.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com')
    def test_malformed_response_and_key_refused(self):
        for value in ['','hai_short','invalid\r\nAuthorization: no']:
            with self.assertRaises(m.LicenseError):m.validate_key(value)
        with self.assertRaisesRegex(m.LicenseError,'invalid_license_response'):m.summary({'ok':True})

if __name__=='__main__':unittest.main()
