import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.quality_audit.store import Store
from backend.quality_audit.redaction import sanitize


def event(index=1,token=7,**changes):
    return dict(trace_id=f'{index:032x}',token_id=token,session_id='synthetic-session',
                category='chat',status=200,latency_ms=12,error_code='none',complete=True,
                origin='gateway',capture_truncated=False,question='Synthetic question canary',
                answer='Synthetic answer canary',steps=[],**changes)


class AuditStore(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=[1000000]
        self.path=Path(self.tmp.name)/'private/audit.db'
        self.key=os.urandom(32);self.identity=os.urandom(32)
        self.store=Store(self.path,self.key,self.identity,clock=lambda:self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_metadata_only_and_no_plaintext_in_db_or_wal(self):
        self.store.ingest(event())
        row=self.store.list(7,include_content=True)['traces'][0]
        self.assertFalse(row['content_saved']);self.assertNotIn('sample',row)
        self.assertNotIn('synthetic-session',json.dumps(row))
        for p in self.path.parent.iterdir():
            self.assertNotIn(b'question canary',p.read_bytes())
            self.assertNotIn(b'answer canary',p.read_bytes())

    def test_consented_content_is_encrypted_and_scope_isolated(self):
        self.store.consent(7,True)
        self.store.ingest(event())
        self.store.ingest(event(2,8))
        row=self.store.list(7,include_content=True)['traces'][0]
        self.assertEqual(row['sample']['question']['text'],'Synthetic question canary')
        self.assertTrue(row['sample_is_untrusted'])
        self.assertNotIn('sample',self.store.list(7)['traces'][0])
        self.assertFalse(self.store.list(8,include_content=True)['traces'][0]['content_saved'])
        self.assertNotEqual(self.store.list(7)['key_ref'],self.store.list(8)['key_ref'])
        for p in self.path.parent.iterdir():
            self.assertNotIn(b'question canary',p.read_bytes())

    def test_redaction_credentials_and_contact_data(self):
        source='api_key="hai_live_'+('X'*40)+'" Bearer synthetic-token secret: thing email person@example.test phone +62 812 3456 7890 server 192.0.2.10 https://u:p@example.test/?token=abc123'
        text=sanitize(source,16000)['text']
        for secret in ('X'*40,'synthetic-token','thing','person@example.test','812 3456','192.0.2.10','u:p','abc123'):
            self.assertNotIn(secret,text)
        pem='-----BEGIN PRIVATE KEY-----\nSYNTHETIC-CANARY\n-----END PRIVATE KEY-----'
        self.assertNotIn('SYNTHETIC-CANARY',sanitize(pem,1000)['text'])

    def test_raw_media_omitted_and_oversize_flagged(self):
        r=sanitize('before data:image/png;base64,QUJDREVGRw== after',10)
        self.assertNotIn('QUJD',r['text']);self.assertTrue(r['truncated'])
        with self.assertRaises(ValueError):sanitize('x'*262145,100)

    def test_revoke_removes_samples_and_prevents_new_samples(self):
        self.store.consent(7,True);self.store.ingest(event())
        self.store.consent(7,False);self.store.ingest(event(2))
        self.assertTrue(all(not r['content_saved'] for r in self.store.list(7,include_content=True)['traces']))
        with self.store.connect() as c:self.assertEqual(c.execute('select count(*) from samples').fetchone()[0],0)

    def test_expired_consent_and_trace_retention(self):
        self.store.consent(7,True,days=1);self.store.ingest(event())
        self.now[0]+=86401;self.store.ingest(event(2));self.store.purge()
        self.assertTrue(all(not r['content_saved'] for r in self.store.list(7,include_content=True)['traces']))
        self.now[0]+=30*86400
        self.assertEqual(self.store.purge()['deleted_traces'],2)
        self.assertFalse(self.store.list(7)['traces'])

    def test_dedup_and_cross_key_trace_conflict(self):
        self.store.ingest(event());self.assertTrue(self.store.ingest(event())['duplicate'])
        with self.assertRaises(ValueError):self.store.ingest(event(token=8))
        self.assertEqual(len(self.store.list(7)['traces']),1)

    def test_no_client_claim_of_server_execution(self):
        e=event();e['steps']=[{'kind':'tool.server_executed','skill_id':'web.search','origin':'client_reported','status':'completed'}]
        with self.assertRaises(ValueError):self.store.ingest(e)
        e['steps'][0].update(kind='tool.client_reported')
        self.store.ingest(e)
        step=self.store.list(7)['traces'][0]['steps'][0]
        self.assertEqual(step['origin'],'client_reported')
        self.assertEqual(step['skill_id'],'web.search')

    def test_unknown_tool_names_are_hashed(self):
        e=event();e['steps']=[{'kind':'tool.requested','skill_id':'private-customer-name','origin':'model_requested','status':'requested'}]
        self.store.ingest(e)
        encoded=json.dumps(self.store.list(7))
        self.assertNotIn('private-customer-name',encoded)

    def test_limits_errors_and_unexpected_fields(self):
        for change in ({'status':True},{'error_code':'raw secret text'},{'origin':'caller'},{'token_id':0},{'api_key':'secret'},{'latency_ms':-1},{'complete':'yes'}):
            e=event();e.update(change)
            with self.assertRaises(ValueError):self.store.ingest(e)
        self.assertFalse(self.store.list(7)['traces'])

    def test_capacity_is_explicit(self):
        self.store.max_events=1;self.store.ingest(event())
        with self.assertRaisesRegex(ValueError,'storage_capacity'):self.store.ingest(event(2))
        self.assertEqual(len(self.store.list(7)['traces']),1)

    def test_pagination_purge_and_summary_are_tenant_bound(self):
        for i in range(1,5):self.store.ingest(event(i))
        e=event(5,8);e.update(status=503,error_code='upstream_unavailable',complete=False);self.store.ingest(e)
        first=self.store.list(7,limit=2);second=self.store.list(7,limit=2,before=first['next_before'])
        self.assertEqual(len({x['trace_id'] for x in first['traces']+second['traces']}),4)
        self.assertFalse(self.store.summary(7)['findings']);self.assertTrue(self.store.summary(8)['findings'])
        self.store.purge(7);self.assertEqual(len(self.store.list(8)['traces']),1)

    def test_parallel_writers_and_wal_permissions(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda i:self.store.ingest(event(i)),range(1,16)))
        self.assertEqual(len(self.store.list(7)['traces']),15)
        with self.store.connect() as c:self.assertEqual(c.execute('pragma journal_mode').fetchone()[0],'wal')
        self.assertEqual(self.path.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_wrong_key_and_ciphertext_tampering_fail_closed(self):
        self.store.consent(7,True);self.store.ingest(event())
        with self.store.connect() as c:c.execute('update samples set ciphertext=?',(b'tampered',))
        with self.assertRaises(Exception):self.store.list(7,include_content=True)

    def test_successful_http_response_can_report_failed_background_job(self):
        e=event();e['steps']=[{'kind':'job.status','skill_id':'job:synthetic-id','origin':'server_reported','status':'failed'}]
        self.store.ingest(e)
        self.assertTrue(self.store.summary(7)['findings'])
        self.assertEqual(self.store.summary(7)['reported_job_states'][0]['status'],'failed')


if __name__=='__main__':unittest.main()
