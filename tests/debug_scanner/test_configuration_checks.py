import base64,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend'))
from debug_scanner.configuration_checks import configuration_scan,cc_origin
class Configuration(unittest.TestCase):
 def jwt(self,header=None,claims=None,signature=''):
  def b(v):return base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip('=')
  return {'token':b(header or {'alg':'none'})+'.'+b(claims or {'exp':2000,'email':'PRIVATE@example.invalid'})+'.'+signature,'allowed_algorithms':['RS256'],'now_epoch':1000,'leeway_seconds':0}
 def test_jwt_unsigned_policy_and_no_claims_or_remote_fetch(self):
  v=self.jwt({'alg':'none','jku':'https://private.invalid/key'});r=configuration_scan('sec.jwt.vulnerability',v);self.assertIn('JWT.UNSIGNED_TOKEN',{f['rule_id'] for f in r['findings']});self.assertFalse(r['signature_verified']);self.assertFalse(r['remote_key_fetch_performed']);self.assertNotIn('PRIVATE',json.dumps(r));self.assertNotIn('private.invalid',json.dumps(r));self.assertNotIn(v['token'],json.dumps(r))
 def test_jwt_signed_segment_is_not_signature_verification(self):
  r=configuration_scan('sec.jwt.vulnerability',self.jwt({'alg':'RS256'},signature='AAAA'));self.assertTrue(r['algorithm_within_declared_policy']);self.assertFalse(r['signature_verified']);self.assertFalse(r['confirmed_vulnerabilities']);self.assertFalse(r['findings'])
 def test_jwt_expiration_boundary_leeway_fractional_dates(self):
  v=self.jwt({'alg':'RS256'},{'exp':1000,'nbf':1002,'iat':1003},'AAAA');r=configuration_scan('sec.jwt.vulnerability',v);self.assertEqual(r['time_checks']['exp'],'expired');self.assertEqual(r['time_checks']['nbf'],'not_yet_valid');v['leeway_seconds']=5;r=configuration_scan('sec.jwt.vulnerability',v);self.assertEqual(set(r['time_checks'].values()),{'within_declared_time'});v=self.jwt({'alg':'RS256'},{'exp':1000.5},'AAAA');self.assertEqual(configuration_scan('sec.jwt.vulnerability',v)['time_checks']['exp'],'within_declared_time')
 def test_jwt_boolean_claim_invalid_base64_duplicates_and_extra_fields(self):
  v=self.jwt({'alg':'RS256'},{'exp':True},'AAAA');self.assertEqual(configuration_scan('sec.jwt.vulnerability',v)['time_checks']['exp'],'invalid_type')
  for token in ['bad','!.e30.','eyJhbGciOiJub25lIiwiYWxnIjoiUlMyNTYifQ.e30.']:
   v=self.jwt();v['token']=token
   with self.subTest(token=token),self.assertRaises(ValueError):configuration_scan('sec.jwt.vulnerability',v)
 def test_cors_wildcard_credentials_browser_rejection_is_not_exploit(self):
  r=configuration_scan('sec.cors.misconfig',{'headers':{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Credentials':'true'},'trusted_origins':['*'],'dynamic_origin':False});self.assertFalse(r['credentialed_response_sharing_structurally_allowed']);self.assertTrue(any('BLOCKED_BY_BROWSER' in f['rule_id'] for f in r['findings']));self.assertFalse(r['confirmed_vulnerabilities'])
 def test_cors_explicit_trust_vary_and_case_sensitive_true(self):
  v={'headers':{'Access-Control-Allow-Origin':'https://good.invalid','Access-Control-Allow-Credentials':'true','Vary':'Origin'},'trusted_origins':['https://good.invalid'],'dynamic_origin':True};self.assertFalse(configuration_scan('sec.cors.misconfig',v)['findings']);v['headers']['Access-Control-Allow-Credentials']='True';self.assertFalse(configuration_scan('sec.cors.misconfig',v)['credential_header_exact_true']);del v['headers']['Vary'];self.assertTrue(any('WITHOUT_VARY' in f['rule_id'] for f in configuration_scan('sec.cors.misconfig',v)['findings']))
 def test_cors_port_zero_and_duplicate_headers(self):
  self.assertNotEqual(cc_origin('https://good.invalid:0'),cc_origin('https://good.invalid'))
  with self.assertRaises(ValueError):configuration_scan('sec.cors.misconfig',{'headers':{'vary':'Origin','Vary':'Cookie'},'trusted_origins':[],'dynamic_origin':False})
 def contrast(self,fg,bg,**kw):return configuration_scan('uiux.color.contrast',{'foreground':fg,'background':bg,'large_text':False,'enhanced':False,**kw})
 def test_contrast_boundaries_opaque_colors_and_large_text(self):
  self.assertEqual(self.contrast('#000000','#FFFFFF')['contrast_ratio'],21);self.assertFalse(self.contrast('#FFFFFF','#FFFFFF')['meets_supplied_text_context']);self.assertTrue(self.contrast('#767676','#FFFFFF')['meets_supplied_text_context']);self.assertFalse(self.contrast('#777777','#FFFFFF')['meets_supplied_text_context']);self.assertTrue(self.contrast('#777777','#FFFFFF',large_text=True)['meets_supplied_text_context']);self.assertFalse(self.contrast('#767676','#FFFFFF',enhanced=True)['meets_supplied_text_context'])
  with self.assertRaises(ValueError):self.contrast('rgba(0,0,0,0.5)','#FFFFFF')
 def html(self,html):return configuration_scan('uiux.accessibility',{'html':html})
 def test_html_missing_names_alt_lang_and_duplicate_ids(self):
  r=self.html('<html><img><button></button><input id="x"><input id="x"></html>');rules={x['rule_id'] for x in r['findings']};self.assertIn('A11Y.IMAGE_ALT_MISSING',rules);self.assertIn('A11Y.DOCUMENT_LANGUAGE_MISSING',rules);self.assertIn('A11Y.DUPLICATE_ID',rules);self.assertIn('A11Y.INTERACTIVE_NAME_MISSING',rules);self.assertFalse(r['rendered_dom_analyzed']);self.assertFalse(r['full_wcag_audit'])
 def test_html_valid_labels_aria_hidden_decorative_and_no_execution(self):
  html='<html lang="id"><img alt=""><button aria-label="Simpan"></button><label for="a">Nama</label><input id="a"><label>Alamat<textarea></textarea></label><button><img alt="Buka"></button><input type="submit"><input type="hidden"><div hidden><button></button></div><script>throw Error("PRIVATE");</script></html>';r=self.html(html);self.assertFalse(r['findings']);self.assertFalse(r['source_executed']);self.assertNotIn('PRIVATE',json.dumps(r))
 def test_html_boolean_attrs_and_labelledby_reference(self):
  r=self.html('<html lang><button aria-label></button><div aria-hidden><button></button></div><span id="n">Buka</span><button aria-labelledby="n"></button>');self.assertTrue(r['findings']);self.assertFalse(any(f['evidence']['line']<1 for f in r['findings']))
 def test_html_depth_limit_and_remote_url_never_fetched(self):
  r=self.html('<button aria-label="Buka" onclick="fetch(\'https://private.invalid\')"></button><img alt="x" src="https://private.invalid/x">');self.assertFalse(r['findings'])
  with self.assertRaises(ValueError):self.html('<div>'*81)
