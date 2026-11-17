"""Master gateway adapter. Never forwards caller-selected identity or destinations."""
import json,urllib.request,urllib.error
from .catalog import IDS
from .common import private_path,PRINCIPAL
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**kw):return None

def invoke(principal,sid,value):
 if sid not in IDS or type(value) is not dict:return {'ok':False,'error':{'code':'invalid_operations_request'}}
 if type(principal) is not str or not PRINCIPAL.fullmatch(principal):return {'ok':False,'error':{'code':'access_denied'}}
 try:
  token=private_path('/etc/harmonika-operations/master.token').read_text().strip()
  request=urllib.request.Request('http://127.0.0.1:18280/invoke',data=json.dumps({'skill_id':sid,'input':value}).encode(),headers={'Content-Type':'application/json','X-Harmonika-Operations-Secret':token,'X-Harmonika-Principal':principal},method='POST')
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=42) as response:raw=response.read(262145)
  except urllib.error.HTTPError as error:raw=error.read(262145)
  if len(raw)>262144:raise ValueError()
  value=json.loads(raw)
  if type(value) is not dict or type(value.get('ok')) is not bool:raise ValueError()
  if sid=='operations.engine.diagnose' and value.get('ok') is True:
   gpu=graphics(principal);value['result']['graphics']=gpu;value['result']['healthy']=value['result'].get('healthy') is True and gpu['status']=='online'
  return value
 except Exception:return {'ok':False,'error':{'code':'operations_unavailable'},'retry_safe':False,'operation_state':'unknown' if sid in ('operations.deploy.apply','operations.deploy.rollback') else 'unavailable'}

def graphics(principal):
 """Reuse the registry's existing RTX probe, without giving its secret to workers."""
 states={'online','offline','busy','vram_full','warming','timeout','auth_failed','recovery_required','unavailable','unknown'}
 try:
  secret=private_path('/etc/harmonika-ai/master-engine.token').read_text().strip()
  req=urllib.request.Request('http://127.0.0.1:18193/capabilities',headers={'X-Harmonika-Master-Secret':secret,'X-Harmonika-Principal':principal},method='GET')
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=3) as response:raw=response.read(1048577)
  if len(raw)>1048576:raise ValueError()
  value=json.loads(raw).get('diagnostics',{}).get('graphics',{})
  status=value.get('status');return {'status':status if status in states else 'unknown','source':'existing_registry_graphics_probe'}
 except Exception:return {'status':'unavailable','source':'existing_registry_graphics_probe'}
