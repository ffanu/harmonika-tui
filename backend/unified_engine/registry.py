"""Exact blueprint catalog, distinct callable skills and enforced internal mechanisms."""
import json
from pathlib import Path
SOURCE=json.loads(Path(__file__).with_name('catalog_source.json').read_text())
PURE=set('core.perceive core.understand core.reason core.retrieve core.decide orch.parse.input orch.detect.channel orch.detect.urgency orch.detect.privacy orch.intent.classify orch.domain.detect orch.complexity.assess orch.multi_intent.detect orch.ambiguity.detect orch.clarify.request orch.scope.define orch.route.chat orch.route.legal orch.route.web_builder orch.route.mobile_builder orch.route.browser_auto orch.route.text_typo orch.route.multi orch.route.fallback orch.route.human orch.route.crisis orch.aggregate.single orch.aggregate.multi orch.aggregate.conflict orch.aggregate.format data.context.pass data.serialize data.deserialize data.validate data.transform'.split())
JOBS=set('core.act orch.receive.request orch.queue.request orch.exec.start bus.request.response bus.async.task'.split())
STATE=set('core.learn orch.detect.session orch.context.load orch.exec.monitor orch.exec.timeout orch.exec.retry orch.exec.cancel bus.subscribe.event bus.dead_letter data.shared.memory shared.memory shared.user_profile'.split())
INTERNAL=set('orch.detect.user bus.priority.queue bus.retry.policy bus.circuit.breaker data.encrypt data.audit.log shared.auth shared.audit_log shared.monitoring gateway.rest gateway.rate_limit gateway.auth'.split())
EXTENDED={'bus.publish.event','shared.knowledge','shared.secrets','gateway.graphql'}
NATIVE={'gateway.websocket'}
NOT_READY={}
def schema(id):
 def obj(p,r=None):return {'type':'object','properties':p,'required':list(p) if r is None else r,'additionalProperties':False}
 string={'type':'string','minLength':1,'maxLength':16000};uuid={'type':'string','format':'uuid'}
 ms={'type':'array','minItems':1,'maxItems':40,'items':obj({'role':{'type':'string','enum':['system','user','assistant']},'content':string})}
 media={'type':'object','description':'type=image|audio|video plus image_base64/audio_base64/frames_base64; no caller URL. <=128KiB total request; max3 frames.'}
 if id=='bus.publish.event':return obj({'topic':{'type':'string','pattern':'^[A-Za-z0-9_.-]{1,80}$'},'payload':{'type':'object'}})
 if id=='shared.knowledge':return obj({'query':{'type':'string','minLength':1,'maxLength':400},'limit':{'type':'integer','minimum':1,'maximum':20}},['query'])
 if id=='shared.secrets':return obj({'operation':{'type':'string','enum':['set','metadata','delete']},'name':{'type':'string','maxLength':64,'pattern':'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'},'value':{'type':'string','maxLength':4096}},['operation','name'])
 if id=='gateway.graphql':return obj({'query':{'type':'string','maxLength':8192},'variables':{'type':['object','null']},'operationName':{'type':'string'}},['query'])
 if id in JOBS:return obj({'messages':ms,'session_id':uuid,'media':media},['messages'])
 if id=='core.perceive':return {'oneOf':[obj({'messages':ms}),obj({'media':media})]}
 if id.startswith('orch.aggregate.'):return obj({'results':{'type':'array','maxItems':8,'items':{'type':'object','required':['engine'],'properties':{'engine':{'type':'string','maxLength':60}}}}})
 if id in ('data.serialize','data.context.pass','data.validate'):return obj({'value':{}})
 if id=='data.deserialize':return obj({'value':{'type':'string','maxLength':65536}})
 if id=='data.transform':
  try:from core_engine.data_tools import descriptors as data
  except ModuleNotFoundError:from backend.core_engine.data_tools import descriptors as data
  return data()[id]['input_schema']
 if id=='core.retrieve':return obj({'query':{'type':'string','minLength':1,'maxLength':400},'sources':{'type':'array','maxItems':20,'items':obj({'id':{'type':'string','maxLength':100},'text':{'type':'string','maxLength':4000},'verified':{'type':'boolean'}},['id','text'])},'limit':{'type':'integer','minimum':1,'maximum':20}},['query'])
 if id in ('core.learn','shared.user_profile'):return obj({'consent':{'type':'boolean','const':True},'key':{'type':'string','maxLength':100},'value':{}})
 if id in ('shared.memory','data.shared.memory'):return obj({'operation':{'type':'string','enum':['memory.get','memory.set','memory.search','memory.clear']},'input':{'type':'object','description':'Exact matching core memory operation schema'}})
 if id in ('orch.detect.session','orch.context.load'):return obj({'session_id':uuid})
 if id=='bus.subscribe.event':return {'oneOf':[obj({'job_id':uuid}),obj({'topic':{'type':'string','maxLength':80},'after_sequence':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':100}},['topic'])]}
 if id in STATE:return obj({'job_id':uuid})
 if id in PURE:return obj({'messages':ms})
 return obj({})
def descriptors():
 out=[]
 for s in SOURCE['skills']:
  id=s['id'];status='available' if id in PURE|JOBS|STATE|EXTENDED else 'native_endpoint' if id in NATIVE else 'internal_only' if id in INTERNAL else 'unavailable'
  mode='native_websocket' if id in NATIVE else 'read_only_graphql' if id=='gateway.graphql' else 'tenant_extension' if id in EXTENDED else 'bounded_analysis' if id in PURE else 'async_job' if id in JOBS else 'tenant_state' if id in STATE else 'enforced_boundary' if id in INTERNAL else 'not_configured'
  out.append({**s,'status':status,'mode':mode,'engine':'unified','execution_target':'unified_backend','directly_invocable':status=='available','reason':'use_authenticated_websocket_endpoint' if status=='native_endpoint' else NOT_READY.get(id) if status=='unavailable' else 'internal_boundary_not_public_tool' if status=='internal_only' else None,'input_schema':schema(id),'output_schema':{'type':'object'},'runtime_readiness_endpoint':'/v1/unified/capabilities'})
  if id=='shared.secrets':out[-1]['required_scopes_by_operation']={'set':'secrets:write','delete':'secrets:write','metadata':'secrets:read'}
  if id=='gateway.websocket':out[-1]['endpoint']='/v1/unified/ws'
 return out
def capabilities():
 rows=descriptors()
 return {'schema_version':1,'engine':'harmonika-unified','catalog_size':len(rows),'source_sha256':SOURCE['source_sha256'],'readiness':{s:sum(x['status']==s for x in rows) for s in ('available','internal_only','native_endpoint','unavailable')},'features':{'durable_jobs':True,'session_history':True,'encrypted_state':True,'tenant_isolation':True,'idempotency':True,'stop':True,'sse_replay':True,'chat_token_streaming':False,'lexical_routing':True,'calibrated_confidence':False,'automatic_training':False,'universal_media_analysis':False,'general_message_bus':True,'websocket':True,'websocket_ticket_auth':True,'graphql':True,'tenant_connector_secrets':True,'priority_queue':True,'specialist_planning':True,'automatic_deploy':False,'verified_corpus_rag':True,'knowledge_method':'bm25_operator_verified_documents','vector_database':False},'limits':{'request_bytes':131072,'messages':40,'text_characters':48000,'job_ttl_seconds':86400,'session_ttl_seconds':604800,'global_workers':2,'queue_size':16,'per_actor_active':1,'jobs_per_actor_per_day':50,'job_timeout_seconds':150,'event_retention':'job_ttl','response_bytes':196608,'event_payload_bytes':16384,'event_ttl_seconds':86400,'secret_value_bytes':4096,'secret_idempotency_ttl_seconds':86400,'websocket_lifetime_seconds':120,'ticket_ttl_seconds':60,'graphql_query_bytes':8192,'graphql_field_cost':40,'graphql_depth':8},'specialists':{'chat':'existing_chat_gateway','text_typo':'bounded_indonesian_rules','web_builder':'existing_pure_requirements_planner','mobile_builder':'existing_pure_spec_planner','legal':'existing_pure_triage_not_legal_opinion','browser_auto':'explicit_authorized_context_required'},'operation_schemas':{'session.create':{},'session.read':{'session_id':'uuid'},'job.read_stop_replay':{'job_id':'uuid'},'shared.memory':{'operation':'memory.get|set|search|clear','input':'existing /v1/core schema'},'core.learn':{'consent':True,'key':'bounded memory key','value':'explicit feedback or preference; no training'},'core.retrieve':{'query':'text','sources':[{'id':'string','text':'text','verified':True}]},'serialization':{'value':'bounded JSON or text'},'data.transform':'existing /v1/core data.transform schema'},'registration_is_not_runtime_readiness':True}
