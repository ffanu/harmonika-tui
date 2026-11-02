"""Signed-owner internal Studio route; credentials never leave Linux."""
import json,urllib.request,urllib.error
from pathlib import Path
def dispatch(data,owner):
 try:
  allowed={'operation','owner_context','action','request_id','asset_id','params','job_id','filename','bytes','sha256','upload_id','offset','data'}
  if set(data)-allowed:return {'ok':False,'error':{'code':'studio_invalid_request'}}
  cfg=json.loads(Path('/etc/harmonika-ai/studio.json').read_text())
  if owner!=cfg['owner']:return {'ok':False,'error':{'code':'studio_owner_required'}}
  body={k:v for k,v in data.items() if k not in ('operation','owner_context')};body['owner']=owner
  req=urllib.request.Request('http://127.0.0.1:18275/rpc',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+cfg['bridge_token'],'Content-Type':'application/json'})
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=12) as r:raw=r.read(2*1024*1024+1)
  if len(raw)>2*1024*1024:raise ValueError()
  return json.loads(raw)
 except Exception:return {'ok':False,'status':'degraded','error':{'code':'studio_transport_unavailable'}}
