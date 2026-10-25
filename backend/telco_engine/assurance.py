"""Policy/evidence reporting, exact outage unions and bounded pure task concurrency."""
import concurrent.futures,datetime as dt,math
from decimal import Decimal,ROUND_HALF_UP
from .common import require,parse_time,money,DomainError
S={'type':'string','minLength':1,'maxLength':100};I={'type':'integer','minimum':0,'maximum':10**15};T={'type':'string','maxLength':40}
def obj(p,r=None):return {'type':'object','properties':p,'required':list(p) if r is None else r,'additionalProperties':False}
HANDLERS={};SCHEMAS={};MUTATIONS=set();LIMITATIONS={}
def reg(id,schema,fn,write=False,note='Submitted/local evidence only, no live company source or external action.'):
 HANDLERS[id]=fn;SCHEMAS[id]=schema;LIMITATIONS[id]=note
 if write:MUTATIONS.add(id)

def uptime(c,a):
 start=parse_time(a['start_at']);end=parse_time(a['end_at']);require(start<end,'invalid_window')
 segments=[]
 for x in a['outages']:
  lo=max(start,parse_time(x['start_at']));hi=min(end,parse_time(x['end_at']));require(parse_time(x['start_at'])<parse_time(x['end_at']),'invalid_outage_interval')
  if lo<hi:segments.append((lo,hi))
 merged=[]
 for lo,hi in sorted(segments):
  if merged and lo<=merged[-1][1]:merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
  else:merged.append((lo,hi))
 window=int((end-start).total_seconds()*1000000);down=sum(int((hi-lo).total_seconds()*1000000) for lo,hi in merged)
 pct=(Decimal(window-down)*100/Decimal(window)).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
 return {'window_seconds':window/1000000,'outage_seconds':down/1000000,'uptime_percent':str(pct),'overlap_union_applied':True,'source':a['source'],'coverage_of_measurement':a['measurement_complete'],'sla_compliance_verified':False}
WINDOW=obj({'start_at':T,'end_at':T,'outages':{'type':'array','maxItems':1000,'items':obj({'start_at':T,'end_at':T})},'source':S,'measurement_complete':{'type':'boolean'}})
reg('report.network.uptime',WINDOW,uptime)
def sla_report(c,a):
 require(a['measurement_complete'],'incomplete_measurement',409);r=uptime(c,a);r['target_percent']=str(Decimal(a['target_bps'])/100);r['meets_supplied_target']=Decimal(r['uptime_percent'])>=Decimal(a['target_bps'])/100;return r
reg('report.sla.compliance',obj({**WINDOW['properties'],'target_bps':{'type':'integer','minimum':0,'maximum':10000}}),sla_report,note='Compares supplied complete measurement window against supplied target, not a legal/contractual SLA certification.')
def churn(c,a):
 require(a['terminated_count']<=a['starting_active'],'invalid_counts')
 return {'churn_percent':None if a['starting_active']==0 else str((Decimal(a['terminated_count'])*100/Decimal(a['starting_active'])).quantize(Decimal('.0001'))),'source':a['source'],'denominator_basis':'supplied_starting_active_subscriptions'}
reg('report.churn.rate',obj({'starting_active':I,'terminated_count':I,'source':S}),churn)
def summary(c,a):
 rows=c.list(a['_kind']) if '_kind' in a else []
 return {'count':len(rows),'source':'tenant_records'}
reg('report.outage.summary',obj({}),lambda c,a:{'tickets':[r for r in c.list('ticket') if r.get('severity')=='P1'],'source':'tenant_tickets','confirmed_network_outage':False})
reg('report.fraud.summary',obj({}),lambda c,a:{'cases':c.list('fraud_case'),'source':'tenant_records','confirmed_fraud_inferred':False})
def productivity(c,a):
 by={}
 for r in a['work_orders']:
  k=r['technician_id'];x=by.setdefault(k,{'completed':0,'duration_seconds':0});x['completed']+=1;x['duration_seconds']+=r['duration_seconds']
 return {'technicians':[{'id':k,**v,'average_duration_seconds':v['duration_seconds']/v['completed']} for k,v in sorted(by.items())],'source':a['source']}
reg('report.field.productivity',obj({'work_orders':{'type':'array','maxItems':1000,'items':obj({'technician_id':S,'duration_seconds':{'type':'number','minimum':0,'maximum':2678400}})},'source':S}),productivity)
def coverage(c,a):
 cities={}
 for row in a['assets']:cities[row['city']]=cities.get(row['city'],0)+1
 return {'asset_counts_by_city':cities,'source':a['source'],'installation_coverage_confirmed':False,'port_capacity_confirmed':False}
reg('report.coverage.area',obj({'assets':{'type':'array','maxItems':1000,'items':obj({'city':S,'asset_id':S})},'source':S}),coverage,note='Inventory counts only; city/proximity is not proof of coverage or free ports.')

def license_record(c,a):
 require(parse_time(a['issued_at'])<parse_time(a['expires_at']),'invalid_license_window')
 return c.create('license_record',{**a,'status':'recorded_unverified','government_verified':False})
LIC=obj({'reference':S,'issuer':S,'service_type':S,'issued_at':T,'expires_at':T,'evidence_reference':S})
reg('reg.license.manage',LIC,license_record,True,note='Local license evidence metadata only; no government/Komdigi validation or permission to operate.')
reg('reg.frequency.license',LIC,license_record,True,note='Local license evidence metadata only; no spectrum allocation or BHP payment confirmation.')
def privacy(c,a):return c.create('privacy_case',{**a,'status':'pending_authorized_review','consent_verified':False,'processing_authorized':False})
reg('reg.customer.privacy',obj({'subject_id':S,'purpose':{'type':'string','maxLength':1000},'evidence_reference':S}),privacy,True,note='Privacy review case only. An asserted evidence reference does not establish valid consent or legal compliance.')
def locality(c,a):
 allowed=set(a['policy_allowed_countries']);outside=[r['dataset_id'] for r in a['datasets'] if r['country_code'] not in allowed]
 return {'policy_id':a['policy_id'],'outside_supplied_policy':outside,'source':a['source'],'legal_compliance_certified':False,'actual_server_locations_verified':False}
reg('reg.data.localize',obj({'policy_id':S,'policy_allowed_countries':{'type':'array','minItems':1,'maxItems':100,'items':{'type':'string','pattern':'[A-Z]{2}'}},'datasets':{'type':'array','maxItems':1000,'items':obj({'dataset_id':S,'country_code':{'type':'string','pattern':'[A-Z]{2}'}})},'source':S}),locality)
def contribution(c,a):
 amount=(a['base_idr']*a['rate_bps']*2+10000)//20000
 return {'calculation_only':True,'base_idr':a['base_idr'],'rate_bps':a['rate_bps'],'amount_idr':amount,'policy_id':a['policy_id'],'period':a['period'],'obligation_or_rate_verified':False,'payment_or_filing_performed':False}
reg('reg.universal.service',obj({'base_idr':I,'rate_bps':{'type':'integer','minimum':0,'maximum':10000},'policy_id':S,'period':{'type':'string','pattern':r'\d{4}-(0[1-9]|1[0-2])'}}),contribution,note='Arithmetic against an explicitly supplied policy/rate only; no statutory default, USO applicability, payment or filing determination.')
reg('reg.numbering.report',obj({}),lambda c,a:{'pools':c.list('number_pool'),'source':'tenant_records','submitted':False,'regulator_format_certified':False})
reg('reg.interconnect.report',obj({'period':{'type':'string','pattern':r'\d{4}-(0[1-9]|1[0-2])'},'records':{'type':'array','maxItems':1000,'items':obj({'partner_reference':S,'amount_idr':I})},'source':S}),lambda c,a:{'period':a['period'],'partner_records':len(a['records']),'total_idr':sum(x['amount_idr'] for x in a['records']),'source':a['source'],'submitted':False,'regulator_format_certified':False})

def parallel(c,a):
 from .analysis import HANDLERS as PURE,SCHEMAS as SPECS
 from .common import validate
 allowed={'capacity.cost.benefit','capacity.bottleneck.identify','fraud.usage.anomaly','netsec.bgp.hijack.detect'}
 require(len({x['id'] for x in a['tasks']})==len(a['tasks']),'duplicate_task_id')
 def run(x):
  require(x['skill_id'] in allowed,'parallel_skill_not_allowed');validate(x['input'],SPECS[x['skill_id']]);return {'id':x['id'],'result':PURE[x['skill_id']](c,x['input'])}
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:result=list(pool.map(run,a['tasks']))
 return {'execution_mode':'bounded_threads_pure_functions','workers_max':4,'results':result,'external_actions_performed':False}
TASK=obj({'id':S,'skill_id':{'type':'string','enum':['capacity.cost.benefit','capacity.bottleneck.identify','fraud.usage.anomaly','netsec.bgp.hijack.detect']},'input':{'type':'object'}})
reg('workflow.parallel',obj({'tasks':{'type':'array','minItems':1,'maxItems':8,'items':TASK}}),parallel,note='Concurrent allowlisted pure analyses only; no DB mutations, arbitrary code, network or nested workflow actions.')

def expansion(c,a):
 candidates=[r for r in a['options'] if r['cost_idr']<=a['budget_idr'] and r['capacity_mbps']>=a['required_mbps']]
 candidates.sort(key=lambda r:(r['cost_idr'],r['id']))
 return {'eligible_options':candidates,'lowest_cost_candidate':None if not candidates else candidates[0]['id'],'source':a['source'],'order_placed':False,'capacity_availability_verified':False}
reg('capacity.expansion.plan',obj({'budget_idr':I,'required_mbps':{'type':'number','minimum':0.001,'maximum':10**9},'options':{'type':'array','maxItems':100,'items':obj({'id':S,'cost_idr':I,'capacity_mbps':{'type':'number','minimum':0.001,'maximum':10**9}})},'source':S}),expansion)
def quarterly(c,a):
 return {'period':a['period'],'samples':len(a['utilization_percent']),'average_utilization_percent':sum(a['utilization_percent'])/len(a['utilization_percent']),'maximum_utilization_percent':max(a['utilization_percent']),'source':a['source'],'forecast_included':False}
reg('capacity.report.quarterly',obj({'period':{'type':'string','pattern':r'\d{4}-Q[1-4]'},'utilization_percent':{'type':'array','minItems':1,'maxItems':1000,'items':{'type':'number','minimum':0,'maximum':100}},'source':S}),quarterly)
