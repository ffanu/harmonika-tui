import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock,patch
import unittest
import json
import shutil
import subprocess

spec=importlib.util.spec_from_file_location('mailjet_native',Path(__file__).resolve().parents[1]/'runtime/tools/mailjet_native.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class NativeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'node required for native-script fixture')
    def test_actual_script_outputs_only_allowlisted_status_not_private_ui_values(self):
        fixture=r'''
const el=(role,text,children=[])=>({role:()=>role,description:()=>text,title:()=>text,value:()=>text,uiElements:()=>children,enabled:()=>true});
const field=el('AXTextField','How many contacts are in your database?');
field.value=()=>{throw Error('field value must never be read')};
const privacy=el('AXTextField',"What is the URL for your business' privacy policy?");privacy.value=field.value;
const extra=el('AXTextArea','Additional details regarding your emailing needs or your organization that could help us review your account');extra.value=field.value;
const confirmation=el('AXCheckBox','I confirm that all the information given is true and accurate. I also understand and agree with Mailjet terms.');confirmation.value=field.value;
const web=el('AXWebArea','',[el('AXStaticText','PRIVATE_SENTINEL'),el('AXStaticText','a'.repeat(32)),
 el('AXStaticText','Your email was successfully verified.'),el('AXStaticText','Verify your business'),
 el('AXStaticText','Account activation is required'),
 el('AXStaticText','Your account will be activated soon - we are reviewing your request.'),
 el('AXStaticText','Your ticket was successfully submitted'),
 el('AXStaticText','Your request is currently being reviewed. You will not be able to send until your business verification ticket is resolved. However, you can start preparing email templates, contact forms and more.'),field,privacy,extra,confirmation]);
const tab={id:()=>'123',url:()=>'https://app.mailjet.com/account/activation'};
const window={tabs:()=>[tab],activeTab:tab};const windows=()=>[window];windows[0]=window;
const nativeWindows=[{uiElements:[web]}];
globalThis.Application=name=>name==='Google Chrome'?{running:()=>true,windows,activate:()=>{}}:{processes:{byName:()=>({windows:nativeWindows})}};
console.log(run([JSON.stringify({action:'status',tab_id:'123'})]));
'''
        r=subprocess.run(['node'],input=m.SCRIPT+fixture,text=True,capture_output=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertNotIn('PRIVATE_SENTINEL',r.stdout);self.assertNotIn('a'*32,r.stdout)
        result=json.loads(r.stdout);self.assertTrue(result['status']['email_verified_observed'])
        self.assertEqual(result['required_inputs'],['contacts_count','privacy_policy_url']);self.assertFalse(result['values_returned'])
        self.assertEqual(result['optional_inputs'],['organization_and_email_use_details'])
        self.assertTrue(result['truth_confirmation_observed'])
        self.assertTrue(result['status']['business_review_pending_observed'])
        self.assertTrue(result['status']['business_submission_received_observed'])
        self.assertTrue(result['status']['sending_blocked_pending_review_observed'])
        self.assertTrue(result['status']['api_credential_activation_gate_observed'])

    @unittest.skipUnless(shutil.which('node'),'node required for native-script fixture')
    def test_actual_script_rejects_credential_pages_and_lookalike_origins_before_ui_access(self):
        for url in ('https://app.mailjet.com/account/reset-key','https://app.mailjet.com.evil.example/account/activation'):
            fixture='''
const tab={id:()=>'123',url:()=>URL_VALUE};const w={tabs:()=>[tab]};
globalThis.Application=name=>{if(name!=='Google Chrome')throw Error('native UI must not be read');return {running:()=>true,windows:()=>[w]}};
console.log(run([JSON.stringify({action:'status',tab_id:'123'})]));
'''.replace('URL_VALUE',json.dumps(url))
            r=subprocess.run(['node'],input=m.SCRIPT+fixture,text=True,capture_output=True,timeout=5)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertEqual(json.loads(r.stdout)['error'],'mailjet_activation_scope_changed')

    @unittest.skipUnless(shutil.which('node'),'node required for native-script fixture')
    def test_api_page_gate_status_does_not_authorize_navigation_or_key_mutation(self):
        fixture='''
const tab={id:()=>'123',url:()=>'https://app.mailjet.com/account/apikeys'};const w={tabs:()=>[tab]};
globalThis.Application=name=>{if(name!=='Google Chrome')throw Error('native UI must not be read');return {running:()=>true,windows:()=>[w]}};
console.log(run([JSON.stringify({action:'open_business_verification',tab_id:'123'})]));
'''
        r=subprocess.run(['node'],input=m.SCRIPT+fixture,text=True,capture_output=True,timeout=5)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(json.loads(r.stdout)['error'],'native_navigation_requires_activation_page')

    def test_secret_mailbox_submission_and_other_actions_never_reach_browser(self):
        b=Mock()
        with patch.dict(sys.modules,{'desktop_browser':b}):
            for action in ('submit','generate_secret_key','read_email','set_permission','capture'):
                self.assertFalse(m.invoke({'action':action,'tab_id':'123'})['ok'])
            self.assertFalse(m.invoke({'action':'status','tab_id':'123','otp':'PRIVATE_SENTINEL'})['ok'])
        b.run_jxa.assert_not_called()

    def test_navigation_needs_existing_authorization(self):
        b=Mock()
        with patch.dict(sys.modules,{'desktop_browser':b}):
            r=m.invoke({'action':'open_activation','tab_id':'123'})
        self.assertFalse(r['action_sent']);b.run_jxa.assert_not_called()

    def test_status_uses_native_script_with_bounded_timeout_and_no_page_js(self):
        b=Mock();b.run_jxa.return_value={'ok':True,'values_returned':False}
        with patch.dict(sys.modules,{'desktop_browser':b}):
            self.assertTrue(m.invoke({'action':'status','tab_id':'123'})['ok'])
        self.assertEqual(b.run_jxa.call_args.kwargs['timeout'],45)
        self.assertEqual(b.run_jxa.call_args.args[1],{'action':'status','tab_id':'123'})
        self.assertNotIn('.execute(',m.SCRIPT)
        self.assertIn('mailjet_activation_scope_changed',m.SCRIPT)

    def test_unknown_navigation_outcome_is_not_marked_retryable(self):
        class BrowserError(Exception):pass
        b=Mock();b.BrowserError=BrowserError;b.run_jxa.side_effect=BrowserError('browser_action_state_unknown_do_not_retry')
        with patch.dict(sys.modules,{'desktop_browser':b}):
            r=m.invoke({'action':'open_activation','tab_id':'123','authorized_activation':True})
        self.assertEqual(r['execution_state'],'unknown');self.assertFalse(r['safe_to_retry'])


if __name__=='__main__':unittest.main()
