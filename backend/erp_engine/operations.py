"""Explicit per-operation boundaries. Catalog entries are never blanket-activated."""
from decimal import Decimal
from .planner import AVAILABLE,BY_ID,DEPS,LABELS,capabilities,detect,plan,require,ERPError
from .schema import generate_schema
from .generator import generate
from .domain_analysis import DOMAIN_OPS,run_domain
GENERATOR_OPS={'builder.scaffold.project','builder.gen.frontend','builder.gen.backend','builder.gen.database','builder.gen.api','builder.gen.auth','builder.gen.dashboard','builder.gen.menu','builder.gen.workflow','builder.gen.report'}
ANALYSIS_OPS=DOMAIN_OPS|{'pos.transaction','finance.journal.entry','finance.general.ledger','finance.invoice','inventory.stock.management','finance.reconciliation','finance.depreciation','finance.budgeting','finance.multi.currency','report.customer.aging','report.vendor.aging'}
def money(value):
 import re
 require(type(value) is str and re.fullmatch(r'0|[1-9][0-9]{0,17}',value),'invalid_money');return int(value)
def analyze(operation,v):
 if operation=='pos.transaction':
  require(set(v)=={'lines','tax_bps','tendered_minor'},'invalid_input');invoice=analyze('finance.invoice',{'lines':v['lines'],'tax_bps':v['tax_bps']})['result'];tendered=money(v['tendered_minor']);require(tendered>=int(invoice['total_minor']),'insufficient_cash_tender');result={**invoice,'tendered_minor':str(tendered),'change_minor':str(tendered-int(invoice['total_minor'])),'cash_received_verified':False,'sale_recorded':False}
 elif operation in DOMAIN_OPS:result=run_domain(operation,v)
 elif operation in {'finance.reconciliation','finance.depreciation','finance.budgeting','finance.multi.currency','report.customer.aging','report.vendor.aging'}:
  result=advanced_analysis(operation,v)
 elif operation=='finance.invoice':
  require(set(v)<={'lines','tax_bps'} and type(v.get('lines')) is list and 1<=len(v['lines'])<=100,'invalid_input');rate=v.get('tax_bps');require(type(rate) is int and 0<=rate<=10000,'explicit_tax_rate_required');subtotal=0
  for line in v['lines']:
   require(type(line) is dict and set(line)=={'unit_minor','quantity'} and type(line['quantity']) is int and 1<=line['quantity']<=1000000,'invalid_line');subtotal+=money(line['unit_minor'])*line['quantity']
  tax=(subtotal*rate+5000)//10000;require(subtotal+tax<=9223372036854775807,'amount_exceeds_storage_limit')
  result={'subtotal_minor':str(subtotal),'tax_minor':str(tax),'total_minor':str(subtotal+tax),'tax_rate_legally_verified':False}
 elif operation in ('finance.journal.entry','finance.general.ledger'):
  require(set(v)=={'lines'} and type(v['lines']) is list and 2<=len(v['lines'])<=1000,'invalid_input');balances={};debit=credit=0
  for line in v['lines']:
   require(type(line) is dict and set(line)=={'account','debit_minor','credit_minor'} and type(line['account']) is str and 1<=len(line['account'])<=30,'invalid_line');d=money(line['debit_minor']);c=money(line['credit_minor']);require((d==0)!=(c==0),'invalid_journal_line');debit+=d;credit+=c;balances[line['account']]=balances.get(line['account'],0)+d-c
  require(debit==credit and debit>0,'unbalanced_journal');require(debit<=9223372036854775807,'amount_exceeds_storage_limit');result={'balanced':True,'debit_minor':str(debit),'credit_minor':str(credit),'account_net_minor':{k:str(n) for k,n in balances.items()}}
 else:
  require(set(v)=={'initial','movements'} and type(v['initial']) is int and 0<=v['initial']<=1000000000 and type(v['movements']) is list and len(v['movements'])<=1000,'invalid_input');stock=v['initial']
  for movement in v['movements']:
   require(type(movement) is int and -1000000<=movement<=1000000,'invalid_movement');stock+=movement;require(0<=stock<=1000000000,'negative_or_excess_stock')
  result={'final_quantity':stock}
 return {'status':'analyzed_input','result':result,'scope':'caller_supplied_data_only','live_database_access':False,'posted':False}
def validate_request(v,check_unicode=True):
 """Bound all pure entrypoints consistently; never stringify raw input into errors."""
 import json,math
 require(type(v) is dict,'invalid_input')
 stack=[(v,0)];nodes=0
 while stack:
  item,depth=stack.pop();nodes+=1
  require(depth<=16 and nodes<=200000,'input_complexity_limit')
  kind=type(item)
  require(kind in (dict,list,str,int,float,bool,type(None)),'invalid_json_value')
  if kind is dict:
   require(all(type(k) is str for k in item),'invalid_json_key')
   stack.extend((k,depth+1) for k in item);stack.extend((x,depth+1) for x in item.values())
  elif kind is list:stack.extend((x,depth+1) for x in item)
  elif kind is str and check_unicode:require(not any(0xD800<=ord(c)<=0xDFFF for c in item),'invalid_unicode')
  elif kind is float:require(math.isfinite(item),'invalid_json_number')
  elif kind is int:require(item.bit_length()<=256,'input_integer_limit')
 raw=json.dumps(v,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode()
 require(len(raw)<=1048576,'input_too_large')
def invoke(operation,v):
 require(type(operation) is str and type(v) is dict,'invalid_input');operation=operation.removeprefix('erp_all.');validate_request(v,operation not in ANALYSIS_OPS)
 if operation in ('catalog','capabilities'):
  require(not v,'unknown_fields');return capabilities()
 if operation in ANALYSIS_OPS:return analyze(operation,v)
 require(set(v)<={'spec','company','industry','optional','exclude','branding','components'},'unknown_fields')
 require('spec' in v,'invalid_spec')
 if operation in ('plan','analyze'):return plan(**v)
 if operation in ('generate',*GENERATOR_OPS):return generate(**v)
 require(operation in AVAILABLE,'skill_not_implemented')
 p=plan(**v)
 if p['status']!='planned':return p
 focused=planning_result(operation,p)
 if operation.startswith('industry.'):return {'status':'analyzed','result':p['detection'],'terminology':p['theme']['terminology'],'operation_result':focused}
 if operation.startswith('module.'):return {'status':'planned','result':p['modules'],'operation_result':focused}
 if operation.startswith('theme.'):return {'status':'planned','result':p['theme'],'operation_result':focused}
 if operation.startswith('schema.'):return {'status':'source_generated','result':generate_schema(p),'migration_executed':False,'operation_result':focused}
 raise ERPError('skill_not_implemented')

def advanced_analysis(operation,v):
 """Bounded deterministic calculations. Never infer bank settlement or tax rules."""
 if operation=='finance.reconciliation':
  require(set(v)=={'bank','ledger'},'invalid_input');groups={};seen={}
  for side in ('bank','ledger'):
   rows=v[side];require(type(rows) is list and len(rows)<=1000,'invalid_rows');seen[side]=set()
   for row in rows:
    require(type(row) is dict and set(row)=={'id','reference','amount_minor','direction','currency'},'invalid_row')
    require(type(row['id']) is str and 1<=len(row['id'])<=100 and row['id'] not in seen[side],'duplicate_or_invalid_id');seen[side].add(row['id'])
    require(type(row['reference']) is str and 1<=len(row['reference'])<=100 and row['direction'] in ('in','out'),'invalid_reference')
    require(type(row['currency']) is str and __import__('re').fullmatch('[A-Z]{3}',row['currency']),'invalid_currency')
    key=(row['reference'],money(row['amount_minor']),row['direction'],row['currency']);groups.setdefault(key,{'bank':[],'ledger':[]})[side].append(row['id'])
  matches=[];ambiguous=[];unmatched={'bank':[],'ledger':[]}
  for key,sides in sorted(groups.items()):
   if len(sides['bank'])==len(sides['ledger'])==1:matches.append({'bank_id':sides['bank'][0],'ledger_id':sides['ledger'][0]})
   elif sides['bank'] and sides['ledger']:ambiguous.append(sides)
   else:
    for side in unmatched:unmatched[side].extend(sides[side])
  return {'matches':matches,'ambiguous':ambiguous,'unmatched':unmatched,'settlement_verified':False,'matching_policy':'exact_reference_amount_direction_currency_unique_only'}
 if operation=='finance.depreciation':
  require(set(v)<={'cost_minor','residual_minor','periods','method','rate_bps'} and {'cost_minor','residual_minor','periods','method'}<=set(v),'invalid_input')
  cost=money(v['cost_minor']);residual=money(v['residual_minor']);periods=v['periods'];require(cost>=residual and type(periods) is int and 1<=periods<=1200,'invalid_asset')
  require(v['method'] in ('straight_line','declining'),'invalid_method');basis=cost-residual;book=cost;schedule=[]
  if v['method']=='declining':require(type(v.get('rate_bps')) is int and 1<=v['rate_bps']<=10000,'explicit_rate_required')
  else:require('rate_bps' not in v,'unexpected_rate')
  for i in range(periods):
   amount=(basis//periods+(1 if i<basis%periods else 0)) if v['method']=='straight_line' else min(book-residual,(book*v['rate_bps']+5000)//10000)
   if i==periods-1:amount=book-residual
   book-=amount;schedule.append({'period':i+1,'depreciation_minor':str(amount),'book_minor':str(book)})
  return {'schedule':schedule,'total_depreciation_minor':str(basis),'policy':'final_period_adjustment_to_residual','tax_rule_verified':False}
 if operation=='finance.budgeting':
  require(set(v)=={'rows'} and type(v['rows']) is list and len(v['rows'])<=1000,'invalid_input');rows=[];seen=set();budget=actual=0
  for row in v['rows']:
   require(type(row) is dict and set(row)=={'account','budget_minor','actual_minor'} and type(row['account']) is str and 1<=len(row['account'])<=100 and row['account'] not in seen,'invalid_account');seen.add(row['account'])
   b=money(row['budget_minor']);a=money(row['actual_minor']);budget+=b;actual+=a;rows.append({'account':row['account'],'variance_minor':str(a-b),'over_budget':a>b})
  return {'rows':rows,'budget_minor':str(budget),'actual_minor':str(actual),'variance_minor':str(actual-budget),'variance_sign':'actual_minus_budget'}
 if operation=='finance.multi.currency':
  require(set(v)=={'amount_minor','rate_numerator','rate_denominator','source_decimals','target_decimals'},'invalid_input');amount=money(v['amount_minor']);numerator=money(v['rate_numerator']);denominator=money(v['rate_denominator']);require(numerator>0 and denominator>0,'invalid_exchange_rate')
  for k in ('source_decimals','target_decimals'):require(type(v[k]) is int and 0<=v[k]<=8,'invalid_decimals')
  n=amount*numerator*10**v['target_decimals'];d=denominator*10**v['source_decimals'];converted=(n+d//2)//d
  require(converted<=9223372036854775807,'amount_exceeds_storage_limit');return {'converted_minor':str(converted),'rate_source':'caller_supplied_not_market_verified','rounding':'half_up'}
 from datetime import date
 require(set(v)=={'as_of','invoices'} and type(v['invoices']) is list and len(v['invoices'])<=1000,'invalid_input')
 def parsed(value):
  require(type(value) is str and __import__('re').fullmatch(r'\d{4}-\d{2}-\d{2}',value),'invalid_date')
  try:return date.fromisoformat(value)
  except ValueError:raise ERPError('invalid_date')
 today=parsed(v['as_of']);buckets={k:0 for k in ['not_due','1_30','31_60','61_90','over_90']};seen=set();rows=[]
 for inv in v['invoices']:
  require(type(inv) is dict and set(inv)=={'id','due','total_minor','paid_minor'} and type(inv['id']) is str and 1<=len(inv['id'])<=100 and inv['id'] not in seen,'invalid_invoice');seen.add(inv['id']);due=parsed(inv['due']);total=money(inv['total_minor']);paid=money(inv['paid_minor']);require(paid<=total,'overpayment');amount=total-paid;days=(today-due).days;bucket='not_due' if days<=0 else '1_30' if days<=30 else '31_60' if days<=60 else '61_90' if days<=90 else 'over_90';buckets[bucket]+=amount;rows.append({'id':inv['id'],'outstanding_minor':str(amount),'days_overdue':max(0,days),'bucket':bucket})
 return {'as_of':v['as_of'],'rows':rows,'buckets_minor':{k:str(n) for k,n in buckets.items()},'total_outstanding_minor':str(sum(buckets.values()))}


def planning_result(op,p):
 """Named planning outputs; legacy result is retained for existing API clients."""
 import copy,re
 d=p['detection'];profile=BY_ID[p['industry']];rows=p['modules'];t=p['theme']
 selected={r['id'] for r in rows};optional=[m for m in profile['optional_modules'] if m not in selected]
 values={
 'industry.analyze.spec':{'industry':p['industry'],'candidates':d['candidates'],'counts':d['counts']},
 'industry.detect.keywords':{'matches':[{'industry':r['industry'],'keywords':r['matched_keywords']} for r in d['candidates']],'negated_industries':d['negated_industries']},
 'industry.classify':{'industry':p['industry'],'name':profile['name'],'source_number':profile['source_number']},
 'industry.detect.context':{'industry':p['industry'],'counts':d['counts'],'region':d['region'],'evidence':d['candidates']},
 'industry.confidence.score':{'score':d['confidence'],'kind':d['confidence_kind'],'clarification_required':d['clarification_required']},
 'industry.clarify':{'required':d['clarification_required'],'question':d['clarification_question']},
 'industry.scale.assess':{'scale':d['scale'],'counts':d['counts'],'kind':d['scale_kind']},
 'industry.tier.set':{'proposed_tier':d['scale'],'kind':d['scale_kind'],'commercial_tier_assigned':False},
 'industry.profile.build':{'profile':copy.deepcopy(profile)},
 'industry.region.detect':{'region':d['region'],'geocoded':False},
 'industry.terminology.extract':{'terms':t['terminology']},
 'module.mapping.industry':{'industry':p['industry'],'required':profile['required_modules'],'optional':profile['optional_modules'],'selected':sorted(selected)},
 'module.required.select':{'modules':[r for r in rows if r['required']]},
 'module.optional.suggest':{'suggestions':[{'id':m,'name':LABELS.get(m,m.replace('_',' ').title())} for m in optional],'automatically_selected':False},
 'module.exclude.irrelevant':{'excluded':[m for m in sorted(DEPS) if m not in selected and m not in optional],'retained':sorted(selected)},
 'module.priority.rank':{'ranking':[{'id':r['id'],'priority':i+1,'reason':r['selection_reason']} for i,r in enumerate(rows)],'policy':'dependencies_before_dependents_then_declared_order'},
 'module.dependency.resolve':{'order':[r['id'] for r in rows],'edges':[{'module':r['id'],'requires':x} for r in rows for x in r['dependencies']]},
 'module.conflict.detect':{'conflicts':[],'checked':['optional_exclude_overlap','required_exclusion','dependency_exclusion','dependency_cycles'],'invalid_selection_policy':'reject_with_structured_error'},
 'module.scale.adjust':{'scale':d['scale'],'counts':d['counts'],'modules':[r['id'] for r in rows],'automatic_module_changes':False,'policy':'explicit_optional_selection_only'},
 'module.finalize':{'modules':rows,'dependency_complete':True},
 'theme.color.industry':{'primary':t['primary'],'primary_text':t['primary_text'],'background':t['background'],'sidebar':t['sidebar']},
 'theme.layout.industry':{'layout':t['layout'],'direction':profile['theme_direction']},
 'theme.typography.industry':{'font':t['font']},
 'theme.terminology.apply':{'terms':t['terminology'],'industry':p['industry']},
 'theme.responsive.optimize':{'responsive':t['responsive'],'full_accessibility_test_required':True},
 'theme.branding.apply':{'branding':t['branding'],'applied_to':'generated_plan_and_source_only'},
 'theme.components.customize':{'components':t['components'],'applied_to':'generated_plan_and_source_only'},
 'theme.dashboard.layout':{'layout':t['layout'],'sections':[{'id':r['id'],'name':r['name']} for r in rows]},
 'theme.navigation.style':{'layout':t['layout'],'sidebar':t['sidebar'],'mobile_breakpoint':t['responsive']['sidebar_breakpoint']},
 }
 if op.startswith('schema.'):
  schema=generate_schema(p);prisma=schema['prisma'];tables=[];relations=[];indexes=[];constraints=[];fields=[]
  for name,body in re.findall(r'model (\w+) \{(.*?)\n\}',prisma,re.S):
   tables.append(name)
   for raw in body.splitlines():
    line=raw.strip()
    if not line:continue
    if line.startswith('@@index'):indexes.append({'entity':name,'declaration':line})
    elif line.startswith('@@unique'):constraints.append({'entity':name,'declaration':line})
    elif '@relation' in line:relations.append({'entity':name,'declaration':line})
    elif not line.startswith('@@'):
     parts=line.split(None,2)
     if len(parts)>=2:fields.append({'entity':name,'name':parts[0],'type':parts[1],'declaration':line})
  values.update({
   'schema.entity.identify':{'entities':tables},
   'schema.relation.design':{'relations':relations},
   'schema.field.define':{'fields':fields},
   'schema.data_type.select':{'fields':[{'entity':f['entity'],'name':f['name'],'type':f['type']} for f in fields]},
   'schema.constraint.add':{'unique_constraints':constraints,'check_constraints_sql':schema['tenant_policies_sql'],'executed':False},
   'schema.index.optimize':{'declared_indexes':indexes,'policy':'tenant_createdAt_and_composite_foreign_key_indexes_skip_unique_prefix','workload_analyzed':False,'performance_measured':False},
   'schema.audit.column':{'fields':[f for f in fields if f['name'] in ('createdAt','updatedAt','deletedAt')]},
   'schema.soft.delete':{'fields':[f for f in fields if f['name']=='deletedAt'],'hard_delete_executed':False},
   'schema.multi.tenant':{'tenant_foreign_keys':[r for r in relations if 'tenantId' in r['declaration']],'rls_sql':schema['tenant_policies_sql'],'executed':False},
   'schema.migration.generate':{'prisma':prisma,'tenant_policies_sql':schema['tenant_policies_sql'],'executed':False},
   'schema.er.diagram':{'mermaid':schema['erd']},
   'schema.document.generate':{'entities':tables,'fields':fields,'relations':relations,'indexes':indexes,'constraints':constraints},
  })
 require(op in values,'skill_not_implemented')
 return {'operation':op,'scope':'planning_or_source_only','data':values[op],'live_business_mutation':False}
