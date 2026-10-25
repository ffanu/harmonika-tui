import datetime as dt,json,sqlite3,tempfile,unittest
from pathlib import Path
from backend.telco_engine.common import DomainError,validate,parse_time,money
from backend.telco_engine.runtime import Runtime
from backend.telco_engine.store import Context
class Foundation(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.a='api-token:700';self.b='api-token:701'
    def tearDown(self):self.tmp.cleanup()
    def test_catalog_actual276_unique25_engines(self):
        c=self.rt.capabilities();self.assertEqual(len(c['skills']),276);self.assertEqual(len(set(x['id'] for x in c['skills'])),276);self.assertEqual(len(set(x['engine']['number'] for x in c['skills'])),25)
        self.assertFalse(c['features']['telco_network_live']);self.assertFalse(c['features']['telco_payment_gateway'])
    def test_no_fake_olt_action(self):
        with self.assertRaises(DomainError) as e:self.rt.invoke(self.a,'olt.firmware.upgrade',{},'one')
        self.assertEqual(e.exception.code,'skill_unavailable')
    def test_identity_spoof_rejected(self):
        with self.assertRaises(DomainError):self.rt.invoke('owner','report.arpu',{'period':'2026-10'},'x')
    def test_analytics_read_and_replay(self):
        r=self.rt.invoke(self.a,'report.arpu',{'period':'2026-10'},'same');self.assertEqual(r['status'],'completed');self.assertEqual(r['result']['local_subscribers'],0)
        self.assertTrue(self.rt.invoke(self.a,'report.arpu',{'period':'2026-10'},'same')['replayed'])
        with self.assertRaises(DomainError):self.rt.invoke(self.a,'report.arpu',{'period':'2026-09'},'same')
    def test_request_cross_actor404(self):
        r=self.rt.invoke(self.a,'report.arpu',{'period':'2026-10'},'id')
        with self.assertRaises(DomainError) as e:self.rt.status(self.b,r['id'])
        self.assertEqual(e.exception.status,404)
    def test_record_tenant_and_version(self):
        with self.rt.store.db() as d:
            a=Context(d,'tenant-a',self.a);b=Context(d,'tenant-b',self.b);r=a.create('subscriber',{'name':'Synthetic'},'same');b.create('subscriber',{'name':'Other'},'same')
            self.assertEqual(a.get('subscriber','same')['name'],'Synthetic');a.update('subscriber','same',{'name':'Updated'},1)
            with self.assertRaises(DomainError):a.update('subscriber','same',{'name':'Bad'},1)
            self.assertEqual(b.get('subscriber','same')['name'],'Other')
    def test_money_and_timestamps_strict(self):
        for bad in (True,1.5,-1,10**16):
            with self.assertRaises(DomainError):money(bad)
        with self.assertRaises(DomainError):parse_time('2026-10-01T00:00:00')
    def test_no_silent_truncated_aggregate(self):
        with self.rt.store.db() as d:
            c=Context(d,'test',self.a)
            for n in range(3):c.create('subscriber',{'name':'Synthetic'},'s'+str(n))
            with self.assertRaises(DomainError):c.list('subscriber',limit=2)
    def test_unknown_fields_and_bool_integer(self):
        with self.assertRaises(DomainError):self.rt.invoke(self.a,'report.arpu',{'period':'2026-10','tenant':'other'},'id')
        with self.assertRaises(DomainError):validate(True,{'type':'integer'})
    def test_audit_does_not_contain_payload(self):
        r=self.rt.invoke(self.a,'reg.audit.prepare',{'policy_id':'p','required_evidence_ids':['private-example-id'],'provided_evidence_ids':[]},'audit')
        with self.rt.store.db() as d:
            data=''.join(x[0] for x in d.execute('SELECT data FROM audit'));self.assertNotIn('private-example-id',data)
    def test_stale_forecast_evidence_is_failed_not_success(self):
        obs=[{'timestamp':'2020-01-%02dT00:00:00Z'%n,'value':n,'source':'fixture'} for n in range(1,7)]
        r=self.rt.invoke(self.a,'capacity.forecast',{'observations':obs,'horizon_seconds':60},'stale');self.assertEqual(r['status'],'failed');self.assertEqual(r['error']['code'],'stale_evidence')
    def test_no_fraud_verdict_from_anomaly(self):
        r=self.rt.invoke(self.a,'fraud.usage.anomaly',{'baseline':[1,2,3,4,5],'value':100,'z_threshold':3,'source':'fixture'},'anomaly')
        self.assertTrue(r['result']['anomaly_indicated']);self.assertFalse(r['result']['confirmed_fraud'])
class Approval(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.rt=Runtime(Path(self.tmp.name)/'state.sqlite');self.a='api-token:710';self.b='api-token:711'
        self.rt.handlers['sub.create']=lambda c,a:c.create('subscriber',{'name':a['name']});self.rt.schemas['sub.create']={'type':'object','properties':{'name':{'type':'string','minLength':1,'maxLength':100}},'required':['name'],'additionalProperties':False};self.rt.mutations.add('sub.create')
    def tearDown(self):self.tmp.cleanup()
    def make(self,key='create',name='Synthetic'):return self.rt.invoke(self.a,'sub.create',{'name':name},key)
    def test_approval_writes_nothing_until_confirmed(self):
        r=self.make();self.assertEqual(r['status'],'approval_required')
        with self.rt.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM records').fetchone()[0],0)
        out=self.rt.approve(self.a,r['id'],r['input_hash']);self.assertEqual(out['status'],'completed')
        again=self.rt.approve(self.a,r['id'],r['input_hash']);self.assertTrue(again['replayed'])
        with self.rt.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM records').fetchone()[0],1)
    def test_cross_actor_and_hash_approval_denied(self):
        r=self.make()
        with self.assertRaises(DomainError):self.rt.approve(self.b,r['id'],r['input_hash'])
        with self.assertRaises(DomainError):self.rt.approve(self.a,r['id'],'bad')
    def test_stale_approval_does_not_change_records(self):
        a=self.make('one');b=self.make('two');self.rt.approve(self.a,a['id'],a['input_hash'])
        out=self.rt.approve(self.a,b['id'],b['input_hash']);self.assertEqual(out['status'],'expired');self.assertEqual(out['error']['code'],'stale_approval')
    def test_expired_approval_is_stable(self):
        r=self.make()
        with self.rt.store.db() as d:d.execute("UPDATE requests SET expires='2000-01-01T00:00:00Z' WHERE id=?",(r['id'],))
        self.assertEqual(self.rt.approve(self.a,r['id'],r['input_hash'])['status'],'expired')
    def test_stopped_pending_never_writes(self):
        r=self.make();self.assertEqual(self.rt.stop(self.a,r['id'])['status'],'stopped')
        with self.assertRaises(DomainError):self.rt.approve(self.a,r['id'],r['input_hash'])
    def test_failed_partial_business_change_rolls_back(self):
        def broken(c,a):c.create('subscriber',{'name':'partial'});raise DomainError('fixture_failure')
        self.rt.handlers['sub.create']=broken;r=self.make();out=self.rt.approve(self.a,r['id'],r['input_hash']);self.assertEqual(out['status'],'failed')
        with self.rt.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM records').fetchone()[0],0)
    def test_idempotent_race_creates_one_request(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(4) as pool:rows=list(pool.map(lambda _:self.make('race'),range(8)))
        self.assertEqual(len(set(x['id'] for x in rows)),1)
    def test_readonly_cannot_write_even_if_handler_forgets_flag(self):
        self.rt.mutations.remove('sub.create');r=self.make();self.assertEqual(r['status'],'failed');self.assertEqual(r['error']['code'],'mutation_not_authorized')
    def test_event_sequences_and_replay(self):
        r=self.make();self.rt.approve(self.a,r['id'],r['input_hash']);e=self.rt.events(self.a,r['id']);self.assertEqual([x['sequence'] for x in e],[1,2,3]);self.assertEqual(self.rt.events(self.a,r['id'],2)[0]['type'],'response.completed')
    def test_running_state_is_durable_and_stop_does_not_claim_cancel(self):
        import concurrent.futures,threading
        entered=threading.Event();proceed=threading.Event()
        def slow(c,a):entered.set();proceed.wait(5);return c.create('subscriber',{'name':'Synthetic'})
        self.rt.handlers['sub.create']=slow;r=self.make()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            f=pool.submit(self.rt.approve,self.a,r['id'],r['input_hash']);self.assertTrue(entered.wait(3))
            self.assertEqual(self.rt.status(self.a,r['id'])['status'],'running')
            with self.assertRaises(DomainError) as e:self.rt.stop(self.a,r['id'])
            self.assertEqual(e.exception.code,'operation_already_committing');proceed.set();self.assertEqual(f.result(3)['status'],'completed')
    def test_restart_never_reexecutes_reserved_operation(self):
        r=self.make()
        with self.rt.store.db() as d:d.execute("UPDATE requests SET state='running' WHERE id=?",(r['id'],))
        self.rt.recover();self.assertEqual(self.rt.status(self.a,r['id'])['status'],'interrupted')
        with self.rt.store.db() as d:self.assertEqual(d.execute('SELECT count(*) FROM records').fetchone()[0],0)
