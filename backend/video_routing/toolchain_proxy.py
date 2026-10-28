"""Fixed authenticated internal adapter. HMAC owner auth is supplied by the facade."""
import json,urllib.request
from pathlib import Path
def dispatch(data,owner):
 op=data.get('operation');route={'backend_tools_capabilities':'/capabilities','backend_tools_self_test':'/self-test'}.get(op)
 if not route or set(data)-{'operation','owner_context'}:return {'ok':False,'error':{'code':'invalid_request'}}
 try:
  req=urllib.request.Request('http://127.0.0.1:18319'+route,data=b'{}',headers={'Content-Type':'application/json','X-Harmonika-Tools-Secret':Path('/etc/harmonika-ai/toolchain.token').read_text().strip()})
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=45) as r:raw=r.read(131073)
  if len(raw)>131072:raise ValueError()
  return json.loads(raw)
 except Exception:return {'ok':False,'status':'degraded','error':{'code':'toolchain_unavailable'}}
