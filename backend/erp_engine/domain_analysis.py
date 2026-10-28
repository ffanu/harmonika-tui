"""Bounded supplied-data domain calculations; no business posting or provider calls."""
from .planner import require,ERPError
DOMAIN_OPS={'isp.billing.cycle','mfg.bom','mfg.mrp','mfg.production.costing','inventory.stock.opname','inventory.batch.tracking','procurement.three.way.match','retail.promotion','pos.cash.management','pm.time.tracking','mfg.production.scheduling','mfg.routing','mfg.scrap.management','mfg.quality.control','crm.pipeline','crm.satisfaction.survey','pm.project.billing','pm.gantt.chart','pm.resource.allocation','pm.risk.management','pm.project.portfolio','pm.milestone','pm.issue.tracking','finance.financial.statement','finance.cashflow.statement','report.financial.statement','report.profit.loss','report.cash.flow','report.export.excel','hr.attendance','hr.leave.management','hr.payroll','hr.time.overtime','hr.expense.claim','hr.loan.management','procurement.rfq','procurement.vendor.performance'}
def dmoney(v):
 import re
 require(type(v) is str and re.fullmatch(r'0|[1-9][0-9]{0,17}',v),'invalid_money');return int(v)
def dqty(v,minimum=0,maximum=1000000):
 require(type(v) is int and minimum<=v<=maximum,'invalid_quantity');return v
def did(v):
 require(type(v) is str and 1<=len(v)<=100 and not any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in v),'invalid_id');return v
def drows(v,maximum=1000):require(type(v) is list and len(v)<=maximum,'invalid_rows');return v
def dfields(v,keys):require(type(v) is dict and set(v)==set(keys),'invalid_fields')
def ddate(v):
 import re
 from datetime import date
 require(type(v) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}',v),'invalid_date')
 try:return date.fromisoformat(v)
 except ValueError:raise ERPError('invalid_date')
def material_plan(v):
 dfields(v,['bom','demand','stock']);graph={};nodes=set();gross={};stock={}
 for row in drows(v['bom'],100):
  dfields(row,['item','components']);item=did(row['item']);require(item not in graph,'duplicate_bom');graph[item]={};nodes.add(item)
  for c in drows(row['components'],100):
   dfields(c,['item','quantity']);child=did(c['item']);require(child not in graph[item],'duplicate_component');graph[item][child]=dqty(c['quantity'],1);nodes.add(child)
 require(len(nodes)<=500,'bom_node_limit')
 for key,target in [('demand',gross),('stock',stock)]:
  for row in drows(v[key],500):
   dfields(row,['item','quantity']);item=did(row['item']);require(item not in target,'duplicate_item');target[item]=dqty(row['quantity']);nodes.add(item)
 require(len(nodes)<=500,'bom_node_limit')
 incoming={n:0 for n in nodes}
 for children in graph.values():
  for child in children:incoming[child]+=1
 ready=sorted(n for n in nodes if incoming[n]==0);order=[]
 while ready:
  n=ready.pop(0);order.append(n)
  for child in sorted(graph.get(n,{})):
   incoming[child]-=1
   if incoming[child]==0:ready.append(child);ready.sort()
 require(len(order)==len(nodes),'bom_cycle');rows=[]
 for item in order:
  needed=gross.get(item,0);used=min(needed,stock.get(item,0));net=needed-used
  rows.append({'item':item,'gross_quantity':needed,'stock_used':used,'net_quantity':net,'kind':'assembly' if graph.get(item) else 'purchased_or_leaf'})
  for child,quantity in graph.get(item,{}).items():
   gross[child]=gross.get(child,0)+net*quantity;require(gross[child]<=1000000000000,'quantity_limit')
 return {'requirements':rows,'topological_order':order,'cycles':False,'units':'integer_only','reservations_posted':False,'procurement_orders_created':False}
def run_domain(op,v):
 if op in {'procurement.rfq','procurement.vendor.performance'}:
  import json
  result=sourcing_analysis(op,v);require(len(json.dumps(result,ensure_ascii=False).encode('utf-8'))<=180000,'sourcing_result_limit');return {**result,'source_evidence_verified':False,'business_data_updated':False,'compliance_verified':False}
 if op in {'hr.attendance','hr.leave.management','hr.payroll','hr.time.overtime','hr.expense.claim','hr.loan.management'}:
  import json
  result=hr_analysis(op,v)
  require(len(json.dumps(result,ensure_ascii=False).encode('utf-8'))<=180000,'hr_result_limit')
  return {**result,'business_data_updated':False,'disbursement_sent':False,'compliance_verified':False}
 if op=='report.export.excel':return export_excel(v)
 if op in {'finance.financial.statement','finance.cashflow.statement','report.financial.statement','report.profit.loss','report.cash.flow'}:return financial_reports(op,v)
 if op in {'pm.milestone','pm.issue.tracking'}:return project_tracking(op,v)
 if op in {'pm.resource.allocation','pm.risk.management','pm.project.portfolio'}:return project_controls(op,v)
 if op in {'crm.pipeline','crm.satisfaction.survey','pm.project.billing','pm.gantt.chart'}:return crm_project_analysis(op,v)
 if op in {'mfg.production.scheduling','mfg.routing','mfg.scrap.management','mfg.quality.control'}:return manufacturing_checks(op,v)
 if op in ('mfg.bom','mfg.mrp'):return material_plan(v)
 if op=='isp.billing.cycle':
  from datetime import timedelta,date
  import calendar
  dfields(v,['start','frequency','count']);start=ddate(v['start']);count=dqty(v['count'],1,120);require(v['frequency'] in ('monthly','daily'),'invalid_frequency');dates=[]
  for n in range(count):
   try:
    if v['frequency']=='daily':day=start+timedelta(days=n)
    else:
     offset=start.year*12+start.month-1+n;year,month=divmod(offset,12);month+=1;day=date(year,month,min(start.day,calendar.monthrange(year,month)[1]))
    dates.append(day.isoformat())
   except (ValueError,OverflowError):raise ERPError('date_range_exceeded')
  return {'dates':dates,'anchor_day':start.day,'end_of_month_policy':'clamp_without_anchor_drift','invoices_created':False}
 if op=='mfg.production.costing':
  dfields(v,['materials','labor_minor','overhead_minor','units']);units=dqty(v['units'],1);materials=0
  for row in drows(v['materials']):dfields(row,['quantity','unit_minor']);materials+=dqty(row['quantity'])*dmoney(row['unit_minor'])
  total=materials+dmoney(v['labor_minor'])+dmoney(v['overhead_minor']);require(total<=9223372036854775807,'amount_exceeds_storage_limit');q,r=divmod(total,units)
  return {'materials_minor':str(materials),'total_minor':str(total),'unit_floor_minor':str(q),'rounding_remainder_minor':str(r),'allocation':'first_remainder_units_plus_one_minor','units':units}
 if op=='inventory.stock.opname':
  dfields(v,['rows']);seen=set();rows=[]
  for row in drows(v['rows']):
   dfields(row,['item','recorded','counted']);item=did(row['item']);require(item not in seen,'duplicate_item');seen.add(item);recorded=dqty(row['recorded']);counted=dqty(row['counted']);rows.append({'item':item,'delta':counted-recorded,'counted':counted,'adjustment_required':counted!=recorded})
  return {'rows':rows,'stock_adjusted':False}
 if op=='inventory.batch.tracking':
  dfields(v,['as_of','required','batches']);today=ddate(v['as_of']);remaining=dqty(v['required']);batches=[];seen=set();excluded=[]
  for row in drows(v['batches']):
   dfields(row,['id','expiry','quantity']);key=did(row['id']);require(key not in seen,'duplicate_batch');seen.add(key);expiry=ddate(row['expiry']);quantity=dqty(row['quantity'])
   if expiry<=today:excluded.append(key)
   else:batches.append((expiry,key,quantity))
  allocations=[]
  for expiry,key,quantity in sorted(batches):
   used=min(remaining,quantity);remaining-=used
   if used:allocations.append({'batch_id':key,'quantity':used,'expiry':expiry.isoformat()})
  return {'allocations':allocations,'shortage':remaining,'expired_or_expiring_today_excluded':excluded,'policy':'FEFO_expiry_strictly_after_as_of','stock_reserved':False}
 if op=='procurement.three.way.match':
  dfields(v,['purchase_order','receipt','invoice']);sides={}
  for side in ['purchase_order','receipt','invoice']:
   sides[side]={}
   for row in drows(v[side]):
    dfields(row,['item','quantity','unit_minor','currency']);item=did(row['item']);require(item not in sides[side],'duplicate_item');require(type(row['currency']) is str and __import__('re').fullmatch('[A-Z]{3}',row['currency']),'invalid_currency');sides[side][item]=(dqty(row['quantity'],1),dmoney(row['unit_minor']),row['currency'])
  require(bool(sides['purchase_order']),'empty_purchase_order');issues=[]
  for item in sorted(set().union(*(set(x) for x in sides.values()))):
   values=[sides[x].get(item) for x in ['purchase_order','receipt','invoice']]
   if None in values:issues.append({'item':item,'reason':'missing_line'})
   elif len(set(values))!=1:issues.append({'item':item,'reason':'quantity_price_or_currency_mismatch'})
  return {'matched':not issues,'issues':issues,'policy':'exact_full_receipt_no_tolerance','payment_authorized':False}
 if op=='retail.promotion':
  dfields(v,['subtotal_minor','discount_bps','cap_minor']);subtotal=dmoney(v['subtotal_minor']);bps=dqty(v['discount_bps'],0,10000);cap=dmoney(v['cap_minor']);discount=min(subtotal,cap,(subtotal*bps+5000)//10000)
  return {'discount_minor':str(discount),'net_minor':str(subtotal-discount),'tax_applied':False,'policy':'single_discount_half_up_capped'}
 if op=='pos.cash.management':
  dfields(v,['opening_minor','cash_in_minor','cash_out_minor','counted_minor']);expected=dmoney(v['opening_minor'])+dmoney(v['cash_in_minor'])-dmoney(v['cash_out_minor']);require(expected>=0,'negative_expected_cash');counted=dmoney(v['counted_minor'])
  return {'expected_minor':str(expected),'counted_minor':str(counted),'variance_minor':str(counted-expected),'balanced':counted==expected,'drawer_updated':False}
 if op=='pm.time.tracking':
  from datetime import datetime
  dfields(v,['entries']);groups={};total=0
  for row in drows(v['entries']):
   dfields(row,['resource','start','end']);resource=did(row['resource'])
   try:
    require(type(row['start']) is str and type(row['end']) is str,'invalid_timestamp');start=datetime.fromisoformat(row['start'].replace('Z','+00:00'));end=datetime.fromisoformat(row['end'].replace('Z','+00:00'))
   except ValueError:raise ERPError('invalid_timestamp')
   require(start.tzinfo is not None and end.tzinfo is not None and start.microsecond==end.microsecond==0,'timezone_and_whole_seconds_required');seconds=(end-start).total_seconds();require(0<seconds<=86400,'invalid_duration');groups.setdefault(resource,[]).append((start,end,int(seconds)))
  totals={}
  for resource,entries in groups.items():
   entries.sort();previous=None
   for start,end,seconds in entries:
    require(previous is None or start>=previous,'overlapping_time_entries');previous=end;totals[resource]=totals.get(resource,0)+seconds;total+=seconds
  return {'seconds_by_resource':totals,'total_seconds':total,'payroll_updated':False}
 raise ERPError('skill_not_implemented')

def production_schedule(v,capacity=True):
 """Deterministic non-preemptive serial schedule; not a claim of optimal makespan."""
 import heapq
 dfields(v,['jobs','work_centers'] if capacity else ['operations']);centers={}
 if capacity:
  for center in drows(v['work_centers'],100):
   dfields(center,['id','available_minute']);key=did(center['id']);require(key not in centers,'duplicate_work_center');centers[key]=dqty(center['available_minute'],0,1000000000)
 jobs={};followers={};incoming={};edges=0
 for row in drows(v['jobs'] if capacity else v['operations'],500):
  dfields(row,['id','duration_minutes','predecessors','work_center','ready_minute'] if capacity else ['id','duration_minutes','predecessors']);key=did(row['id']);require(key not in jobs,'duplicate_operation');dqty(row['duration_minutes'],1);pred=drows(row['predecessors'],500);require(len(set(did(x) for x in pred))==len(pred),'duplicate_predecessor');jobs[key]=row;incoming[key]=len(pred);edges+=len(pred)
  if capacity:did(row['work_center']);require(row['work_center'] in centers,'unknown_work_center');dqty(row['ready_minute'],0,1000000000)
 require(jobs and edges<=10000,'invalid_routing_size')
 for key,row in jobs.items():
  for pred in row['predecessors']:require(pred in jobs,'unknown_predecessor');followers.setdefault(pred,[]).append(key)
 ready=[key for key,n in incoming.items() if n==0];heapq.heapify(ready);end={};rows=[];paths={}
 while ready:
  key=heapq.heappop(ready);row=jobs[key];pred=row['predecessors'];previous=max(pred,key=lambda p:(end[p],p)) if pred else None;start=end[previous] if previous else 0
  if capacity:start=max(start,row['ready_minute'],centers[row['work_center']])
  finish=start+row['duration_minutes'];require(finish<=1000000000,'schedule_horizon_exceeded');end[key]=finish;paths[key]=(paths[previous] if previous else [])+[key];item={'id':key,'start_minute':start,'end_minute':finish}
  if capacity:centers[row['work_center']]=finish;item['work_center']=row['work_center']
  rows.append(item)
  for child in followers.get(key,[]):
   incoming[child]-=1
   if incoming[child]==0:heapq.heappush(ready,child)
 require(len(rows)==len(jobs),'routing_cycle');last=max(end,key=lambda p:(end[p],p))
 return {'operations':rows,'makespan_minutes':max(end.values()),'dependency_critical_path':paths[last] if not capacity else None,'policy':'lexicographic_serial_single_machine' if capacity else 'earliest_dependency_schedule','optimal_schedule_claimed':False,'calendars_or_breaks_applied':False,'orders_scheduled':False}

def manufacturing_checks(op,v):
 if op in ('mfg.production.scheduling','mfg.routing'):return production_schedule(v,op=='mfg.production.scheduling')
 if op=='mfg.scrap.management':
  dfields(v,['issued_quantity','good_quantity','scrap_quantity']);issued=dqty(v['issued_quantity'],1);good=dqty(v['good_quantity']);scrap=dqty(v['scrap_quantity']);require(good+scrap<=issued,'invalid_mass_balance')
  return {'remaining_wip_quantity':issued-good-scrap,'good_yield_bps':(good*10000+issued//2)//issued,'scrap_bps':(scrap*10000+issued//2)//issued,'inventory_adjusted':False,'environmental_compliance_verified':False}
 dfields(v,['inspected','accepted','rejected','criteria']);inspected=dqty(v['inspected'],1);accepted=dqty(v['accepted']);rejected=dqty(v['rejected']);require(accepted+rejected==inspected,'invalid_inspection_count');criteria=[];seen=set()
 for row in drows(v['criteria'],100):
  dfields(row,['id','unit','value_scaled','minimum_scaled','maximum_scaled']);key=did(row['id']);did(row['unit']);require(key not in seen,'duplicate_criterion');seen.add(key)
  for field in ['value_scaled','minimum_scaled','maximum_scaled']:dqty(row[field],-1000000000,1000000000)
  require(row['minimum_scaled']<=row['maximum_scaled'],'invalid_tolerance');criteria.append({'id':key,'within_tolerance':row['minimum_scaled']<=row['value_scaled']<=row['maximum_scaled']})
 require(criteria,'inspection_criteria_required');passed=rejected==0 and all(x['within_tolerance'] for x in criteria)
 return {'result':'PASSED' if passed else 'FAILED','criteria':criteria,'inspected':inspected,'accepted':accepted,'rejected':rejected,'production_state_changed':False,'certification_verified':False}

def crm_project_analysis(op,v):
 if op=='crm.pipeline':
  dfields(v,['opportunities']);rows=[];seen=set();totals={s:0 for s in ['NEW','QUALIFIED','WON','LOST']};forecast=0
  for row in drows(v['opportunities']):
   dfields(row,['id','stage','value_minor','probability_bps']);key=did(row['id']);require(key not in seen,'duplicate_opportunity');seen.add(key);require(type(row['stage']) is str and row['stage'] in totals,'invalid_stage');value=dmoney(row['value_minor']);prob=dqty(row['probability_bps'],0,10000);require(row['stage']!='WON' or prob==10000,'won_probability_inconsistent');require(row['stage']!='LOST' or prob==0,'lost_probability_inconsistent');weighted=(value*prob+5000)//10000;totals[row['stage']]+=value;forecast+=weighted;rows.append({'id':key,'weighted_minor':str(weighted)})
  return {'rows':rows,'value_by_stage_minor':{k:str(n) for k,n in totals.items()},'weighted_minor':str(forecast),'probability_kind':'caller_supplied_not_prediction','rounding':'per_opportunity_half_up','crm_records_changed':False}
 if op=='crm.satisfaction.survey':
  dfields(v,['csat','nps']);csat=drows(v['csat'],10000);nps=drows(v['nps'],10000);require(csat or nps,'survey_samples_required')
  for score in csat:dqty(score,1,5)
  for score in nps:dqty(score,0,10)
  positive=sum(score>=4 for score in csat);promoters=sum(score>=9 for score in nps);detractors=sum(score<=6 for score in nps);diff=promoters-detractors;bps=(1 if diff>=0 else -1)*((abs(diff)*10000+len(nps)//2)//len(nps)) if nps else None
  return {'csat_count':len(csat),'csat_positive':positive,'csat_bps':(positive*10000+len(csat)//2)//len(csat) if csat else None,'nps_count':len(nps),'promoters':promoters,'detractors':detractors,'nps_basis_points':bps,'nps_score':('-' if bps is not None and bps<0 else '')+str(abs(bps)//100)+'.'+str(abs(bps)%100).zfill(2) if bps is not None else None,'agent_attribution_inferred':False}
 if op=='pm.project.billing':
  dfields(v,['entries','tax_bps']);rate=dqty(v['tax_bps'],0,10000);entries=drows(v['entries']);require(entries,'billing_entries_required');rows=[];seen=set();subtotal=0
  for row in entries:
   dfields(row,['id','seconds','rate_minor_per_hour']);key=did(row['id']);require(key not in seen,'duplicate_time_entry');seen.add(key);seconds=dqty(row['seconds'],1,86400);value=(seconds*dmoney(row['rate_minor_per_hour'])+1800)//3600;subtotal+=value;rows.append({'id':key,'amount_minor':str(value)})
  tax=(subtotal*rate+5000)//10000;require(subtotal+tax<=9223372036854775807,'amount_exceeds_storage_limit')
  return {'rows':rows,'subtotal_minor':str(subtotal),'tax_minor':str(tax),'total_minor':str(subtotal+tax),'rounding':'per_entry_half_up_seconds_over_3600','invoice_created':False,'time_entries_marked_billed':False,'tax_rule_verified':False}
 dfields(v,['operations']);require(len(drows(v['operations'],100))>0,'chart_operations_required');schedule=production_schedule(v,False)
 from xml.sax.saxutils import escape
 from xml.etree import ElementTree as ET
 rows=schedule['operations'];makespan=schedule['makespan_minutes'];height=60+32*len(rows);parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img"><title>Dependency Gantt in relative minutes</title><rect width="1000" height="{height}" fill="white"/><text x="12" y="24" font-family="sans-serif" font-size="14">Minutes from planning origin · dependencies only</text>']
 for i,row in enumerate(rows):
  y=42+i*32;x=230+(row['start_minute']*740//makespan);width=max(1,(row['end_minute']-row['start_minute'])*740//makespan);label=escape(row['id']);parts.append(f'<text x="12" y="{y+17}" font-family="sans-serif" font-size="12">{label}</text><rect x="{x}" y="{y}" width="{width}" height="23" rx="3" fill="#1763a6"><title>{label}: {row["start_minute"]}–{row["end_minute"]}</title></rect>')
 parts.append('</svg>');svg=''.join(parts)
 try:ET.fromstring(svg)
 except (ET.ParseError,UnicodeError):raise ERPError('invalid_chart_label')
 return {'svg':svg,'mime_type':'image/svg+xml','task_count':len(rows),'makespan_minutes':makespan,'work_center_capacity_applied':False,'projects_changed':False}

def project_controls(op,v):
 if op=='pm.resource.allocation':
  dfields(v,['resources','assignments']);resources={};events={};assignment_ids=set()
  for row in drows(v['resources'],200):
   dfields(row,['id','capacity_units']);key=did(row['id']);require(key not in resources,'duplicate_resource');resources[key]=dqty(row['capacity_units'],1,1000000);events[key]={}
  for row in drows(v['assignments'],1000):
   dfields(row,['id','resource_id','start_minute','end_minute','units']);key=did(row['id']);require(key not in assignment_ids,'duplicate_assignment');assignment_ids.add(key);resource=did(row['resource_id']);require(resource in resources,'unknown_resource');start=dqty(row['start_minute'],0,1000000000);end=dqty(row['end_minute'],1,1000000000);require(end>start,'invalid_interval');units=dqty(row['units'],1,1000000);events[resource][start]=events[resource].get(start,0)+units;events[resource][end]=events[resource].get(end,0)-units
  rows=[]
  for key in sorted(resources):
   load=peak=0;over=[];previous=None;unit_minutes=0
   for minute,delta in sorted(events[key].items()):
    if previous is not None and minute>previous:
     unit_minutes+=load*(minute-previous)
     if load>resources[key]:over.append({'start_minute':previous,'end_minute':minute,'units':load,'excess_units':load-resources[key]})
    load+=delta;peak=max(peak,load);previous=minute
   rows.append({'id':key,'capacity_units':resources[key],'peak_units':peak,'unit_minutes':str(unit_minutes),'overallocations':over})
  return {'resources':rows,'feasible':not any(r['overallocations'] for r in rows),'interval_policy':'half_open_end_equals_start_is_not_overlap','calendar_breaks_applied':False,'assignments_changed':False}
 if op=='pm.risk.management':
  dfields(v,['risks']);rows=[];seen=set();total=0
  for row in drows(v['risks']):
   dfields(row,['id','probability_bps','impact_minor']);key=did(row['id']);require(key not in seen,'duplicate_risk');seen.add(key);prob=dqty(row['probability_bps'],0,10000);impact=dmoney(row['impact_minor']);expected=(prob*impact+5000)//10000;total+=expected;rows.append({'id':key,'expected_exposure_minor':str(expected),'probability_bps':prob,'impact_minor':str(impact)})
  require(total<=9223372036854775807,'amount_exceeds_storage_limit');rows.sort(key=lambda r:(-int(r['expected_exposure_minor']),r['id']))
  return {'risks':rows,'expected_exposure_sum_minor':str(total),'probabilities_inferred':False,'correlations_or_mutual_exclusion_modeled':False,'reserve_posted':False}
 dfields(v,['projects']);rows=[];seen=set();budget=actual=earned=weighted_progress=0;by_status={s:0 for s in ['PLANNED','ACTIVE','COMPLETED','CANCELLED']}
 for row in drows(v['projects'],500):
  dfields(row,['id','status','budget_minor','actual_cost_minor','completion_bps']);key=did(row['id']);require(key not in seen,'duplicate_project');seen.add(key);require(type(row['status']) is str and row['status'] in by_status,'invalid_project_status');b=dmoney(row['budget_minor']);cost=dmoney(row['actual_cost_minor']);progress=dqty(row['completion_bps'],0,10000);require(row['status']!='COMPLETED' or progress==10000,'completed_progress_inconsistent');require(row['status']!='PLANNED' or progress==0,'planned_progress_inconsistent');by_status[row['status']]+=1;ev=(b*progress+5000)//10000;budget+=b;actual+=cost;earned+=ev;weighted_progress+=b*progress;rows.append({'id':key,'budget_variance_minor':str(b-cost),'earned_value_minor':str(ev),'earned_cost_variance_minor':str(ev-cost)})
 require(max(budget,actual,earned)<=9223372036854775807,'amount_exceeds_storage_limit')
 return {'projects':rows,'counts_by_status':by_status,'budget_minor':str(budget),'actual_cost_minor':str(actual),'earned_value_minor':str(earned),'budget_variance_minor':str(budget-actual),'earned_cost_variance_minor':str(earned-actual),'budget_weighted_completion_bps':(weighted_progress+budget//2)//budget if budget else None,'costs_or_progress_inferred':False,'ledger_posted':False}

def project_tracking(op,v):
 if op=='pm.milestone':
  dfields(v,['as_of','tasks','milestones']);today=ddate(v['as_of']);tasks={};rows=[];seen=set()
  for task in drows(v['tasks'],1000):
   dfields(task,['id','status']);key=did(task['id']);require(key not in tasks,'duplicate_task');require(type(task['status']) is str and task['status'] in ('TODO','IN_PROGRESS','BLOCKED','DONE','CANCELLED'),'invalid_task_status');tasks[key]=task['status']
  for row in drows(v['milestones'],500):
   dfields(row,['id','status','due_on','task_ids']);key=did(row['id']);require(key not in seen,'duplicate_milestone');seen.add(key);require(type(row['status']) is str and row['status'] in ('PENDING','REACHED','CANCELLED'),'invalid_milestone_status');due=ddate(row['due_on']);ids=drows(row['task_ids'],100);require(ids,'milestone_tasks_required');valid=[did(x) for x in ids];require(len(set(valid))==len(valid),'duplicate_task');require(all(x in tasks for x in valid),'unknown_task');done=sum(tasks[x]=='DONE' for x in valid);require(row['status']!='REACHED' or done==len(valid),'reached_tasks_incomplete');rows.append({'id':key,'done_tasks':done,'total_tasks':len(valid),'completion_bps':(done*10000+len(valid)//2)//len(valid),'eligible_to_reach':row['status']=='PENDING' and done==len(valid),'overdue':row['status']=='PENDING' and due<today,'incomplete_task_ids':[x for x in valid if tasks[x]!='DONE']})
  return {'milestones':rows,'statuses_changed':False,'tasks_inferred':False}
 dfields(v,['as_of','issues']);today=ddate(v['as_of']);seen=set();rows=[];counts={s:0 for s in ['OPEN','IN_PROGRESS','RESOLVED','CLOSED']};priorities={s:0 for s in ['LOW','NORMAL','HIGH','CRITICAL']};resolution_days=resolved_count=0;oldest=None
 for row in drows(v['issues'],1000):
  dfields(row,['id','status','priority','opened_on','resolved_on','closed_on']);key=did(row['id']);require(key not in seen,'duplicate_issue');seen.add(key);status=row['status'];require(type(status) is str and status in counts,'invalid_issue_status');priority=row['priority'];require(type(priority) is str and priority in priorities,'invalid_priority');start=ddate(row['opened_on']);require(start<=today,'future_issue_date');resolved=ddate(row['resolved_on']) if row['resolved_on'] is not None else None;closed=ddate(row['closed_on']) if row['closed_on'] is not None else None;require((status in ('OPEN','IN_PROGRESS') and resolved is None and closed is None) or (status=='RESOLVED' and resolved is not None and closed is None) or (status=='CLOSED' and resolved is not None and closed is not None),'inconsistent_issue_dates')
  require(resolved is None or start<=resolved<=today,'invalid_resolution_date');require(closed is None or resolved<=closed<=today,'invalid_closure_date');counts[status]+=1;priorities[priority]+=1;age=(today-start).days
  if status in ('OPEN','IN_PROGRESS'):oldest=max(oldest or 0,age)
  elapsed=(resolved-start).days if resolved is not None else None
  if elapsed is not None:resolution_days+=elapsed;resolved_count+=1
  rows.append({'id':key,'age_calendar_days':age,'resolution_calendar_days':elapsed,'awaiting_owner_closure':status=='RESOLVED'})
 return {'issues':rows,'counts_by_status':counts,'counts_by_priority':priorities,'oldest_unresolved_calendar_days':oldest,'resolution_calendar_day_sum':resolution_days,'resolved_samples':resolved_count,'statuses_changed':False,'notifications_sent':False}

def financial_reports(op,v):
 import re
 dfields(v,['period','currency','accounts','journals'])
 def period(value):require(type(value) is str and re.fullmatch(r'[1-9][0-9]{3}-(0[1-9]|1[0-2])',value),'invalid_period');return value
 target=period(v['period']);require(type(v['currency']) is str and re.fullmatch('[A-Z]{3}',v['currency']),'invalid_currency');accounts={};balances={};debits={};credits={}
 for row in drows(v['accounts'],200):
  dfields(row,['code','type','is_cash']);key=did(row['code']);require(key not in accounts,'duplicate_account');require(type(row['type']) is str and row['type'] in ('asset','liability','equity','revenue','expense'),'invalid_account_type');require(type(row['is_cash']) is bool and (not row['is_cash'] or row['type']=='asset'),'invalid_cash_account');accounts[key]=row;balances[key]=debits[key]=credits[key]=0
 require(accounts,'accounts_required');seen=set();totals={k:0 for k in ['asset','liability','equity','revenue','expense']};flows={k:0 for k in ['operating','investing','financing','unclassified']};revenue=expense=opening_cash=0;included=0;line_count=0;unclassified_count=0
 for row in drows(v['journals'],1000):
  dfields(row,['id','period','status','cashflow_category','lines']);key=did(row['id']);require(key not in seen,'duplicate_journal');seen.add(key);jp=period(row['period']);require(row['status']=='POSTED','posted_journals_only');category=row['cashflow_category'];require(type(category) is str and category in (*flows,'internal_transfer'),'invalid_cashflow_category');lines=drows(row['lines'],100);require(len(lines)>=2,'invalid_journal');line_count+=len(lines);require(line_count<=10000,'report_line_limit');debit=credit=netcash=0;parsed=[]
  for line in lines:
   dfields(line,['account_code','debit_minor','credit_minor']);code=did(line['account_code']);require(code in accounts,'unknown_account');d=dmoney(line['debit_minor']);c=dmoney(line['credit_minor']);require((d>0)!=(c>0),'invalid_journal_line');debit+=d;credit+=c;parsed.append((code,d,c));netcash+=(d-c) if accounts[code]['is_cash'] else 0
  require(debit==credit and debit>0,'unbalanced_journal')
  if category=='internal_transfer':require(netcash==0 and all(accounts[code]['is_cash'] for code,_,_ in parsed),'invalid_internal_transfer')
  if jp>target:continue
  included+=1
  for code,d,c in parsed:
   balances[code]+=d-c;debits[code]+=d;credits[code]+=c;a=accounts[code]
   if jp==target:
    if a['type']=='revenue':revenue+=c-d
    elif a['type']=='expense':expense+=d-c
  if jp<target:opening_cash+=netcash
  elif category!='internal_transfer':
   flows[category]+=netcash
   if category=='unclassified' and any(accounts[code]['is_cash'] for code,_,_ in parsed):unclassified_count+=1
 for code,balance in balances.items():totals[accounts[code]['type']]+=balance if accounts[code]['type'] in ('asset','expense') else -balance
 accumulated=totals['revenue']-totals['expense'];closing_cash=sum(balances[k] for k,a in accounts.items() if a['is_cash']);net_cash=sum(flows.values());require(max([abs(x) for x in [*totals.values(),*flows.values(),revenue,expense,revenue-expense,accumulated,opening_cash,closing_cash,net_cash,*debits.values(),*credits.values()]])<=9223372036854775807,'amount_exceeds_storage_limit')
 common={'period':target,'currency':v['currency'],'journal_count_included':included,'source':'caller_asserted_posted_journals','live_database_verified':False,'ledger_posted':False,'compliance_certified':False,'source_completeness_verified':False,'bank_reconciled':False,'equation_self_checks_only':True,'data_status':'posted_activity_supplied' if included else 'no_posted_activity_supplied','future_journals_excluded':len(seen)-included}
 profit={'revenue_minor':str(revenue),'expense_minor':str(expense),'profit_minor':str(revenue-expense)}
 cash={'opening_minor':str(opening_cash),'categories_minor':{k:str(n) for k,n in flows.items()},'net_minor':str(net_cash),'closing_minor':str(closing_cash),'reconciliation_difference_minor':str(closing_cash-opening_cash-net_cash),'classification_required':unclassified_count>0,'unclassified_cash_journals':unclassified_count,'classification_independently_verified':False}
 if op in ('finance.cashflow.statement','report.cash.flow'):return {**common,'report_type':'cash_flow','cash_flow':cash}
 if op=='report.profit.loss':return {**common,'report_type':'profit_loss','profit_loss':profit}
 balance={'assets_minor':str(totals['asset']),'liabilities_minor':str(totals['liability']),'equity_minor':str(totals['equity']),'accumulated_profit_minor':str(accumulated),'imbalance_minor':str(totals['asset']-totals['liability']-totals['equity']-accumulated)}
 return {**common,'report_type':'financial_statements','balance_sheet':balance,'profit_loss':profit,'cash_flow':cash,'accounts':[{'code':code,'type':accounts[code]['type'],'debit_minor':str(debits[code]),'credit_minor':str(credits[code]),'debit_balance_minor':str(balances[code])} for code in sorted(accounts)]}

def export_excel(v):
 import base64,hashlib,io,zipfile
 from xml.sax.saxutils import escape
 from xml.etree import ElementTree as ET
 dfields(v,['title','headers','rows']);title=did(v['title']);headers=drows(v['headers'],30);require(headers and all(type(h) is str for h in headers),'headers_required');headers=[did(h) for h in headers];require(len(set(headers))==len(headers),'duplicate_header');rows=drows(v['rows'],1000);require(len(rows)*len(headers)<=10000,'export_cell_limit');cells=[headers];total_bytes=sum(len(h.encode('utf-8')) for h in headers)
 for row in rows:
  require(type(row) is list and len(row)==len(headers),'invalid_export_row');require(all(type(c) is str and len(c)<=1000 and not any(0xD800<=ord(ch)<=0xDFFF for ch in c) for c in row),'text_cells_required');total_bytes+=sum(len(c.encode('utf-8')) for c in row);require(total_bytes<=131072,'export_text_limit');cells.append(row)
 def column(index):
  out=''
  while index:index,n=divmod(index-1,26);out=chr(65+n)+out
  return out
 body=[]
 for y,row in enumerate(cells,1):
  current=[]
  for x,value in enumerate(row,1):
   require(not any(ord(c)<32 and c not in '\t\n\r' for c in value),'invalid_xml_text');current.append(f'<c r="{column(x)}{y}" t="inlineStr" s="{1 if y==1 else 0}"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
  body.append(f'<row r="{y}">'+''.join(current)+'</row>')
 sheet='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>'+''.join(f'<col min="{i}" max="{i}" width="24" customWidth="1"/>' for i in range(1,len(headers)+1))+'</cols><sheetData>'+''.join(body)+f'</sheetData><autoFilter ref="A1:{column(len(headers))}{len(cells)}"/></worksheet>'
 ns='http://schemas.openxmlformats.org'
 files={
 '[Content_Types].xml':f'<Types xmlns="{ns}/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>',
 '_rels/.rels':f'<Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="{ns}/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
 'docProps/core.xml':f'<cp:coreProperties xmlns:cp="{ns}/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{escape(title)}</dc:title><dc:creator>Harmonika Ai</dc:creator></cp:coreProperties>',
 'xl/workbook.xml':f'<workbook xmlns="{ns}/spreadsheetml/2006/main" xmlns:r="{ns}/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>',
 'xl/_rels/workbook.xml.rels':f'<Relationships xmlns="{ns}/package/2006/relationships"><Relationship Id="rId1" Type="{ns}/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="{ns}/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
 'xl/worksheets/sheet1.xml':sheet,
 'xl/styles.xml':f'<styleSheet xmlns="{ns}/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="49" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'}
 try:
  for xml in files.values():ET.fromstring(xml)
 except (ET.ParseError,UnicodeError):raise ERPError('invalid_xml_text')
 require(sum(len(xml.encode('utf-8')) for xml in files.values())<=1048576,'export_uncompressed_limit')
 buffer=io.BytesIO()
 with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as z:
  for name,xml in files.items():
   info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o600<<16;z.writestr(info,xml.encode('utf-8'))
 data=buffer.getvalue();require(len(data)<=180000,'export_artifact_limit')
 return {'title':title,'row_count':len(rows),'column_count':len(headers),'cell_policy':'explicit_text_preserves_identifiers','formulas_enabled':False,'external_links_enabled':False,'business_data_updated':False,'artifact':{'name':'harmonika-report.xlsx','mime_type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','sha256':hashlib.sha256(data).hexdigest(),'base64':base64.b64encode(data).decode()}}


def hr_analysis(op,v):
 """Bounded supplied-data HR calculations; no payroll posting, disbursement or statutory inference."""
 if op=='hr.attendance':
  from datetime import datetime
  import re
  def stamp(value):
   require(type(value) is str and re.fullmatch(r'[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?',value),'invalid_timestamp')
   if re.search(r'[+-][0-9]{2}:[0-9]{2}$',value):
    zone=value[-6:];hours=int(zone[1:3]);minutes=int(zone[4:]);require(hours<=14 and minutes<60 and (hours!=14 or minutes==0),'invalid_timestamp')
   try:return datetime.fromisoformat(value.replace('Z','+00:00'))
   except ValueError:raise ERPError('invalid_timestamp')
  dfields(v,['punches']);groups={};seen=set();total=0
  for row in drows(v['punches'],1000):
   dfields(row,['id','employee','check_in','check_out']);key=did(row['id']);require(key not in seen,'duplicate_punch');seen.add(key);employee=did(row['employee'])
   require(type(row['check_in']) is str and type(row['check_out']) is str,'invalid_timestamp')
   start=stamp(row['check_in']);end=stamp(row['check_out'])
   require(start.tzinfo is not None and end.tzinfo is not None and start.microsecond==end.microsecond==0,'timezone_and_whole_seconds_required')
   seconds=int((end-start).total_seconds());require(0<seconds<=86400,'invalid_duration');groups.setdefault(employee,[]).append((start,end,seconds))
  employees=[]
  for employee in sorted(groups):
   entries=sorted(groups[employee]);previous=None;seconds_sum=0
   for start,end,seconds in entries:
    require(previous is None or start>=previous,'overlapping_attendance');previous=end;seconds_sum+=seconds
   total+=seconds_sum;employees.append({'employee':employee,'punches':len(entries),'seconds':seconds_sum})
  return {'employees':employees,'total_punches':len(seen),'total_seconds':total,'interval_policy':'half_open_end_equals_start_is_not_overlap','attendance_records_written':False,'payroll_inferred':False}
 if op=='hr.leave.management':
  dfields(v,['balances','requests']);balances={};rows=[];granted_total=taken_total=requested_total=0
  for row in drows(v['balances'],500):
   dfields(row,['employee','leave_type','granted_units','taken_units']);employee=did(row['employee']);kind=did(row['leave_type']);require((employee,kind) not in balances,'duplicate_balance');granted=dqty(row['granted_units'],0);taken=dqty(row['taken_units'],0);require(taken<=granted,'leave_overdrawn');balances[(employee,kind)]=granted-taken;granted_total+=granted;taken_total+=taken
   rows.append({'employee':employee,'leave_type':kind,'granted_units':granted,'taken_units':taken,'remaining_units':granted-taken})
  seen=set();requests=[]
  for row in drows(v['requests'],500):
   dfields(row,['id','employee','leave_type','units']);key=did(row['id']);require(key not in seen,'duplicate_request');seen.add(key);employee=did(row['employee']);kind=did(row['leave_type']);require((employee,kind) in balances,'unknown_leave_balance');units=dqty(row['units'],1);remaining=balances[(employee,kind)]-units;require(remaining>=0,'leave_overdrawn');balances[(employee,kind)]=remaining;requested_total+=units
   requests.append({'id':key,'employee':employee,'leave_type':kind,'units':units,'remaining_units_after':remaining})
  for row in rows:row['recorded_remaining_units']=row['remaining_units'];row['remaining_units_after_requests']=balances[(row['employee'],row['leave_type'])]
  return {'request_simulation_only':True,'balances':rows,'requests':requests,'total_granted_units':granted_total,'total_taken_units':taken_total,'total_requested_units':requested_total,'units_kind':'caller_supplied_not_calendar_days','leave_records_changed':False,'approvals_recorded':False}
 if op=='hr.payroll':
  dfields(v,['employees']);rows=[];seen=set();gross_total=deduction_total=total_components=0
  for row in drows(v['employees'],500):
   dfields(row,['id','earnings','deductions']);key=did(row['id']);require(key not in seen,'duplicate_employee');seen.add(key);gross=deductions=0
   earning_ids=set()
   for item in drows(row['earnings'],100):
    total_components+=1;require(total_components<=10000,'hr_component_limit')
    dfields(item,['component','amount_minor']);component=did(item['component']);require(component not in earning_ids,'duplicate_component');earning_ids.add(component);gross+=dmoney(item['amount_minor'])
   deduction_ids=set()
   for item in drows(row['deductions'],100):
    total_components+=1;require(total_components<=10000,'hr_component_limit')
    dfields(item,['component','amount_minor']);component=did(item['component']);require(component not in deduction_ids,'duplicate_component');deduction_ids.add(component);deductions+=dmoney(item['amount_minor'])
   require(deductions<=gross,'deductions_exceed_gross');gross_total+=gross;deduction_total+=deductions
   rows.append({'id':key,'gross_minor':str(gross),'deductions_minor':str(deductions),'net_minor':str(gross-deductions)})
  require(max(gross_total,deduction_total,gross_total-deduction_total)<=9223372036854775807,'amount_exceeds_storage_limit')
  return {'employees':rows,'gross_minor':str(gross_total),'deductions_minor':str(deduction_total),'net_minor':str(gross_total-deduction_total),'components_source':'caller_supplied','tax_or_statutory_deductions_inferred':False,'payslips_issued':False,'payroll_disbursed':False,'compliance_verified':False}
 if op=='hr.time.overtime':
  dfields(v,['days']);rows=[];seen=set();day_keys=set();total_minutes=total_amount=0
  for row in drows(v['days'],1000):
   dfields(row,['id','employee','date','regular_minutes','worked_minutes','overtime_rate_minor_per_hour']);key=did(row['id']);require(key not in seen,'duplicate_day');seen.add(key);employee=did(row['employee']);day=ddate(row['date']);require((employee,day) not in day_keys,'duplicate_employee_day');day_keys.add((employee,day))
   regular=dqty(row['regular_minutes'],0,1440);worked=dqty(row['worked_minutes'],0,1440);rate=dmoney(row['overtime_rate_minor_per_hour']);overtime=max(0,worked-regular);amount=(overtime*rate+30)//60
   require(amount<=9223372036854775807,'amount_exceeds_storage_limit');total_minutes+=overtime;total_amount+=amount
   rows.append({'id':key,'employee':employee,'date':day.isoformat(),'regular_minutes':regular,'worked_minutes':worked,'overtime_minutes':overtime,'amount_minor':str(amount)})
  require(total_amount<=9223372036854775807,'amount_exceeds_storage_limit')
  return {'rows':rows,'total_overtime_minutes':total_minutes,'total_amount_minor':str(total_amount),'rounding':'per_day_half_up_minutes_over_60','regular_threshold_source':'caller_supplied_per_day_not_statutory','statutory_overtime_rules_applied':False,'payroll_posted':False}
 if op=='hr.expense.claim':
  dfields(v,['policy','claims']);caps={}
  for row in drows(v['policy'],100):
   dfields(row,['category','cap_minor']);category=did(row['category']);require(category not in caps,'duplicate_policy_category');caps[category]=dmoney(row['cap_minor'])
  require(caps,'policy_required');rows=[];seen=set();claimed=within=over=0
  for row in drows(v['claims'],1000):
   dfields(row,['id','employee','category','amount_minor']);key=did(row['id']);require(key not in seen,'duplicate_claim');seen.add(key);employee=did(row['employee']);category=did(row['category']);require(category in caps,'unknown_expense_category');amount=dmoney(row['amount_minor']);cap=caps[category];excess=max(0,amount-cap);claimed+=amount;within+=amount-excess;over+=excess
   rows.append({'id':key,'employee':employee,'category':category,'amount_minor':str(amount),'cap_minor':str(cap),'within_policy':amount<=cap,'excess_minor':str(excess)})
  require(max(claimed,within,over)<=9223372036854775807,'amount_exceeds_storage_limit')
  return {'rows':rows,'claimed_minor':str(claimed),'within_policy_minor':str(within),'over_policy_minor':str(over),'policy_source':'caller_supplied_not_statutory','cap_scope':'per_claim_not_per_period','receipts_verified':False,'approval_recorded':False,'claims_recorded':False,'reimbursement_paid':False,'compliance_verified':False}
 dfields(v,['loans']);rows=[];seen=set();outstanding_total=total_installments=total_payments=0;all_payment_ids=set()
 for row in drows(v['loans'],500):
  dfields(row,['id','employee','principal_minor','charges_minor','payments','installments']);key=did(row['id']);require(key not in seen,'duplicate_loan');seen.add(key);employee=did(row['employee']);principal=dmoney(row['principal_minor']);charges=dmoney(row['charges_minor']);paid=0;payment_ids=set()
  for payment in drows(row['payments'],1000):
   total_payments+=1;require(total_payments<=10000,'hr_payment_limit')
   dfields(payment,['id','amount_minor']);payment_id=did(payment['id']);require(payment_id not in payment_ids and payment_id not in all_payment_ids,'duplicate_payment');payment_ids.add(payment_id);all_payment_ids.add(payment_id);paid+=dmoney(payment['amount_minor'])
  total=principal+charges;require(total<=9223372036854775807,'amount_exceeds_storage_limit');require(paid<=total,'payment_exceeds_loan_total');outstanding=total-paid;installments=dqty(row['installments'],0,1200);schedule=None
  if installments:
   require(outstanding>0,'installments_require_outstanding_balance');require(installments<=outstanding,'installment_below_one_minor');total_installments+=installments;require(total_installments<=2000,'hr_installment_limit');share,remainder=divmod(outstanding,installments);schedule=[{'number':n+1,'amount_minor':str(share+1 if n<remainder else share)} for n in range(installments)]
  outstanding_total+=outstanding
  rows.append({'id':key,'employee':employee,'principal_minor':str(principal),'charges_minor':str(charges),'total_due_minor':str(total),'paid_minor':str(paid),'outstanding_minor':str(outstanding),'settled':outstanding==0,'installment_allocation':schedule})
 require(outstanding_total<=9223372036854775807,'amount_exceeds_storage_limit')
 return {'loans':rows,'total_outstanding_minor':str(outstanding_total),'charges_source':'caller_supplied','interest_inferred':False,'payment_evidence_verified':False,'ledger_posted':False,'payroll_deduction_scheduled':False}


def sourcing_analysis(op,v):
 """Bounded supplied-data sourcing calculations; no award, PO, sanction or vendor mutation."""
 import re
 if op=='procurement.rfq':
  dfields(v,['as_of','currency','required','quotes']);today=ddate(v['as_of']);require(type(v['currency']) is str and re.fullmatch('[A-Z]{3}',v['currency']),'invalid_currency')
  basket={}
  for row in drows(v['required'],200):
   dfields(row,['item','quantity']);item=did(row['item']);require(item not in basket,'duplicate_item');basket[item]=dqty(row['quantity'],1)
  require(basket,'basket_required')
  seen=set();quotes=[]
  for row in drows(v['quotes'],100):
   dfields(row,['vendor','submitted_on','valid_until','tax_bps','lines','shipping_minor','discount_minor','shipping_taxable']);vendor=did(row['vendor']);require(vendor not in seen,'duplicate_vendor');seen.add(vendor)
   submitted=ddate(row['submitted_on']);valid=ddate(row['valid_until']);require(submitted<=valid,'invalid_validity_window');require(submitted<=today,'future_submission_date');require(valid>=today,'expired_quote_validity')
   rate=dqty(row['tax_bps'],0,10000);units={}
   for line in drows(row['lines'],200):
    dfields(line,['item','unit_minor']);item=did(line['item']);require(item not in units,'duplicate_quote_item');units[item]=dmoney(line['unit_minor'])
   require(set(units)==set(basket),'quote_basket_mismatch')
   subtotal=sum(unit*basket[item] for item,unit in units.items());require(subtotal<=9223372036854775807,'amount_exceeds_storage_limit')
   discount=dmoney(row['discount_minor']);shipping=dmoney(row['shipping_minor']);require(discount<=subtotal,'discount_exceeds_subtotal');net=subtotal-discount+shipping;require(type(row['shipping_taxable']) is bool,'explicit_shipping_tax_policy_required');tax_base=subtotal-discount+(shipping if row['shipping_taxable'] else 0);tax=(tax_base*rate+5000)//10000;total=net+tax
   require(total<=9223372036854775807,'amount_exceeds_storage_limit')
   quotes.append({'vendor':vendor,'subtotal_minor':str(subtotal),'discount_minor':str(discount),'shipping_minor':str(shipping),'net_before_tax_minor':str(net),'tax_base_minor':str(tax_base),'shipping_taxable':row['shipping_taxable'],'tax_minor':str(tax),'total_minor':str(total),'valid_until':valid.isoformat(),'tax_bps':rate})
  require(quotes,'quotes_required');quotes.sort(key=lambda q:(int(q['total_minor']),q['vendor']));best=int(quotes[0]['total_minor'])
  for quote in quotes:quote['delta_to_lowest_minor']=str(int(quote['total_minor'])-best)
  lowest=[quote['vendor'] for quote in quotes if int(quote['total_minor'])==best]
  return {'as_of':today.isoformat(),'currency':v['currency'],'basket_items':len(basket),'total_required_units':sum(basket.values()),'quotes':quotes,'lowest_total_minor':str(best),'lowest_total_vendors':lowest,'tied_lowest_quotes':len(lowest)>1,'quote_validity_policy':'valid_until_inclusive_of_as_of','rounding':'tax_half_up_on_net_before_tax','fx_conversion_applied':False,'supplier_awarded':False,'purchase_order_created':False,'tax_rate_verified':False}
 dfields(v,['as_of','weights','records']);today=ddate(v['as_of'])
 metrics={'on_time_delivery','quantity_accuracy','quality_acceptance'};weights={}
 for row in drows(v['weights'],3):
  dfields(row,['metric','weight_bps']);metric=did(row['metric']);require(metric in metrics,'invalid_metric');require(metric not in weights,'duplicate_metric');weights[metric]=dqty(row['weight_bps'],0,10000)
 require(set(weights)==metrics and sum(weights.values())==10000,'weights_must_sum_to_10000')
 seen=set();vendors={}
 for row in drows(v['records'],1000):
  dfields(row,['id','vendor','ordered_on','promised_on','delivered_on','ordered_quantity','delivered_quantity','accepted_quantity','rejected_quantity']);key=did(row['id']);require(key not in seen,'duplicate_record');seen.add(key)
  vendor=did(row['vendor']);ordered=ddate(row['ordered_on']);promised=ddate(row['promised_on']);require(ordered<=promised,'invalid_promised_date');require(ordered<=today,'future_order_date')
  ordered_units=dqty(row['ordered_quantity'],1);delivered_units=dqty(row['delivered_quantity']);accepted=dqty(row['accepted_quantity']);rejected=dqty(row['rejected_quantity'])
  if row['delivered_on'] is None:require(delivered_units==accepted==rejected==0,'invalid_open_order')
  else:
   delivered=ddate(row['delivered_on']);require(ordered<=delivered<=today,'invalid_delivery_date');require(delivered_units>=1 and accepted+rejected==delivered_units,'invalid_inspection_balance')
  stat=vendors.setdefault(vendor,{'orders':0,'delivered':0,'on_time':0,'exact':0,'ordered_units':0,'delivered_units':0,'accepted_units':0,'rejected_units':0,'overdue_open':0});stat['orders']+=1;stat['ordered_units']+=ordered_units
  if row['delivered_on'] is None and promised<today:stat['overdue_open']+=1
  if row['delivered_on'] is not None:
   stat['delivered']+=1;stat['on_time']+=delivered<=promised;stat['exact']+=delivered_units==ordered_units;stat['delivered_units']+=delivered_units;stat['accepted_units']+=accepted;stat['rejected_units']+=rejected
 rows=[];unscored=[]
 for vendor in sorted(vendors):
  stat=vendors[vendor];count=stat['delivered']
  if count==0:unscored.append({'vendor':vendor,'order_count':stat['orders'],'delivered_samples':0,'score_bps':None,'reason':'zero_delivered_samples','open_orders':stat['orders'],'overdue_open_orders':stat['overdue_open'],'delivery_sample_coverage_bps':0});continue
  on_time=(stat['on_time']*10000+count//2)//count;exact=(stat['exact']*10000+count//2)//count;units=stat['delivered_units'];quality=(stat['accepted_units']*10000+units//2)//units
  score=(weights['on_time_delivery']*on_time+weights['quantity_accuracy']*exact+weights['quality_acceptance']*quality+5000)//10000
  rows.append({'vendor':vendor,'order_count':stat['orders'],'delivered_samples':count,'open_orders':stat['orders']-count,'overdue_open_orders':stat['overdue_open'],'delivery_sample_coverage_bps':(count*10000+stat['orders']//2)//stat['orders'],'on_time_samples':stat['on_time'],'exact_quantity_samples':stat['exact'],'ordered_units':stat['ordered_units'],'delivered_units':units,'accepted_units':stat['accepted_units'],'rejected_units':stat['rejected_units'],'on_time_delivery_bps':on_time,'quantity_accuracy_bps':exact,'quality_acceptance_bps':quality,'score_bps':score})
 rows.sort(key=lambda r:(-r['score_bps'],r['vendor']));top=rows[0]['score_bps'] if rows else None;top_vendors=[r['vendor'] for r in rows if r['score_bps']==top] if rows else []
 return {'as_of':today.isoformat(),'weights':{metric:weights[metric] for metric in sorted(weights)},'vendors':rows,'unscored_vendors':unscored,'top_score_bps':top,'top_vendors':top_vendors,'tied_top_vendors':len(top_vendors)>1,'sample_policy':'delivered_orders_only_no_rating_with_zero_samples','quantity_policy':'exact_quantity_match','rounding':'per_metric_half_up','weights_source':'caller_supplied','quality_inferred':False,'rating_labels_inferred':False,'sanctions_applied':False,'vendor_records_changed':False}
