"""Run the real action JavaScript in a small DOM contract fixture."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/tools'))
import desktop_browser as browser

NODE = r'''
const vm=require('node:vm'),fs=require('node:fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0,changes=0;
function Input(){};function Select(){};function Textarea(){};
for(const [proto,key] of [[Input.prototype,'value'],[Input.prototype,'checked'],[Select.prototype,'value']])Object.defineProperty(proto,key,{set(v){this[key==='checked'?'checked':'value']=v}});
const el={tagName:p.el.tag||'BUTTON',id:'fixture',type:p.el.type||'button',name:p.el.name||'',
form:p.el.form||null,
innerText:p.el.label||'Preview',textContent:p.el.label||'Preview',href:p.el.href||'',disabled:!!p.el.disabled,
isConnected:true,value:'all',checked:false,attributes:[],
getAttribute(k){return k==='aria-label'?this.innerText:''},getClientRects:()=>[{}],focus(){},
click(){clicks++},dispatchEvent(){changes++},options:[{value:'offline',disabled:false}]};
let fingerprints;
if(p.stale)fingerprints=['old-control-signature'];
const window={__harmonikaView:{token:'v',url:'https://example.test',elements:[el],fingerprints}};
const context={URL,window,location:{href:p.url||'https://example.test'},HTMLInputElement:Input,HTMLSelectElement:Select,HTMLTextAreaElement:Textarea,Event:function(){}};
window.__harmonikaView.url=context.location.href;
const result=JSON.parse(vm.runInNewContext(p.source.replace('__PAYLOAD__',JSON.stringify({view_id:'v',ref:'0',...p.action})),context));
process.stdout.write(JSON.stringify({result,clicks,changes,value:el.value,checked:el.checked}));
'''


class BrowserDomTests(unittest.TestCase):
    def run_action(self, el, action, **extra):
        r = subprocess.run(['node', '-e', NODE], input=json.dumps({'source': browser.ACTION_JS, 'el': el, 'action': action, **extra}),
                           text=True, capture_output=True, check=True)
        return json.loads(r.stdout)

    def test_changed_control_rejected_before_click(self):
        r = self.run_action({'label': 'Detail Beta'}, {'action': 'click'}, stale=True)
        self.assertEqual(r['clicks'], 0);self.assertEqual(r['result']['error'], 'stale_control_take_snapshot')

    def test_disabled_click_has_no_dispatch(self):
        r = self.run_action({'label': 'Preview', 'disabled': True}, {'action': 'click'})
        self.assertEqual(r['clicks'], 0);self.assertFalse(r['result']['action_sent'])

    def test_filter_select_dispatches_input_and_change(self):
        r = self.run_action({'tag': 'SELECT', 'label': 'Status perangkat'}, {'action': 'select', 'value': 'offline'})
        self.assertTrue(r['result']['ok']);self.assertEqual(r['value'], 'offline');self.assertEqual(r['changes'], 2)

    def test_invalid_option_has_no_events(self):
        r = self.run_action({'tag': 'SELECT', 'label': 'Status perangkat'}, {'action': 'select', 'value': 'invented'})
        self.assertFalse(r['result']['ok']);self.assertEqual(r['changes'], 0)

    def test_checkbox_sets_state_without_toggling_click(self):
        r = self.run_action({'tag': 'INPUT', 'type': 'checkbox', 'label': 'Hanya prioritas'}, {'action': 'set_checked', 'checked': True})
        self.assertTrue(r['checked']);self.assertEqual(r['clicks'], 0);self.assertEqual(r['changes'], 2)

    def test_consent_and_payment_are_not_filters(self):
        for label in ('Setuju consent', 'Status payment', 'Status token'):
            r = self.run_action({'tag': 'INPUT', 'type': 'checkbox', 'label': label}, {'action': 'set_checked', 'checked': True})
            self.assertFalse(r['result']['ok']);self.assertEqual(r['changes'], 0)

    def test_password_typing_remains_blocked(self):
        r = self.run_action({'tag': 'INPUT', 'type': 'password', 'label': 'Search password'}, {'action': 'type', 'text': 'private'})
        self.assertFalse(r['result']['ok']);self.assertEqual(r['changes'], 0)

    def test_delete_and_submit_remain_blocked(self):
        for el in ({'label': 'Hapus data'}, {'label': 'Continue', 'type': 'submit'}):
            r = self.run_action(el, {'action': 'click'})
            self.assertFalse(r['result']['ok']);self.assertEqual(r['clicks'], 0)

    def test_play_notifications_default_submit_without_form_is_readonly_navigation(self):
        r = self.run_action({'label': 'Notifikasi yang belum dibaca Buka pusat notifikasi', 'type': 'submit'},
                            {'action': 'click'}, url='https://play.google.com/console/u/0/developers/123/home')
        self.assertTrue(r['result']['ok']);self.assertEqual(r['clicks'], 1)

    def test_play_exception_never_allows_form_submission_publish_or_other_origin(self):
        for el, url in [({'label':'Buka pusat notifikasi','type':'submit','form':True},'https://play.google.com/console/u/0/developers/123/home'),
                        ({'label':'Buka pusat notifikasi','type':'submit'},'https://other.test/home'),
                        ({'label':'Publish','type':'submit'},'https://play.google.com/console/u/0/developers/123/home')]:
            r = self.run_action(el, {'action':'click'}, url=url)
            self.assertFalse(r['result']['ok']);self.assertEqual(r['clicks'], 0)


if __name__ == '__main__': unittest.main()
