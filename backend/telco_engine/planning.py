"""Local workflow/resource plans. These records never grant carrier/regulatory authority."""
import datetime as dt,math,re
from .common import require,identifier,parse_time,money
S={'type':'string','minLength':1,'maxLength':100};I={'type':'integer','minimum':0,'maximum':10**15}
def obj(p,r=None):return {'type':'object','properties':p,'required':list(p) if r is None else r,'additionalProperties':False}
HANDLERS={};SCHEMAS={};MUTATIONS=set();LIMITATIONS={}
def reg(id,schema,fn,write=False,note='Tenant-local planning only; no external operation or company data access.'):
 HANDLERS[id]=fn;SCHEMAS[id]=schema;LIMITATIONS[id]=note
 if write:MUTATIONS.add(id)
def number_plan(c,a):
 require(re.fullmatch(r'[1-9][0-9]{1,10}',a['prefix']) is not None,'invalid_prefix');require(len(a['prefix'])+a['suffix_digits']<=15,'number_too_long')
 require(a['start']+a['count']<=10**a['suffix_digits'],'range_overflow')
 for r in c.list('number_pool'):
  if r['prefix']==a['prefix'] and r['suffix_digits']==a['suffix_digits']:require(a['start']+a['count']<=r['start'] or r['start']+r['count']<=a['start'],'overlapping_pool',409)
 return c.create('number_pool',{**a,'status':'planned','carrier_allocation_verified':False})
reg('numbering.plan.manage',obj({'name':S,'prefix':S,'suffix_digits':{'type':'integer','minimum':1,'maximum':10},'start':{'type':'integer','minimum':0,'maximum':10**10},'count':{'type':'integer','minimum':1,'maximum':10000}}),number_plan,True)
def allocate(c,a):
 pool=c.get('number_pool',a['pool_id']);c.get('subscriber',a['subscriber_id']);used={r['number'] for r in c.list('number_assignment') if r['status'] in ('assigned','reserved') and (r.get('expires_at') is None or parse_time(r['expires_at'])>parse_time(c.now))}
 requested=a.get('number');choices=[pool['prefix']+str(n).zfill(pool['suffix_digits']) for n in range(pool['start'],pool['start']+pool['count'])]
 if requested is not None:require(requested in choices,'outside_pool');choices=[requested]
 number=next((n for n in choices if n not in used),None);require(number is not None,'pool_exhausted',409)
 return c.create('number_assignment',{'pool_id':pool['id'],'subscriber_id':a['subscriber_id'],'number':number,'status':'assigned','expires_at':None,'carrier_allocation_verified':False})
reg('numbering.allocate',obj({'pool_id':S,'subscriber_id':S,'number':S},['pool_id','subscriber_id']),allocate,True)
def release(c,a):
 r=c.get('number_assignment',a['assignment_id']);require(r['status'] in ('assigned','reserved'),'invalid_transition',409);return c.update('number_assignment',r['id'],{'status':'released'},a['expected_version'])
reg('numbering.deallocate',obj({'assignment_id':S,'expected_version':{'type':'integer','minimum':1}}),release,True)
def reserve(c,a):
 r=allocate(c,a);return c.update('number_assignment',r['id'],{'status':'reserved','expires_at':(parse_time(c.now)+dt.timedelta(minutes=30)).isoformat().replace('+00:00','Z')},r['version'])
reg('numbering.reservation',SCHEMAS['numbering.allocate'],reserve,True,'Local reservation expires after30 minutes; not an operator/MNP reservation.')
reg('numbering.audit.regulator',obj({}),lambda c,a:{'pools':c.list('number_pool'),'assignments':c.list('number_assignment'),'submitted':False,'carrier_allocation_verified':False},False,'Inventory export only; not a regulator filing or verified numbering authorization.')

def graph(steps):
 ids={x['id'] for x in steps};require(len(ids)==len(steps),'duplicate_step_id');done=set();waves=[]
 for step in steps:require(set(step['depends_on'])<=ids,'unknown_dependency');require(step['id'] not in step['depends_on'],'workflow_cycle')
 while len(done)<len(ids):
  wave=[s['id'] for s in steps if s['id'] not in done and set(s['depends_on'])<=done];require(bool(wave),'workflow_cycle');waves.append(sorted(wave));done.update(wave)
 return waves
STEP=obj({'id':S,'skill_id':S,'depends_on':{'type':'array','maxItems':30,'items':S}})
def template(c,a):
 from .registry import DOCUMENT_IDS
 require(all(s['skill_id'] in DOCUMENT_IDS for s in a['steps']),'unknown_skill');waves=graph(a['steps'])
 return c.create('workflow_template',{'name':a['name'],'steps':a['steps'],'waves':waves,'status':'draft','execution_mode':'manual_step_dispatch','automatic_actions_enabled':False})
reg('workflow.templating',obj({'name':S,'steps':{'type':'array','minItems':1,'maxItems':30,'items':STEP}}),template,True,'Creates a validated DAG template. Steps require separate approved invokes; no autonomous action is implied.')
def trigger(c,a):
 t=c.get('workflow_template',a['template_id']);require(t['version']==a['expected_version'],'version_conflict',409)
 return c.create('workflow_instance',{'template_id':t['id'],'template_version':t['version'],'status':'awaiting_step_dispatch','step_ids':[s['id'] for s in t['steps']],'event_reference':a['event_reference'],'automatic_actions_enabled':False})
reg('workflow.trigger',obj({'template_id':S,'expected_version':{'type':'integer','minimum':1},'event_reference':S}),trigger,True,'Records a trigger and queued manual-dispatch instance only; no customer/network/message action runs.')
def condition(c,a):
 op=a['operator'];x=a['left'];y=a['right']
 if op in ('gt','gte','lt','lte'):require(type(x) in (int,float) and type(y) in (int,float) and math.isfinite(x) and math.isfinite(y),'numeric_values_required')
 result={'eq':lambda:x==y,'ne':lambda:x!=y,'gt':lambda:x>y,'gte':lambda:x>=y,'lt':lambda:x<y,'lte':lambda:x<=y}[op]()
 return {'matched':result,'expression_evaluated':False,'operator':op}
reg('workflow.condition',obj({'operator':{'type':'string','enum':['eq','ne','gt','gte','lt','lte']},'left':{},'right':{}}),condition,False,'Fixed comparison operators only; no eval, code or external data retrieval.')
def approval(c,a):
 t=c.get('workflow_template',a['template_id']);return c.update('workflow_template',t['id'],{'status':a['decision'],'reviewed_by':c.actor},a['expected_version'])
reg('workflow.approval',obj({'template_id':S,'expected_version':{'type':'integer','minimum':1},'decision':{'type':'string','enum':['approved','rejected']}}),approval,True,'Business template review only; does not grant API permissions or approve live carrier actions.')
reg('workflow.dashboard',obj({}),lambda c,a:{'templates':c.list('workflow_template'),'instances':c.list('workflow_instance'),'automatic_actions_enabled':False})
def notification(c,a):
 c.get('subscriber',a['subscriber_id']);return c.create('notification_draft',{'subscriber_id':a['subscriber_id'],'channel':a['channel'],'text':a['text'],'status':'draft','sent':False})
reg('workflow.notification',obj({'subscriber_id':S,'channel':{'type':'string','enum':['email','whatsapp','sms']},'text':{'type':'string','minLength':1,'maxLength':2000}}),notification,True,'Draft only; no message sent and no communication consent is inferred.')
def timed_out(c,a):
 now=parse_time(c.now);items=[]
 for r in c.list('workflow_instance'):
  if r['status']=='awaiting_step_dispatch' and (now-parse_time(r['created_at'])).total_seconds()>a['timeout_seconds']:items.append(r['id'])
 return {'overdue_instance_ids':items,'escalation_draft_required':bool(items),'notifications_sent':False,'source':'tenant_records'}
reg('workflow.timeout.escalation',obj({'timeout_seconds':{'type':'integer','minimum':1,'maximum':2678400}}),timed_out,False,'Overdue detection only; no automatic escalation notification is sent.')
def optimize(c,a):
 waves=graph(a['steps']);dur={x['id']:x['duration_seconds'] for x in a['steps']};finish={}
 for wave in waves:
  for id in wave:
   s=next(x for x in a['steps'] if x['id']==id);finish[id]=dur[id]+max([finish[d] for d in s['depends_on']] or [0])
 return {'waves':waves,'critical_path_seconds':max(finish.values()),'serial_seconds':sum(dur.values()),'parallel_execution_performed':False,'source':'submitted_measured_durations'}
OPTSTEP=obj({**STEP['properties'],'duration_seconds':{'type':'number','minimum':0,'maximum':2678400}})
reg('workflow.optimization',obj({'steps':{'type':'array','minItems':1,'maxItems':30,'items':OPTSTEP}}),optimize,False,'Critical-path calculation of supplied measured durations; not autonomous workflow execution.')

def recommend(c,a):
 target=a['target_utilization_percent'];needed=math.ceil(a['peak_demand_mbps']*100/target)
 return {'required_capacity_mbps':needed,'additional_capacity_mbps':max(0,needed-a['current_capacity_mbps']),'input_source':a['source'],'hardware_ordered':False,'network_changed':False}
reg('capacity.upgrade.recommend',obj({'peak_demand_mbps':{'type':'number','minimum':0,'maximum':10**9},'target_utilization_percent':{'type':'number','minimum':1,'maximum':100},'current_capacity_mbps':{'type':'number','minimum':0,'maximum':10**9},'source':S}),recommend,False,'Sizing arithmetic only; no inferred demand, physical port availability or hardware order.')

def maintenance(c,a):
 c.get('device',a['device_id']);require(parse_time(a['due_at'])>parse_time(c.now),'due_time_must_be_future')
 return c.create('maintenance_plan',{'device_id':a['device_id'],'due_at':a['due_at'],'reason':a['reason'],'status':'proposed','dispatched':False})
reg('predict.maintenance.schedule',obj({'device_id':S,'due_at':{'type':'string','maxLength':40},'reason':{'type':'string','minLength':1,'maxLength':1000}}),maintenance,True,'Proposed maintenance record only; no trained failure prediction or technician dispatch.')

def spectrum(c,a):
 rows=a['samples'];require(len(set(x['frequency_mhz'] for x in rows))==len(rows),'duplicate_frequency')
 peak=max(rows,key=lambda x:x['power_dbm']);return {'peak_sample':peak,'samples':sorted(rows,key=lambda x:x['frequency_mhz']),'source':a['source'],'interference_cause_verified':False,'hardware_measurement_performed':False}
reg('capacity.spectrum.analyze',obj({'samples':{'type':'array','minItems':1,'maxItems':500,'items':obj({'frequency_mhz':{'type':'number','minimum':0.001,'maximum':10**6},'power_dbm':{'type':'number','minimum':-200,'maximum':100}})},'source':S}),spectrum,False,'Sorts supplied spectrum samples and reports peak; no RF probe or interference attribution.')
