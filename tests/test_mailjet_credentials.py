import importlib.util
from pathlib import Path
import sys
import unittest
import tempfile
import json
import time
from unittest.mock import Mock,patch
from backend.zimbra import relay

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mailjet_capture',ROOT/'runtime/tools/mailjet_credentials.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class CaptureTests(unittest.TestCase):
    def test_capture_writes_owner_private_files_but_never_returns_keys(self):
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary);expected=root/'mailjet-api-key';expected.write_text('a'*32);expected.chmod(0o600)
            browser=Mock();browser.exec_tab.return_value={'ok':True,'candidates':[{'value':'a'*32,'label':'API Key'},{'value':'b'*32,'label':'Secret Key','kind':'password'}]}
            with patch.object(m,'ROOT',root),patch.dict(sys.modules,{'desktop_browser':browser}):
                result=m.invoke({'action':'capture','tab_id':'123','authorized_secret_capture':True})
            self.assertTrue(result['credentials_saved']);self.assertNotIn('a'*32,json.dumps(result));self.assertNotIn('b'*32,json.dumps(result))
            for filename in ('mailjet-api-key','mailjet-secret-key','mailjet-credentials.json'):
                self.assertEqual((root/filename).stat().st_mode&0o777,0o600)

    def test_verification_prerequisite_blocks_generation_without_consuming_grant(self):
        with tempfile.TemporaryDirectory()as temporary:
            root=Path(temporary);p=root/'mailjet-generation-grant.json';p.write_text(json.dumps({'operation':'generate_mailjet_secret','tab_id':'123','state':'approved','at':time.time()}));p.chmod(0o600)
            browser=Mock();browser.exec_tab.return_value={'ok':True,'account_flags':{'requiresEmailVerification':True,'canCreateApiKey':True}}
            with patch.object(m,'ROOT',root),patch.dict(sys.modules,{'desktop_browser':browser,'zimbra_relay':relay}):
                result=m.invoke({'action':'generate_secret_key','tab_id':'123','authorized_secret_generation':True})
            self.assertFalse(result['action_sent']);self.assertEqual(result['error'],'resolve_mailjet_account_prerequisites_first')
            self.assertEqual(json.loads(p.read_text())['state'],'approved');self.assertEqual(browser.exec_tab.call_count,1)

    def test_harmless_tab_label_is_normalized_instead_of_false_account_failure(self):
        browser=Mock();browser.exec_tab.return_value={'ok':True,'links':[]}
        with patch.dict(sys.modules,{'desktop_browser':browser}):
            self.assertTrue(m.invoke({'action':'inspect','tab_id':'tab293670737'})['ok'])
        self.assertEqual(browser.exec_tab.call_args.args[0],'293670737')

    def test_exact_pair_and_expected_key(self):
        rows=[{'value':'a'*32,'label':'API Key'},{'value':'b'*32,'label':'Secret Key','kind':'password'}]
        self.assertEqual(m.select_pair(rows,'a'*32),('a'*32,'b'*32))

    def test_masked_values_or_multiple_secrets_fail_closed(self):
        for rows in ([{'value':'***','label':'Secret Key'}],[{'value':'a'*32,'label':'API Key'},{'value':'b'*32,'label':'Secret Key'},{'value':'c'*32,'label':'Secret Key'}]):
            with self.assertRaises(ValueError):m.select_pair(rows,'a'*32)

    def test_wrong_account_key_rejected_without_echo(self):
        with self.assertRaisesRegex(ValueError,'expected_api_key_not_on_page'):
            m.select_pair([{'value':'b'*32,'label':'API Key'}],'a'*32)

    def test_capture_requires_permission_and_does_not_run_browser(self):
        browser=Mock()
        with patch.dict(sys.modules,{'desktop_browser':browser}):
            result=m.invoke({'action':'capture','tab_id':'123'})
        self.assertFalse(result['ok']);browser.exec_tab.assert_not_called()

    def test_navigation_requires_observed_link_and_no_reset(self):
        browser=Mock();m.VIEWS['123']={'/account/reset-key'}
        with patch.dict(sys.modules,{'desktop_browser':browser}):
            for path in ('/account/unobserved','/account/reset-key'):
                self.assertFalse(m.invoke({'action':'navigate_settings','tab_id':'123','path':path})['ok'])
        browser.exec_tab.assert_not_called()


if __name__=='__main__':unittest.main()
