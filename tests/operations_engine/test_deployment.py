"""Real owned file/HTTP canaries; no external services, commands or signals."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from backend.operations_engine.deployment import DeploymentEngine,DeploymentError,tree,alias_lock
from backend.operations_engine.jobs import JobStore,JobError
KEY=b'synthetic-deploy-fixture-key-32b!'
ACTOR='api-token:7';PROJECT='fixture-project'

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        try:healthy=(self.server.release/'current/health.txt').read_bytes()==b'healthy' and not self.server.fail_all
        except OSError:healthy=False
        body=b'healthy' if healthy else b'unhealthy'
        self.send_response(200 if healthy else 503);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def hold_physical_worker(root,pipe):
    with alias_lock(Path(root),'owned-fixture-worker'):
        pipe.send(True);pipe.recv()
    pipe.close()

def crash_physical_worker(root,pipe):
    with alias_lock(Path(root),'owned-crash-fixture'):
        pipe.send(True);pipe.close();os._exit(0)

class Crash(BaseException):pass

class Deployments(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.release=self.root/'release';self.release.mkdir(mode=0o700);(self.release/'releases').mkdir()
        self.old=self.release/'releases/baseline';self.old.mkdir();(self.old/'health.txt').write_bytes(b'healthy');(self.old/'index.html').write_text('old fixture')
        (self.release/'current').symlink_to('releases/baseline',target_is_directory=True)
        self.artifacts=self.root/'artifacts';self.artifacts.mkdir(mode=0o700);self.artifact=self.artifacts/'build';self.artifact.mkdir();(self.artifact/'health.txt').write_bytes(b'healthy');(self.artifact/'index.html').write_text('new fixture')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.release=self.release;self.server.fail_all=False
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
        self.config={'deployments':{'fixture':{'release_root':str(self.release),'artifacts_root':str(self.artifacts),'health_url':'http://127.0.0.1:'+str(self.server.server_port)+'/health','health_expect':{'status':200,'body_sha256':hashlib.sha256(b'healthy').hexdigest()},'actors':[ACTOR],'projects':[PROJECT]}}}
        self.store=JobStore(self.root/'state',KEY);self.engine=DeploymentEngine(self.root/'state',KEY,self.config,self.store)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(5)
    def preview(self):return self.engine.preview(ACTOR,PROJECT,'fixture','build')
    def apply(self,plan=None,key='fixture-apply'):return self.engine.apply(ACTOR,PROJECT,'fixture','build',(plan or self.preview())['plan_sha256'],True,key)
    def test_preview_is_stable_scoped_readonly_and_not_authorization(self):
        one=self.preview();two=self.preview();self.assertEqual(one,two)
        self.assertFalse(one['mutation_authorized']);self.assertTrue(one['requires_review']);self.assertEqual(os.readlink(self.release/'current'),'releases/baseline')
        self.assertNotIn(str(self.root),json.dumps(one));self.assertNotIn('build',json.dumps(one));self.assertFalse((self.release/'.operations-deployment.lock').exists())
    def test_real_deployment_and_lost_ack_duplicate_do_not_replay(self):
        plan=self.preview();result=self.apply(plan)
        self.assertTrue(result['ok'],result);self.assertTrue(result['job']['evidence']['health_verified']);self.assertEqual((self.release/'current/index.html').read_text(),'new fixture')
        pointer=os.readlink(self.release/'current');versions=len(list((self.release/'releases').iterdir()))
        with patch.object(self.engine,'_switch',side_effect=AssertionError('must not switch twice')):
            duplicate=self.apply(plan)
        self.assertEqual(duplicate['job']['job_ref'],result['job']['job_ref']);self.assertTrue(duplicate['ok']);self.assertEqual(os.readlink(self.release/'current'),pointer);self.assertEqual(len(list((self.release/'releases').iterdir())),versions)
    def test_failed_health_canary_has_verified_rollback_never_false_success(self):
        (self.artifact/'health.txt').write_bytes(b'broken')
        result=self.apply();self.assertFalse(result['ok'],result);self.assertEqual(result['status'],'rolled_back');self.assertTrue(result['rollback_verified']);self.assertTrue(result['job']['evidence']['health_verified'])
        self.assertEqual(os.readlink(self.release/'current'),'releases/baseline');self.assertEqual((self.release/'current/index.html').read_text(),'old fixture')
    def test_rollback_health_failure_is_unknown_until_actual_reconciliation(self):
        (self.artifact/'health.txt').write_bytes(b'broken');self.server.fail_all=True
        result=self.apply();self.assertEqual(result['status'],'unknown');self.assertFalse(result['rollback_verified']);self.assertFalse(result['ok'])
        self.server.fail_all=False
        with patch.object(self.engine,'_switch',side_effect=AssertionError('reconcile must only observe')):
            observed=self.engine.reconcile(ACTOR,PROJECT,'fixture',result['job']['job_ref'],result['job']['version'])
        self.assertEqual(observed['status'],'rolled_back');self.assertTrue(observed['rollback_verified']);self.assertFalse(observed['ok'])
    def test_explicit_plan_and_review_required(self):
        plan=self.preview()
        for ack in (False,1,'true',None):
            with self.assertRaisesRegex(DeploymentError,'review_required'):self.engine.apply(ACTOR,PROJECT,'fixture','build',plan['plan_sha256'],ack,'fixture')
        result=self.engine.apply(ACTOR,PROJECT,'fixture','build','0'*64,True,'wrong-plan');self.assertFalse(result['ok']);self.assertEqual(result['status'],'failed');self.assertEqual(os.readlink(self.release/'current'),'releases/baseline')
    def test_changed_artifact_and_pointer_invalidate_reviewed_plan(self):
        plan=self.preview();(self.artifact/'index.html').write_text('changed after review');result=self.apply(plan)
        self.assertFalse(result['ok']);self.assertEqual(os.readlink(self.release/'current'),'releases/baseline')
        newer=self.release/'releases/other';newer.mkdir();(newer/'health.txt').write_bytes(b'healthy');(newer/'index.html').write_text('other')
        plan=self.preview();(self.release/'current').unlink();(self.release/'current').symlink_to('releases/other',target_is_directory=True)
        result=self.apply(plan,key='changed-pointer');self.assertFalse(result['ok']);self.assertEqual(os.readlink(self.release/'current'),'releases/other')
    def test_registered_actor_project_and_exact_health_url_only(self):
        for actor,project in [('api-token:8',PROJECT),(ACTOR,'foreign')]:
            with self.assertRaisesRegex(DeploymentError,'scope_denied'):self.engine.preview(actor,project,'fixture','build')
        for url in ['http://example.invalid:80/health','http://127.0.0.1:80/health?token=private','http://user:private@127.0.0.1:80/health','https://127.0.0.1:80/health']:
            self.config['deployments']['fixture']['health_url']=url
            with self.assertRaisesRegex(DeploymentError,'health_url_denied'):self.preview()
    def test_traversal_symlinks_and_hardlinks_refused(self):
        for value in ['../build','/build','build/../build','build\\file','build//file']:
            with self.assertRaises(DeploymentError):self.engine.preview(ACTOR,PROJECT,'fixture',value)
        secret=self.root/'private-fixture';secret.write_text('do not copy')
        (self.artifact/'link').symlink_to(secret)
        with self.assertRaisesRegex(DeploymentError,'path_unsafe'):self.preview()
        (self.artifact/'link').unlink();os.link(secret,self.artifact/'hardlink')
        with self.assertRaisesRegex(DeploymentError,'path_unsafe'):self.preview()
    def test_manual_rollback_requires_reviewed_plan_and_guarded_current_release(self):
        deployed=self.apply();job=deployed['job']['job_ref'];preview=self.engine.rollback_preview(ACTOR,PROJECT,'fixture',job)
        result=self.engine.rollback(ACTOR,PROJECT,'fixture',job,preview['plan_sha256'],True,'manual-rollback')
        self.assertTrue(result['ok'],result);self.assertEqual(os.readlink(self.release/'current'),'releases/baseline')
        duplicate=self.engine.rollback(ACTOR,PROJECT,'fixture',job,preview['plan_sha256'],True,'manual-rollback');self.assertEqual(duplicate['job']['job_ref'],result['job']['job_ref'])
    def test_backup_tampering_never_rolls_back_foreign_or_changed_release(self):
        result=self.apply();(self.old/'index.html').write_text('tampered baseline')
        with self.assertRaisesRegex(DeploymentError,'previous_changed'):self.engine.rollback_preview(ACTOR,PROJECT,'fixture',result['job']['job_ref'])
    def test_exception_after_pointer_effect_reconciles_without_replay(self):
        switch=self.engine._switch
        def lost_ack(*args):switch(*args);raise OSError('synthetic lost acknowledgement')
        plan=self.preview()
        with patch.object(self.engine,'_switch',side_effect=lost_ack):result=self.apply(plan)
        self.assertEqual(result['status'],'unknown');self.assertEqual((self.release/'current/index.html').read_text(),'new fixture')
        with patch.object(self.engine,'_switch',side_effect=AssertionError('must not repeat')):
            duplicate=self.apply(plan,key='fixture-apply');self.assertEqual(duplicate['status'],'unknown')
            observed=self.engine.reconcile(ACTOR,PROJECT,'fixture',result['job']['job_ref'],result['job']['version'])
        self.assertTrue(observed['ok']);self.assertEqual(observed['status'],'succeeded')
    def test_process_lease_stale_only_resume_no_actions(self):
        plan=self.preview();switch=self.engine._switch
        def crash(*args):switch(*args);raise Crash()
        with patch.object(self.engine,'_switch',side_effect=crash):
            with self.assertRaises(Crash):self.apply(plan)
        # Simulate passage of durable lease time; do not signal any process.
        self.store.clock=lambda:10**12
        with self.store._db() as db:reference=self.store._ref(db.execute('SELECT * FROM jobs').fetchone())
        with patch.object(self.engine,'_switch',side_effect=AssertionError('resume must not execute')):
            result=self.engine.resume(ACTOR,PROJECT,'fixture',reference)
        self.assertEqual(result['status'],'unknown');self.assertFalse(result['ok']);self.assertFalse(result['retry_safe'])
    def test_unknown_deployment_blocks_replay_under_new_key(self):
        plan=self.preview();switch=self.engine._switch
        def lost_ack(*args):switch(*args);raise OSError('synthetic lost acknowledgement')
        with patch.object(self.engine,'_switch',side_effect=lost_ack):unknown=self.apply(plan)
        self.assertEqual(unknown['status'],'unknown');newplan=self.preview()
        with patch.object(self.engine,'_switch',side_effect=AssertionError('new key must not bypass uncertainty')):
            blocked=self.apply(newplan,key='different-key')
        self.assertFalse(blocked['ok']);self.assertEqual(blocked['error']['code'],'deployment_reconciliation_required')
        self.assertEqual(blocked['status'],'prepared')

    def test_reconciliation_requires_physical_worker_quiescence_not_only_lease_expiry(self):
        plan=self.preview();switch=self.engine._switch
        def lost_ack(*args):switch(*args);raise OSError('synthetic lost acknowledgement')
        with patch.object(self.engine,'_switch',side_effect=lost_ack):unknown=self.apply(plan)
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe()
        process=context.Process(target=hold_physical_worker,args=(str(self.release),child));process.start();child.close()
        try:
            self.assertTrue(parent.poll(20));self.assertTrue(parent.recv())
            blocked=self.engine.reconcile(ACTOR,PROJECT,'fixture',unknown['job']['job_ref'],unknown['job']['version'])
            self.assertEqual(blocked['status'],'unknown');self.assertEqual(blocked['error']['code'],'deployment_worker_not_quiescent')
        finally:
            parent.send('release');parent.close();process.join(20)
        self.assertEqual(process.exitcode,0)
        result=self.engine.reconcile(ACTOR,PROJECT,'fixture',unknown['job']['job_ref'],unknown['job']['version'])
        self.assertTrue(result['ok'])

    def test_kernel_lock_released_after_real_fixture_process_exit(self):
        context=multiprocessing.get_context('spawn');parent,child=context.Pipe(duplex=False)
        process=context.Process(target=crash_physical_worker,args=(str(self.release),child));process.start();child.close()
        self.assertTrue(parent.poll(20));self.assertTrue(parent.recv());parent.close();process.join(20);self.assertEqual(process.exitcode,0)
        with alias_lock(self.release,'new-owned-fixture'):pass
        self.assertTrue((self.release/'.operations-deployment.lock').is_file())

    def test_crash_before_mutation_intent_can_reconcile_without_replay(self):
        plan=self.preview()
        with patch.object(self.engine,'preview',side_effect=Crash()):
            with self.assertRaises(Crash):self.apply(plan)
        self.store.clock=lambda:10**12
        with self.store._db() as db:reference=self.store._ref(db.execute('SELECT * FROM jobs').fetchone())
        unknown=self.engine.resume(ACTOR,PROJECT,'fixture',reference)
        self.assertEqual(unknown['job']['evidence']['last_phase'],'prepared')
        with patch.object(self.engine,'_switch',side_effect=AssertionError('never switch')):
            result=self.engine.reconcile(ACTOR,PROJECT,'fixture',reference,unknown['job']['version'])
        self.assertEqual(result['status'],'failed');self.assertFalse(result['ok'])
        self.assertEqual(os.readlink(self.release/'current'),'releases/baseline')

    def test_journal_data_firewall(self):
        result=self.apply();blob=b''.join(p.read_bytes() for p in (self.root/'state').iterdir() if p.is_file())
        for private in (ACTOR.encode(),PROJECT.encode(),str(self.root).encode(),b'index.html',b'new fixture',b'fixture-apply',b'http://127.0.0.1'):
            self.assertNotIn(private,blob)
        self.assertNotIn(str(self.root),json.dumps(result))

if __name__=='__main__':unittest.main()
