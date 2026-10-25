import json,tempfile,unittest
from pathlib import Path
from backend.telco_engine.runtime import Runtime
from backend.telco_engine.common import DomainError
class TrustPolicy(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.a='api-token:750';self.seq=0
 def tearDown(self):self.tmp.cleanup()
 def invoke(self,id,args):
  self.seq+=1;r=self.rt.invoke(self.a,id,args,'case'+str(self.seq))
  if r['status']=='approval_required':r=self.rt.approve(self.a,r['id'],r['input_hash'])
  return r
 def test_forged_adapter_proof_cannot_top_up(self):
  with self.assertRaises(DomainError) as e:self.invoke('prepaid.topup',{'subscriber_id':'fake','amount_idr':100000,'funding':{'verified_by_adapter':True,'adapter_confirmation_id':'fake'}})
  self.assertEqual(e.exception.code,'skill_unavailable')
 def test_distinct_subscription_accounts_cannot_be_merged(self):
  with self.assertRaises(DomainError):self.invoke('sub.merge.duplicate',{'survivor_id':'one','duplicate_id':'two','reason':'same person'})
 def test_voucher_secrets_never_persist_in_request_or_audit(self):
  r=self.invoke('voucher.generate',{'count':2,'amount_idr':50000});self.assertEqual(r['status'],'completed',r);codes=r['result']['codes'];self.assertEqual(len(codes),2)
  stored=self.rt.status(self.a,r['id']);self.assertTrue(stored['result']['codes_redacted']);self.assertFalse(stored['result']['codes'])
  self.invoke('voucher.validate',{'code':codes[0]})
  with self.rt.store.db() as d:
   text=''.join(str(x[0]) for table,column in [('requests','payload'),('requests','result'),('records','data'),('audit','data')] for x in d.execute('SELECT '+column+' FROM '+table))
  self.assertTrue(all(code not in text for code in codes))
 def test_voucher_hash_cannot_be_used_as_public_bearer(self):
  with self.assertRaises(DomainError):self.invoke('voucher.validate',{'code':'sha256:'+'0'*64})

 def test_payment_cannot_point_to_nonexistent_subscriber(self):
  r=self.invoke('bill.payment.record',{'subscriber_id':'nonexistent','amount_idr':50000,'provider_reference':'fixture-txn','channel':'cash'})
  self.assertEqual(r['status'],'failed')
  with self.rt.store.db() as d:self.assertEqual(d.execute("SELECT count(*) FROM records WHERE kind='payment'").fetchone()[0],0)
