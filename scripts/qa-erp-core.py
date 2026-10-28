#!/usr/bin/env python3
"""Run on engine host: isolated PostgreSQL, generated Nest API, synthetic tenants.
All processes/containers created here are owned fixtures and removed in finally.
"""
import concurrent.futures,json,os,secrets,subprocess,time,urllib.request,urllib.error,uuid
from pathlib import Path
ROOT=Path('/opt/harmonika-ai/erp-build-qa');name='harmonika-erp-qa-'+uuid.uuid4().hex[:8];owner=secrets.token_hex(20);app_password=secrets.token_hex(20);jwt=secrets.token_hex(32);password=secrets.token_urlsafe(18);port=18432;api=None;results=[]
def check(ok,label):
 if not ok:raise AssertionError(label)
 results.append(label)
def run(args,env=None,input=None):
 r=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,input=input,text=True,timeout=120)
 if r.returncode:raise RuntimeError('fixture_command_failed:'+str(args[:3])+':'+r.stderr[-300:])
 return r.stdout
baseenv={**os.environ,'JWT_SECRET':jwt,'APP_ORIGIN':'http://127.0.0.1:18400','COOKIE_SECURE':'false','PORT':'18401','LISTEN_HOST':'127.0.0.1'}
def call(path,body=None,auth=None,key=None,method=None):
 h={'Content-Type':'application/json','Origin':baseenv['APP_ORIGIN']}
 if auth:h.update({'Cookie':'erp_session='+auth['cookie'],'X-CSRF-Token':auth['csrf']})
 if body is not None:h['Idempotency-Key']=key or uuid.uuid4().hex
 req=urllib.request.Request('http://127.0.0.1:18401/'+path,data=json.dumps(body).encode() if body is not None else None,headers=h,method=method)
 try:
  with urllib.request.urlopen(req,timeout=15) as r:return r.status,json.loads(r.read()),r.headers
 except urllib.error.HTTPError as e:return e.code,json.loads(e.read()),e.headers
try:
 run(['docker','run','-d','--rm','--name',name,'--memory','512m','--cpus','1','--pids-limit','128','-p','127.0.0.1:'+str(port)+':5432','-e','POSTGRES_DB=erp','-e','POSTGRES_USER=erp_owner','-e','POSTGRES_PASSWORD='+owner,'postgres:16.11'])
 for _ in range(45):
  p=subprocess.run(['docker','exec',name,'pg_isready','-h','127.0.0.1','-U','erp_owner','-d','erp'],capture_output=True)
  if p.returncode==0:break
  time.sleep(.5)
 else:raise RuntimeError('fixture_database_not_ready')
 sql="CREATE ROLE erp_app LOGIN PASSWORD '"+app_password+"' NOSUPERUSER NOBYPASSRLS;GRANT CONNECT ON DATABASE erp TO erp_app;GRANT USAGE ON SCHEMA public TO erp_app;ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO erp_app;"
 run(['docker','exec','-i',name,'psql','-v','ON_ERROR_STOP=1','-U','erp_owner','-d','erp'],input=sql)
 migration={**baseenv,'DATABASE_URL':'postgresql://erp_owner:'+owner+'@127.0.0.1:'+str(port)+'/erp'}
 run(['npm','run','db:init','-w','apps/api'],env=migration)
 for tenant in ['qa-a','qa-b']:
  run(['npm','run','bootstrap','-w','apps/api'],env={**migration,'ALLOW_BOOTSTRAP':'true','OWNER_EMAIL':'owner@'+tenant+'.invalid','OWNER_PASSWORD':password,'OWNER_TENANT_SLUG':tenant,'OWNER_COMPANY':'Synthetic '+tenant})
 api=subprocess.Popen(['node','apps/api/dist/main.js'],cwd=ROOT,env={**baseenv,'DATABASE_URL':'postgresql://erp_app:'+app_password+'@127.0.0.1:'+str(port)+'/erp'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 for _ in range(50):
  try:
   if call('health')[0]==200:break
  except OSError:pass
  time.sleep(.2)
 else:raise RuntimeError('fixture_api_not_ready')
 check(call('customers')[0]==401,'anonymous_access_denied')
 sessions=[]
 for tenant in ['qa-a','qa-b']:
  code,v,headers=call('auth/login',{'tenant':tenant,'email':'owner@'+tenant+'.invalid','password':password});check(code==201,'login_'+tenant)
  cookie=headers['Set-Cookie'].split(';')[0].split('=',1)[1];sessions.append({'cookie':cookie,'csrf':v['csrf']})
 a,b=sessions
 code,customer,_=call('customers',{'name':'Synthetic A'},a);check(code==201,'customer_create')
 check(len(call('customers',auth=b)[1])==0,'tenant_data_isolation')
 check(call('customers',{'name':'Bad','tenantId':'forged'},a)[0]==422,'forged_tenant_body_denied')
 bad={**a,'csrf':'wrong'};check(call('customers',{'name':'Bad'},bad)[0]==403,'csrf_rejected')
 code,product,_=call('products',{'sku':'QA-1','name':'Synthetic Product','unitMinor':'12500'},a);check(code==201,'product_create')
 stockkey=uuid.uuid4().hex
 check(call('products/'+product['id']+'/stock',{'delta':10,'reason':'Synthetic opening','revision':0},a,stockkey)[0]==201,'stock_add')
 check(call('products/'+product['id']+'/stock',{'delta':10,'reason':'Synthetic opening','revision':0},a,stockkey)[0]==201,'stock_replay_same_result')
 check(call('products',auth=a)[1][0]['quantity']==10,'stock_replay_no_duplicate')
 check(call('products/'+product['id']+'/stock',{'delta':-11,'reason':'Synthetic issue','revision':1},a)[0]==409,'negative_stock_blocked')
 def compete(i):return call('products/'+product['id']+'/stock',{'delta':1,'reason':'Synthetic race','revision':1},a)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:codes=list(pool.map(compete,range(2)))
 check(sorted(codes)==[201,409],'stock_concurrency_one_writer')
 check(call('products',auth=a)[1][0]['quantity']==11,'stock_quantity_after_race')
 code,invoice,_=call('invoices',{'customerId':customer['id'],'lines':[{'productId':product['id'],'quantity':2}],'taxBps':0},a);check(code==201 and invoice.get('totalMinor')=='25000','invoice_server_calculated')
 check(call('invoices',{'customerId':customer['id'],'lines':[{'productId':product['id'],'quantity':2}],'taxBps':0},b)[0]==404,'cross_tenant_invoice_denied')
 check(call('invoices/'+invoice['id']+'/payment',{'amountMinor':'30000','verified':True},a)[0]==409,'overpayment_blocked')
 check(call('invoices/'+invoice['id']+'/payment',{'amountMinor':'25000','verified':False},a)[0]==422,'unverified_payment_blocked')
 paykey=uuid.uuid4().hex
 check(call('invoices/'+invoice['id']+'/payment',{'amountMinor':'25000','verified':True},a,paykey)[0]==201,'manual_verified_payment')
 check(call('invoices/'+invoice['id']+'/payment',{'amountMinor':'25000','verified':True},a,paykey)[0]==201,'payment_replay')
 row=call('invoices',auth=a)[1][0];check(row['status']=='PAID' and len(row['payments'])==1,'payment_replay_no_duplicate')
 for code,kind in [('1000','asset'),('4000','revenue')]:check(call('ledger/accounts',{'code':code,'name':'Synthetic account','type':kind},a)[0]==201,'account_'+code)
 lines=[{'accountCode':'1000','debitMinor':'100','creditMinor':'0'},{'accountCode':'4000','debitMinor':'0','creditMinor':'100'}]
 check(call('ledger/journals',{'memo':'Synthetic journal','period':'2026-01','lines':lines},a)[0]==201,'balanced_journal_posted')
 lines[1]['creditMinor']='99';check(call('ledger/journals',{'memo':'Invalid journal','period':'2026-01','lines':lines},a)[0]==422,'unbalanced_journal_denied')
 check(len(call('ledger',auth=b)[1])==0,'ledger_tenant_isolation')
 # Commercial workflow: frozen prices, exact states, tenant boundaries and replay.
 code,quote,_=call('sales/documents',{'kind':'QUOTE','customerId':customer['id'],'lines':[{'productId':product['id'],'quantity':2}],'taxBps':0},a);check(code==201 and quote.get('totalMinor')=='25000','quotation_create')
 check(call('sales/documents/'+quote['id']+'/confirm',{'revision':0,'confirmed':True},b)[0]==404,'quotation_cross_tenant_denied')
 check(call('sales/documents/'+quote['id']+'/invoice',{'revision':0,'confirmed':True},a)[0]==409,'quotation_cannot_invoice_directly')
 check(call('sales/documents/'+quote['id']+'/confirm',{'revision':0,'confirmed':True},a)[0]==201,'quotation_accept')
 convertkey=uuid.uuid4().hex
 code,order,_=call('sales/documents/'+quote['id']+'/convert',{'revision':1,'confirmed':True},a,convertkey);check(code==201 and order.get('orderId'),'quote_convert_to_order')
 check(call('sales/documents/'+quote['id']+'/convert',{'revision':1,'confirmed':True},a,convertkey)[1].get('orderId')==order['orderId'],'quote_conversion_replay')
 check(call('sales/documents/'+quote['id']+'/convert',{'revision':1,'confirmed':True},a)[0]==409,'quote_conversion_duplicate_denied')
 productNow=call('products',auth=a)[1][0]
 check(call('products/'+product['id'],{'name':'Updated synthetic','sku':'QA-1','unitMinor':'20000','revision':productNow['revision']},a,method='PATCH')[0]==200,'product_edit_cas')
 check(call('products/'+product['id'],{'name':'Stale','sku':'QA-1','unitMinor':'1','revision':productNow['revision']},a,method='PATCH')[0]==409,'product_stale_edit_denied')
 code,issued,_=call('sales/documents/'+order['orderId']+'/invoice',{'revision':0,'confirmed':True},a);check(code==201 and issued.get('invoiceId'),'order_issue_invoice')
 inv=next(x for x in call('invoices',auth=a)[1] if x['id']==issued['invoiceId']);check(inv['totalMinor']=='25000','quote_price_frozen_after_product_change')
 fulfillkey=uuid.uuid4().hex
 check(call('sales/documents/'+order['orderId']+'/fulfill',{'revision':1,'confirmed':True},a,fulfillkey)[0]==201,'order_fulfillment')
 check(call('sales/documents/'+order['orderId']+'/fulfill',{'revision':1,'confirmed':True},a,fulfillkey)[0]==201,'fulfillment_replay')
 check(call('products',auth=a)[1][0]['quantity']==9,'fulfillment_stock_once')
 check(call('products/'+product['id'],{'revision':3},a,method='DELETE')[0]==409,'product_with_stock_delete_denied')
 code,report,_=call('reports/financial?period=2026-01',auth=a);check(code==200 and report['profitLoss']['profitMinor']=='100' and report['balanceSheet']['imbalanceMinor']=='0','financial_report_exact')
 journal=call('ledger',auth=a)[1][0]
 code,rev,_=call('ledger/journals/'+journal['id']+'/reverse',{'period':'2026-02','memo':'Synthetic reversal','confirmed':True},a);check(code==201,'journal_reversal')
 check(call('ledger/journals/'+journal['id']+'/reverse',{'period':'2026-02','memo':'Duplicate reversal','confirmed':True},a)[0]==409,'duplicate_reversal_denied')
 check(call('reports/financial?period=2026-02',auth=a)[1]['balanceSheet']['assetsMinor']=='0','reversal_financial_effect')
 check(call('ledger/periods/close',{'period':'2026-01','confirmed':True},a)[0]==201,'fiscal_period_closed')
 lines[1]['creditMinor']='100'
 check(call('ledger/journals',{'memo':'Closed period post','period':'2026-01','lines':lines},a)[0]==409,'closed_period_post_denied')
 check(call('ledger/journals/'+journal['id']+'/reverse',{'period':'2026-01','memo':'Closed reversal','confirmed':True},a)[0]==409,'closed_period_reversal_denied')
 check(call('customers/'+customer['id'],{'name':'Updated synthetic','revision':0},a,method='PATCH')[0]==200,'customer_edit')
 check(call('customers/'+customer['id'],{'revision':1},a,method='DELETE')[0]==409,'customer_unpaid_delete_denied')
 check(call('ledger/journals',{'memo':'Backdated after closing','period':'2025-12','lines':lines},a)[0]==409,'closed_snapshot_cannot_change_by_backdating')
 code,large,_=call('sales/documents',{'kind':'ORDER','customerId':customer['id'],'lines':[{'productId':product['id'],'quantity':100}],'taxBps':0},a);check(code==201,'large_order_create')
 check(call('sales/documents/'+large['id']+'/confirm',{'revision':0,'confirmed':True},a)[0]==201,'large_order_confirm')
 check(call('sales/documents/'+large['id']+'/invoice',{'revision':1,'confirmed':True},a)[0]==201,'large_order_invoice')
 check(call('sales/documents/'+large['id']+'/fulfill',{'revision':2,'confirmed':True},a)[0]==409,'insufficient_stock_fulfillment_rollback')
 check(call('products',auth=a)[1][0]['quantity']==9,'failed_fulfillment_preserves_stock')
 code,supplier,_=call('suppliers',{'name':'Synthetic supplier'},a);check(code==201,'supplier_create')
 pv={'supplierId':supplier['id'],'lines':[{'productId':product['id'],'quantity':3,'unitMinor':'5000'}],'taxBps':0}
 check(call('purchases',pv,b)[0]==404,'purchase_cross_tenant_supplier_denied')
 code,purchase,_=call('purchases',pv,a);check(code==201 and purchase['totalMinor']=='15000','purchase_order_create')
 check(call('purchases/'+purchase['id']+'/receive',{'revision':0,'confirmed':True},a)[0]==409,'unapproved_receipt_denied')
 check(call('purchases/'+purchase['id']+'/payment',{'amountMinor':'15000','verified':True},a)[0]==404,'unreceived_purchase_payment_denied')
 check(call('purchases/'+purchase['id']+'/approve',{'revision':0,'confirmed':True},a)[0]==201,'purchase_approved')
 receiptkey=uuid.uuid4().hex
 check(call('purchases/'+purchase['id']+'/receive',{'revision':1,'confirmed':True},a,receiptkey)[0]==201,'purchase_received')
 check(call('purchases/'+purchase['id']+'/receive',{'revision':1,'confirmed':True},a,receiptkey)[0]==201,'purchase_receipt_replay')
 check(call('products',auth=a)[1][0]['quantity']==12,'purchase_stock_once')
 check(call('purchases/'+purchase['id']+'/cancel',{'revision':2,'confirmed':True},a)[0]==409,'received_purchase_cancel_denied')
 check(call('purchases/'+purchase['id']+'/payment',{'amountMinor':'15001','verified':True},a)[0]==409,'vendor_overpayment_denied')
 vendorpaykey=uuid.uuid4().hex
 check(call('purchases/'+purchase['id']+'/payment',{'amountMinor':'15000','verified':True},a,vendorpaykey)[0]==201,'vendor_verified_payment')
 check(call('purchases/'+purchase['id']+'/payment',{'amountMinor':'15000','verified':True},a,vendorpaykey)[0]==201,'vendor_payment_replay')
 check(len(call('purchases',auth=a)[1][0]['payments'])==1,'vendor_payment_once')
 code,c2,_=call('customers',{'name':'Open offer customer'},a);check(code==201,'open_offer_customer')
 code,p2,_=call('products',{'sku':'QA-EMPTY','name':'Zero stock quote','unitMinor':'100'},a);check(code==201,'zero_stock_product')
 code,q2,_=call('sales/documents',{'kind':'QUOTE','customerId':c2['id'],'lines':[{'productId':p2['id'],'quantity':1}],'taxBps':0},a);check(code==201,'open_offer_create')
 check(call('customers/'+c2['id'],{'revision':0},a,method='DELETE')[0]==409,'customer_open_offer_delete_denied')
 check(call('products/'+p2['id'],{'revision':0},a,method='DELETE')[0]==409,'product_open_offer_delete_denied')
 check(call('sales/documents/'+q2['id']+'/cancel',{'revision':0,'confirmed':True},a)[0]==201,'quote_cancel')
 check(call('products/'+p2['id'],{'revision':0},a,method='DELETE')[0]==200,'unused_product_soft_delete')
 check(call('customers/'+c2['id'],{'revision':0},a,method='DELETE')[0]==200,'unused_customer_soft_delete')
 subbody={'customerId':customer['id'],'productId':product['id'],'startMonth':'2026-01','endMonth':'2026-12','taxBps':0,'billingPolicy':'full_month'}
 check(call('isp/subscriptions',{**subbody,'billingPolicy':'assumed'},a)[0]==422,'subscription_requires_explicit_billing_policy')
 check(call('isp/subscriptions',subbody,b)[0]==404,'subscription_cross_tenant_denied')
 code,sub,_=call('isp/subscriptions',subbody,a);check(code==201 and sub['unitMinor']=='20000' and not sub['network_provisioned'],'subscription_create_no_network_action')
 check(call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2025-12','confirmed':True},a)[0]==409,'billing_before_start_denied')
 check(call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2027-01','confirmed':True},a)[0]==409,'billing_after_end_denied')
 productNow=next(x for x in call('products',auth=a)[1] if x['id']==product['id'])
 check(call('products/'+product['id'],{'name':'Price changed','sku':'QA-1','unitMinor':'30000','revision':productNow['revision']},a,method='PATCH')[0]==200,'subscription_catalog_price_change')
 code,bill,_=call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2026-01','confirmed':True},a);check(code==201 and not bill['already_billed'],'subscription_monthly_invoice')
 code,replay,_=call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2026-01','confirmed':True},a);check(code==201 and replay['invoiceId']==bill['invoiceId'] and replay['already_billed'],'subscription_same_period_different_key_no_duplicate')
 sinv=next(x for x in call('invoices',auth=a)[1] if x['id']==bill['invoiceId']);check(sinv['totalMinor']=='20000','subscription_frozen_price')
 def bill_race(i):return call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2026-02','confirmed':True},a)
 before=len(call('invoices',auth=a)[1])
 with concurrent.futures.ThreadPoolExecutor(2) as pool:raced=list(pool.map(bill_race,range(2)))
 check(all(x[0] in [201,409] for x in raced) and any(x[0]==201 for x in raced),'subscription_concurrent_billing_safe_status')
 check(len(call('invoices',auth=a)[1])==before+1,'subscription_concurrent_invoice_once')
 check(call('isp/subscriptions/'+sub['id']+'/state',{'revision':0,'status':'PAUSED','confirmed':True},a)[0]==201,'subscription_pause')
 check(call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2026-03','confirmed':True},a)[0]==409,'paused_subscription_no_invoice')
 check(call('isp/subscriptions/'+sub['id']+'/state',{'revision':0,'status':'ACTIVE','confirmed':True},a)[0]==409,'subscription_state_revision_conflict')
 check(call('isp/subscriptions/'+sub['id']+'/state',{'revision':1,'status':'ACTIVE','confirmed':True},a)[0]==201,'subscription_resume')
 check(call('isp/subscriptions/'+sub['id']+'/state',{'revision':2,'status':'CANCELLED','confirmed':True},a)[0]==201,'subscription_cancel')
 check(call('isp/subscriptions/'+sub['id']+'/state',{'revision':3,'status':'ACTIVE','confirmed':True},a)[0]==409,'cancelled_subscription_no_implicit_reactivation')
 code,ticket,_=call('isp/tickets',{'customerId':customer['id'],'subject':'Synthetic LOS','description':'Synthetic modem indicator','priority':'urgent'},a);check(code==201 and ticket['status']=='OPEN','service_ticket_create')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},b)[0]==404,'ticket_cross_tenant_denied')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':0,'status':'CLOSED','confirmed':True},a)[0]==409,'ticket_cannot_skip_resolution')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':0,'status':'IN_PROGRESS','assignedUserId':'11111111-1111-4111-8111-111111111111','confirmed':True},a)[0]==404,'ticket_unknown_assignee_denied')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},a)[0]==201,'ticket_in_progress')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':1,'status':'RESOLVED','confirmed':True},a)[0]==201,'ticket_resolved')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':2,'status':'CLOSED','confirmed':True},a)[0]==201,'ticket_closed')
 check(call('isp/tickets/'+ticket['id']+'/state',{'revision':3,'status':'IN_PROGRESS','confirmed':True},a)[0]==409,'closed_ticket_immutable')
 check(call('customers',{'name':'Pagination customer'},a)[0]==201,'pagination_second_customer_fixture')
 page1=call('customers?limit=1&page=1',auth=a)[1];page2=call('customers?limit=1&page=2',auth=a)[1]
 check(len(page1)==len(page2)==1 and page1[0]['id']!=page2[0]['id'],'customer_pagination_stable_pages')
 check(call('customers?limit=1&page=1',auth=a)[1][0]['id']==page1[0]['id'],'customer_pagination_repeat_stable')
 check(call('customers?page=0',auth=a)[0]==422,'invalid_page_denied')
 check(call('products?limit=101',auth=a)[0]==422,'unbounded_page_limit_denied')
 check(call('products?q=Price%20changed',auth=a)[1][0]['id']==product['id'],'product_search_name')
 check(call('customers?q=Updated%20synthetic',auth=b)[1]==[],'search_preserves_tenant_isolation')
 code,raw,_=call('products',{'sku':'MFG-RAW','name':'Synthetic raw material','unitMinor':'999999'},a);check(code==201,'manufacturing_raw_product')
 code,finished,_=call('products',{'sku':'MFG-OUT','name':'Synthetic finished item','unitMinor':'1000000'},a);check(code==201,'manufacturing_output_product')
 check(call('products/'+raw['id']+'/stock',{'delta':10,'reason':'Manufacturing fixture','revision':0},a)[0]==201,'manufacturing_raw_stock')
 bomdata={'outputProductId':finished['id'],'version':1,'components':[{'productId':raw['id'],'quantity':2,'unitCostMinor':'7'}]}
 check(call('manufacturing/boms',bomdata,b)[0]==404,'manufacturing_cross_tenant_product_denied')
 code,bom,_=call('manufacturing/boms',bomdata,a);check(code==201,'manufacturing_bom_create')
 check(call('manufacturing/boms',bomdata,a)[0]==409,'manufacturing_bom_version_unique')
 check(call('manufacturing/boms',{'outputProductId':raw['id'],'version':1,'components':[{'productId':raw['id'],'quantity':1,'unitCostMinor':'1'}]},a)[0]==409,'manufacturing_self_cycle_denied')
 check(call('manufacturing/boms',{'outputProductId':raw['id'],'version':1,'components':[{'productId':finished['id'],'quantity':1,'unitCostMinor':'1'}]},a)[0]==409,'manufacturing_cross_bom_cycle_denied')
 check(call('manufacturing/orders',{'bomId':bom['id'],'quantity':3},b)[0]==404,'manufacturing_cross_tenant_bom_denied')
 code,mo,_=call('manufacturing/orders',{'bomId':bom['id'],'quantity':3},a);check(code==201 and mo['materialEstimateMinor']=='42' and not mo['stock_reserved'],'manufacturing_order_explicit_cost_not_sales_price')
 check(call('manufacturing/boms',{**bomdata,'version':2,'components':[{'productId':raw['id'],'quantity':3,'unitCostMinor':'9'}]},a)[0]==201,'manufacturing_new_immutable_bom_version')
 current=next(x for x in call('manufacturing/orders',auth=a)[1] if x['id']==mo['id']);check(current['inputs'][0]['quantity']==6 and current['inputs'][0]['unitCostMinor']=='7','manufacturing_input_snapshot_unchanged')
 check(call('manufacturing/orders/'+mo['id']+'/quality',{'revision':0,'result':'PASSED','note':'Premature inspection','confirmed':True},a)[0]==409,'manufacturing_quality_requires_release')
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':0,'confirmed':True},a)[0]==409,'manufacturing_cannot_complete_draft')
 releasekey=uuid.uuid4().hex
 check(call('manufacturing/orders/'+mo['id']+'/release',{'revision':0,'confirmed':True},a,releasekey)[0]==201,'manufacturing_order_release')
 check(call('manufacturing/orders/'+mo['id']+'/release',{'revision':0,'confirmed':True},a,releasekey)[0]==201,'manufacturing_release_replay')
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':1,'confirmed':True},a)[0]==409,'manufacturing_quality_pass_required')
 check(call('manufacturing/orders/'+mo['id']+'/quality',{'revision':1,'result':'FAILED','note':'Synthetic failed check','confirmed':True},a)[0]==201,'manufacturing_quality_failed')
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':2,'confirmed':True},a)[0]==409,'manufacturing_failed_quality_blocks_stock')
 check(call('manufacturing/orders/'+mo['id']+'/quality',{'revision':2,'result':'PASSED','note':'Synthetic reinspection passed','confirmed':True},a)[0]==201,'manufacturing_quality_reinspection_pass')
 completekey=uuid.uuid4().hex
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':3,'confirmed':True},a,completekey)[0]==201,'manufacturing_complete')
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':3,'confirmed':True},a,completekey)[0]==201,'manufacturing_completion_replay')
 stocks={x['id']:x['quantity'] for x in call('products',auth=a)[1]};check(stocks[raw['id']]==4 and stocks[finished['id']]==3,'manufacturing_stock_consumed_and_produced_once')
 check(call('manufacturing/orders/'+mo['id']+'/complete',{'revision':4,'confirmed':True},a)[0]==409,'manufacturing_second_completion_denied')
 code,short,_=call('manufacturing/orders',{'bomId':bom['id'],'quantity':3},a);check(code==201,'manufacturing_shortage_order')
 check(call('manufacturing/orders/'+short['id']+'/release',{'revision':0,'confirmed':True},a)[0]==201,'manufacturing_shortage_release')
 check(call('manufacturing/orders/'+short['id']+'/quality',{'revision':1,'result':'PASSED','note':'Synthetic quality passed','confirmed':True},a)[0]==201,'manufacturing_shortage_quality')
 check(call('manufacturing/orders/'+short['id']+'/complete',{'revision':2,'confirmed':True},a)[0]==409,'manufacturing_insufficient_stock_atomic_rollback')
 stocks={x['id']:x['quantity'] for x in call('products',auth=a)[1]};check(stocks[raw['id']]==4 and stocks[finished['id']]==3,'manufacturing_failed_completion_changes_nothing')
 check(call('manufacturing/orders/'+short['id']+'/cancel',{'revision':2,'confirmed':True},a)[0]==201,'manufacturing_uncompleted_order_cancel')
 check(call('manufacturing/boms',auth=b)[1]==[] and call('manufacturing/orders',auth=b)[1]==[],'manufacturing_lists_tenant_isolated')
 race_orders=[]
 for index in range(2):
  code,race_order,_=call('manufacturing/orders',{'bomId':bom['id'],'quantity':2},a);check(code==201,'manufacturing_race_order_'+str(index));race_orders.append(race_order)
  check(call('manufacturing/orders/'+race_order['id']+'/release',{'revision':0,'confirmed':True},a)[0]==201,'manufacturing_race_release_'+str(index))
  check(call('manufacturing/orders/'+race_order['id']+'/quality',{'revision':1,'result':'PASSED','note':'Synthetic race inspection','confirmed':True},a)[0]==201,'manufacturing_race_quality_'+str(index))
 def manufacturing_race(order):return call('manufacturing/orders/'+order['id']+'/complete',{'revision':2,'confirmed':True},a)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:manufacturing_codes=list(pool.map(manufacturing_race,race_orders))
 check(sorted(manufacturing_codes)==[201,409],'manufacturing_competing_orders_one_has_stock')
 stocks={x['id']:x['quantity'] for x in call('products',auth=a)[1]};check(stocks[raw['id']]==0 and stocks[finished['id']]==5,'manufacturing_concurrency_stock_exact')
 cycle_products=[]
 for sku in ['MFG-CYCLE-A','MFG-CYCLE-B']:
  code,pr,_=call('products',{'sku':sku,'name':'Synthetic cycle node','unitMinor':'1'},a);check(code==201,'cycle_product_'+sku);cycle_products.append(pr)
 def cycle_race(index):return call('manufacturing/boms',{'outputProductId':cycle_products[index]['id'],'version':1,'components':[{'productId':cycle_products[1-index]['id'],'quantity':1,'unitCostMinor':'1'}]},a)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:cycle_codes=list(pool.map(cycle_race,range(2)))
 check(sorted(cycle_codes)==[201,409],'manufacturing_concurrent_cycle_not_committed:'+str(cycle_codes))
 role_sessions={}
 for role in ['operator','finance']:
  code,created,_=call('users',{'email':role+'@qa.invalid','password':password,'role':role},a);check(code==201,'role_user_'+role)
  code,v,h=call('auth/login',{'tenant':'qa-a','email':role+'@qa.invalid','password':password});check(code==201,'role_login_'+role)
  role_sessions[role]={'csrf':v['csrf'],'cookie':h['Set-Cookie'].split(';')[0].split('=',1)[1]}
 check(call('isp/subscriptions/'+sub['id']+'/bill',{'period':'2026-01','confirmed':True},role_sessions['operator'])[0]==403,'operator_cannot_issue_recurring_invoice')
 check(call('manufacturing/orders/'+mo['id']+'/release',{'revision':4,'confirmed':True},role_sessions['finance'])[0]==403,'finance_cannot_mutate_production')
 check(call('manufacturing/orders',auth=role_sessions['finance'])[0]==200,'finance_can_review_production_costs')
 check(call('isp/tickets',auth=role_sessions['finance'])[0]==403,'finance_cannot_read_support_tickets')
 check(call('isp/subscriptions',auth=role_sessions['finance'])[0]==200,'finance_can_read_billing_subscriptions')
 check(call('isp/tickets',auth=role_sessions['operator'])[0]==200,'operator_can_read_support_tickets')
 code,pp,_=call('products',{'sku':'POS-ITEM','name':'Synthetic POS item','unitMinor':'100'},a);check(code==201,'pos_product_create')
 check(call('products/'+pp['id']+'/stock',{'delta':10,'reason':'POS fixture','revision':0},a)[0]==201,'pos_stock_opening')
 check(call('pos/shifts',{'openingMinor':'1000','confirmed':False},a)[0]==422,'pos_cash_attestation_required')
 code,shift,_=call('pos/shifts',{'openingMinor':'1000','confirmed':True},a);check(code==201,'pos_shift_open')
 check(call('pos/shifts',{'openingMinor':'0','confirmed':True},a)[0]==409,'pos_one_open_shift_per_cashier')
 checkout={'shiftId':shift['id'],'lines':[{'productId':pp['id'],'quantity':2},{'productId':pp['id'],'quantity':1}],'taxBps':0,'tenderedMinor':'500','confirmed':True}
 check(call('pos/sales',{**checkout,'lines':[{'productId':pp['id'],'quantity':1,'unitMinor':'1'}]},a)[0]==422,'pos_client_cannot_override_prices')
 check(call('pos/sales',{**checkout,'tenderedMinor':'299'},a)[0]==409,'pos_underpayment_denied')
 check(call('pos/sales',checkout,role_sessions['finance'])[0]==403,'pos_finance_cannot_checkout')
 check(call('pos/sales',checkout,b)[0]==404,'pos_cross_tenant_shift_denied')
 salekey=uuid.uuid4().hex
 code,sale,_=call('pos/sales',checkout,a,salekey);check(code==201 and sale['totalMinor']=='300' and sale['changeMinor']=='200' and not sale['bank_verified'],'pos_cash_sale_server_totals_and_change')
 check(call('pos/sales',checkout,a,salekey)[1]['id']==sale['id'],'pos_sale_replay_once')
 check(call('pos/sales',checkout,role_sessions['operator'],salekey)[0]==409,'pos_other_cashier_cannot_replay_receipt_key')
 check(next(x for x in call('products',auth=a)[1] if x['id']==pp['id'])['quantity']==7,'pos_duplicate_lines_stock_aggregate')
 check(call('pos/shifts/'+shift['id']+'/balance',auth=a)[1]['expectedMinor']=='1300','pos_drawer_uses_net_sale_not_tender')
 check(call('pos/sales/'+sale['id']+'/refund',{'shiftId':shift['id'],'restock':True,'reason':'Synthetic refund','confirmed':True},role_sessions['operator'])[0]==403,'pos_operator_cannot_refund')
 refundkey=uuid.uuid4().hex;refundbody={'shiftId':shift['id'],'restock':True,'reason':'Synthetic full refund','confirmed':True}
 code,refund,_=call('pos/sales/'+sale['id']+'/refund',refundbody,a,refundkey);check(code==201 and refund['amountMinor']=='300' and not refund['bank_transfer_sent'],'pos_supervised_full_cash_refund')
 check(call('pos/sales/'+sale['id']+'/refund',refundbody,a,refundkey)[1]['id']==refund['id'],'pos_refund_replay_once')
 check(call('pos/sales/'+sale['id']+'/refund',refundbody,a)[0]==409,'pos_second_refund_denied')
 check(next(x for x in call('products',auth=a)[1] if x['id']==pp['id'])['quantity']==10,'pos_refund_restock_once')
 check(call('pos/shifts/'+shift['id']+'/balance',auth=a)[1]['expectedMinor']=='1000','pos_refund_cash_balance')
 code,sale2,_=call('pos/sales',{**checkout,'lines':[{'productId':pp['id'],'quantity':2}],'tenderedMinor':'200'},a);check(code==201,'pos_second_sale')
 check(call('pos/sales/'+sale2['id']+'/refund',{**refundbody,'restock':False},a)[0]==201,'pos_refund_without_restock')
 check(next(x for x in call('products',auth=a)[1] if x['id']==pp['id'])['quantity']==8,'pos_no_implicit_restock')
 check(call('pos/shifts/'+shift['id']+'/close',{'revision':0,'countedMinor':'1000','note':'Stale close','confirmed':True},a)[0]==409,'pos_stale_shift_close_denied')
 shiftrow=next(x for x in call('pos/shifts?own=true',auth=a)[1] if x['id']==shift['id'])
 code,closed,_=call('pos/shifts/'+shift['id']+'/close',{'revision':shiftrow['revision'],'countedMinor':'990','note':'Synthetic ten minor unit shortage','confirmed':True},a);check(code==201 and closed['expectedMinor']=='1000' and closed['varianceMinor']=='-10','pos_close_reconciles_cash_variance')
 check(call('pos/sales',checkout,a)[0]==404,'pos_closed_shift_cannot_checkout')
 check(call('pos/shifts/'+shift['id']+'/close',{'revision':closed['revision'],'countedMinor':'1000','note':'Duplicate close','confirmed':True},a)[0]==404,'pos_closed_shift_immutable')
 code,newshift,_=call('pos/shifts',{'openingMinor':'1000','confirmed':True},a);check(code==201,'pos_new_shift_after_close')
 code,bshift,_=call('pos/shifts',{'openingMinor':'1000','confirmed':True},b);check(code==201,'pos_other_tenant_shift')
 check(call('pos/sales/'+sale['id']+'/refund',{**refundbody,'shiftId':bshift['id']},b)[0]==404,'pos_cross_tenant_sale_refund_denied')
 check(call('pos/shifts/'+newshift['id']+'/balance',auth=b)[0]==404,'pos_cross_tenant_balance_denied')
 check(call('pos/sales',auth=b)[1]==[],'pos_sales_tenant_isolated')
 code,opshift,_=call('pos/shifts',{'openingMinor':'0','confirmed':True},role_sessions['operator']);check(code==201,'pos_operator_own_shift')
 check(call('pos/sales',{**checkout,'shiftId':newshift['id']},role_sessions['operator'])[0]==404,'pos_cannot_use_other_cashier_shift')
 check(len(call('pos/shifts',auth=role_sessions['operator'])[1])==1,'pos_operator_shift_list_is_own')
 code,lastitem,_=call('products',{'sku':'POS-LAST','name':'Synthetic last POS unit','unitMinor':'100'},a);check(code==201,'pos_race_product')
 check(call('products/'+lastitem['id']+'/stock',{'delta':1,'reason':'Last item race','revision':0},a)[0]==201,'pos_race_stock')
 race_cashiers=[(a,newshift),(role_sessions['operator'],opshift)]
 def pos_race(pair):
  auth,drawer=pair;return call('pos/sales',{'shiftId':drawer['id'],'lines':[{'productId':lastitem['id'],'quantity':1}],'taxBps':0,'tenderedMinor':'100','confirmed':True},auth)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:pos_results=list(pool.map(pos_race,race_cashiers))
 check(sorted(x[0] for x in pos_results)==[201,409],'pos_competing_cashiers_one_last_item')
 check(next(x for x in call('products',auth=a)[1] if x['id']==lastitem['id'])['quantity']==0,'pos_race_no_negative_stock')
 combined=sum(int(call('pos/shifts/'+drawer['id']+'/balance',auth=a)[1]['expectedMinor']) for _,drawer in race_cashiers)
 check(combined==1100,'pos_race_cash_recorded_once')
 race_sale=next(x[1] for x in pos_results if x[0]==201)
 def refund_race(index):return call('pos/sales/'+race_sale['id']+'/refund',{'shiftId':newshift['id'],'restock':True,'reason':'Synthetic concurrent refund','confirmed':True},a)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:refund_codes=list(pool.map(refund_race,range(2)))
 check(sorted(refund_codes)==[201,409],'pos_concurrent_refund_once')
 check(next(x for x in call('products',auth=a)[1] if x['id']==lastitem['id'])['quantity']==1,'pos_concurrent_refund_stock_once')
 combined=sum(int(call('pos/shifts/'+drawer['id']+'/balance',auth=a)[1]['expectedMinor']) for _,drawer in race_cashiers);check(combined==1000,'pos_concurrent_refund_cash_once')
 prior=call('pos/shifts/'+newshift['id']+'/balance',auth=a)[1]
 def closing_race(index):
  if index==0:return call('pos/sales',{'shiftId':newshift['id'],'lines':[{'productId':pp['id'],'quantity':1}],'taxBps':0,'tenderedMinor':'100','confirmed':True},a)[0]
  return call('pos/shifts/'+newshift['id']+'/close',{'revision':prior['revision'],'countedMinor':prior['expectedMinor'],'note':'Synthetic concurrent closing','confirmed':True},a)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:close_codes=list(pool.map(closing_race,range(2)))
 check(close_codes[0]==201 and close_codes[1]==409 or close_codes[1]==201 and close_codes[0] in [404,409],'pos_checkout_close_serialized')
 after=call('pos/shifts/'+newshift['id']+'/balance',auth=a)[1]
 check(int(after['expectedMinor'])==int(prior['expectedMinor'])+(100 if close_codes[0]==201 else 0),'pos_closing_cash_snapshot_consistent')
 check((after['status']=='CLOSED')==(close_codes[1]==201),'pos_closed_shift_has_no_late_sale')
 code,protected_item,_=call('products',{'sku':'POS-HISTORY','name':'Synthetic unrefunded item','unitMinor':'100'},a);check(code==201,'pos_history_product')
 check(call('products/'+protected_item['id']+'/stock',{'delta':1,'reason':'Receipt reference fixture','revision':0},a)[0]==201,'pos_history_stock')
 check(call('pos/sales',{'shiftId':opshift['id'],'lines':[{'productId':protected_item['id'],'quantity':1}],'taxBps':0,'tenderedMinor':'100','confirmed':True},role_sessions['operator'])[0]==201,'pos_history_sale')
 protected=next(x for x in call('products',auth=a)[1] if x['id']==protected_item['id']);check(protected['quantity']==0,'pos_history_sold_out')
 code,denied,_=call('products/'+protected_item['id'],{'revision':protected['revision']},a,method='DELETE');check(code==409 and denied['error']['code']=='product_has_unrefunded_sales','pos_unrefunded_product_reference_protected')
 op=role_sessions['operator'];opid=call('auth/me',auth=op)[1]['user_id']
 code,other_user,_=call('users',{'email':'other-op@qa.invalid','password':password,'role':'operator'},a);check(code==201,'crm_second_operator_fixture')
 code,v,h=call('auth/login',{'tenant':'qa-a','email':'other-op@qa.invalid','password':password});other={'csrf':v['csrf'],'cookie':h['Set-Cookie'].split(';')[0].split('=',1)[1]}
 code,lead,_=call('crm/leads',{'name':'Synthetic prospect','email':'prospect@example.invalid','phone':'+62 800000001','potentialMinor':'50000'},op);check(code==201 and lead['stage']=='NEW','crm_lead_create')
 check(call('crm/leads',auth=other)[1]==[],'crm_other_operator_cannot_list_lead')
 check(call('crm/leads',auth=role_sessions['finance'])[0]==403,'crm_finance_cannot_read_private_prospects')
 check(call('crm/leads/'+lead['id']+'/convert',{'revision':0,'mode':'new','confirmed':True},op)[0]==409,'crm_unqualified_conversion_denied')
 check(call('crm/leads/'+lead['id']+'/stage',{'revision':0,'stage':'QUALIFIED','confirmed':True},op)[0]==201,'crm_lead_qualified')
 note={'kind':'FOLLOW_UP','summary':'Synthetic follow-up note','dueAt':'2026-11-01T09:00:00+07:00'};notekey=uuid.uuid4().hex
 code,activity,_=call('crm/leads/'+lead['id']+'/activities',note,op,notekey);check(code==201 and activity['dueAt']=='2026-11-01T02:00:00.000Z','crm_follow_up_timezone_normalized')
 check(call('crm/leads/'+lead['id']+'/activities',{'kind':'FOLLOW_UP','summary':'Missing due'},op)[0]==422,'crm_follow_up_requires_date')
 check(call('crm/leads/'+lead['id']+'/activities',auth=other)[0]==404,'crm_note_private_to_owner')
 current=next(x for x in call('crm/leads',auth=a)[1] if x['id']==lead['id'])
 check(call('crm/leads/'+lead['id']+'/assign',{'revision':current['revision'],'ownerId':other_user['id'],'confirmed':True},a)[0]==201,'crm_admin_reassigns')
 check(call('crm/leads/'+lead['id']+'/activities',note,op,notekey)[0]==404,'crm_revoked_owner_cannot_replay_cached_note')
 check(call('crm/activities/'+activity['id']+'/complete',{'revision':0,'confirmed':True},other)[0]==201,'crm_new_owner_completes_follow_up')
 check(call('crm/activities/'+activity['id']+'/complete',{'revision':0,'confirmed':True},other)[0]==409,'crm_activity_cannot_complete_twice')
 current=next(x for x in call('crm/leads',auth=other)[1] if x['id']==lead['id']);before_customers=len(call('customers',auth=a)[1])
 def convert_race(i):return call('crm/leads/'+lead['id']+'/convert',{'revision':current['revision'],'mode':'new','confirmed':True},other)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:conversions=list(pool.map(convert_race,range(2)))
 check(sorted(x[0] for x in conversions)==[201,409],'crm_concurrent_conversion_one_winner')
 check(len(call('customers',auth=a)[1])==before_customers+1,'crm_conversion_no_orphan_customers')
 won=next(x[1] for x in conversions if x[0]==201);check(won['stage']=='WON' and not won['external_messages_sent'],'crm_explicit_customer_conversion')
 check(call('crm/leads/'+lead['id']+'/stage',{'revision':won['revision'],'stage':'NEW','confirmed':True},other)[0]==409,'crm_won_lead_terminal')
 code,project,_=call('work/projects',{'name':'Synthetic service project','customerId':won['customerId'],'budgetMinor':'100000','startsOn':'2020-01-01','dueOn':'2020-12-31'},op);check(code==201,'work_project_create')
 check(call('work/projects',{'name':'Invalid dates','startsOn':'2026-03-01','dueOn':'2026-02-28'},op)[0]==422,'work_due_before_start_denied')
 check(call('work/projects',{'name':'Invalid calendar','startsOn':'2026-02-30'},op)[0]==422,'work_real_calendar_validated')
 check(call('work/projects',auth=other)[1]==[],'work_unassigned_operator_cannot_list_project')
 code,task,_=call('work/projects/'+project['id']+'/tasks',{'title':'Synthetic installation','assigneeId':other_user['id']},op);check(code==201,'work_task_assign_to_operator')
 check(call('work/projects/'+project['id']+'/tasks',{'title':'Unprivileged task'},other)[0]==403,'work_assignee_cannot_create_manager_tasks')
 check(call('work/tasks/'+task['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},other)[0]==409,'work_task_requires_active_project')
 project_now=next(x for x in call('work/projects',auth=op)[1] if x['id']==project['id']);check(call('work/projects/'+project['id']+'/state',{'revision':project_now['revision'],'status':'ACTIVE','confirmed':True},op)[0]==201,'work_project_activate')
 check(call('work/tasks/'+task['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},other)[0]==201,'work_assignee_starts_task')
 timebody={'startedAt':'2020-01-01T09:00:00+07:00','endedAt':'2020-01-01T10:00:00+07:00','note':'Synthetic field work'};timekey=uuid.uuid4().hex
 code,entry,_=call('work/tasks/'+task['id']+'/time',timebody,other,timekey);check(code==201 and entry['durationSeconds']==3600 and not entry['invoice_created'],'work_time_exact_seconds_no_invoice')
 check(call('work/tasks/'+task['id']+'/time',timebody,other,timekey)[1]['id']==entry['id'],'work_time_idempotent')
 check(call('work/tasks/'+task['id']+'/time',timebody,other)[0]==409,'work_overlapping_time_denied')
 check(call('work/tasks/'+task['id']+'/time',{**timebody,'endedAt':'2020-01-01T09:00:00+07:00'},other)[0]==422,'work_nonpositive_duration_denied')
 check(call('work/tasks/'+task['id']+'/time',{**timebody,'startedAt':'2020-01-01T09:00:00'},other)[0]==422,'work_time_requires_timezone')
 check(call('work/projects/'+project['id']+'/summary',auth=role_sessions['finance'])[1]['trackedSeconds']==3600,'work_finance_summary_exact')
 project_now=next(x for x in call('work/projects',auth=op)[1] if x['id']==project['id']);check(call('work/projects/'+project['id']+'/state',{'revision':project_now['revision'],'status':'COMPLETED','confirmed':True},op)[0]==409,'work_open_tasks_block_completion')
 check(call('work/tasks/'+task['id']+'/assign',{'revision':1,'assigneeId':opid,'confirmed':True},op)[0]==201,'work_manager_reassigns_task')
 check(call('work/tasks/'+task['id']+'/time',timebody,other,timekey)[0]==404,'work_revoked_assignee_cannot_replay_cached_time')
 check(call('work/tasks/'+task['id']+'/state',{'revision':2,'status':'DONE','confirmed':True},op)[0]==201,'work_task_done')
 project_now=next(x for x in call('work/projects',auth=op)[1] if x['id']==project['id']);check(call('work/projects/'+project['id']+'/state',{'revision':project_now['revision'],'status':'COMPLETED','confirmed':True},op)[0]==201,'work_project_complete')
 check(call('work/projects/'+project['id']+'/tasks',{'title':'Late task'},op)[0]==409,'work_closed_project_no_new_task')
 check(call('work/tasks/'+task['id']+'/time',{**timebody,'startedAt':'2020-01-01T11:00:00+07:00','endedAt':'2020-01-01T12:00:00+07:00'},op)[0]==409,'work_closed_project_no_new_time')
 check(call('work/projects/'+project['id']+'/tasks',auth=b)[0]==404,'work_cross_tenant_project_denied')
 check(call('work/tasks/'+task['id']+'/state',{'revision':3,'status':'IN_PROGRESS','confirmed':True},role_sessions['finance'])[0]==403,'work_finance_cannot_mutate_tasks')
 parallel_projects=[];parallel_tasks=[]
 for i in range(2):
  code,pj,_=call('work/projects',{'name':'Synthetic parallel project '+str(i)},op);check(code==201,'work_parallel_project_'+str(i));parallel_projects.append(pj)
  code,tk,_=call('work/projects/'+pj['id']+'/tasks',{'title':'Parallel work','assigneeId':opid},op);check(code==201,'work_parallel_task_'+str(i));parallel_tasks.append(tk)
  check(call('work/projects/'+pj['id']+'/state',{'revision':1,'status':'ACTIVE','confirmed':True},op)[0]==201,'work_parallel_project_activate_'+str(i))
  check(call('work/tasks/'+tk['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},op)[0]==201,'work_parallel_task_start_'+str(i))
 parallel_time={'startedAt':'2020-02-01T10:00:00Z','endedAt':'2020-02-01T11:00:00Z','note':'Synthetic concurrent cross-project work'}
 def time_race(i):return call('work/tasks/'+parallel_tasks[i]['id']+'/time',parallel_time,op)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:time_results=list(pool.map(time_race,range(2)))
 check(sorted(x[0] for x in time_results)==[201,409],'work_cross_project_concurrent_overlap_rejected')
 winning=next(x[1] for x in time_results if x[0]==201)
 totals=sum(call('work/projects/'+p['id']+'/summary',auth=op)[1]['trackedSeconds'] for p in parallel_projects);check(totals==3600,'work_cross_project_time_counted_once')
 voidkey=uuid.uuid4().hex;voidbody={'reason':'Synthetic correction','confirmed':True}
 check(call('work/time/'+winning['id']+'/void',voidbody,op,voidkey)[0]==201,'work_time_void_preserves_original')
 check(call('work/time/'+winning['id']+'/void',voidbody,op,voidkey)[0]==201,'work_void_idempotent')
 check(call('work/time/'+winning['id']+'/void',voidbody,op)[0]==409,'work_second_void_denied')
 check(call('work/tasks/'+winning['taskId']+'/time',parallel_time,op)[0]==201,'work_corrected_interval_can_be_recorded')
 check(call('work/time/'+entry['id']+'/void',voidbody,op)[0]==404,'work_project_owner_cannot_void_other_user_time')
 code,closing_project,_=call('work/projects',{'name':'Synthetic closing race'},op);check(code==201,'work_closing_race_project')
 check(call('work/projects/'+closing_project['id']+'/state',{'revision':0,'status':'ACTIVE','confirmed':True},op)[0]==201,'work_closing_race_activate')
 def project_close_race(i):
  if i==0:return call('work/projects/'+closing_project['id']+'/state',{'revision':1,'status':'COMPLETED','confirmed':True},op)[0]
  return call('work/projects/'+closing_project['id']+'/tasks',{'title':'Concurrent new task'},op)[0]
 with concurrent.futures.ThreadPoolExecutor(2) as pool:project_close_codes=list(pool.map(project_close_race,range(2)))
 check(sorted(project_close_codes)==[201,409],'work_project_close_task_creation_serialized')
 closed_state=next(p for p in call('work/projects',auth=op)[1] if p['id']==closing_project['id']);tasks_after=call('work/projects/'+closing_project['id']+'/tasks',auth=op)[1];check((closed_state['status']=='COMPLETED' and len(tasks_after)==0) or (closed_state['status']=='ACTIVE' and len(tasks_after)==1),'work_completed_project_has_no_late_task')
 block_task=parallel_tasks[0];block_project=parallel_projects[0]
 check(call('work/tasks/'+block_task['id']+'/state',{'revision':1,'status':'BLOCKED','confirmed':True},op)[0]==422,'work_blocked_task_requires_reason')
 check(call('work/tasks/'+block_task['id']+'/state',{'revision':1,'status':'BLOCKED','reason':'Synthetic waiting for material','confirmed':True},op)[0]==201,'work_blocked_task_records_reason')
 blocked=next(x for x in call('work/projects/'+block_project['id']+'/tasks',auth=op)[1] if x['id']==block_task['id']);check(blocked['stateNote']=='Synthetic waiting for material','work_blocker_note_persisted')
 check(call('work/tasks/'+block_task['id']+'/state',{'revision':2,'status':'IN_PROGRESS','confirmed':True},op)[0]==201,'work_blocked_task_resumed')
 resumed=next(x for x in call('work/projects/'+block_project['id']+'/tasks',auth=op)[1] if x['id']==block_task['id']);check(resumed['stateNote'] is None,'work_resumed_task_has_no_stale_blocker_note')
 # Project invoice source: preview, immutable time/rate snapshot and one-time billing.
 finance=role_sessions['finance'];billing_id=project['id']
 def revision(pid):return next(x['revision'] for x in call('work/projects',auth=a)[1] if x['id']==pid)
 billing_body={'revision':revision(billing_id),'issuedOn':'2026-02-01','taxBps':1000,'lines':[{'entryId':entry['id'],'rateMinorPerHour':'1001'}]}
 check(call('work/projects/'+billing_id+'/billing-preview',billing_body,op)[0]==403,'work_billing_operator_denied')
 check(call('work/projects/'+billing_id+'/billing-preview',billing_body,b)[0]==404,'work_billing_cross_tenant_denied')
 check(call('work/projects/'+billing_id+'/billing-preview',{**billing_body,'issuedOn':'2026-01-01'},finance)[0]==409,'work_billing_closed_period_denied')
 check(call('work/projects/'+billing_id+'/billing-preview',{**billing_body,'issuedOn':'9999-01-01'},finance)[0]==422,'work_billing_future_date_denied')
 check(call('work/projects/'+billing_id+'/billing-preview',{**billing_body,'lines':billing_body['lines']*2},finance)[0]==422,'work_billing_duplicate_entry_denied')
 check(call('work/projects/'+billing_id+'/billing-preview',{**billing_body,'lines':[{'entryId':entry['id'],'rateMinorPerHour':'0'}]},finance)[0]==422,'work_billing_zero_rate_denied')
 before_bills=len(call('invoices',auth=a)[1])
 code,preview,_=call('work/projects/'+billing_id+'/billing-preview',billing_body,finance);check(code==201 and preview.get('subtotal')=='1001' and preview.get('tax')=='100' and preview.get('total')=='1101' and not preview['invoice_created'],'work_billing_preview_exact_no_invoice')
 check(len(call('invoices',auth=a)[1])==before_bills,'work_billing_preview_no_side_effects')
 check(call('work/projects/'+billing_id+'/bills',{**billing_body,'confirmed':True},finance)[0]==422,'work_billing_requires_preview')
 issue_body={**billing_body,'previewHash':preview['previewHash'],'confirmed':True}
 check(call('work/projects/'+billing_id+'/bills',{**issue_body,'lines':[{'entryId':entry['id'],'rateMinorPerHour':'2000'}]},finance)[0]==409,'work_billing_changed_rate_invalidates_preview')
 check(call('work/projects/'+billing_id+'/bills',{**issue_body,'revision':billing_body['revision']-1},finance)[0]==409,'work_billing_stale_revision_denied')
 check(call('work/projects/'+billing_id+'/bills',{**issue_body,'previewHash':'0'*64},finance)[0]==409,'work_billing_wrong_preview_hash_denied')
 check(call('work/projects/'+billing_id+'/bills',{**issue_body,'issuedOn':'2026-02-02'},finance)[0]==409,'work_billing_changed_date_invalidates_preview')
 check(call('work/projects/'+billing_id+'/bills',{**issue_body,'taxBps':0},finance)[0]==409,'work_billing_changed_tax_invalidates_preview')
 check(call('work/projects/'+billing_id+'/billing-preview',billing_body,a)[0]==201,'work_billing_admin_can_preview')
 billing_key=uuid.uuid4().hex
 code,bill,_=call('work/projects/'+billing_id+'/bills',issue_body,finance,billing_key);check(code==201 and bill.get('totalMinor')=='1101' and not bill['ledger_posted'] and not bill['payment_verified'],'work_billing_issues_explicit_invoice')
 check(call('work/projects/'+billing_id+'/bills',issue_body,finance,billing_key)[1].get('invoiceId')==bill['invoiceId'],'work_billing_replay_same_invoice')
 check(call('work/projects/'+billing_id+'/bills',issue_body,a,billing_key)[0]==409,'work_billing_actor_bound_replay')
 check(call('work/projects/'+billing_id+'/billing-preview',{**billing_body,'revision':revision(billing_id)},finance)[0]==409,'work_billing_entry_cannot_bill_twice')
 saved=call('work/projects/'+billing_id+'/bills',auth=finance)[1];check(len(saved)==1 and saved[0]['lines'][0]['rateMinorPerHour']=='1001' and saved[0]['lines'][0]['durationSeconds']==3600,'work_billing_immutable_rate_time_snapshot')
 listed=next(x for x in call('invoices',auth=a)[1] if x['id']==bill['invoiceId']);check(len(listed['workBills'])==1 and listed['totalMinor']=='1101','work_billing_visible_in_invoice_list')
 check(call('work/projects/'+billing_id+'/bills',auth=b)[0]==404,'work_billing_history_cross_tenant_denied')
 check(call('invoices/'+bill['invoiceId']+'/payment',{'amountMinor':'1101','verified':True},finance)[0]==201,'work_billing_existing_payment_workflow')
 # Active-project billed-time void and concurrent invoice/void races.
 code,bp,_=call('work/projects',{'name':'Synthetic bill race','customerId':customer['id']},op);check(code==201,'work_billing_race_project')
 code,bt,_=call('work/projects/'+bp['id']+'/tasks',{'title':'Synthetic billable task','assigneeId':opid},op);check(code==201,'work_billing_race_task')
 check(call('work/projects/'+bp['id']+'/state',{'revision':revision(bp['id']),'status':'ACTIVE','confirmed':True},op)[0]==201,'work_billing_race_activate')
 check(call('work/tasks/'+bt['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},op)[0]==201,'work_billing_race_start_task')
 def create_billable(day):
  code,value,_=call('work/tasks/'+bt['id']+'/time',{'startedAt':day+'T09:00:00Z','endedAt':day+'T09:30:00Z','note':'Synthetic billable interval'},op)
  check(code==201,'work_billing_time_'+day);return value
 def bill_quote(value):
  body={'revision':revision(bp['id']),'issuedOn':'2026-02-02','taxBps':0,'lines':[{'entryId':value['id'],'rateMinorPerHour':'1001'}]}
  code,v,_=call('work/projects/'+bp['id']+'/billing-preview',body,finance);check(code==201,'work_billing_quote_'+value['startedAt'][:10]);return {**body,'previewHash':v['previewHash'],'confirmed':True}
 fixed=create_billable('2021-01-01');fixed_body=bill_quote(fixed)
 check(call('work/projects/'+bp['id']+'/bills',fixed_body,finance)[1].get('totalMinor')=='501','work_billing_half_hour_rounding')
 check(call('work/time/'+fixed['id']+'/void',{'reason':'Synthetic correction','confirmed':True},op)[0]==409,'work_billing_billed_time_cannot_void')
 race_entry=create_billable('2021-01-02');race_body=bill_quote(race_entry)
 def billing_race(i):return call('work/projects/'+bp['id']+'/bills',race_body,finance)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:bill_codes=list(pool.map(billing_race,range(2)))
 check(sorted(x[0] for x in bill_codes)==[201,409],'work_billing_concurrent_one_invoice')
 bills_now=call('work/projects/'+bp['id']+'/bills',auth=finance)[1];check(sum(l['entryId']==race_entry['id'] for x in bills_now for l in x['lines'])==1,'work_billing_unique_entry_database_guarantee')
 race_entry=create_billable('2021-01-03');race_body=bill_quote(race_entry)
 def void_bill_race(i):
  return call('work/projects/'+bp['id']+'/bills',race_body,finance) if i==0 else call('work/time/'+race_entry['id']+'/void',{'reason':'Concurrent correction','confirmed':True},op)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:race_codes=list(pool.map(void_bill_race,range(2)))
 check(sorted(x[0] for x in race_codes)==[201,409],'work_billing_void_invoice_race_one_winner')
 after_entry=next(x for x in call('work/projects/'+bp['id']+'/time',auth=a)[1] if x['id']==race_entry['id']);check(bool(after_entry.get('voidedAt'))!=bool(after_entry.get('billingLine')),'work_billing_no_voided_entry_in_invoice')
 voided=create_billable('2021-01-04');check(call('work/time/'+voided['id']+'/void',{'reason':'Synthetic unbillable','confirmed':True},op)[0]==201,'work_billing_void_fixture')
 void_body={'revision':revision(bp['id']),'issuedOn':'2026-02-02','taxBps':0,'lines':[{'entryId':voided['id'],'rateMinorPerHour':'1001'}]}
 check(call('work/projects/'+bp['id']+'/billing-preview',void_body,finance)[0]==409,'work_billing_voided_time_not_billable')
 check(call('work/projects/'+bp['id']+'/billing-preview',{**void_body,'lines':[{'entryId':entry['id'],'rateMinorPerHour':'1001'}]},finance)[0]==409,'work_billing_entry_from_other_project_denied')
 check(call('work/projects/'+billing_id+'/bills',auth=op)[0]==200,'work_billing_project_owner_can_review')
 check(call('work/projects/'+bp['id']+'/tasks',{'title':'Synthetic assigned viewer','assigneeId':other_user['id']},op)[0]==201,'work_billing_assignee_fixture')
 check(call('work/projects/'+bp['id']+'/bills',auth=other)[0]==404,'work_billing_assignee_cannot_review_finance')
 missing=parallel_projects[0]
 missing_body={**void_body,'revision':revision(missing['id'])}
 check(call('work/projects/'+missing['id']+'/billing-preview',missing_body,finance)[0]==404,'work_billing_requires_customer')
 code,planned,_=call('work/projects',{'name':'Synthetic not billable','customerId':customer['id']},op);check(code==201,'work_billing_planned_fixture')
 check(call('work/projects/'+planned['id']+'/billing-preview',{**void_body,'revision':0},finance)[0]==409,'work_billing_planned_project_denied')
 check(call('work/projects/'+planned['id']+'/state',{'revision':0,'status':'CANCELLED','reason':'Synthetic cancellation','confirmed':True},op)[0]==201,'work_billing_cancel_fixture')
 check(call('work/projects/'+planned['id']+'/billing-preview',{**void_body,'revision':1},finance)[0]==409,'work_billing_cancelled_project_denied')
 check(call('work/projects/'+bp['id']+'/billing-preview',{**void_body,'lines':void_body['lines']*101},finance)[0]==422,'work_billing_line_limit_enforced')
 final_entry=create_billable('2021-01-05')
 final_body={'revision':revision(bp['id']),'issuedOn':'2026-03-01','taxBps':0,'lines':[{'entryId':final_entry['id'],'rateMinorPerHour':'1000'}]}
 code,q,_=call('work/projects/'+bp['id']+'/billing-preview',final_body,a);check(code==201,'work_billing_period_race_preview')
 final_body.update(previewHash=q['previewHash'],confirmed=True)
 def closing_invoice_race(i):
  return call('work/projects/'+bp['id']+'/bills',final_body,a) if i==0 else call('ledger/periods/close',{'period':'2026-03','confirmed':True},a)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:close_results=list(pool.map(closing_invoice_race,range(2)))
 check(close_results[0][0] in (201,409) and close_results[1][0] in (201,409) and any(x[0]==201 for x in close_results),'work_billing_invoice_period_close_serialized')
 if close_results[1][0]==409:check(call('ledger/periods/close',{'period':'2026-03','confirmed':True},a)[0]==201,'work_billing_period_close_explicit_retry')
 check(call('work/projects/'+bp['id']+'/billing-preview',{k:v for k,v in final_body.items() if k not in ('previewHash','confirmed')},a)[0]==409,'work_billing_no_invoice_after_period_close')
 # Milestones and issues: explicit source dependencies, assignment and closure guards.
 code,cp,_=call('work/projects',{'name':'Synthetic milestone project','customerId':customer['id']},op);check(code==201,'work_control_project')
 code,ct,_=call('work/projects/'+cp['id']+'/tasks',{'title':'Synthetic milestone task','assigneeId':opid},op);check(code==201,'work_control_task')
 milestone_body={'title':'Synthetic delivery','dueOn':'2026-12-01','taskIds':[ct['id']]}
 check(call('work/projects/'+cp['id']+'/milestones',{**milestone_body,'dueOn':'2026-02-30'},op)[0]==422,'work_milestone_real_calendar')
 check(call('work/projects/'+cp['id']+'/milestones',{**milestone_body,'taskIds':[]},op)[0]==422,'work_milestone_requires_task')
 check(call('work/projects/'+cp['id']+'/milestones',{**milestone_body,'taskIds':[ct['id'],ct['id']]},op)[0]==422,'work_milestone_duplicate_task_denied')
 check(call('work/projects/'+cp['id']+'/milestones',{**milestone_body,'taskIds':[task['id']]},op)[0]==404,'work_milestone_cross_project_task_denied')
 check(call('work/projects/'+cp['id']+'/milestones',milestone_body,finance)[0]==403,'work_milestone_finance_cannot_create')
 code,ms,_=call('work/projects/'+cp['id']+'/milestones',milestone_body,op);check(code==201,'work_milestone_created')
 check(call('work/milestones/'+ms['id']+'/state',{'revision':0,'status':'REACHED','confirmed':True},op)[0]==409,'work_milestone_planned_project_not_reached')
 check(call('work/projects/'+cp['id']+'/state',{'revision':revision(cp['id']),'status':'ACTIVE','confirmed':True},op)[0]==201,'work_control_project_active')
 check(call('work/milestones/'+ms['id']+'/state',{'revision':0,'status':'REACHED','confirmed':True},op)[0]==409,'work_milestone_incomplete_tasks_block')
 check(call('work/tasks/'+ct['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},op)[0]==201,'work_milestone_start_task')
 check(call('work/tasks/'+ct['id']+'/state',{'revision':1,'status':'DONE','confirmed':True},op)[0]==201,'work_milestone_complete_task')
 check(call('work/projects/'+cp['id']+'/state',{'revision':revision(cp['id']),'status':'COMPLETED','confirmed':True},op)[0]==409,'work_milestone_pending_blocks_project_close')
 mk=uuid.uuid4().hex;mb={'revision':0,'status':'REACHED','confirmed':True}
 check(call('work/milestones/'+ms['id']+'/state',mb,op,mk)[0]==201,'work_milestone_reached_after_tasks')
 check(call('work/milestones/'+ms['id']+'/state',mb,op,mk)[0]==201,'work_milestone_replay_terminal')
 check(call('work/milestones/'+ms['id']+'/state',{'revision':1,'status':'CANCELLED','reason':'Synthetic changed mind','confirmed':True},op)[0]==409,'work_milestone_terminal_immutable')
 check(call('work/projects/'+cp['id']+'/milestones',auth=b)[0]==404,'work_milestone_tenant_isolation')
 issue_body={'subject':'Synthetic installation blocker','description':'Synthetic QA detail','priority':'HIGH','assigneeId':other_user['id']}
 check(call('work/projects/'+cp['id']+'/issues',{**issue_body,'priority':'INVALID'},op)[0]==422,'work_issue_explicit_priority')
 check(call('work/projects/'+cp['id']+'/issues',issue_body,finance)[0]==403,'work_issue_finance_cannot_create')
 code,issue,_=call('work/projects/'+cp['id']+'/issues',issue_body,op);check(code==201,'work_issue_created')
 check(call('work/projects/'+cp['id']+'/issues',auth=other)[1][0]['id']==issue['id'],'work_issue_assignee_can_read')
 check(any(x['id']==cp['id'] for x in call('work/projects',auth=other)[1]),'work_issue_assignee_project_visible')
 check(call('work/projects/'+cp['id']+'/milestones',auth=other)[0]==403,'work_issue_assignee_no_manager_milestones')
 check(call('work/issues/'+issue['id']+'/state',{'revision':0,'status':'RESOLVED','resolution':'Invalid skip','confirmed':True},other)[0]==409,'work_issue_cannot_skip_progress')
 progress_key=uuid.uuid4().hex;progress_body={'revision':0,'status':'IN_PROGRESS','confirmed':True}
 check(call('work/issues/'+issue['id']+'/state',progress_body,other,progress_key)[0]==201,'work_issue_assignee_starts')
 check(call('work/issues/'+issue['id']+'/assign',{'revision':1,'assigneeId':opid,'confirmed':True},other)[0]==403,'work_issue_assignee_cannot_reassign')
 check(call('work/issues/'+issue['id']+'/assign',{'revision':1,'assigneeId':opid,'confirmed':True},op)[0]==201,'work_issue_owner_reassigns')
 check(call('work/issues/'+issue['id']+'/state',progress_body,other,progress_key)[0]==404,'work_issue_old_assignee_cached_replay_denied')
 check(call('work/projects/'+cp['id']+'/state',{'revision':revision(cp['id']),'status':'COMPLETED','confirmed':True},op)[0]==409,'work_issue_open_blocks_project_close')
 check(call('work/issues/'+issue['id']+'/state',{'revision':2,'status':'RESOLVED','confirmed':True},op)[0]==422,'work_issue_resolution_required')
 check(call('work/issues/'+issue['id']+'/state',{'revision':2,'status':'RESOLVED','resolution':'Synthetic verified fix','confirmed':True},op)[0]==201,'work_issue_resolved')
 check(call('work/projects/'+cp['id']+'/state',{'revision':revision(cp['id']),'status':'COMPLETED','confirmed':True},op)[0]==409,'work_issue_resolved_requires_owner_closure')
 check(call('work/issues/'+issue['id']+'/state',{'revision':3,'status':'IN_PROGRESS','confirmed':True},op)[0]==201,'work_issue_reopened')
 reopened=call('work/projects/'+cp['id']+'/issues',auth=op)[1][0];check(reopened['resolution'] is None and reopened['resolvedAt'] is None,'work_issue_reopen_clears_stale_resolution')
 check(call('work/issues/'+issue['id']+'/state',{'revision':4,'status':'RESOLVED','resolution':'Synthetic reverified fix','confirmed':True},op)[0]==201,'work_issue_reresolved')
 check(call('work/issues/'+issue['id']+'/state',{'revision':5,'status':'CLOSED','confirmed':True},op)[0]==201,'work_issue_owner_closed')
 check(call('work/issues/'+issue['id']+'/state',{'revision':6,'status':'IN_PROGRESS','confirmed':True},op)[0]==409,'work_issue_closed_immutable')
 check(call('work/issues/'+issue['id']+'/state',{'revision':6,'status':'IN_PROGRESS','confirmed':True},b)[0]==404,'work_issue_cross_tenant_denied')
 check(call('work/projects/'+cp['id']+'/state',{'revision':revision(cp['id']),'status':'COMPLETED','confirmed':True},op)[0]==201,'work_controls_settled_project_completed')
 check(call('work/projects/'+cp['id']+'/issues',issue_body,op)[0]==409,'work_issue_completed_project_no_new_issue')
 # Current role is checked before replaying prior admin-only closure/cancellation.
 code,temporary_admin,_=call('users',{'email':'temporary-admin@qa.invalid','password':password,'role':'admin'},a);check(code==201,'work_controls_temporary_admin')
 code,login,headers=call('auth/login',{'tenant':'qa-a','email':'temporary-admin@qa.invalid','password':password});check(code==201,'work_controls_admin_login');temporary={'cookie':headers['Set-Cookie'].split(';')[0].split('=',1)[1],'csrf':login['csrf']}
 code,rp,_=call('work/projects',{'name':'Synthetic role-revocation project'},op);check(code==201,'work_controls_role_project')
 check(call('work/projects/'+rp['id']+'/state',{'revision':0,'status':'ACTIVE','confirmed':True},op)[0]==201,'work_controls_role_project_active')
 code,ri,_=call('work/projects/'+rp['id']+'/issues',{**issue_body,'assigneeId':temporary_admin['id']},op);check(code==201,'work_controls_role_issue')
 check(call('work/issues/'+ri['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},temporary)[0]==201,'work_controls_admin_starts_issue')
 check(call('work/issues/'+ri['id']+'/state',{'revision':1,'status':'RESOLVED','resolution':'Synthetic verified','confirmed':True},temporary)[0]==201,'work_controls_admin_resolves_issue')
 closekey=uuid.uuid4().hex;closebody={'revision':2,'status':'CLOSED','confirmed':True}
 check(call('work/issues/'+ri['id']+'/state',closebody,temporary,closekey)[0]==201,'work_controls_admin_closes_issue')
 code,rt,_=call('work/projects/'+rp['id']+'/tasks',{'title':'Synthetic role cancellation','assigneeId':temporary_admin['id']},op);check(code==201,'work_controls_role_task')
 cancelkey=uuid.uuid4().hex;cancelbody={'revision':0,'status':'CANCELLED','reason':'Synthetic admin cancellation','confirmed':True}
 check(call('work/tasks/'+rt['id']+'/state',cancelbody,temporary,cancelkey)[0]==201,'work_controls_admin_cancels_task')
 run(['docker','exec','-i',name,'psql','-v','ON_ERROR_STOP=1','-U','erp_owner','-d','erp'],input='UPDATE "User" SET role=\'operator\' WHERE id=\''+temporary_admin['id']+'\';')
 check(call('work/issues/'+ri['id']+'/state',closebody,temporary,closekey)[0]==403,'work_controls_revoked_admin_cannot_replay_issue_close')
 check(call('work/tasks/'+rt['id']+'/state',cancelbody,temporary,cancelkey)[0]==403,'work_controls_revoked_admin_cannot_replay_task_cancel')
 code,mp,_=call('work/projects',{'name':'Synthetic controls race'},op);check(code==201,'work_controls_close_race_fixture')
 check(call('work/projects/'+mp['id']+'/state',{'revision':0,'status':'ACTIVE','confirmed':True},op)[0]==201,'work_controls_close_race_active')
 def control_close_race(i):
  return call('work/projects/'+mp['id']+'/state',{'revision':1,'status':'COMPLETED','confirmed':True},op) if i==0 else call('work/projects/'+mp['id']+'/issues',issue_body,op)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:control_codes=list(pool.map(control_close_race,range(2)))
 check(sorted(x[0] for x in control_codes)==[201,409],'work_controls_new_issue_close_race_serialized')
 control_state=next(x for x in call('work/projects',auth=op)[1] if x['id']==mp['id']);control_issues=call('work/projects/'+mp['id']+'/issues',auth=op)[1];check((control_state['status']=='COMPLETED' and len(control_issues)==0) or (control_state['status']=='ACTIVE' and len(control_issues)==1),'work_controls_closed_project_has_no_late_issue')
 # Cancellation retains unfinished history; it must never be labeled completed.
 code,xp,_=call('work/projects',{'name':'Synthetic cancellation audit'},op);check(code==201,'work_controls_cancel_project_fixture')
 check(call('work/projects/'+xp['id']+'/state',{'revision':0,'status':'ACTIVE','confirmed':True},op)[0]==201,'work_controls_cancel_active')
 code,xt,_=call('work/projects/'+xp['id']+'/tasks',{'title':'Synthetic cancelled scope'},op);check(code==201,'work_controls_cancel_task_fixture')
 code,xm,_=call('work/projects/'+xp['id']+'/milestones',{'title':'Synthetic original target','dueOn':'2026-12-01','taskIds':[xt['id']]},op);check(code==201,'work_controls_cancel_milestone_fixture')
 check(call('work/tasks/'+xt['id']+'/state',{'revision':0,'status':'CANCELLED','reason':'Synthetic scope removed','confirmed':True},op)[0]==201,'work_controls_linked_task_cancelled')
 check(call('work/milestones/'+xm['id']+'/state',{'revision':0,'status':'REACHED','confirmed':True},op)[0]==409,'work_controls_cancelled_task_never_counts_done')
 check(call('work/milestones/'+xm['id']+'/state',{'revision':0,'status':'CANCELLED','reason':'Synthetic replacement target required','confirmed':True},op)[0]==201,'work_controls_pending_target_can_explicitly_cancel')
 code,xt2,_=call('work/projects/'+xp['id']+'/tasks',{'title':'Synthetic frozen work'},op);check(code==201,'work_controls_frozen_task_fixture')
 code,xm2,_=call('work/projects/'+xp['id']+'/milestones',{'title':'Synthetic pending history','dueOn':'2026-12-01','taskIds':[xt2['id']]},op);check(code==201,'work_controls_frozen_target_fixture')
 code,xi,_=call('work/projects/'+xp['id']+'/issues',issue_body,op);check(code==201,'work_controls_frozen_issue_fixture')
 check(call('work/projects/'+xp['id']+'/state',{'revision':revision(xp['id']),'status':'CANCELLED','reason':'Synthetic project cancelled','confirmed':True},op)[0]==201,'work_controls_cancel_freezes_history')
 check(call('work/milestones/'+xm2['id']+'/state',{'revision':0,'status':'CANCELLED','reason':'After cancel','confirmed':True},op)[0]==409,'work_controls_cancelled_project_target_frozen')
 check(call('work/issues/'+xi['id']+'/state',{'revision':0,'status':'IN_PROGRESS','confirmed':True},op)[0]==409,'work_controls_cancelled_project_issue_frozen')
 frozen_m=next(x for x in call('work/projects/'+xp['id']+'/milestones',auth=op)[1] if x['id']==xm2['id']);frozen_i=call('work/projects/'+xp['id']+'/issues',auth=op)[1][0]
 check(frozen_m['status']=='PENDING' and frozen_i['status']=='OPEN','work_controls_cancellation_does_not_fake_resolution')
 # Opposite unclassified cash journals do not conceal missing classification.
 check(call('ledger/accounts',{'code':'1100','name':'Synthetic cash account','type':'asset','isCash':True},a)[0]==201,'financial_report_explicit_cash_account')
 check(call('ledger/accounts',{'code':'5000','name':'Synthetic expense account','type':'expense'},a)[0]==201,'financial_report_expense_account')
 cash_income=[{'accountCode':'1100','debitMinor':'100','creditMinor':'0'},{'accountCode':'4000','debitMinor':'0','creditMinor':'100'}]
 cash_expense=[{'accountCode':'5000','debitMinor':'100','creditMinor':'0'},{'accountCode':'1100','debitMinor':'0','creditMinor':'100'}]
 check(call('ledger/journals',{'memo':'Synthetic unclassified cash in','period':'2026-04','lines':cash_income},a)[0]==201,'financial_report_unclassified_cash_in')
 check(call('ledger/journals',{'memo':'Synthetic unclassified cash out','period':'2026-04','lines':cash_expense},a)[0]==201,'financial_report_unclassified_cash_out')
 cash_report=call('reports/financial?period=2026-04',auth=a)[1]['cashFlow'];check(cash_report['categories']['unclassified']=='0' and cash_report['classificationRequired'] and cash_report['unclassifiedCashJournals']==2,'financial_report_offsetting_unclassified_still_flagged')
 check(call('ledger/accounts',{'code':'1200','name':'Synthetic bank account','type':'asset','isCash':True},a)[0]==201,'financial_report_bank_account')
 check(call('ledger/journals',{'memo':'Synthetic classified funding','period':'2026-04','cashflowCategory':'operating','lines':cash_income},a)[0]==201,'financial_report_classified_funding')
 transfer=[{'accountCode':'1200','debitMinor':'100','creditMinor':'0'},{'accountCode':'1100','debitMinor':'0','creditMinor':'100'}]
 check(call('ledger/journals',{'memo':'Synthetic unclassified internal transfer','period':'2026-04','lines':transfer},a)[0]==201,'financial_report_unclassified_internal_transfer')
 cash_report=call('reports/financial?period=2026-04',auth=a)[1]['cashFlow'];check(cash_report['categories']['unclassified']=='0' and cash_report['classificationRequired'] and cash_report['unclassifiedCashJournals']==3,'financial_report_net_zero_unclassified_transfer_flagged')
 # RFQ -> explicit finance award -> draft PO, no duplicate order or vendor send.
 code,rp1,_=call('products',{'sku':'RFQ-1','name':'Synthetic RFQ product','unitMinor':'9999'},a);check(code==201,'rfq_product_fixture')
 code,rp2,_=call('products',{'sku':'RFQ-2','name':'Synthetic RFQ second','unitMinor':'9999'},a);check(code==201,'rfq_second_product_fixture')
 code,rv1,_=call('suppliers',{'name':'Synthetic RFQ vendor A'},a);check(code==201,'rfq_vendor_a')
 code,rv2,_=call('suppliers',{'name':'Synthetic RFQ vendor B'},a);check(code==201,'rfq_vendor_b')
 rfq_body={'title':'Synthetic RFQ basket','currency':'IDR','dueOn':'2099-12-01','lines':[{'productId':rp1['id'],'quantity':3},{'productId':rp2['id'],'quantity':1}]}
 check(call('sourcing/rfqs',{**rfq_body,'dueOn':'2026-02-30'},op)[0]==422,'rfq_real_calendar_required')
 check(call('sourcing/rfqs',{**rfq_body,'dueOn':'2000-01-01'},op)[0]==422,'rfq_past_deadline_denied')
 check(call('sourcing/rfqs',{**rfq_body,'lines':rfq_body['lines'][:1]*2},op)[0]==422,'rfq_duplicate_item_denied')
 check(call('sourcing/rfqs',rfq_body,finance)[0]==403,'rfq_finance_cannot_create')
 check(call('sourcing/rfqs',rfq_body,b)[0]==404,'rfq_cross_tenant_product_denied')
 rfq_key=uuid.uuid4().hex;code,rfq,_=call('sourcing/rfqs',rfq_body,op,rfq_key);check(code==201 and not rfq['external_messages_sent'],'rfq_create_no_vendor_message')
 check(call('sourcing/rfqs',rfq_body,op,rfq_key)[1]['id']==rfq['id'],'rfq_create_idempotent')
 check(call('sourcing/rfqs',auth=other)[1]==[],'rfq_operator_owner_scope')
 check(call('products/'+rp1['id'],{'revision':0},a,method='DELETE')[0]==409,'rfq_open_product_delete_denied')
 def rfq_revision(rid):return next(x['revision'] for x in call('sourcing/rfqs',auth=a)[1] if x['id']==rid)
 offer_body={'rfqRevision':0,'supplierId':rv1['id'],'reference':'RFQ-A-1','currency':'IDR','validUntil':'2099-12-31','deliveryDays':10,'taxBps':1000,'confirmed':True,'lines':[{'productId':rp1['id'],'unitMinor':'1001'},{'productId':rp2['id'],'unitMinor':'200'}]}
 endpoint='sourcing/rfqs/'+rfq['id']
 check(call(endpoint+'/offers',offer_body,other)[0]==404,'rfq_offer_wrong_owner_denied')
 check(call(endpoint+'/offers',{**offer_body,'currency':'USD'},op)[0]==422,'rfq_offer_currency_mismatch_denied')
 check(call(endpoint+'/offers',{**offer_body,'validUntil':'2000-01-01'},op)[0]==422,'rfq_offer_expired_denied')
 check(call(endpoint+'/offers',{**offer_body,'lines':offer_body['lines'][:1]},op)[0]==422,'rfq_offer_requires_exact_basket')
 offerkey=uuid.uuid4().hex;code,of1,_=call(endpoint+'/offers',offer_body,op,offerkey);check(code==201 and of1['subtotalMinor']=='3203' and of1['taxMinor']=='320' and of1['totalMinor']=='3523','rfq_offer_server_frozen_quantity_price_tax')
 check(call(endpoint+'/offers',offer_body,op,offerkey)[1]['id']==of1['id'],'rfq_offer_replay_no_duplicate')
 check(call(endpoint+'/offers',{**offer_body,'rfqRevision':1,'reference':'RFQ-A-2'},op)[0]==409,'rfq_one_active_quote_per_vendor')
 other_offer={**offer_body,'rfqRevision':1,'supplierId':rv2['id'],'reference':'RFQ-B-1'}
 code,of2,_=call(endpoint+'/offers',other_offer,op);check(code==201,'rfq_second_vendor_quote')
 check(call('products/'+rp1['id'],{'name':'Synthetic renamed product','sku':'RFQ-1-NEW','unitMinor':'50000','revision':0},a,method='PATCH')[0]==200,'rfq_product_reprice_fixture')
 rfq_snapshot=next(x for x in call('sourcing/rfqs',auth=a)[1] if x['id']==rfq['id']);snapshot_line=next(x for x in rfq_snapshot['lines'] if x['productId']==rp1['id']);check(snapshot_line['nameSnapshot']=='Synthetic RFQ product' and snapshot_line['skuSnapshot']=='RFQ-1','rfq_request_name_sku_snapshot_immutable')
 compare=call(endpoint+'/comparison',auth=finance)[1];check(len(compare['lowestCostOfferIds'])==2 and not compare['auto_awarded'],'rfq_equal_cost_ties_no_auto_award')
 check(call(endpoint+'/comparison',auth=b)[0]==404,'rfq_comparison_tenant_isolation')
 choice=next(x for x in compare['offers'] if x['id']==of1['id']);award={'revision':2,'offerId':of1['id'],'comparisonHash':choice['comparisonHash'],'reason':'Synthetic explicit finance selection','confirmed':True}
 check(call(endpoint+'/award',award,op)[0]==403,'rfq_operator_cannot_award')
 check(call(endpoint+'/award',{**award,'comparisonHash':'0'*64},finance)[0]==409,'rfq_comparison_hash_required')
 withdraw={'rfqRevision':2,'revision':0,'reason':'Synthetic quote correction','confirmed':True};wk=uuid.uuid4().hex
 check(call(endpoint+'/offers/'+of1['id']+'/withdraw',withdraw,op,wk)[0]==201,'rfq_withdraw_preserves_history')
 check(call(endpoint+'/offers/'+of1['id']+'/withdraw',withdraw,op,wk)[0]==201,'rfq_withdraw_terminal_replay')
 check(call(endpoint+'/award',award,finance)[0]==409,'rfq_stale_comparison_after_withdraw')
 check(call(endpoint+'/award',{**award,'revision':3},finance)[0]==404,'rfq_withdrawn_quote_cannot_award')
 replacement={**offer_body,'rfqRevision':3,'reference':'RFQ-A-2','lines':[{'productId':rp1['id'],'unitMinor':'1100'},{'productId':rp2['id'],'unitMinor':'200'}]}
 code,replaced,_=call(endpoint+'/offers',replacement,op);check(code==201,'rfq_corrected_quote_new_immutable_reference')
 compare=call(endpoint+'/comparison',auth=finance)[1];check(compare['lowestCostOfferIds']==[of2['id']],'rfq_compare_active_prices_only')
 # Choose the more expensive offer explicitly; no hidden automatic price preference.
 chosen=next(x for x in compare['offers'] if x['id']==replaced['id']);award={'revision':4,'offerId':replaced['id'],'comparisonHash':chosen['comparisonHash'],'reason':'Synthetic explicit delivery preference','confirmed':True}
 before_orders=len(call('purchases',auth=a)[1]);awardkeys=[uuid.uuid4().hex,uuid.uuid4().hex]
 def award_race(i):return call(endpoint+'/award',award,finance,awardkeys[i])
 with concurrent.futures.ThreadPoolExecutor(2) as pool:awards=list(pool.map(award_race,range(2)))
 check(sorted(x[0] for x in awards)==[201,409],'rfq_concurrent_award_one_winner')
 won_award=next(x[1] for x in awards if x[0]==201);check(len(call('purchases',auth=a)[1])==before_orders+1,'rfq_no_orphan_duplicate_po')
 check(won_award['purchaseStatus']=='DRAFT' and not won_award['payment_sent'] and not won_award['supplier_notified'],'rfq_award_requires_existing_po_approval')
 po=next(x for x in call('purchases',auth=a)[1] if x['id']==won_award['purchaseId']);check(po['currency']=='IDR' and po['subtotalMinor']=='3500' and po['totalMinor']=='3850' and sorted(x['quantity'] for x in po['lines'])==[1,3],'rfq_award_exact_offer_snapshot')
 check(call('purchases/'+po['id']+'/receive',{'revision':0,'confirmed':True},op)[0]==409,'rfq_award_not_silent_stock_receipt')
 check(call(endpoint+'/cancel',{'revision':5,'reason':'After award','confirmed':True},op)[0]==409,'rfq_awarded_cannot_cancel')
 check(call(endpoint+'/offers',{**other_offer,'rfqRevision':5,'reference':'late'},op)[0]==409,'rfq_awarded_no_new_quotes')
 # Exact same successful actor/key replays after terminal state without another PO.
 winning_key=awardkeys[next(i for i,x in enumerate(awards) if x[0]==201)]
 check(call(endpoint+'/award',award,finance,winning_key)[1]['purchaseId']==po['id'],'rfq_award_terminal_idempotent')
 code,cancel_rfq,_=call('sourcing/rfqs',{**rfq_body,'title':'Synthetic cancellation race'},op);check(code==201,'rfq_cancel_race_fixture')
 cancel_endpoint='sourcing/rfqs/'+cancel_rfq['id'];new_offer={**offer_body,'reference':'CANCEL-A','rfqRevision':0}
 def cancel_race(i):return call(cancel_endpoint+'/cancel',{'revision':0,'reason':'Synthetic cancel','confirmed':True},op) if i==0 else call(cancel_endpoint+'/offers',new_offer,op)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:cancel_codes=list(pool.map(cancel_race,range(2)))
 check(sorted(x[0] for x in cancel_codes)==[201,409],'rfq_cancel_new_quote_race_serialized')
 record=next(x for x in call('sourcing/rfqs',auth=a)[1] if x['id']==cancel_rfq['id']);history=call(cancel_endpoint+'/offers',auth=a)[1]
 check((record['status']=='CANCELLED' and len(history)==0) or (record['status']=='OPEN' and len(history)==1),'rfq_no_quote_inserted_after_cancel')
 # Replay scope includes both parent RFQ and offer, even for previously successful keys.
 check(call(cancel_endpoint+'/offers/'+of1['id']+'/withdraw',withdraw,op,wk)[0]==404,'rfq_withdraw_cache_wrong_parent_denied')
 check(call(endpoint+'/offers/'+of1['id']+'/withdraw',withdraw,op,wk)[0]==201,'rfq_withdraw_replay_after_award')
 check(call(endpoint+'/award',award,a,winning_key)[0]==409,'rfq_award_cross_actor_replay_denied')
 check(call(endpoint+'/offers',auth=other)[0]==404 and call(endpoint+'/comparison',auth=other)[0]==404,'rfq_read_nonowner_operator_denied')
 code,late,_=call('sourcing/rfqs',{**rfq_body,'title':'Synthetic time eligibility'},op);check(code==201,'rfq_time_fixture')
 late_endpoint='sourcing/rfqs/'+late['id'];code,lq,_=call(late_endpoint+'/offers',offer_body,op);check(code==201,'rfq_time_quote_fixture')
 def fixture_sql(sql):return run(['docker','exec','-i',name,'psql','-v','ON_ERROR_STOP=1','-U','erp_owner','-d','erp'],input=sql)
 fixture_sql('UPDATE "PurchaseRfq" SET "dueOn"=\'2000-01-01\' WHERE id=\''+late['id']+'\';')
 check(call(late_endpoint+'/offers',{**other_offer,'rfqRevision':1},op)[0]==409,'rfq_past_deadline_blocks_new_offer')
 before_expiry=call(late_endpoint+'/comparison',auth=finance)[1];check(before_expiry['offers'][0]['eligible'] and before_expiry['scope']=='product_basket_tax_only_no_shipping_discount_fx','rfq_award_after_offer_deadline_policy_explicit')
 fixture_sql('UPDATE "RfqOffer" SET "validUntil"=\'2000-01-01\' WHERE id=\''+lq['id']+'\';')
 expired=call(late_endpoint+'/comparison',auth=finance)[1];check(expired['lowestCostOfferIds']==[] and not expired['offers'][0]['eligible'],'rfq_no_eligible_offer_empty_comparison')
 late_award={'revision':1,'offerId':lq['id'],'comparisonHash':expired['offers'][0]['comparisonHash'],'reason':'Synthetic expiry test','confirmed':True}
 check(call(late_endpoint+'/award',late_award,finance)[0]==409,'rfq_offer_expiry_revalidated_at_award')
 fixture_sql('UPDATE "RfqOffer" SET "validUntil"=\'2099-12-31\' WHERE id=\''+lq['id']+'\';')
 late_award['comparisonHash']=call(late_endpoint+'/comparison',auth=finance)[1]['offers'][0]['comparisonHash'];check(call(late_endpoint+'/award',late_award,finance)[0]==201,'rfq_valid_quote_awarded_after_submission_deadline')
 # Withdrawal and finance selection of the same offer serialize through RFQ CAS.
 code,race_rfq,_=call('sourcing/rfqs',{**rfq_body,'title':'Synthetic withdrawal race'},op);check(code==201,'rfq_withdraw_race_fixture')
 race_endpoint='sourcing/rfqs/'+race_rfq['id'];code,race_quote,_=call(race_endpoint+'/offers',offer_body,op);check(code==201,'rfq_withdraw_race_quote')
 race_comparison=call(race_endpoint+'/comparison',auth=finance)[1];race_award={'revision':1,'offerId':race_quote['id'],'comparisonHash':race_comparison['offers'][0]['comparisonHash'],'reason':'Synthetic race','confirmed':True}
 def withdraw_award_race(i):return call(race_endpoint+'/award',race_award,finance) if i==0 else call(race_endpoint+'/offers/'+race_quote['id']+'/withdraw',{'rfqRevision':1,'revision':0,'reason':'Synthetic withdrawal','confirmed':True},op)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:race_codes=list(pool.map(withdraw_award_race,range(2)))
 check(sorted(x[0] for x in race_codes)==[201,409],'rfq_withdraw_award_one_winner')
 race_state=next(x for x in call('sourcing/rfqs',auth=a)[1] if x['id']==race_rfq['id']);race_history=call(race_endpoint+'/offers',auth=a)[1]
 check((race_state['status']=='AWARDED' and race_history[0]['status']=='ACTIVE') or (race_state['status']=='OPEN' and race_history[0]['status']=='WITHDRAWN'),'rfq_never_awards_withdrawn_quote')
 # Serializable default protects delete-vs-reference without relying on READ COMMITTED.
 code,delete_product,_=call('products',{'name':'Synthetic delete race','sku':'RFQ-DELETE','unitMinor':'1'},op);check(code==201,'rfq_delete_race_product')
 def delete_reference_race(i):
  return call('sourcing/rfqs',{**rfq_body,'title':'Synthetic delete reference','lines':[{'productId':delete_product['id'],'quantity':1}]},op) if i==0 else call('products/'+delete_product['id'],{'revision':0},op,method='DELETE')
 with concurrent.futures.ThreadPoolExecutor(2) as pool:delete_results=list(pool.map(delete_reference_race,range(2)))
 check(sum(200<=x[0]<300 for x in delete_results)==1 and any(x[0] in (404,409) for x in delete_results),'rfq_delete_reference_race_one_winner')
 remaining_product=any(x['id']==delete_product['id'] for x in call('products',auth=op)[1]);check(remaining_product==(delete_results[0][0]==201),'rfq_no_open_reference_to_deleted_product')
 check(call('users',{'email':'viewer@qa.invalid','password':password,'role':'viewer'},a)[0]==201,'viewer_user_created')
 code,v,h=call('auth/login',{'tenant':'qa-a','email':'viewer@qa.invalid','password':password});viewer={'csrf':v['csrf'],'cookie':h['Set-Cookie'].split(';')[0].split('=',1)[1]}
 check(call('ledger',auth=viewer)[0]==403,'viewer_ledger_denied')
 check(call('reports/financial?period=2026-01',auth=viewer)[0]==403,'viewer_financial_report_denied')
 check(call('products',{'sku':'BAD','name':'Forbidden','unitMinor':'1'},viewer)[0]==403,'viewer_write_denied')
 zero=run(['docker','exec','-e','PGPASSWORD='+app_password,name,'psql','-h','127.0.0.1','-U','erp_app','-d','erp','-Atc','SELECT COUNT(*) FROM "Customer";']).strip();check(zero=='0','database_rls_denies_unscoped_reads')
 role=run(['docker','exec','-e','PGPASSWORD='+app_password,name,'psql','-h','127.0.0.1','-U','erp_app','-d','erp','-Atc','SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user;']).strip();check(role=='f|f','runtime_database_not_privileged')
 check(call('auth/logout',{},a)[0]==201,'logout')
 check(call('dashboard',auth=a)[0]==401,'revoked_session_denied')
 report={'passed':len(results),'checks':results,'real_postgresql':True,'existing_database_accessed':False}
 (ROOT/'qa-result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
finally:
 if api:
  api.terminate()
  try:api.wait(timeout=5)
  except subprocess.TimeoutExpired:api.kill();api.wait()
 subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
