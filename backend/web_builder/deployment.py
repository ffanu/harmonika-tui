"""Registered Vercel adapter. Submission is not a claim that a build is READY.
Official contract: https://vercel.com/docs/rest-api/deployments/create-a-new-deployment
No account is created, no secret accepted from public requests, no blind POST retry.
"""
import asyncio,base64,json,os,re,stat,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlencode
from .common import BuilderError,require,safe_path
SCHEMA={'type':'object','properties':{'target':{'type':'string','pattern':'^[a-zA-Z0-9_-]{1,40}$'},'environment':{'type':'string','enum':['preview','production']}},'required':['target'],'additionalProperties':False}
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise BuilderError('deployment_redirect_refused',502)
def config(connectors,actor,alias,environment):
 require(connectors is not None,'deployment_connector_not_configured',503);t=getattr(connectors,'deployments',{}).get(alias)
 require(type(t) is dict and type(t.get('principals')) is list and actor in t['principals'],'deployment_target_not_authorized',403);require(t.get('provider')=='vercel' and t.get('cost_authorized') is True,'deployment_not_authorized',403)
 require(environment!='production' or t.get('production_authorized') is True,'production_not_authorized',403)
 require(re.fullmatch(r'[a-z][a-z0-9-]{0,49}',t.get('project','')) is not None,'invalid_deployment_config',503)
 return t
def credential(t):
 p=Path(t.get('token_file',''));require(str(p).startswith('/etc/harmonika-ai/builder-targets/') and p.is_absolute() and p==p.resolve(),'invalid_deployment_config',503)
 fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
 with os.fdopen(fd) as f:
  s=os.fstat(f.fileno());require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_mode&0o077==0,'unsafe_deployment_credential',503);token=f.read(1024).strip()
 require(16<=len(token)<=512 and '\n' not in token,'invalid_deployment_credential',503);return token
def request(t,method,path,body=None):
 require(path.startswith(('/v13/deployments','/v6/deployments/')),'invalid_deployment_request')
 team=t.get('team_id')
 if team:
  require(re.fullmatch(r'team_[A-Za-z0-9]{1,100}',team) is not None,'invalid_deployment_config',503);path+='?'+urlencode({'teamId':team})
 raw=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request('https://api.vercel.com'+path,data=raw,method=method,headers={'Authorization':'Bearer '+credential(t),'Content-Type':'application/json'})
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=45) as response:data=response.read(1048577)
 except urllib.error.HTTPError:raise BuilderError('deployment_provider_rejected',502) from None
 except (TimeoutError,OSError,urllib.error.URLError):raise BuilderError('deployment_transport_unknown',502) from None
 require(len(data)<=1048576,'deployment_response_limit',502)
 try:value=json.loads(data)
 except ValueError:raise BuilderError('invalid_deployment_response',502) from None
 require(type(value) is dict,'invalid_deployment_response',502);return value
def payload(t,files,j,environment):
 entries=[]
 for path,content in files.items():
  safe_path(path);name=Path(path).name.lower()
  if name=='.env' or name.startswith('.env.') and name!='.env.example' or name.endswith(('.pem','.key')):raise BuilderError('deployment_secret_file_rejected',409)
  raw=base64.b64decode(content,validate=True)
  require(not re.search(rb'hai_(?:live|internal)_[0-9a-f]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',raw),'deployment_secret_content_rejected',409)
  entries.append({'file':path,'data':content,'encoding':'base64'})
 require(bool(entries),'empty_project',409)
 out={'name':t['project'],'files':entries,'meta':{'harmonika_job_id':j},'projectSettings':{'framework':t.get('framework','nextjs')}}
 if environment=='production':out['target']='production'
 return out
async def submit(rt,args,files,j,actor):
 environment=args.get('environment','preview');t=config(rt.connectors,actor,args['target'],environment);body=payload(t,files,j,environment);v=await asyncio.to_thread(request,t,'POST','/v13/deployments',body)
 uid=v.get('id') or v.get('uid');require(type(uid) is str and re.fullmatch(r'dpl_[A-Za-z0-9]{1,100}',uid) is not None,'invalid_deployment_response',502)
 state=v.get('readyState') or v.get('status') or 'UNKNOWN'
 return {'status':'completed','execution_kind':'deployment_submission','data':{'target':args['target'],'deployment_id':uid,'provider_state':state,'ready_confirmed':state=='READY','environment':environment,'remote_submission_started':True,'client_job_id':j},'limitations':['Submission can trigger a billable remote build. READY is not claimed until the provider reports READY. Stop of the local job does not roll back a remote build.']}
async def status(rt,a,j):
 job=rt.job(a,j);require(job['skill']=='builder.deploy','deployment_job_required');result=job.get('result',{}).get('data',{});uid=result.get('deployment_id');require(type(uid) is str and re.fullmatch(r'dpl_[A-Za-z0-9]{1,100}',uid) is not None,'deployment_id_unavailable',409)
 t=config(rt.connectors,a,result['target'],result['environment']);v=await asyncio.to_thread(request,t,'GET','/v13/deployments/'+uid);state=v.get('readyState') or v.get('status') or 'UNKNOWN';return {'deployment_id':uid,'provider_state':state,'ready_confirmed':state=='READY','environment':result['environment']}
