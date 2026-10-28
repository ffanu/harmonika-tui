"""Root-managed shared adapters; metadata never grants private owner access."""
import copy,json,os,re,stat,urllib.request,urllib.error
from pathlib import Path
SEO_IDS=frozenset('seo.'+s for s in ['page_audit','technical','content','schema','sitemap','performance','visual','drift','local','keyword_cluster','geo','backlinks','search_console','ga4'])
CONTROL_IDS={'seo.job.status','seo.artifact.get'}
class Bindings:
 def __init__(self,path=None):self.path=Path(path or os.environ.get('HARMONIKA_SKILL_BINDINGS','/etc/harmonika-ai/skill-bindings.json'))
 def config(self):
  try:
   st=self.path.lstat()
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode&0o022 or st.st_size>131072:return {}
   data=json.loads(self.path.read_text());return data if type(data) is dict and data.get('schema_version')==1 else {}
  except (OSError,ValueError):return {}
 def supports(self,sid):return self.config().get('seo_enabled') is True and sid in SEO_IDS|CONTROL_IDS
 def rows(self,base):
  cfg=self.config();rows=copy.deepcopy(base);by={x['id']:x for x in rows}
  if cfg.get('seo_enabled') is True:
   for sid in SEO_IDS:
    row=by.get(sid)
    if not row:continue
    google=sid in {'seo.search_console','seo.ga4'}
    visual=sid=='seo.visual'
    row.update(status='unavailable' if google or visual else 'available',reason='google_oauth_and_property_authorization_required' if google else 'cpu_browser_isolation_qualification_pending' if visual else None,execution_target='shared_seo_backend',engine='seo',transport='async_jobs',access=['api','tui'])
    schema=row.setdefault('input_schema',{'type':'object','properties':{},'additionalProperties':False});schema.setdefault('properties',{})['request_id']={'type':'string','pattern':'^[a-f0-9]{32}$','description':'Stable request ID for asynchronous submission/recovery'};schema.setdefault('required',[]).append('request_id')
    for field in ['output_dir','baseline_path']:
     if field in schema['properties']:schema['properties'][field].pop('format',None);schema['properties'][field]['description']='Relative path inside this API principal private backend project'
    row.setdefault('limitations',[])
    if type(row['limitations']) is list:row['limitations']+=['Google account access is never inherited from browser or owner sessions.']
   for sid in sorted(CONTROL_IDS):
    if sid not in by:rows.append({'id':sid,'status':'available','reason':None,'engine':'seo','execution_target':'shared_seo_backend','transport':'authenticated_async_job_control','input_schema':{'type':'object','properties':({'job_id':{'type':'string'},'request_id':{'type':'string'}} if sid=='seo.job.status' else {'artifact_id':{'type':'string'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':65536}}),'additionalProperties':False},'access':['api','tui']})
  for sid,descriptor in cfg.get('owner_media',{}).items():
   if not isinstance(sid,str) or sid not in {'audio.synthesize','video.generate','video.animate_image','video.narrate'} or type(descriptor) is not dict:continue
   row=by.get(sid)
   if row is None:row={'id':sid,'input_schema':{'type':'object'}};rows.append(row)
   row.update(status='context_required',reason='authenticated_owner_terminal_required',execution_target='owner_terminal_rpc',audience='internal_owner',available_in_owner_tui=True,public_invocation_enabled=False)
   row['qualified_duration_seconds']=descriptor.get('qualified_duration_seconds',[])
  return sorted(rows,key=lambda x:x['id'])
 def invoke(self,principal,sid,value):
  if not self.supports(sid):return {'ok':False,'error':{'code':'engine_context_required'}}
  if type(value) is not dict:return {'ok':False,'error':{'code':'invalid_request'}}
  if sid=='seo.job.status':
   if set(value)=={'job_id'}:path='/job';body=value
   elif set(value)=={'request_id'}:path='/request';body=value
   else:return {'ok':False,'error':{'code':'invalid_request'}}
  elif sid=='seo.artifact.get':path='/artifact';body=value
  else:
   body=dict(value);rid=body.pop('request_id',None)
   if not isinstance(rid,str) or not re.fullmatch('[a-f0-9]{32}',rid):return {'ok':False,'error':{'code':'seo_request_id_required'}}
   path='/invoke';body={'skill_id':sid,'input':body,'request_id':rid}
  try:
   token=Path('/etc/harmonika-ai/seo-shared.token').read_text().strip();req=urllib.request.Request('http://127.0.0.1:18260'+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Harmonika-SEO-Secret':token,'X-Harmonika-Principal':principal})
   try:
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=16) as response:raw=response.read(262145)
   except urllib.error.HTTPError as e:raw=e.read(262145)
   if len(raw)>262144:raise ValueError()
   return json.loads(raw)
  except Exception:return {'ok':False,'error':{'code':'seo_unavailable'}}
