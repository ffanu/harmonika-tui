"""Durable synthetic jobs. No external services, user keys, processes or logs."""
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from backend.operations_engine.jobs import JobStore,JobError,digest
KEY=b'synthetic-job-fixture-key-32-bytes!'

def concurrent_claim(root,queue):
    store=JobStore(root,KEY)
    job=store.prepare('actor','project','tool.execution','same-key',digest({'mechanic':1}))
    try:
        job=store.transition('actor','project',job['job_ref'],job['version'],'running',{'phase':'prepared'},lease_owner=str(os.getpid()))
        queue.put(('claimed',job['job_ref']))
    except JobError as error:queue.put((error.code,job['job_ref']))

def crash_after_intent(root,pipe):
    store=JobStore(root,KEY,clock=lambda:100)
    job=store.prepare('actor','project','tool.execution','crash-key',digest({'mechanic':2}))
    store.transition('actor','project',job['job_ref'],job['version'],'running',{'phase':'switch_started'},lease_owner='own-fixture-worker',lease_seconds=1)
    pipe.send(job['job_ref']);pipe.close();os._exit(0)

class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.now=100
        self.store=JobStore(self.root,KEY,clock=lambda:self.now)
    def prepare(self,key='fixture-key'):return self.store.prepare('actor','project','tool.execution',key,digest({'request':1}))
    def test_idempotency_durable_and_conflicting_request_refused(self):
        one=self.prepare();two=JobStore(self.root,KEY).prepare('actor','project','tool.execution','fixture-key',digest({'request':1}))
        self.assertEqual(one['job_ref'],two['job_ref'])
        with self.assertRaisesRegex(JobError,'idempotency_conflict'):self.store.prepare('actor','project','tool.execution','fixture-key',digest({'request':2}))
    def test_replacement_idempotency_key_cannot_replay_same_unknown_request(self):
        job=self.prepare();job=self.store.transition('actor','project',job['job_ref'],1,'running',{'phase':'switch_started'},lease_owner='worker')
        job=self.store.transition('actor','project',job['job_ref'],job['version'],'unknown',{'phase':'unknown'},lease_owner='worker')
        replacement=self.store.prepare('actor','project','tool.execution','replacement-key',digest({'request':1}))
        self.assertEqual(replacement['job_ref'],job['job_ref']);self.assertEqual(replacement['state'],'unknown')

    def test_owner_project_and_mac_bound_refs(self):
        job=self.prepare()
        for actor,project,ref in [('other','project',job['job_ref']),('actor','other',job['job_ref']),('actor','project',job['job_ref'][:-1]+('0' if job['job_ref'][-1]!='0' else '1'))]:
            with self.assertRaisesRegex(JobError,'job_not_found'):self.store.resume(actor,project,ref)
    def test_cas_and_worker_lease_fence(self):
        job=self.prepare();running=self.store.transition('actor','project',job['job_ref'],1,'running',{'phase':'prepared'},lease_owner='worker')
        with self.assertRaisesRegex(JobError,'version_conflict'):self.store.transition('actor','project',job['job_ref'],1,'running',{},lease_owner='other')
        with self.assertRaisesRegex(JobError,'lease_mismatch'):self.store.transition('actor','project',job['job_ref'],running['version'],'succeeded',{},lease_owner='other')
        complete=self.store.transition('actor','project',job['job_ref'],running['version'],'succeeded',{'phase':'verified'},lease_owner='worker')
        self.assertTrue(complete['terminal']);self.assertFalse(complete['retry_safe'])
        with self.assertRaisesRegex(JobError,'transition_invalid'):self.store.transition('actor','project',job['job_ref'],complete['version'],'running',{},lease_owner='worker')
    def test_stale_lease_unknown_reconciliation_does_not_replay(self):
        job=self.prepare();running=self.store.transition('actor','project',job['job_ref'],1,'running',{'phase':'switch_started'},lease_owner='worker',lease_seconds=1)
        self.now=102;unknown=self.store.resume('actor','project',job['job_ref'])
        self.assertEqual(unknown['state'],'unknown');self.assertGreater(unknown['version'],running['version']);self.assertTrue(unknown['resume_only'])
        with self.assertRaisesRegex(JobError,'transition_invalid'):self.store.transition('actor','project',job['job_ref'],unknown['version'],'running',{},lease_owner='worker')
        result=self.store.reconcile('actor','project',job['job_ref'],unknown['version'],'succeeded',{'effect_observed':True,'health_verified':True})
        self.assertEqual(result['state'],'succeeded')
    def test_cross_process_claim_executes_once(self):
        context=multiprocessing.get_context('spawn');queue=context.Queue()
        children=[context.Process(target=concurrent_claim,args=(str(self.root),queue)) for _ in range(5)]
        for child in children:child.start()
        results=[queue.get(timeout=20) for _ in children]
        for child in children:child.join(20);self.assertEqual(child.exitcode,0)
        self.assertEqual(sum(status=='claimed' for status,_ in results),1);self.assertEqual(len({ref for _,ref in results}),1)
        queue.close();queue.join_thread()
    def test_real_process_exit_after_intent_not_reexecuted(self):
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe(duplex=False)
        process=context.Process(target=crash_after_intent,args=(str(self.root),child));process.start();child.close()
        self.assertTrue(parent.poll(20));reference=parent.recv();parent.close();process.join(20);self.assertEqual(process.exitcode,0)
        self.now=105;job=self.store.resume('actor','project',reference);self.assertEqual(job['state'],'unknown')
        same=self.store.prepare('actor','project','tool.execution','crash-key',digest({'mechanic':2}));self.assertEqual(same['job_ref'],reference);self.assertEqual(same['state'],'unknown')
    def test_wal_and_data_firewall(self):
        job=self.store.prepare('private-actor@example.invalid','private-customer-project','tool.execution','do-not-log-chat-or-command',digest({'mechanic':3}))
        with sqlite3.connect(self.store.path) as db:self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
        for item in self.root.iterdir():
            if item.is_file():
                blob=item.read_bytes()
                for private in (b'private-actor',b'private-customer',b'do-not-log-chat',str(self.root).encode()):self.assertNotIn(private,blob)
        self.assertNotIn('lease_owner',job);self.assertNotIn('actor',job);self.assertNotIn('project',job)
        for raw in ({'command':'rm fixture'},{'path':'fixture'},{'output':'private'},{'error_type':'private_customer'},{'phase':'private_chat'}):
            with self.assertRaisesRegex(JobError,'evidence_invalid'):self.store.transition('private-actor@example.invalid','private-customer-project',job['job_ref'],1,'running',raw,lease_owner='fixture')
    def test_generic_reconciliation_cannot_assert_deployment_success(self):
        job=self.store.prepare('actor','project','deployment.apply','deploy-fixture',digest({'request':5}))
        job=self.store.transition('actor','project',job['job_ref'],job['version'],'running',{},lease_owner='worker')
        job=self.store.transition('actor','project',job['job_ref'],job['version'],'unknown',{},lease_owner='worker')
        with self.assertRaisesRegex(JobError,'engine_reconciliation_required'):
            self.store.reconcile('actor','project',job['job_ref'],job['version'],'succeeded',{'effect_observed':True,'health_verified':True})
        self.assertEqual(self.store.get('actor','project',job['job_ref'])['state'],'unknown')

    def test_database_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();outside=root/'outside';outside.write_text('private fixture');(root/'jobs.sqlite').symlink_to(outside)
            with self.assertRaisesRegex(JobError,'database_unsafe'):JobStore(root,KEY)
            self.assertEqual(outside.read_text(),'private fixture')

if __name__=='__main__':unittest.main()
