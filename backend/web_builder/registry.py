"""Exact161-ID catalog; readiness distinguishes artifacts from live operations."""
import json
from pathlib import Path
SOURCE=json.loads(Path(__file__).with_name('catalog_source.json').read_text());IDS=frozenset(x['id'] for x in SOURCE['skills'])
def _raw_schema(skill):
 from .operations import SCHEMAS
 if skill in SCHEMAS:return SCHEMAS[skill]
 if skill.startswith('ssh.'):
  return {'type':'object','properties':{'target':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,40}$'},'domain':{'type':'string','maxLength':253}},'additionalProperties':False}
 import importlib
 number=next(x['number'] for x in SOURCE['skills'] if x['id']==skill)
 return importlib.import_module('.planning' if number<=46 or 134<=number<=151 else '.generation',__package__).schema(skill)
def schema_for(skill):
 import copy
 def bind(s,name=''):
  if type(s) is not dict:return s
  out=copy.deepcopy(s)
  if out.get('type')=='string' and 'maxLength' not in out:out['maxLength']=50000 if name in ('text','spec','content','code') else 4000
  if out.get('type')=='array' and 'maxItems' not in out:out['maxItems']=1000
  if 'properties' in out:out['properties']={k:bind(v,k) for k,v in out['properties'].items()}
  if 'items' in out:out['items']=bind(out['items'],name)
  return out
 return bind(_raw_schema(skill))
def catalog(sandbox_ready=False,ssh_ready=False,asr_ready=False,notion_ready=False):
 from .constants import EXEC_IDS
 out=[]
 for r in SOURCE['skills']:
  id=r['id'];status='available';reason=None
  if id in EXEC_IDS and not sandbox_ready:status='unavailable';reason='sandbox_unavailable'
  if id in ('ssh.connect','ssh.exec','ssh.upload','ssh.download','ssh.tunnel') and not ssh_ready:status='unavailable';reason='authorized_ssh_target_required'
  if id=='spec.read.notation' and not notion_ready:status='unavailable';reason='tenant_notion_connector_required'
  if id=='spec.read.voice' and not asr_ready:status='unavailable';reason='asr_unavailable'
  modes=['sandbox_execution'] if id in EXEC_IDS else ['operator_recipe'] if id=='ssh.tunnel' else ['registered_connector'] if id in ('ssh.connect','ssh.exec','ssh.upload','ssh.download') else ['operator_recipe'] if id.startswith('ssh.') else ['workspace_read'] if id.startswith('spec.read.') else ['analysis','artifact_generation']
  try:schema=schema_for(id)
  except Exception as e:
   if getattr(e,'code',None)!='generator_pending':raise
   status='unavailable';reason='generator_pending';schema={'type':'object','properties':{},'additionalProperties':False}
  out.append({'id':id,'description':r['description'],'input_schema':schema,'execution_target':'builder_project','access':['api','tui'],'readiness':{'status':status,'reason':reason},'modes':modes,'external_deployment_claimed':False,'permissions':{'host_shell':False,'tenant_workspace':True},'requires_approval':id in EXEC_IDS or id in ('ssh.connect','ssh.exec','ssh.upload','ssh.download','ssh.tunnel')})
 return out
