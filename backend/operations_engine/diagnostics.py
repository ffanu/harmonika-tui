"""Bounded registered health probes; never echoes URLs, bodies or credentials."""
import concurrent.futures,json,time,urllib.request,urllib.error,ipaddress
from urllib.parse import urlsplit
from .common import fields,require,private_path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**kw):return None

def probe(name,cfg):
 start=time.monotonic();state='unavailable';code=None
 try:
  url=cfg['url'];parsed=urlsplit(url)
  require(parsed.scheme in ('http','https') and not parsed.username and not parsed.password and not parsed.fragment and not parsed.query,'invalid_probe_configuration',503)
  require(ipaddress.ip_address(parsed.hostname).is_loopback,'invalid_probe_configuration',503)
  headers={}
  if cfg.get('token_file'):
   token=private_path(cfg['token_file']).read_text().strip();require(16<=len(token)<=256 and '\n' not in token,'invalid_probe_configuration',503)
   header=cfg.get('token_header','Authorization');require(header in ('Authorization','X-Harmonika-Master-Secret'),'invalid_probe_configuration',503)
   headers[header]=('Bearer ' if header=='Authorization' else '')+token
  req=urllib.request.Request(url,headers=headers,method='GET')
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=2) as response:
   code=response.status;raw=response.read(65537);require(len(raw)<=65536,'probe_result_limit',502)
   value=json.loads(raw);require(type(value) is dict,'probe_bad_payload',502)
   state='online' if code==200 and value.get('ok') is True else 'degraded'
   if cfg.get('graphics'):
    graphics=value.get('diagnostics',{}).get('graphics',{});s=graphics.get('status')
    state=s if s in ('online','offline','busy','vram_full','warming','timeout','auth_failed','recovery_required','unavailable') else 'unknown'
 except urllib.error.HTTPError as e:code=e.code;state='auth_failed' if code in (401,403) else 'unavailable'
 except (OSError,ValueError,KeyError,TypeError):state='unavailable'
 return {'component':name,'status':state,'http_status':code,'latency_ms':round((time.monotonic()-start)*1000,2)}

def diagnose(config,payload):
 fields(payload,())
 probes=config.get('health_probes',{});require(type(probes) is dict and len(probes)<=8,'invalid_probe_configuration',503)
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(lambda item:probe(*item),probes.items()))
 return {'status':'completed','healthy':bool(rows) and all(x['status']=='online' for x in rows),'components':rows,'probe_scope':'registered_read_only_endpoints','readiness_not_inferred_from_catalog':True}
