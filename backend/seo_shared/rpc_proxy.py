"""Owner RPC and authenticated registry share this fixed, private SEO transport."""
import json,re,urllib.request,urllib.error
from pathlib import Path
OPS={'seo_capabilities':'/capabilities','seo_invoke':'/invoke','seo_job':'/job','seo_request':'/request','seo_artifact':'/artifact'}
FIELDS={'seo_capabilities':set(),'seo_invoke':{'skill_id','input','request_id'},'seo_job':{'job_id'},'seo_request':{'request_id'},'seo_artifact':{'artifact_id','offset','limit'}}
def call(principal,path,data):
 try:
  token=Path('/etc/harmonika-ai/seo-shared.token').read_text().strip();body=json.dumps(data,separators=(',',':')).encode()
  req=urllib.request.Request('http://127.0.0.1:18260'+path,data=body,headers={'Content-Type':'application/json','X-Harmonika-SEO-Secret':token,'X-Harmonika-Principal':principal})
  try:
   with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=16) as r:raw=r.read(262145)
  except urllib.error.HTTPError as e:raw=e.read(262145)
  if len(raw)>262144:raise ValueError()
  return json.loads(raw)
 except Exception:return {'ok':False,'status':'degraded','error':{'code':'seo_unavailable'}}
def dispatch(data,owner):
 op=data.get('operation')
 if op not in OPS or set(data)-FIELDS[op]-{'operation','owner_context'}:return {'ok':False,'error':{'code':'invalid_request'}}
 return call('owner:'+owner,OPS[op],{k:v for k,v in data.items() if k not in ['operation','owner_context']})
