"""Cross-domain acceptance using real SQLite transactions, never company data."""
import asyncio,datetime as dt,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.telco_engine.runtime import Runtime
from backend.telco_engine.common import DomainError,now_iso
from backend.telco_engine.agent import run_agent,redact_one_time

class Acceptance(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.a='api-token:760';self.n=0
 def tearDown(self):self.tmp.cleanup()
 def invoke(self,id,args,actor=None,expect='completed'):
  self.n+=1;r=self.rt.invoke(actor or self.a,id,args,'accept'+str(self.n))
  if r['status']=='approval_required':r=self.rt.approve(actor or self.a,r['id'],r['input_hash'])
  self.assertEqual(r['status'],expect,r);return r
 def seed(self):
  p=self.invoke('product.create',{'name':'Fixture Plan','price_idr':150000,'speed_mbps':30})['result']['product']['id']
  s=self.invoke('sub.create',{'name':'Fixture Customer','package_id':p})['result']['subscriber']['id'];return s,p
 def test_invoice_unverified_payment_does_not_make_paid(self):
  s,p=self.seed();args={'subscriber_id':s,'product_id':p,'period':'2026-09','due_at':'2026-10-02T00:00:00Z'}
  invoice=self.invoke('bill.invoice.generate',args)['result']['invoice'];self.assertEqual(invoice['amount_idr'],150000)
  self.invoke('bill.invoice.generate',args,expect='failed')
  pay=self.invoke('bill.payment.record',{'subscriber_id':s,'amount_idr':150000,'channel':'cash','provider_reference':'fixture-unverified'})['result']['payment']
  self.assertEqual(pay['status'],'recorded_unverified')
  with self.rt.store.db() as d:
   row=json.loads(d.execute("SELECT data FROM records WHERE kind='invoice'").fetchone()[0]);self.assertEqual(row['paid_idr'],0)
  self.invoke('prov.order.create',{'subscriber_id':s,'product_id':p},actor='api-token:761',expect='failed')
 def test_two_orders_reserve_single_port_atomically(self):
  s,p=self.seed();d=self.invoke('netinv.device.register',{'name':'Fixture OLT','device_type':'olt'})['result']['device_id']
  self.invoke('netinv.port.management',{'device_id':d,'ports':[{'port_code':'1/1/1'}]})
  orders=[self.invoke('prov.order.create',{'subscriber_id':s,'product_id':p})['result']['provision_order_id'] for _ in range(2)]
  self.invoke('prov.allocate.resource',{'provision_order_id':orders[0],'resource_type':'olt_port','count':1})
  self.invoke('prov.allocate.resource',{'provision_order_id':orders[1],'resource_type':'olt_port','count':1},expect='failed')
  with self.rt.store.db() as db:
   rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM records WHERE kind='resource'")];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['provision_order_id'],orders[0])
 def test_exact_outage_union_not_double_counted(self):
  result=self.invoke('report.network.uptime',{'start_at':'2026-10-01T00:00:00Z','end_at':'2026-10-01T01:00:00Z','outages':[{'start_at':'2026-10-01T00:00:00Z','end_at':'2026-10-01T00:20:00Z'},{'start_at':'2026-10-01T00:10:00Z','end_at':'2026-10-01T00:30:00Z'}],'source':'fixture','measurement_complete':True})['result']
  self.assertEqual(result['outage_seconds'],1800);self.assertEqual(result['uptime_percent'],'50.000000')
 def test_no_default_regulatory_rate_or_legal_certification(self):
  r=self.invoke('reg.universal.service',{'base_idr':101,'rate_bps':5000,'policy_id':'fixture-not-law','period':'2026-09'})['result']
  self.assertEqual(r['amount_idr'],51);self.assertFalse(r['obligation_or_rate_verified']);self.assertFalse(r['payment_or_filing_performed'])
  with self.assertRaises(DomainError):self.invoke('reg.universal.service',{'base_idr':101,'period':'2026-09'})
 def test_parallel_pure_computation_no_arbitrary_action(self):
  r=self.invoke('workflow.parallel',{'tasks':[{'id':'cost','skill_id':'capacity.cost.benefit','input':{'capex_idr':100,'monthly_incremental_revenue_idr':20,'monthly_incremental_opex_idr':10}},{'id':'cost2','skill_id':'capacity.cost.benefit','input':{'capex_idr':50,'monthly_incremental_revenue_idr':20,'monthly_incremental_opex_idr':10}}]})['result'];self.assertEqual(len(r['results']),2);self.assertEqual(r['results'][0]['result']['break_even_months'],'10.00')
 def test_actor_cleanup_never_purges_other_tenant(self):
  old=self.rt.invoke('api-token:761','report.arpu',{'period':'2026-09'},'other')
  with self.rt.store.db() as d:d.execute("UPDATE requests SET created='2026-01-01T00:00:00Z' WHERE id=?",(old['id'],))
  self.invoke('report.arpu',{'period':'2026-09'});self.assertEqual(self.rt.status('api-token:761',old['id'])['status'],'completed')
 def test_abandoned_approval_expires_on_next_invocation(self):
  job=self.rt.invoke(self.a,'sub.create',{'name':'Pending'},'expired')
  with self.rt.store.db() as d:d.execute("UPDATE requests SET expires='2000-01-01T00:00:00Z' WHERE id=?",(job['id'],))
  self.invoke('report.arpu',{'period':'2026-09'});self.assertEqual(self.rt.status(self.a,job['id'])['status'],'expired')
 def test_voucher_agent_redaction_and_approval_recovery(self):
  raw={'skill_id':'voucher.generate','result':{'codes':['SECRET-FIXTURE']},'one_time_secret_material':True}
  self.assertNotIn('SECRET-FIXTURE',json.dumps(redact_one_time({'steps':[raw],'request':raw})))
  async def scenario():
   async def decide(payload):return {'tool_calls':[{'id':'read','function':{'name':'report_arpu','arguments':'{"period":"2026-09"}'}}]}
   self.rt.mutations.add('report.arpu');msgs=[{'role':'user','content':'laporan arpu'}]
   first=await run_agent(self.rt,self.a,msgs,'agent-approve',decide);j=first['request'];self.rt.approve(self.a,j['id'],j['input_hash'])
   second=await run_agent(self.rt,self.a,msgs,'agent-approve',decide);self.assertTrue(second['replayed']);self.assertEqual(second['status'],'completed');self.assertFalse(second['approval_required']);self.assertEqual(second['steps'][0]['status'],'completed')
  asyncio.run(scenario())
 def test_real_sqlite_splitter_and_known_length_route(self):
  s,p=self.seed();devices=[self.invoke('netinv.device.register',{'name':'Device'+str(i),'device_type':'odp'})['result']['device_id'] for i in range(2)]
  self.invoke('netinv.link.management',{'a_device_id':devices[0],'b_device_id':devices[1],'length_m':120})
  route=self.invoke('netinv.fiber.route',{'from_device_id':devices[0],'to_device_id':devices[1]})['result'];self.assertEqual(route['total_length_m'],120);self.assertFalse(route['coverage_promised'])
  splitter=self.invoke('netinv.splitter.management',{'action':'register','device_id':devices[0],'ratio':4})['result']['splitter_id']
  self.invoke('netinv.splitter.management',{'action':'allocate','splitter_id':splitter,'subscriber_id':s,'port_no':1})
  self.invoke('netinv.splitter.management',{'action':'allocate','splitter_id':splitter,'subscriber_id':s,'port_no':1},expect='failed')
  self.invoke('netinv.splitter.management',{'action':'release','splitter_id':splitter,'port_no':1})
  self.assertEqual(self.invoke('netinv.splitter.management',{'action':'inspect','splitter_id':splitter})['result']['free_count'],4)
 def test_unknown_link_length_is_not_invented_as_zero(self):
  devices=[self.invoke('netinv.device.register',{'name':'Unknown'+str(i),'device_type':'odp'})['result']['device_id'] for i in range(2)]
  self.invoke('netinv.link.management',{'a_device_id':devices[0],'b_device_id':devices[1]})
  r=self.invoke('netinv.fiber.route',{'from_device_id':devices[0],'to_device_id':devices[1]})['result'];self.assertIsNone(r['total_length_m']);self.assertEqual(r['links_missing_length_ignored'],1)
 def test_cpu_fractional_future_and_equivalent_timestamps(self):
  from backend.telco_engine.extensions import _evidence_samples
  class C:now='2026-10-24T12:00:00Z'
  r=_evidence_samples(C(),[{'source':'p','observed_at':'2026-10-24T11:59:00Z','value_int':80},{'source':'p','observed_at':'2026-10-24T18:59:00+07:00','value_int':80},{'source':'p','observed_at':'2026-10-24T12:00:00.100Z','value_int':99}],3600,{'value_int':(0,100)})
  self.assertEqual(r['duplicates'],1);self.assertEqual(r['future'],1);self.assertEqual(len(r['fresh']),1)
