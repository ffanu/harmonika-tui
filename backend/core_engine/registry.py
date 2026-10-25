"""Exact 99-ID compatibility registry. No dynamic code, no semantic prefix aliases."""
import copy,json
from pathlib import Path
ROOT=Path(__file__).parent
SOURCE=json.loads((ROOT/'catalog_source.json').read_text());CANONICAL={x['id']:x for x in SOURCE['skills']}
CODING=json.loads((ROOT/'coding_catalog.json').read_text())
ALIASES={
 'file.read':'fs.read','file.write':'fs.write','file.edit':'code.edit','file.search':'fs.glob','file.grep':'code.grep','file.list':'fs.list',
 'shell.exec':'exec.shell','shell.background':'exec.background','code.run.python':'exec.python','code.run.node':'exec.node','code.run.shell':'exec.shell',
 'code.lint':'lint.run','code.format':'code.format','code.test':'test.run','code.scaffold':'scaffold.project',
 'git.status':'git.status','git.diff':'git.diff','git.commit':'git.commit','system.info':'exec.python'}
LOCAL=frozenset(('doc.read_pdf','doc.read_docx','doc.create_pdf','doc.create_docx','doc.create_xlsx','doc.create_pptx'))
CHAT=frozenset(('llm.chat','llm.complete'))
SYSTEM_INFO_CODE="import json,platform,os,shutil; d=shutil.disk_usage('.'); print(json.dumps({'scope':'owned_sandbox','os':platform.system(),'architecture':platform.machine(),'cpu_count':os.cpu_count(),'disk':{'total':d.total,'free':d.free}}))"

def local_descriptors():
 from . import data_tools,state
 return {**data_tools.descriptors(),**state.descriptors()}

def descriptors():
 impl=local_descriptors();rows=[]
 for id,s in CANONICAL.items():
  row={**copy.deepcopy(s),'status':'unavailable','reason':'implementation_missing','execution_target':'not_implemented','directly_invocable':False}
  if id in ALIASES:
   target=ALIASES[id];d=CODING[target]
   row.update(status='context_required',reason='coding_workspace_required',engine='coding',execution_target='coding_workspace',resolved_skill_id=target,input_schema=copy.deepcopy(d['input_schema']),requires_approval=d['requires_approval'],write_permission=d['write_permission'],runtime_readiness_endpoint='/v1/coding/capabilities',directly_invocable=True)
   if id=='system.info':row.update(input_schema={'type':'object','properties':{},'additionalProperties':False},description='Info OS/CPU/disk dalam sandbox workspace milik token; tidak membuka info host server.')
  if id in LOCAL:row.update(status='local_only',reason='local_document_runtime_required',engine='documents',execution_target='local_worker',runtime_readiness_command='harmonika skill documents capabilities')
  if id in CHAT:
   item={'type':'object','additionalProperties':False,'properties':{'role':{'type':'string','enum':['system','user','assistant']},'content':{'type':'string','minLength':1,'maxLength':16000}},'required':['role','content']}
   schema={'type':'object','additionalProperties':False,'properties':{'messages':{'type':'array','minItems':1,'maxItems':20,'items':item}},'required':['messages']}
   if id=='llm.complete':schema={'type':'object','additionalProperties':False,'properties':{'prompt':{'type':'string','minLength':1,'maxLength':16000}},'required':['prompt']}
   row.update(status='native_endpoint',reason='existing_chat_gateway',engine='chat',execution_target='chat_gateway',input_schema=schema,runtime_readiness_endpoint='/v1/public-ai/capabilities',directly_invocable=True)
  if id in impl:row.update(status='available',reason=None,engine='core',execution_target='tenant_core',input_schema=copy.deepcopy(impl[id]['input_schema']),description=impl[id]['description'],directly_invocable=True)
  if id=='workflow.schedule':row['reason']='scheduler_not_configured'
  if id in ('web.fetch','web.search','web.scrape','http.request','api.call','webhook.send'):row['reason']='network_adapter_not_bound'
  if id in ('process.kill','secret.get','env.get','env.set','file.delete','file.move'):row['reason']='scoped_adapter_required'
  if id in ('email.send','email.read','sms.send','chat.send','notification.push','calendar.create','calendar.list'):row['reason']='authorized_connector_required'
  rows.append(row)
 return rows

def capabilities():
 rows=descriptors();keys=('available','context_required','local_only','native_endpoint','unavailable')
 return {'schema_version':1,'engine':'harmonika-shared-core','source_sha256':SOURCE['sha256'],'catalog_size':99,'readiness':{s:sum(r['status']==s for r in rows) for s in keys},'features':{'bounded_data':True,'core_sse':False,'core_websocket':False,'native_mcp_tools':False,'private_memory':True,'bounded_workflows':True,'workspace_adapters':True,'chat_gateway_adapter':True,'workflow_scheduler':False,'host_filesystem_access':False,'host_shell_access':False,'host_secret_access':False,'outbound_messages':False},'limits':{'input_bytes':131072,'data_input_bytes':65536,'output_bytes':262144,'global_active':2,'per_actor_active':1,'per_actor_per_minute':60,'timeout_seconds':20,'memory_records_per_actor':100,'memory_value_bytes':16384,'memory_actor_bytes':1048576,'state_global_bytes':104857600,'memory_ttl_max_seconds':604800,'workflow_steps':12,'workflow_budget_seconds':15},'registration_is_not_runtime_readiness':True}

class CoreError(ValueError):
 def __init__(self,code,status=400):self.code=code;self.status=status

def dispatch(id,v,actor,store,idem=None):
 from . import data_tools
 pure=data_tools.descriptors()
 if id in pure:
  out=data_tools.dispatch(id,v)
  if out.get('status')!='completed':
   code=out.get('error',{}).get('code','invalid_input')
   raise CoreError(code,413 if code=='limit_exceeded' else 400)
  return {**out,'skill_id':id}
 def runner(sid,inp):
  if sid in pure:return data_tools.dispatch(sid,inp)
  try:from master_engine.analyzers import dispatch as analyze
  except ModuleNotFoundError:from backend.master_engine.analyzers import dispatch as analyze
  return analyze(sid,inp)
 return store.execute(id,v,actor,idem,runner=runner)
