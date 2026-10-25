"""Evidence-based analyses; no live probes, trained-model claims or regulator submissions."""
import collections,datetime as dt,math,statistics
from decimal import Decimal,ROUND_HALF_UP
from .common import require,DomainError,parse_time,money,identifier
S={'type':'string','minLength':1,'maxLength':100};N={'type':'number','minimum':-10**12,'maximum':10**12};I={'type':'integer','minimum':0,'maximum':10**15}
def obj(props,required=None):return {'type':'object','properties':props,'required':list(props) if required is None else required,'additionalProperties':False}
OBS=obj({'timestamp':{'type':'string','maxLength':40},'value':N,'source':S})
SERIES={'type':'array','minItems':6,'maxItems':500,'items':OBS}
HANDLERS={};SCHEMAS={};MUTATIONS=set();LIMITATIONS={}
def register(names,schema,fn,limitation):
    for name in names:HANDLERS[name]=fn;SCHEMAS[name]=schema;LIMITATIONS[name]=limitation

def forecast(ctx,a):
    rows=sorted(a['observations'],key=lambda v:parse_time(v['timestamp']));times=[parse_time(r['timestamp']) for r in rows]
    require(len(set(times))==len(times),'duplicate_observation_time')
    require(times[-1]<=parse_time(ctx.now),'future_observation')
    seconds=(times[-1]-times[0]).total_seconds();require(seconds>0,'insufficient_time_span')
    require((parse_time(ctx.now)-times[-1]).total_seconds()<=a.get('max_age_seconds',86400),'stale_evidence',409)
    xs=[(t-times[0]).total_seconds() for t in times];ys=[r['value'] for r in rows];mx=statistics.mean(xs);my=statistics.mean(ys)
    slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
    target=ys[-1]+slope*a['horizon_seconds'];threshold=a.get('threshold')
    return {'method':'ordinary_least_squares_trend','trained_model':False,'samples':len(rows),'last_value':ys[-1],'forecast_value':round(target,6),'slope_per_day':round(slope*86400,6),'horizon_seconds':a['horizon_seconds'],'threshold_crossing_indicated':None if threshold is None else min(ys[-1],target)<=threshold<=max(ys[-1],target),'evidence_sources':sorted(set(r['source'] for r in rows)),'verified_failure_prediction':False,'warning':'Extrapolation from submitted evidence; no certainty or actual device inspection is implied.'}
forecast_schema=obj({'observations':SERIES,'horizon_seconds':{'type':'integer','minimum':1,'maximum':2678400},'threshold':N,'max_age_seconds':{'type':'integer','minimum':1,'maximum':2678400}},['observations','horizon_seconds'])
register(('capacity.forecast','predict.fiber.degrade','predict.olt.failure','predict.ont.battery','predict.ups.failure','predict.network.congestion','predict.churn.risk','predict.payment.default'),forecast_schema,forecast,'Deterministic trend estimate from supplied time series; not a trained predictive model or confirmed hardware fault.')

def bottleneck(ctx,a):
    out=[]
    for x in a['links']:
        require(x['capacity_mbps']>0,'invalid_capacity');pct=x['used_mbps']/x['capacity_mbps']*100
        out.append({'id':x['id'],'utilization_percent':round(pct,4),'exceeds_threshold':pct>=a['threshold_percent']})
    return {'links':out,'bottlenecks':[r['id'] for r in out if r['exceeds_threshold']],'live_monitoring':False}
link=obj({'id':S,'capacity_mbps':{'type':'number','exclusiveMinimum':0,'minimum':0.001,'maximum':10**9},'used_mbps':{'type':'number','minimum':0,'maximum':10**9}})
register(('capacity.bottleneck.identify','capacity.link.utilization'),obj({'links':{'type':'array','maxItems':500,'items':link},'threshold_percent':{'type':'number','minimum':1,'maximum':100}}),bottleneck,'Measures supplied utilization samples only, no SNMP/polling.')

def cost_benefit(ctx,a):
    upfront=money(a['capex_idr']);monthly=money(a['monthly_incremental_revenue_idr'])-money(a['monthly_incremental_opex_idr'])
    return {'monthly_net_idr':monthly,'break_even_months':None if monthly<=0 else str((Decimal(upfront)/Decimal(monthly)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)),'requires_verified_forecast':True}
register(('capacity.cost.benefit',),obj({'capex_idr':I,'monthly_incremental_revenue_idr':I,'monthly_incremental_opex_idr':I}),cost_benefit,'Arithmetic only; no invented demand or investment recommendation.')

def anomaly(ctx,a):
    baseline=a['baseline'];require(len(baseline)>=5,'insufficient_baseline');mean=statistics.mean(baseline);sd=statistics.pstdev(baseline);value=a['value'];score=None if sd==0 else abs(value-mean)/sd
    indicated=value!=mean if sd==0 else score>=a['z_threshold']
    return {'anomaly_indicated':indicated,'z_score':None if score is None else round(score,6),'baseline_mean':mean,'baseline_stddev':sd,'confirmed_fraud':False,'decision':'manual_review' if indicated else 'no_statistical_anomaly','method':'baseline_z_score','source':a['source']}
register(('fraud.usage.anomaly','fraud.sms.spam','fraud.call.bombing'),obj({'baseline':{'type':'array','minItems':5,'maxItems':500,'items':{'type':'number','minimum':0,'maximum':10**12}},'value':{'type':'number','minimum':0,'maximum':10**12},'z_threshold':{'type':'number','minimum':1,'maximum':10},'source':S}),anomaly,'Statistical indicator only, never a fraud verdict, blacklist or message interception.')

def rate_audit(ctx,a):
    mismatches=[];seen=set();duplicates=[];unbilled=[]
    for row in a['charges']:
        if row['charge_id'] in seen:duplicates.append(row['charge_id'])
        seen.add(row['charge_id'])
        if row['amount_idr']!=row['expected_idr']:mismatches.append({'charge_id':row['charge_id'],'expected_idr':row['expected_idr'],'actual_idr':row['amount_idr'],'difference_idr':row['amount_idr']-row['expected_idr']})
        if row['billable'] and not row['invoice_id']:unbilled.append(row['charge_id'])
    return {'rate_mismatches':mismatches,'duplicate_charge_ids':sorted(set(duplicates)),'billable_without_invoice':unbilled,'source':a['source'],'recovery_or_adjustment_executed':False}
charge=obj({'charge_id':S,'amount_idr':I,'expected_idr':I,'billable':{'type':'boolean'},'invoice_id':{'type':'string','maxLength':100}})
register(('revass.leak.detect','revass.usage.not.billed','revass.double.billing','revass.rate.mismatch','revass.reconciliation'),obj({'charges':{'type':'array','maxItems':1000,'items':charge},'source':S}),rate_audit,'Reconciliation of supplied evidence only; no automatic billing adjustment or recovery.')

def routing_audit(ctx,a):
    actual=set(a['observed_origins']);allowed=set(a['authorized_origins']);unexpected=sorted(actual-allowed)
    return {'prefix':a['prefix'],'unexpected_origin_asns':unexpected,'hijack_suspected':bool(unexpected),'confirmed_hijack':False,'source':a['source'],'changes_applied':False}
register(('netsec.bgp.hijack.detect',),obj({'prefix':S,'observed_origins':{'type':'array','maxItems':100,'items':{'type':'integer','minimum':1,'maximum':4294967295}},'authorized_origins':{'type':'array','minItems':1,'maxItems':100,'items':{'type':'integer','minimum':1,'maximum':4294967295}},'source':S}),routing_audit,'Compare submitted origins; not a BGP collector or RPKI validation service.')

def report(ctx,a):
    subscribers=ctx.list('subscriber');invoices=ctx.list('invoice');tickets=ctx.list('ticket');work=ctx.list('work_order')
    revenue=sum(x.get('paid_idr',0) for x in invoices if x.get('period')==a['period'])
    active=sum(x.get('status')=='active' for x in subscribers);resolved=sum(x.get('status')=='resolved' for x in tickets)
    return {'period':a['period'],'local_subscribers':len(subscribers),'active_record_count':active,'local_paid_revenue_idr':revenue,'arpu_idr':None if active==0 else str((Decimal(revenue)/Decimal(active)).quantize(Decimal('.01'))),'tickets':len(tickets),'resolved_ticket_count':resolved,'completed_work_orders':sum(x.get('status')=='completed' for x in work),'source':'tenant_records','date_filter_scope':'invoice period; other figures are current snapshot','company_data_connected':False}
register(('report.arpu','report.revenue.monthly','report.executive.dashboard'),obj({'period':{'type':'string','pattern':r'\d{4}-(0[1-9]|1[0-2])','maxLength':7}}),report,'Bounded current tenant records; no assertion of company-wide completeness or historical subscriber status.')

def growth(ctx,a):
    start=parse_time(a['start_at']);end=parse_time(a['end_at']);require(start<end,'invalid_window');rows=ctx.list('subscriber')
    created=sum(start<=parse_time(r['created_at'])<end for r in rows)
    return {'start_at':a['start_at'],'end_at':a['end_at'],'new_subscribers_recorded':created,'source':'tenant_records','historical_churn_available':False}
register(('report.subscriber.growth',),obj({'start_at':{'type':'string','maxLength':40},'end_at':{'type':'string','maxLength':40}}),growth,'Counts locally recorded subscriber creation timestamps; no imported historical churn implied.')

def audit_prepare(ctx,a):
    provided=set(a['provided_evidence_ids']);required=set(a['required_evidence_ids'])
    return {'policy_id':a['policy_id'],'present':sorted(provided&required),'missing':sorted(required-provided),'checklist_complete':required<=provided,'legal_compliance_certified':False,'submitted_to_regulator':False,'requires_authorized_review':True}
register(('reg.audit.prepare','netsec.audit.compliance'),obj({'policy_id':S,'required_evidence_ids':{'type':'array','minItems':1,'maxItems':100,'items':S},'provided_evidence_ids':{'type':'array','maxItems':100,'items':S}}),audit_prepare,'Evidence checklist only; no legal certification, fixed tax/retention rule, interception or regulator filing.')
