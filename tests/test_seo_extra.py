import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
from seo_extra import performance,backlinks,drift,google,google_config

class Fetch:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def __call__(self,url):return self.fetch(url)
    def fetch(self,url,**kw):
        self.calls.append((url,kw));value=self.pages(url,kw) if callable(self.pages) else self.pages[url]
        return value

def page(url,text,status=200):return {'url':url,'text':text,'status':status,'headers':{},'content_type':'text/html'}

class ExtraTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='harmonika-seo-extra-');self.root=Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
    def test_drift_accepts_valueless_html_attributes(self):
        from seo_extra import snapshot
        result=snapshot(page('https://example.com/','<title>T</title><meta name="description" content><link rel href="/x"><link rel="canonical" href>'))
        self.assertEqual(result['title'],'T');self.assertEqual(result['description'],'')
    def test_performance_distinguishes_real_lab_and_missing_provider_data(self):
        psi={'lighthouseResult':{'categories':{'performance':{'score':.83}},'lighthouseVersion':'fixture-version','audits':{'total-blocking-time':{'numericValue':80,'numericUnit':'millisecond'}}}}
        result=performance({'url':'https://example.com/'},Fetch(lambda u,kw:page(u,json.dumps(psi))))
        self.assertEqual(result['lab_score'],83);self.assertFalse(result['field_data_available'])
        self.assertNotIn('INP',result['lab_metrics'])
        result=performance({'url':'https://example.com/'},Fetch(lambda u,kw:page(u,'quota',429)))
        self.assertEqual(result['reason'],'performance_rate_limited');self.assertFalse(result['measured'])
        fetch=Fetch({});result=performance({'url':'https://example.com/?access_token=fixture'},fetch)
        self.assertEqual(fetch.calls,[]);self.assertEqual(result['reason'],'sensitive_url_not_sent_to_provider')
    def test_google_auth_lists_are_not_substring_checks(self):
        config=self.config(principal_ids='local-test')
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):self.assertEqual(google_config('local')[1],'google_config_invalid')
    def test_google_refresh_is_server_side_and_denied_responses_are_sanitized(self):
        config=self.config(expires_at=1,refresh_token='fixture-refresh',client_id='fixture-client',client_secret='fixture-secret')
        def provider(url,kw):
            if url=='https://oauth2.googleapis.com/token':return page(url,json.dumps({'access_token':'fixture-refreshed'}))
            self.assertEqual(kw['headers']['Authorization'],'Bearer fixture-refreshed')
            return page(url,'provider secret detail must not appear',403)
        fetch=Fetch(provider)
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):
            result=google('seo.ga4',{'property_id':'12345','start_date':'2026-10-01','end_date':'2026-10-20'},fetch,'local-test')
        self.assertEqual(result['reason'],'google_access_denied')
        self.assertEqual(len(fetch.calls),2);self.assertNotIn('fixture-',json.dumps(result))
        self.assertNotIn('secret detail',json.dumps(result))
    def test_pagespeed_key_is_private_and_never_returned(self):
        config=self.config(pagespeed_api_key='fixture-private-psi-key')
        fetch=Fetch(lambda u,kw:page(u,json.dumps({'lighthouseResult':{'categories':{'performance':{'score':.7}}}})))
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):
            result=performance({'url':'https://example.com/'},fetch,'local-test')
        self.assertIn('key=fixture-private-psi-key',fetch.calls[0][0])
        self.assertNotIn('fixture-private-psi-key',json.dumps(result))
    def test_drift_baseline_compare_and_no_overwrite(self):
        url='https://example.com/';file=self.root/'baseline.json'
        data={'title':'One'}
        fetch=Fetch(lambda u,kw:page(u,'<title>'+data['title']+'</title><h1>Heading</h1>'))
        result=drift({'url':url,'action':'capture','baseline_path':str(file)},fetch)
        self.assertEqual(result['status'],'completed');before=file.read_bytes()
        with self.assertRaises(FileExistsError):drift({'url':url,'action':'capture','baseline_path':str(file)},fetch)
        self.assertEqual(before,file.read_bytes())
        data['title']='Two';result=drift({'url':url,'action':'compare','baseline_path':str(file)},fetch)
        self.assertTrue(any(f['code']=='changed_title' for f in result['findings']))
        self.assertNotIn('<title>',file.read_text())
    def test_drift_rejects_unrelated_baseline_and_bad_capture(self):
        url='https://example.com/';file=self.root/'baseline.json'
        file.write_text(json.dumps({'snapshot':{'url':'https://other.example/'}}))
        result=drift({'url':url,'action':'compare','baseline_path':str(file)},Fetch(lambda u,kw:page(u,'x')))
        self.assertEqual(result['reason'],'baseline_url_mismatch')
        result=drift({'url':url,'action':'capture','baseline_path':str(self.root/'missing.json')},Fetch(lambda u,kw:page(u,'x',403)))
        self.assertEqual(result['status'],'degraded');self.assertFalse((self.root/'missing.json').exists())
    def test_backlinks_are_verified_only_from_supplied_pages(self):
        urls=['https://source.example/one','https://source.example/two']
        fetch=Fetch({urls[0]:page(urls[0],'<a href="https://target.example/path" rel="nofollow">Link</a>'),urls[1]:page(urls[1],'<p>no link</p>')})
        result=backlinks({'url':'https://target.example/','links':urls},fetch)
        self.assertEqual([r['verified'] for r in result['results']],[True,False])
        self.assertEqual(result['results'][0]['links'][0]['rel'],'nofollow')
        self.assertNotIn('authority_score',result)
        self.assertEqual(backlinks({'url':'https://target.example/'},fetch)['status'],'degraded')
    def config(self,**extra):
        file=self.root/'google-private.json'
        data={'access_token':'fixture-token','expires_at':time.time()+3600,'principal_ids':['local-test'],'search_console_properties':['https://example.com/'],'ga4_properties':['12345']}
        data.update(extra);file.write_text(json.dumps(data));file.chmod(0o600)
        return file
    def test_missing_auth_and_wrong_principal_fail_closed(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNotNone(google_config('local-test')[1])
        config=self.config()
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):
            self.assertEqual(google_config('other')[1],'google_principal_not_authorized')
            config.chmod(0o644);self.assertEqual(google_config('local-test')[1],'google_config_permissions_invalid')
    def test_google_real_request_shape_and_no_token_in_result(self):
        config=self.config();calls=[]
        def provider(url,kw):
            calls.append((url,kw));return page(url,json.dumps({'rows':[{'keys':['query','page'],'clicks':1}],'rowCount':1}))
        fetch=Fetch(provider);payload={'property_id':'https://example.com/','start_date':'2026-10-01','end_date':'2026-10-20','limit':50}
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):
            result=google('seo.search_console',payload,fetch,'local-test')
            self.assertEqual(result['status'],'completed');self.assertTrue(result['connection_verified'])
            self.assertIn('/webmasters/v3/sites/https%3A%2F%2Fexample.com%2F/searchAnalytics/query',calls[-1][0])
            body=json.loads(calls[-1][1]['body']);self.assertEqual(body['rowLimit'],50)
            self.assertEqual(calls[-1][1]['headers']['Authorization'],'Bearer fixture-token')
            result=google('seo.ga4',dict(payload,property_id='12345'),fetch,'local-test')
            self.assertEqual(result['status'],'completed');self.assertIn('/properties/12345:runReport',calls[-1][0])
            self.assertNotIn('fixture-token',json.dumps(result))
            denied=google('seo.ga4',dict(payload,property_id='99999'),fetch,'local-test')
            self.assertEqual(denied['status'],'unavailable');self.assertEqual(len(calls),2)
    def test_expired_token_never_sent_and_dates_bounded(self):
        config=self.config(expires_at=1);fetch=Fetch({})
        payload={'property_id':'12345','start_date':'2026-10-01','end_date':'2026-10-20'}
        with patch.dict(os.environ,{'HARMONIKA_SEO_GOOGLE_CONFIG':str(config)}):
            result=google('seo.ga4',payload,fetch,'local-test');self.assertEqual(result['status'],'degraded');self.assertEqual(fetch.calls,[])
            result=google('seo.ga4',dict(payload,start_date='2020-01-01'),fetch,'local-test');self.assertEqual(result['reason'],'invalid_date_range')

if __name__=='__main__':unittest.main(verbosity=2)
