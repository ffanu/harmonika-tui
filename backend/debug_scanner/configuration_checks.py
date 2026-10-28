"""Offline JWT/CORS policy and supplied solid-color/HTML checks; no signing or fetch."""
import base64,json,re,math
from html.parser import HTMLParser
from .common import fields,need,text,integer,digest,bounded
CONFIGURATION_OPS=frozenset(('sec.jwt.vulnerability','sec.cors.misconfig','uiux.color.contrast','uiux.accessibility'))
CC_ALGORITHMS=frozenset(('none','HS256','HS384','HS512','RS256','RS384','RS512','ES256','ES384','ES512','PS256','PS384','PS512','EdDSA'))
CC_VOID=frozenset(('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'))
def cc_finding(rule,sha,line=1,severity='medium',cwe=None):return {'rule_id':rule,'severity':severity,'cwe':cwe,'confidence_kind':'supplied_configuration_or_source_candidate','evidence':{'file':'supplied_configuration','line':line,'sha256':sha},'message':'Review '+rule+' in the supplied configuration/source; runtime impact is not confirmed.'}
def cc_json_pairs(pairs):
 result={}
 for key,value in pairs:need(key not in result,'jwt_duplicate_json_key');result[key]=value
 return result
def cc_nonfinite(_):raise ValueError('invalid_jwt_json_number')
def cc_decode(segment):
 need(type(segment) is str and re.fullmatch(r'[A-Za-z0-9_-]*',segment),'invalid_jwt_segment')
 try:return base64.b64decode(segment+'='*((-len(segment))%4),altchars=b'-_',validate=True)
 except (ValueError,TypeError):raise ValueError('invalid_jwt_segment') from None

def cc_jwt(v):
 fields(v,['token','allowed_algorithms','now_epoch','leeway_seconds']);token=text(v['token'],16000);parts=token.split('.');need(len(parts)==3 and parts[0] and parts[1],'invalid_jwt_compact');allowed=v['allowed_algorithms'];need(type(allowed) is list and 1<=len(allowed)<=13 and all(type(a) is str and a in CC_ALGORITHMS for a in allowed) and len(set(allowed))==len(allowed),'invalid_algorithm_policy');now=integer(v['now_epoch'],0,9223372036854775807);leeway=integer(v['leeway_seconds'],0,3600)
 try:
  header=json.loads(cc_decode(parts[0]).decode('utf-8'),object_pairs_hook=cc_json_pairs,parse_constant=cc_nonfinite);claims=json.loads(cc_decode(parts[1]).decode('utf-8'),object_pairs_hook=cc_json_pairs,parse_constant=cc_nonfinite);cc_decode(parts[2])
 except (UnicodeError,ValueError,RecursionError):raise ValueError('invalid_jwt_json_or_encoding') from None
 need(type(header) is dict and type(claims) is dict,'jwt_objects_required');bounded({'header':header,'claims':claims});sha=digest(token);findings=[];alg=header.get('alg');known=type(alg) is str and alg in CC_ALGORITHMS
 if not known:findings.append(cc_finding('JWT.MISSING_OR_UNKNOWN_ALGORITHM',sha))
 elif alg not in allowed:findings.append(cc_finding('JWT.ALGORITHM_OUTSIDE_DECLARED_POLICY',sha,severity='high',cwe=347))
 if alg=='none':findings.append(cc_finding('JWT.UNSIGNED_TOKEN',sha,severity='high',cwe=347))
 elif not parts[2]:findings.append(cc_finding('JWT.SIGNATURE_SEGMENT_EMPTY',sha,severity='high',cwe=347))
 if any(k in header for k in ('jku','x5u','jwk')):findings.append(cc_finding('JWT.KEY_MATERIAL_HEADER_REQUIRES_TRUST_CHECK',sha,cwe=347))
 timing={}
 for key in ('exp','nbf','iat'):
  if key not in claims:timing[key]='not_present';continue
  value=claims[key]
  if type(value) not in (int,float):timing[key]='invalid_type';findings.append(cc_finding('JWT.TIME_CLAIM_TYPE',sha));continue
  if key=='exp':timing[key]='expired' if now>=value+leeway else 'within_declared_time'
  elif key=='nbf':timing[key]='not_yet_valid' if now+leeway<value else 'within_declared_time'
  else:timing[key]='future_issued_at' if now+leeway<value else 'within_declared_time'
  if timing[key] in ('expired','not_yet_valid','future_issued_at'):findings.append(cc_finding('JWT.TIME_'+timing[key].upper(),sha,severity='low'))
 return {'algorithm':alg if known else 'unrecognized','algorithm_within_declared_policy':known and alg in allowed,'time_checks':timing,'findings':findings,'signature_verified':False,'claims_returned':False,'token_returned':False,'remote_key_fetch_performed':False,'auth_bypass_tested':False}

def cc_origin(value):
 from urllib.parse import urlsplit
 if value in ('*','null'):return value
 value=text(value,500)
 try:p=urlsplit(value);port=p.port
 except ValueError:raise ValueError('invalid_cors_origin') from None
 need(p.scheme in ('http','https') and p.hostname and not p.username and not p.password and not p.query and not p.fragment and p.path=='','invalid_cors_origin')
 return (p.scheme,p.hostname.lower(),port if port is not None else (443 if p.scheme=='https' else 80))
def cc_cors(v):
 fields(v,['headers','trusted_origins','dynamic_origin']);headers=v['headers'];trusted=v['trusted_origins'];need(type(headers) is dict and len(headers)<=4 and type(trusted) is list and len(trusted)<=50 and type(v['dynamic_origin']) is bool,'invalid_cors_policy');normalized={};allowed={'access-control-allow-origin','access-control-allow-credentials','access-control-allow-methods','vary'}
 for k,value in headers.items():
  need(type(k) is str and k.lower() in allowed and k.lower() not in normalized,'invalid_or_duplicate_cors_header');need(type(value) is str and len(value)<=500 and not any(ord(c)<32 or ord(c)==127 for c in value),'invalid_cors_header_value');normalized[k.lower()]=value.strip()
 policy=[cc_origin(x) for x in trusted];origin=normalized.get('access-control-allow-origin');credential=normalized.get('access-control-allow-credentials');sha=digest(json.dumps(v,sort_keys=True));findings=[];matched=origin is not None and (cc_origin(origin) in policy or '*' in policy)
 if credential not in (None,'true'):findings.append(cc_finding('CORS.NONSTANDARD_CREDENTIAL_HEADER',sha,severity='low'))
 wildcard=origin=='*';credentialed=credential=='true'
 if wildcard and credentialed:findings.append(cc_finding('CORS.WILDCARD_CREDENTIAL_COMBINATION_BLOCKED_BY_BROWSER',sha,severity='low'))
 if origin is not None and not matched:findings.append(cc_finding('CORS.ORIGIN_OUTSIDE_DECLARED_POLICY',sha,severity='high',cwe=942))
 if origin=='null':findings.append(cc_finding('CORS.NULL_ORIGIN_REQUIRES_CONTEXT_REVIEW',sha,cwe=942))
 vary={x.strip().lower() for x in normalized.get('vary','').split(',')}
 if v['dynamic_origin'] and origin is not None and not wildcard and 'origin' not in vary and '*' not in vary:findings.append(cc_finding('CORS.DYNAMIC_ORIGIN_WITHOUT_VARY',sha,severity='low'))
 return {'origin_header_present':origin is not None,'origin_matches_declared_policy':matched,'credential_header_exact_true':credentialed,'credentialed_response_sharing_structurally_allowed':origin is not None and not wildcard and credentialed,'findings':findings,'header_values_returned':False,'browser_test_performed':False,'server_reflection_verified':False}

def cc_contrast(v):
 fields(v,['foreground','background','large_text','enhanced']);need(type(v['large_text']) is bool and type(v['enhanced']) is bool,'invalid_contrast_context')
 def luminance(color):
  need(type(color) is str and re.fullmatch(r'#[0-9A-Fa-f]{6}',color),'opaque_srgb_hex_required');channels=[int(color[i:i+2],16)/255 for i in (1,3,5)];linear=[c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4 for c in channels];return sum(a*b for a,b in zip(linear,[0.2126,0.7152,0.0722]))
 a=luminance(v['foreground']);b=luminance(v['background']);ratio=(max(a,b)+0.05)/(min(a,b)+0.05);required=(4.5 if v['large_text'] else 7) if v['enhanced'] else (3 if v['large_text'] else 4.5);passed=ratio>=required
 return {'contrast_ratio':round(ratio,6),'minimum_ratio':required,'meets_supplied_text_context':passed,'decision_uses_unrounded_ratio':True,'full_wcag_audit':False,'rendered_pixels_sampled':False,'findings':[] if passed else [cc_finding('A11Y.SOLID_TEXT_CONTRAST_BELOW_CONTEXT_THRESHOLD',digest(json.dumps(v,sort_keys=True)),severity='low')]}

class CCHTML(HTMLParser):
 def __init__(self):
  super().__init__(convert_charrefs=True);self.nodes=[];self.stack=[]
 def handle_starttag(self,tag,attrs):
  need(len(self.nodes)<2000 and len(self.stack)<80,'html_structure_limit');attr={k:(value if value is not None else '') for k,value in attrs};parent=self.stack[-1] if self.stack else None;node={'tag':tag,'attrs':attr,'line':self.getpos()[0],'children':[],'text':[],'parent':parent};index=len(self.nodes);self.nodes.append(node)
  if parent is not None:self.nodes[parent]['children'].append(index)
  if tag not in CC_VOID:self.stack.append(index)
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if tag not in CC_VOID:self.stack.pop()
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,-1,-1):
   if self.nodes[self.stack[i]]['tag']==tag:self.stack=self.stack[:i];break
 def handle_data(self,data):
  if self.stack:self.nodes[self.stack[-1]]['text'].append(data)

def cc_accessibility(v):
 fields(v,['html']);html=text(v['html'],60000);parser=CCHTML();parser.feed(html);parser.close();nodes=parser.nodes;ids={};duplicates=set();findings=[];sha=digest(html)
 for i,n in enumerate(nodes):
  key=n['attrs'].get('id')
  if key:
   if key in ids:duplicates.add(key)
   else:ids[key]=i
 def hidden(index):
  while index is not None:
   n=nodes[index];a=n['attrs']
   if 'hidden' in a or a.get('aria-hidden','').lower()=='true':return True
   index=n['parent']
  return False
 def content(index,depth=0):
  need(depth<=80,'html_structure_limit');n=nodes[index]
  if hidden(index) or n['tag'] in ('script','style','template'):return ''
  alt=n['attrs'].get('alt','') if n['tag']=='img' else ''
  return alt+' '.join(n['text'])+' '+''.join(content(c,depth+1) for c in n['children'])
 def named(index):
  n=nodes[index];a=n['attrs']
  if a.get('aria-label','').strip():return True
  refs=a.get('aria-labelledby','').split()
  if refs and all(x in ids and x not in duplicates for x in refs) and any(content(ids[x]).strip() for x in refs):return True
  if a.get('title','').strip():return True
  if n['tag'] in ('button','a'):return bool(content(index).strip())
  if n['tag']=='input' and a.get('type','text').lower() in ('submit','reset'):return True
  if n['tag']=='input' and a.get('type','text').lower() in ('button','image'):return bool(a.get('value','').strip() or a.get('alt','').strip())
  key=a.get('id')
  if key and key not in duplicates and any(other['tag']=='label' and other['attrs'].get('for')==key and content(j).strip() for j,other in enumerate(nodes)):return True
  ancestor=n['parent']
  while ancestor is not None:
   if nodes[ancestor]['tag']=='label' and content(ancestor).strip():return True
   ancestor=nodes[ancestor]['parent']
  return False
 for i,n in enumerate(nodes):
  a=n['attrs'];tag=n['tag']
  if hidden(i):continue
  rule=None
  if tag=='html' and not a.get('lang','').strip():rule='A11Y.DOCUMENT_LANGUAGE_MISSING'
  elif tag=='img' and 'alt' not in a:rule='A11Y.IMAGE_ALT_MISSING'
  elif tag=='button' or tag=='a' and 'href' in a or tag in ('input','select','textarea') and a.get('type','text').lower()!='hidden':
   if not named(i):rule='A11Y.INTERACTIVE_NAME_MISSING'
  if rule:findings.append(cc_finding(rule,sha,n['line'],severity='low'))
  if a.get('id') in duplicates:findings.append(cc_finding('A11Y.DUPLICATE_ID',sha,n['line'],severity='low'))
 need(len(findings)<=200,'accessibility_finding_limit')
 return {'nodes_analyzed':len(nodes),'findings':findings,'rendered_dom_analyzed':False,'full_wcag_audit':False,'html_or_text_returned':False,'css_visibility_computed':False,'limitations':['Static supplied HTML name/alt/lang checks only. CSS, shadow DOM, scripting, focus order, computed accessibility tree and complete WCAG conformance are not evaluated.']}
def configuration_scan(op,v):
 bounded(v)
 if op=='sec.jwt.vulnerability':result=cc_jwt(v)
 elif op=='sec.cors.misconfig':result=cc_cors(v)
 elif op=='uiux.color.contrast':result=cc_contrast(v)
 elif op=='uiux.accessibility':result=cc_accessibility(v)
 else:raise ValueError('configuration_operation_not_implemented')
 need(len(json.dumps(result,ensure_ascii=True).encode())<=120000,'configuration_result_limit');return {'status':'analyzed_supplied_configuration_or_source',**result,'confirmed_vulnerabilities':False,'source_executed':False,'production_state_changed':False}

def configuration_schema(op):
 def string(n):return {'type':'string','maxLength':n}
 if op=='sec.jwt.vulnerability':props={'token':string(16000),'allowed_algorithms':{'type':'array','minItems':1,'maxItems':13,'items':{'type':'string','enum':sorted(CC_ALGORITHMS)}},'now_epoch':{'type':'integer','minimum':0,'maximum':9223372036854775807},'leeway_seconds':{'type':'integer','minimum':0,'maximum':3600}}
 elif op=='sec.cors.misconfig':props={'headers':{'type':'object','additionalProperties':True,'description':'Only Access-Control-Allow-Origin, Access-Control-Allow-Credentials, Access-Control-Allow-Methods and Vary; values strings, no credentials/cookies.'},'trusted_origins':{'type':'array','maxItems':50,'items':string(500)},'dynamic_origin':{'type':'boolean'}}
 elif op=='uiux.color.contrast':props={'foreground':string(7),'background':string(7),'large_text':{'type':'boolean'},'enhanced':{'type':'boolean'}}
 else:props={'html':string(60000)}
 return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
