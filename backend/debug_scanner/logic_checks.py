"""Checks over explicit models/observations; never execute business/user code."""
import json,re
from .common import fields,need,safe_id,integer,digest,bounded
LOGIC_OPS=frozenset(('logic.state.machine','logic.workflow.bug','logic.data.consistency','logic.calculation.error','logic.edge.case','logic.integration.mismatch','logic.race.condition','logic.concurrency'))
def lc_list(value,maximum=200,minimum=0):need(type(value) is list and minimum<=len(value)<=maximum,'invalid_logic_rows');return value
def lc_ids(value,maximum=200):
 rows=[safe_id(x) for x in lc_list(value,maximum)];need(len(rows)==len(set(rows)),'duplicate_logic_id');return rows
def lc_issue(rule,index):return {'rule_id':rule,'index':index,'confidence_kind':'violation_in_supplied_model_or_trace'}
def lc_state(v):
 fields(v,['states','initial','terminal_states','transitions','trace']);states=set(lc_ids(v['states']));need(states and type(v['initial']) is str and v['initial'] in states,'invalid_initial_state');terminals=set(lc_ids(v['terminal_states']));need(terminals<=states,'unknown_terminal_state');edges=set();issues=[]
 for i,row in enumerate(lc_list(v['transitions'],1000)):
  fields(row,['from','to','event']);a=safe_id(row['from']);b=safe_id(row['to']);event=safe_id(row['event']);need(a in states and b in states,'unknown_state');key=(a,event,b);need(key not in edges,'duplicate_transition');edges.add(key)
  if a in terminals:issues.append(lc_issue('STATE.TERMINAL_HAS_OUTGOING',i))
 current=v['initial']
 for i,row in enumerate(lc_list(v['trace'],1000)):
  fields(row,['from','to','event']);a=safe_id(row['from']);b=safe_id(row['to']);event=safe_id(row['event']);need(a in states and b in states,'unknown_state')
  if a!=current:issues.append(lc_issue('STATE.TRACE_DISCONTINUITY',i))
  if current in terminals:issues.append(lc_issue('STATE.TRANSITION_AFTER_TERMINAL',i))
  if (a,event,b) not in edges:issues.append(lc_issue('STATE.UNDECLARED_TRANSITION',i))
  current=b
 return {'violations':issues,'trace_steps':len(v['trace']),'final_state_ref':digest(current),'terminal_reached':current in terminals,'model_valid':not issues}
def lc_workflow(v):
 fields(v,['nodes','dependencies','trace']);nodes=set(lc_ids(v['nodes']));need(nodes,'empty_workflow');deps={n:set() for n in nodes}
 for edge in lc_list(v['dependencies'],1000):
  fields(edge,['node','requires']);a=safe_id(edge['node']);b=safe_id(edge['requires']);need(a in nodes and b in nodes,'unknown_workflow_node');need(b not in deps[a],'duplicate_dependency');deps[a].add(b)
 incoming={n:len(ds) for n,ds in deps.items()};ready=sorted(n for n,c in incoming.items() if not c);order=[]
 while ready:
  n=ready.pop(0);order.append(n)
  for other in sorted(nodes):
   if n in deps[other]:
    incoming[other]-=1
    if incoming[other]==0:ready.append(other);ready.sort()
 issues=[];started=set();completed=set();failed=set()
 for i,event in enumerate(lc_list(v['trace'],1000)):
  fields(event,['node','action']);n=safe_id(event['node']);action=event['action'];need(n in nodes and type(action) is str and action in ('start','complete','fail'),'invalid_workflow_event')
  if action=='start':
   if n in started or n in completed or n in failed:issues.append(lc_issue('WORKFLOW.DUPLICATE_OR_TERMINAL_START',i))
   if not deps[n]<=completed:issues.append(lc_issue('WORKFLOW.DEPENDENCY_NOT_COMPLETED',i))
   started.add(n)
  else:
   if n not in started:issues.append(lc_issue('WORKFLOW.TERMINAL_WITHOUT_START',i))
   if n in completed or n in failed:issues.append(lc_issue('WORKFLOW.DUPLICATE_TERMINAL_EVENT',i))
   if action=='complete':completed.add(n)
   else:failed.add(n)
   started.discard(n)
 return {'cycle_detected':len(order)!=len(nodes),'blocked_node_refs':[digest(n) for n in sorted(nodes-set(order))],'topological_order_refs':[digest(n) for n in order],'violations':issues,'completed_count':len(completed),'tasks_executed':False}
def lc_consistency(v):
 fields(v,['entities','references']);seen=set();issues=[]
 for i,row in enumerate(lc_list(v['entities'],1000)):
  fields(row,['type','id']);key=(safe_id(row['type']),safe_id(row['id']))
  if key in seen:issues.append(lc_issue('DATA.DUPLICATE_ENTITY_KEY',i))
  seen.add(key)
 for i,row in enumerate(lc_list(v['references'],2000)):
  fields(row,['from_type','from_id','to_type','to_id']);source=(safe_id(row['from_type']),safe_id(row['from_id']));target=(safe_id(row['to_type']),safe_id(row['to_id']))
  if source not in seen:issues.append(lc_issue('DATA.MISSING_REFERENCE_SOURCE',i))
  if target not in seen:issues.append(lc_issue('DATA.DANGLING_REFERENCE',i))
 return {'violations':issues,'unique_entity_count':len(seen),'snapshot_completeness_verified':False,'database_read':False}
def lc_money(v):need(type(v) is str and re.fullmatch(r'0|[1-9][0-9]{0,18}',v),'invalid_minor_amount');need(int(v)<=9223372036854775807,'amount_exceeds_storage_limit');return int(v)
def lc_calculation(v):
 fields(v,['lines','tax_bps','reported_subtotal_minor','reported_tax_minor','reported_total_minor']);rate=integer(v['tax_bps'],0,10000);subtotal=0;seen=set()
 for row in lc_list(v['lines'],100,1):
  fields(row,['id','quantity','unit_minor']);key=safe_id(row['id']);need(key not in seen,'duplicate_line');seen.add(key);subtotal+=integer(row['quantity'],1,1000000)*lc_money(row['unit_minor'])
 tax=(subtotal*rate+5000)//10000;total=subtotal+tax;need(total<=9223372036854775807,'amount_exceeds_storage_limit');expected={'subtotal_minor':subtotal,'tax_minor':tax,'total_minor':total};issues=[]
 for name,n in expected.items():
  observed=lc_money(v['reported_'+name])
  if observed!=n:issues.append({'rule_id':'CALC.MISMATCH','field':name,'expected_minor':str(n),'reported_minor':str(observed)})
 return {'violations':issues,'expected':{k:str(n) for k,n in expected.items()},'rounding':'half_up_per_total','tax_rate_legally_verified':False,'invoice_posted':False}
def lc_cases(v):
 fields(v,['cases']);seen=set();rows=[]
 for row in lc_list(v['cases'],200,1):
  fields(row,['id','expected','observed']);key=safe_id(row['id']);need(key not in seen,'duplicate_case');seen.add(key)
  expected=json.dumps(row['expected'],sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':'));observed=json.dumps(row['observed'],sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(',',':'))
  rows.append({'case_ref':digest(key),'matches':expected==observed,'expected_sha256':digest(expected),'observed_sha256':digest(observed)})
 return {'cases':rows,'mismatch_count':sum(not r['matches'] for r in rows),'cases_executed':False,'equality':'canonical_typed_json'}
def lc_contract(v):
 fields(v,['fields','records','allow_extra']);need(type(v['allow_extra']) is bool,'invalid_extra_field_policy');types={'string':(str,),'integer':(int,),'number':(int,float),'boolean':(bool,),'object':(dict,),'array':(list,),'null':(type(None),)};schema={}
 for f in lc_list(v['fields'],100,1):
  fields(f,['name','kind','required']);name=safe_id(f['name']);need(name not in schema and type(f['kind']) is str and f['kind'] in types and type(f['required']) is bool,'invalid_contract_field');schema[name]=f
 issues=[];seen=set()
 for i,row in enumerate(lc_list(v['records'],200)):
  fields(row,['id','payload']);key=safe_id(row['id']);need(key not in seen,'duplicate_record');seen.add(key);payload=row['payload'];need(type(payload) is dict,'invalid_record_payload')
  for name,f in schema.items():
   if name not in payload:
    if f['required']:issues.append({'rule_id':'CONTRACT.MISSING_FIELD','record_index':i,'field_ref':digest(name)})
   elif type(payload[name]) not in types[f['kind']]:issues.append({'rule_id':'CONTRACT.FIELD_TYPE','record_index':i,'field_ref':digest(name)})
  if not v['allow_extra']:
   for name in set(payload)-set(schema):issues.append({'rule_id':'CONTRACT.UNDECLARED_FIELD','record_index':i,'field_ref':digest(name)})
 return {'violations':issues,'nested_schema_validated':False,'api_called':False,'record_count':len(v['records'])}
def lc_transactions(op,v):
 fields(v,['initial_versions','transactions']);initial={};records=[];seen=set()
 for row in lc_list(v['initial_versions'],200,1):
  fields(row,['resource','version']);key=safe_id(row['resource']);need(key not in initial,'duplicate_resource');initial[key]=integer(row['version'],0,2147483647)
 for row in lc_list(v['transactions'],200):
  fields(row,['id','resource','started','finished','read_version','write_version','committed']);key=safe_id(row['id']);resource=safe_id(row['resource']);need(key not in seen and resource in initial,'invalid_transaction_identity');seen.add(key);start=integer(row['started'],0,2147483647);end=integer(row['finished'],start,2147483647);read=integer(row['read_version'],0,2147483647);write=integer(row['write_version'],0,2147483647);need(type(row['committed']) is bool,'invalid_commit_flag');records.append({'id':key,'resource':resource,'start':start,'end':end,'read':read,'write':write,'committed':row['committed']})
 committed=sorted((r for r in records if r['committed']),key=lambda r:(r['end'],r['id']));pairs=[]
 for i,a in enumerate(committed):
  for b in committed[i+1:]:
   if a['resource']==b['resource'] and a['read']==b['read'] and max(a['start'],b['start'])<min(a['end'],b['end']):pairs.append({'first_ref':digest(a['id']),'second_ref':digest(b['id']),'resource_ref':digest(a['resource']),'rule_id':'CAS.CONCURRENT_DOUBLE_COMMIT'})
   need(len(pairs)<=1000,'transaction_violation_limit')
 versions=dict(initial);issues=[];last={};seen_times=set();ties=set()
 for r in committed:
  pair=(r['resource'],r['end'])
  if pair in seen_times:ties.add(r['resource'])
  seen_times.add(pair)
 for r in committed:
  resource=r['resource']
  if last.get(resource)==r['end']:ties.add(resource)
  last[resource]=r['end']
  if resource not in ties and r['read']!=versions[resource]:issues.append({'rule_id':'CAS.STALE_COMMITTED_READ','transaction_ref':digest(r['id'])})
  if r['write']!=r['read']+1:issues.append({'rule_id':'CAS.INVALID_VERSION_INCREMENT','transaction_ref':digest(r['id'])})
  versions[resource]=r['write']
 if op=='logic.race.condition':return {'conflicting_commit_pairs':pairs,'threads_started':False,'cas_policy':'explicit_increment_by_one','runtime_race_reproduced':False}
 return {'violations':issues,'commit_order_ambiguous_resource_refs':[digest(n) for n in sorted(ties)],'ordered_commit_history_verified':False,'threads_started':False,'committed_count':len(committed),'cas_policy':'explicit_increment_by_one'}
def logic_scan(op,v):
 bounded(v)
 if op=='logic.state.machine':data=lc_state(v)
 elif op=='logic.workflow.bug':data=lc_workflow(v)
 elif op=='logic.data.consistency':data=lc_consistency(v)
 elif op=='logic.calculation.error':data=lc_calculation(v)
 elif op=='logic.edge.case':data=lc_cases(v)
 elif op=='logic.integration.mismatch':data=lc_contract(v)
 elif op in ('logic.race.condition','logic.concurrency'):data=lc_transactions(op,v)
 else:raise ValueError('logic_operation_not_implemented')
 need(len(json.dumps(data,ensure_ascii=True).encode())<=120000,'logic_result_limit');return {'status':'analyzed_declared_model_or_trace','data':data,'provenance':'caller_supplied_not_independently_observed','business_code_executed':False,'production_state_changed':False,'root_cause_confirmed':False}

def logic_contract(op):
 contracts={
 'logic.state.machine':'Exact keys states:[id],initial:id,terminal_states:[id],transitions:[{from,to,event}],trace:[{from,to,event}]. Checks declared/observed transitions only; no code execution.',
 'logic.workflow.bug':'Exact keys nodes:[id],dependencies:[{node,requires}],trace:[{node,action:start|complete|fail}]. Checks cycles and prerequisites in supplied trace only.',
 'logic.data.consistency':'Exact keys entities:[{type,id}],references:[{from_type,from_id,to_type,to_id}]. Caller asserts snapshot; no database read.',
 'logic.calculation.error':'Exact keys lines:[{id,quantity:positive integer,unit_minor:integer string}],tax_bps:0..10000,reported_subtotal_minor:string,reported_tax_minor:string,reported_total_minor:string. Integer minor units, half-up total tax; no legal-rate verification.',
 'logic.edge.case':'Exact keys cases:[{id,expected:any JSON,observed:any JSON}]. Typed canonical JSON comparison only; cases are NOT executed.',
 'logic.integration.mismatch':'Exact keys fields:[{name,kind:string|integer|number|boolean|object|array|null,required:boolean}],records:[{id,payload:object}],allow_extra:boolean. Top-level contract only; no API requests.',
 'logic.race.condition':'Exact keys initial_versions:[{resource,version}],transactions:[{id,resource,started,finished,read_version,write_version,committed:boolean}]. Integer times; supplied interval trace/CAS increment-by-one policy only.',
 'logic.concurrency':'Exact keys initial_versions:[{resource,version}],transactions:[{id,resource,started,finished,read_version,write_version,committed:boolean}]. Supplied CAS trace version chain; equal finish times remain ambiguous; no threads executed.'}
 return contracts[op]

def logic_schema(op):
 def obj(p):return {'type':'object','properties':p,'required':list(p),'additionalProperties':False}
 def arr(item,n=200):return {'type':'array','items':item,'maxItems':n}
 sid={'type':'string','maxLength':80};boolean={'type':'boolean'};version={'type':'integer','minimum':0,'maximum':1000000000};amount={'type':'string','maxLength':19}
 if op=='logic.state.machine':p={'states':arr(sid),'initial':sid,'terminal_states':arr(sid),'transitions':arr(obj({'from':sid,'to':sid,'event':sid}),1000),'trace':arr(obj({'from':sid,'to':sid,'event':sid}),1000)}
 elif op=='logic.workflow.bug':p={'nodes':arr(sid),'dependencies':arr(obj({'node':sid,'requires':sid}),1000),'trace':arr(obj({'node':sid,'action':{'type':'string','enum':['start','complete','fail']}}),1000)}
 elif op=='logic.data.consistency':p={'entities':arr(obj({'type':sid,'id':sid}),1000),'references':arr(obj({k:sid for k in ('from_type','from_id','to_type','to_id')}),2000)}
 elif op=='logic.calculation.error':p={'lines':arr(obj({'id':sid,'quantity':{'type':'integer','minimum':1,'maximum':1000000},'unit_minor':amount}),100),'tax_bps':{'type':'integer','minimum':0,'maximum':10000},**{k:amount for k in ('reported_subtotal_minor','reported_tax_minor','reported_total_minor')}}
 elif op=='logic.edge.case':p={'cases':arr({'type':'object','properties':{'id':sid},'required':['id'],'additionalProperties':True,'description':'Exact keys id,expected,observed. Handler requires both JSON values and rejects unknown keys; JSON types preserved.'})}
 elif op=='logic.integration.mismatch':p={'fields':arr(obj({'name':sid,'kind':{'type':'string','enum':['string','integer','number','boolean','object','array','null']},'required':boolean}),100),'records':arr(obj({'id':sid,'payload':{'type':'object','additionalProperties':True}})),'allow_extra':boolean}
 else:p={'initial_versions':arr(obj({'resource':sid,'version':version})),'transactions':arr(obj({'id':sid,'resource':sid,'started':version,'finished':version,'read_version':version,'write_version':version,'committed':boolean}))}
 result=obj(p);result['description']=logic_contract(op);return result
