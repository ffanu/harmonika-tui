"""Signed owner bridge; request JSON cannot choose principal, URL or secret."""
import json,urllib.request,urllib.error
from pathlib import Path
OPS={'pro_cs_capabilities':'/capabilities','pro_cs_invoke':'/invoke','pro_cs_plan':'/plan','pro_cs_validate':'/validate','pro_cs_takeover':'/takeover','pro_cs_close':'/close'}
def dispatch(data,owner):
 op=data.get('operation')
 if op not in OPS or set(data)-{'operation','owner_context','input'} or type(data.get('input',{})) is not dict:return {'ok':False,'error':{'code':'invalid_request'}}
 req=urllib.request.Request('http://127.0.0.1:18321'+OPS[op],data=json.dumps(data.get('input',{}),allow_nan=False).encode(),headers={'Content-Type':'application/json','X-Pro-CS-Secret':Path('/etc/harmonika-ai/pro-cs.token').read_text().strip(),'X-Harmonika-Principal':'owner:'+owner})
 try:
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=30) as r:raw=r.read(256001)
  except urllib.error.HTTPError as e:raw=e.read(256001)
  if len(raw)>256000:raise ValueError()
  return json.loads(raw)
 except Exception:return {'ok':False,'error':{'code':'pro_cs_unavailable'}}
