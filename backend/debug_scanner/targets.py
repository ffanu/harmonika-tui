"""Operator-approved GET-only probes. No payload URL, redirects or credentials."""
from .common import fields,need,text,safe_id,digest,ScannerError
TARGET_CONFIG='/opt/harmonika-ai/debug-scanner-targets.json'
TARGET_OPS=frozenset(('target.classify','target.web.detect','target.mobile.detect','target.desktop.detect','target.iot.detect','target.network.detect'))
SCOPE_OPS=frozenset(('target.scope.define','target.permission.check','ethics.authorized.scan','ethics.scope.adherence'))
PROBE_OPS=frozenset(('dast.reproduce','web.api.endpoint.bug','monitor.health.check','sec.headers.check','sec.cookies.analyze'))
def classify(op,v):
 import ipaddress
 from urllib.parse import urlsplit
 fields(v,['target']);target=text(v['target'],500);kind='unknown';details={}
 try:
  network=ipaddress.ip_network(target,strict=True);kind='network';details={'ip_version':network.version,'address_count':network.num_addresses,'live_scan_performed':False}
 except ValueError:pass
 if kind=='unknown':
  try:
   parts=urlsplit(target)
   if parts.scheme in ('http','https') and parts.hostname:
    need(not parts.username and not parts.password,'target_credentials_rejected');kind='web';details={'scheme':parts.scheme,'live_probe_performed':False}
  except ValueError:raise ScannerError('invalid_target') from None
 if kind=='unknown':
  suffix=target.lower().rsplit('.',1)[-1]
  if suffix in ('apk','aab','ipa'):kind='mobile';details={'platform':'ios' if suffix=='ipa' else 'android','file_opened':False}
  elif suffix in ('exe','msi','dmg','app','deb','rpm','elf'):kind='desktop';details={'platform':'windows' if suffix in ('exe','msi') else 'macos' if suffix in ('dmg','app') else 'linux','file_opened':False}
  elif suffix in ('bin','img','hex'):kind='firmware';details={'device_identity_verified':False,'file_opened':False}
 expected={'target.web.detect':'web','target.mobile.detect':'mobile','target.desktop.detect':'desktop','target.iot.detect':'firmware','target.network.detect':'network'}.get(op)
 return {'status':'classified_hint','kind':kind,'matches_requested_kind':kind==expected if expected else None,'target_ref':digest(target),'details':details,'classification_verified':False,'authorization_granted':False}
def load_targets(config_path=None):
 import os,stat,json
 path=config_path or TARGET_CONFIG
 try:
  # Fixed operator file only; this argument is never exposed in a skill schema.
  fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
  with os.fdopen(fd) as f:
   info=os.fstat(f.fileno());need(stat.S_ISREG(info.st_mode) and not info.st_mode&0o022 and info.st_size<=16384 and info.st_uid in (0,os.geteuid()),'unsafe_target_registry')
   raw=f.read(16385);need(len(raw)<=16384,'target_registry_limit');cfg=json.loads(raw)
  need(type(cfg) is dict and set(cfg)=={'targets'} and type(cfg['targets']) is dict and len(cfg['targets'])<=20,'invalid_target_registry')
  return cfg['targets']
 except FileNotFoundError:return {}
 except (OSError,ValueError):raise ScannerError('target_registry_unavailable') from None

def approved(v,config_path=None):
 import ipaddress
 from urllib.parse import urlsplit
 fields(v,['target_id','path'],['target_id']);alias=safe_id(v['target_id']);entries=load_targets(config_path);need(alias in entries,'target_not_registered');entry=entries[alias]
 fields(entry,['url','paths','public_probe','allow_loopback_fixture'],['url','paths','public_probe'])
 need(entry['public_probe'] is True,'target_not_authorized_for_public_probe');url=text(entry['url'],1000)
 try:parts=urlsplit(url);port=parts.port or (443 if parts.scheme=='https' else 80)
 except ValueError:raise ScannerError('invalid_registered_target') from None
 need(parts.scheme in ('https','http') and parts.hostname and not parts.username and not parts.password and not parts.query and not parts.fragment and parts.path in ('','/'),'invalid_registered_target')
 need(type(entry['paths']) is list and 1<=len(entry['paths'])<=10,'invalid_registered_paths');path=v.get('path','/')
 for candidate in entry['paths']:
  need(type(candidate) is str and candidate.startswith('/') and not candidate.startswith('//') and len(candidate)<=200 and all(32<ord(c)<127 for c in candidate) and not any(x in candidate for x in ('?','#','%','\\','..')),'invalid_registered_paths')
 need(type(path) is str and path in entry['paths'],'path_out_of_scope')
 fixture=entry.get('allow_loopback_fixture',False);need(type(fixture) is bool,'invalid_fixture_policy')
 if fixture:
  try:need(ipaddress.ip_address(parts.hostname).is_loopback,'fixture_must_be_literal_loopback')
  except ValueError:raise ScannerError('fixture_must_be_literal_loopback') from None
 else:need(parts.scheme=='https','public_probe_requires_https')
 return {'alias':alias,'host':parts.hostname,'port':port,'scheme':parts.scheme,'path':path,'fixture':fixture}
def scope_check(op,v,config_path=None):
 try:
  target=approved(v,config_path);return {'status':'authorized','target_id':target['alias'],'method':'GET','path':target['path'],'authorization_source':'operator_registered_public_probe','authorization_granted':True,'payload_boolean_authorization':False,'private_session_access':False}
 except ScannerError as e:
  if e.code in ('target_not_registered','target_not_authorized_for_public_probe','path_out_of_scope'):return {'status':'denied','reason':e.code,'authorization_granted':False}
  raise

def http_probe(op,v,config_path=None):
 import http.client,ipaddress,socket,ssl,time,hashlib
 target=approved(v,config_path);start=time.monotonic();host=target['host'];port=target['port']
 try:
  addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
  need(1<=len(addresses)<=16,'dns_answer_limit');ips=[]
  for entry in addresses:
   ip=ipaddress.ip_address(entry[4][0]);need(ip.is_loopback if target['fixture'] else ip.is_global and not ip.is_multicast and not ip.is_unspecified,'target_address_out_of_scope');ips.append(str(ip))
  address=ips[0]
  # Connect to validated numeric IP once; preserve original TLS SNI/certificate host.
  connection=http.client.HTTPConnection(host,port,timeout=4)
  connection.sock=socket.create_connection((address,port),timeout=4)
  if target['scheme']=='https':connection.sock=ssl.create_default_context().wrap_socket(connection.sock,server_hostname=host)
  try:
   connection.request('GET',target['path'],headers={'User-Agent':'Harmonika-Defensive-Scanner/1','Accept-Encoding':'identity','Accept':'text/html,application/json;q=0.8'})
   response=connection.getresponse();headers=response.getheaders();body=response.read(131073);need(len(body)<=131072,'probe_response_limit')
   names={k.lower() for k,_ in headers};code=response.status
   cookie_checks=[]
   for key,value in headers:
    if key.lower()=='set-cookie':
     attributes={x.strip().split('=',1)[0].lower() for x in value.split(';')[1:]};cookie_checks.append({'secure':'secure' in attributes,'httponly':'httponly' in attributes,'samesite':'samesite' in attributes})
   result={'status':'observed','target_id':target['alias'],'http_status':code,'reachable':True,'healthy':200<=code<300,'application_health_verified':False,'bug_reproduced':False,'reproduction_kind':'passive_get_only','redirect_followed':False,'redirect_observed':300<=code<400,'latency_ms':round((time.monotonic()-start)*1000),'response_bytes':len(body),'response_sha256':hashlib.sha256(body).hexdigest(),'security_headers':{name:name in names for name in ('content-security-policy','strict-transport-security','x-content-type-options','x-frame-options','referrer-policy')},'cookie_flags':cookie_checks[:20],'provenance':'server_registered_get_probe','body_returned':False,'cookies_returned':False,'state_mutation_requested':False,'exploit_tested':False}
   if op=='sec.headers.check':result['check_scope']='header_presence_only_no_policy_or_full_OWASP_certification'
   if op=='sec.cookies.analyze':result['check_scope']='cookie_attribute_presence_only_no_cookie_values_or_session_validation'
   return result
  finally:connection.close()
 except ScannerError:raise
 except (OSError,ValueError,http.client.HTTPException):return {'status':'degraded','reason':'registered_probe_network_or_tls_error','target_id':target['alias'],'latency_ms':round((time.monotonic()-start)*1000),'reachable':False,'exploit_tested':False,'body_returned':False}
