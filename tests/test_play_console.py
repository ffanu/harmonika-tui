import tempfile
from pathlib import Path
import unittest
import importlib.util
import sys
import json
import subprocess
from unittest.mock import patch
import zipfile
from backend.play_console.engine import scope, canonical_url, same_page, assess, preflight
from backend.play_console import engine

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'))
with patch.dict(sys.modules,{'play_console_engine':engine}):
    spec=importlib.util.spec_from_file_location('play_adapter',ROOT/'runtime/tools/play_console.py')
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)

BASE='https://play.google.com/console/u/0/developers/123'


class PlayConsoleTests(unittest.TestCase):
    def test_scope_distinguishes_developer_and_app(self):
        self.assertEqual(scope(BASE+'/app/11/publishing')['app'],'11')
        self.assertIsNone(scope(BASE+'/home')['app'])
        for url in ('https://other.test/console/u/0/developers/123/home',BASE.replace('/123','/%3Ctarget_developer_id%3E')+'/home'):
            with self.assertRaises(ValueError):scope(url)

    def test_observed_short_link_only_resolves_known_app(self):
        url='https://play.google.com/console/app/11/tracks/77/releases/3/details'
        self.assertEqual(canonical_url(url,scope(BASE+'/home'),['11']),BASE+'/app/11/tracks/77/releases/3/details')
        with self.assertRaises(ValueError):canonical_url(url,scope(BASE+'/home'),['22'])

    def test_read_profile_blocks_other_account_mutations_and_broken_placeholders(self):
        for url in (BASE.replace('/123','/999')+'/home',BASE+'/app/22/publishing',BASE+'/app/11/create-release',BASE+'/app/11/tracks/production/edit',BASE+'/home?token=secret',BASE+'/account/developer-details'):
            with self.assertRaises(ValueError):canonical_url(url,scope(BASE+'/home'),['11'])

    def test_release_pages_allowed_but_not_submission(self):
        for route in ('publishing','tracks/production','app-content/overview','closed-testing'):
            self.assertEqual(canonical_url(BASE+'/app/11/'+route,scope(BASE+'/home'),['11']),BASE+'/app/11/'+route)
        with self.assertRaises(ValueError):canonical_url(BASE+'/app/11/submit',scope(BASE+'/home'),['11'])

    def test_default_metric_redirect_is_same_readonly_page_not_account_change(self):
        url=BASE+'/app/11/store-listings'
        self.assertTrue(same_page(url,url+'?metric=METRIC_ACQUISITION&dateRange=2026_01_01-2026_02_01',scope(BASE+'/home'),['11']))
        self.assertFalse(same_page(url,url+'?action=delete',scope(BASE+'/home'),['11']))
        self.assertFalse(same_page(url,url.replace('/app/11/','/app/22/'),scope(BASE+'/home'),['11','22']))
        self.assertFalse(same_page(url+'?metric=first',url+'?metric=other',scope(BASE+'/home'),['11']))

    def test_async_default_query_addition_does_not_stop_next_audit_step(self):
        calls=[]
        pages={BASE+'/home':{'context':scope(BASE+'/home'),'url':BASE+'/home','title':'Home','text':'Apps','text_truncated':False,'apps':[{'url':BASE+'/app/11/app-dashboard','text':'Sample App\nid.sample.app'}], 'links':[], 'menu':[], 'fields':[], 'pagination':[], 'observed_at':'now'},
               BASE+'/policy-center':{'url':BASE+'/policy-center','title':'Policy','text':'Policy actual','text_truncated':False,'apps':[], 'links':[], 'menu':[], 'fields':[], 'pagination':[], 'observed_at':'now'},
               BASE+'/app/11/app-dashboard':{'url':BASE+'/app/11/app-dashboard','title':'Dashboard','text':'Testing gate','text_truncated':False,'apps':[], 'links':[], 'menu':[], 'fields':[], 'pagination':[], 'observed_at':'now'}}
        state={'url':BASE+'/home','reads':0}
        def observe(tab):return dict(pages[state['url'].split('?')[0]]),{}
        def desktop(args):
            if args['action']=='navigate':state['url']=args['url'];calls.append(args['url'])
            return {'ok':True}
        def current(*a):
            state['reads']+=1
            if state['reads']==2:state['url']=BASE+'/home?tab=default'
            return {'ok':True,'url':state['url']}
        with tempfile.TemporaryDirectory() as folder,patch.object(adapter,'observe',side_effect=observe),patch.object(adapter,'read_notifications',return_value={'items':[],'rendered_complete':True}),patch.object(adapter.browser,'desktop',side_effect=desktop),patch.object(adapter.browser,'exec_tab',side_effect=current),patch('browser_audit.prepare_output',return_value=Path(folder)):
            r=adapter.audit({'tab_id':'1','output_dir':folder})
        self.assertEqual(r['status'],'complete_for_discovered_scope')
        self.assertEqual(r['coverage']['pages_read'],3)

    def test_findings_are_evidence_bound_and_preview_only(self):
        data=assess([{'url':BASE+'/home','text':'Nyatakan status pedagang paling lambat 1 Februari 2027. Versi SDK yang Anda gunakan sudah usang.'}])
        self.assertEqual({x['code'] for x in data['findings']},{'merchant_declaration','sdk_outdated'})
        self.assertTrue(all(x['execution']=='preview_only' for x in data['findings']))
        self.assertEqual(assess([{'text':'0 tugas kebijakan'}])['findings'],[])

    def test_published_notice_is_not_production_readiness(self):
        data=assess([{'text':'Update aplikasi dipublikasikan. Pengujian tertutup.'}])
        self.assertEqual(data['release_state'],'not_authorized_to_upload_or_publish')
        self.assertIn('tidak membuktikan',data['findings'][0]['next_action'])

    def test_zip_structure_never_verifies_package_signature_or_upload_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'sample.aab'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('BundleConfig.pb',b'fixture');z.writestr('base/manifest/AndroidManifest.xml',b'fixture')
            r=preflight(p,'id.example.test')
            self.assertTrue(r['archive_structure_valid'])
            for key in ('package_verified','version_code_verified','signing_verified','target_sdk_verified','ready_to_upload','uploaded'):
                self.assertFalse(r[key])

    def test_traversal_archive_is_rejected_without_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'sample.apk'
            with zipfile.ZipFile(p,'w') as z:z.writestr('../outside','private')
            with self.assertRaises(ValueError):preflight(p)
            self.assertFalse((Path(folder).parent/'outside').exists())

    def test_each_notification_is_captured_before_accordion_collapses(self):
        state={'open':None};clicks=[]
        def observe(tab):
            items=[{'index':i,'text':('detail-' if state['open']==i else 'heading-')+str(i),
                    'text_truncated':False,'expanders':[{'label':'Card '+str(i)+' Luaskan notifikasi','expanded':'true' if state['open']==i else 'false'}]}
                   for i in range(3)]
            return {'notifications_open':True,'notifications':items,'notifications_truncated':False}, {'view_id':'v','controls':[{'label':n['expanders'][0]['label'],'ref':str(n['index'])}for n in items]}
        def desktop(args):
            if args['action']=='click':state['open']=int(args['ref']);clicks.append(state['open'])
            return {'ok':True}
        with patch.object(adapter,'observe',side_effect=observe),patch.object(adapter,'notification_button'),patch.object(adapter.browser,'desktop',side_effect=desktop):
            r=adapter.read_notifications('1')
        self.assertTrue(r['rendered_complete']);self.assertEqual(clicks,[0,1,2])
        self.assertEqual([x['text'] for x in r['items']],['detail-0','detail-1','detail-2'])
        self.assertFalse(r['history_beyond_rendered_verified'])

    def test_failed_notification_expansion_never_claims_complete(self):
        page={'notifications_open':True,'notifications_truncated':False,'notifications':[{'index':0,'text':'Heading','text_truncated':False,'expanders':[{'label':'Card Luaskan notifikasi','expanded':'false'}]}]}
        snap={'view_id':'v','controls':[{'label':'Card Luaskan notifikasi','ref':'0'}]}
        with patch.object(adapter,'observe',return_value=(page,snap)),patch.object(adapter,'notification_button'),patch.object(adapter.browser,'desktop',return_value={'ok':True}):
            r=adapter.read_notifications('1')
        self.assertFalse(r['rendered_complete']);self.assertIn('notification_expansion_unverified',r['blocked'])

    def test_nested_content_holder_is_not_mistaken_for_entire_report(self):
        code="""const vm=require('node:vm'),fs=require('node:fs'),source=JSON.parse(fs.readFileSync(0,'utf8')).source;
const node=text=>({innerText:text,getBoundingClientRect:()=>({width:500,height:200,left:0,right:500}),closest:()=>null,querySelectorAll:()=>[]});
const outer=node('Full policy report: no account issue; check every application.'),inner=node('Select app');
const document={title:'Policy',querySelectorAll:s=>s==='.content-holder'?[outer,inner]:[]};
const r=vm.runInNewContext(source,{document,location:{href:'https://play.google.com/console/u/0/developers/123/policy-center'},innerWidth:1000,getComputedStyle:()=>({visibility:'visible'})});process.stdout.write(r);"""
        r=subprocess.run(['node','-e',code],input=json.dumps({'source':adapter.READ_JS}),capture_output=True,text=True,check=True)
        self.assertTrue(json.loads(r.stdout)['text'].startswith('Full policy report'))


if __name__=='__main__':unittest.main()
