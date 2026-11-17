import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

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
            # Windows stat does not represent POSIX mode bits / NTFS ACLs.
            if os.name == 'posix':self.assertEqual(stat.S_IMODE(file.stat().st_mode),0o600)
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

class TerminalLicenseGate(unittest.TestCase):
    def test_saved_license_checked_without_prompt(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': ''}):
            root=Path(temp);(root/'harmonika-api-key').write_text(KEY)
            with patch.object(m,'interactive_tui_box') as prompt:
                result=m.ensure_terminal_license(root,lambda key:m.summary(data()))
            self.assertTrue(result['coding']);prompt.assert_not_called()

    def test_environment_key_has_precedence(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': KEY}):
            root=Path(temp);(root/'harmonika-api-key').write_text('old-invalid')
            checked=[]
            m.ensure_terminal_license(root,lambda key:checked.append(key) or m.summary(data()))
            self.assertEqual(checked,[KEY])

    def test_missing_key_without_tty_blocks_agent(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': ''}), patch.object(m.sys.stdin,'isatty',return_value=False), patch.object(m,'interactive_tui_box') as prompt:
            with self.assertRaisesRegex(m.LicenseError,'license_key_required_before_terminal'):
                m.ensure_terminal_license(Path(temp))
            prompt.assert_not_called()

    def test_missing_key_with_tty_prompts_privately(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': ''}), patch.object(m.sys.stdin,'isatty',return_value=True), patch.object(m,'interactive_tui_box',return_value={'saved':True}) as prompt:
            self.assertTrue(m.ensure_terminal_license(Path(temp))['saved'])
            prompt.assert_called_once_with(Path(temp))

    def test_revoked_key_blocks_without_tty_and_reprompts_with_tty(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': KEY}), patch.object(m.sys.stdin,'isatty') as tty, patch.object(m,'interactive_tui_box',return_value={'saved':True}) as prompt:
            def revoked(key):raise m.LicenseError('invalid_or_revoked_license')
            tty.return_value=False
            with self.assertRaisesRegex(m.LicenseError,'invalid_or_revoked_license'):m.ensure_terminal_license(Path(temp),revoked)
            prompt.assert_not_called();tty.return_value=True
            with patch.object(m.sys,'stderr',io.StringIO()):self.assertTrue(m.ensure_terminal_license(Path(temp),revoked)['saved'])

    def test_outage_does_not_open_agent_or_request_replacement_key(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': KEY}), patch.object(m.sys.stdin,'isatty',return_value=True), patch.object(m,'interactive_tui_box') as prompt:
            def outage(key):raise m.LicenseError('license_service_unavailable')
            with self.assertRaisesRegex(m.LicenseError,'license_service_unavailable'):m.ensure_terminal_license(Path(temp),outage)
            prompt.assert_not_called()

    def test_chat_only_key_cannot_open_terminal_agent(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'HARMONIKA_API_KEY': KEY}), patch.object(m.sys.stdin,'isatty',return_value=False):
            with self.assertRaisesRegex(m.LicenseError,'key_not_enabled_for_terminal_coding'):
                m.ensure_terminal_license(Path(temp),lambda key:m.summary(data(False)))

class VisibleWindowsLicenseInput(unittest.TestCase):
    def test_windows_paste_uses_visible_line_input_instead_of_getpass(self):
        with patch.object(m.sys,'platform','win32'), patch('builtins.input',return_value='  '+KEY+'  ') as read, patch.object(m.getpass,'getpass',side_effect=AssertionError('hidden input must not run')):
            self.assertEqual(m.read_license_key('KEY: '),KEY)
        read.assert_called_once_with('KEY: ')

    def test_visible_paste_is_normalized_then_validated_and_not_returned(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(m.sys,'platform','win32'), patch.object(m.sys.stdin,'isatty',return_value=True), patch('builtins.input',return_value='Bearer '+KEY), patch.object(m.sys,'stderr',io.StringIO()) as screen:
            # Exercise real validation/storage with a synthetic capability response.
            original=m.activate
            with patch.object(m,'activate',side_effect=lambda key,config:original(key,config,lambda checked:m.summary(data()))):
                result=m.interactive_tui_box(Path(temp))
            self.assertTrue(result['saved']);self.assertNotIn(KEY,json.dumps(result))
            self.assertEqual((Path(temp)/'harmonika-api-key').read_text().strip(),KEY)
            self.assertIn('Ctrl+V',screen.getvalue());self.assertIn('Key terlihat',screen.getvalue())

    def test_windows_eof_cancels_instead_of_repeating_empty_prompt(self):
        with patch.object(m.sys,'platform','win32'), patch.object(m.sys.stdin,'isatty',return_value=True), patch('builtins.input',side_effect=EOFError), patch.object(m.sys,'stderr',io.StringIO()), patch.object(m,'activate') as activate:
            with self.assertRaises(SystemExit) as stopped:m.interactive_tui_box()
            self.assertEqual(stopped.exception.code,1);activate.assert_not_called()

    def test_redirected_input_never_reads_or_echoes_key(self):
        with patch.object(m.sys.stdin,'isatty',return_value=False), patch('builtins.input') as read:
            with self.assertRaisesRegex(m.LicenseError,'activation_requires_private_terminal_prompt'):m.interactive_tui_box()
            read.assert_not_called()

    def test_posix_retains_hidden_prompt(self):
        with patch.object(m.sys,'platform','linux'), patch.object(m.getpass,'getpass',return_value=KEY) as hidden, patch('builtins.input') as visible:
            self.assertEqual(m.read_license_key('KEY: '),KEY)
            hidden.assert_called_once_with('KEY: ');visible.assert_not_called()

if __name__=='__main__':unittest.main()
