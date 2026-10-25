"""Shared core acceptance: real documents, scoped identity, jobs and failures."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'runtime/tools'
sys.path.insert(0,str(TOOLS))
from skill_executor import SkillExecutor,ExecutionContext
from skill_registry import Registry,SkillError

class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='harmonika-shared-test-')
        self.base=Path(self.tmp.name).resolve();self.root=self.base/'project';self.root.mkdir()
        self.executor=SkillExecutor(self.base/'private-state')
        self.ctx=ExecutionContext('account-a',str(self.root),frozenset(item['id'] for item in Registry().list()),True)
    def tearDown(self):self.tmp.cleanup()
    async def ready(self):
        result=await self.executor.capabilities()
        if not all(item['readiness']['status']=='available' for item in result['skills'] if item['id'].startswith('doc.')):
            if os.environ.get('HARMONIKA_DOCUMENTS_REQUIRE_ALL')=='1':self.fail('Required document worker dependencies missing')
            self.skipTest('Optional document engine unavailable')
        return result
    async def test_cancellation_during_reservation_does_not_leave_running_job(self):
        import threading,time
        started=threading.Event();original=self.executor.store.begin
        def delayed(*args):
            started.set();time.sleep(.15);return original(*args)
        self.executor.store.begin=delayed
        task=asyncio.create_task(self.executor.execute('doc.read_pdf',{'path':str(self.root/'none.pdf')},self.ctx))
        await asyncio.to_thread(started.wait,2);task.cancel()
        result=await task
        self.assertEqual(result['status'],'stopped')
        status=await self.executor.status(result['job_id'],self.ctx)
        self.assertEqual(status['status'],'stopped')
    async def test_cancelled_callback_cannot_rewrite_completed_state(self):
        await self.ready()
        async def callback(event):
            if event['type']=='job.completed':raise asyncio.CancelledError()
        payload={'output_path':str(self.root/'callback.pdf'),'content':{'paragraphs':['x']}}
        result=await self.executor.execute('doc.create_pdf',payload,self.ctx,'callback-key',callback)
        self.assertEqual(result['status'],'completed')
        self.assertEqual((await self.executor.status(result['job_id'],self.ctx))['status'],'completed')
        self.assertTrue((await self.executor.execute('doc.create_pdf',payload,self.ctx,'callback-key'))['replayed'])
    async def test_private_directory_case_alias_is_blocked(self):
        from skill_executor import scoped
        (self.root/'secrets').mkdir();(self.root/'secrets'/'customer.pdf').write_bytes(b'private')
        with self.assertRaises(SkillError):scoped(self.root,str(self.root/'SECRETS'/'customer.pdf'))
        alias=self.root.with_name(self.root.name.upper())
        if alias.exists():
            other=ExecutionContext('account-a',str(alias),self.ctx.allowed_skills,True)
            self.assertEqual(self.ctx.scope()[0],other.scope()[0])
    async def test_payload_snapshot_prevents_mutation_during_await(self):
        await self.ready();output=self.root/'snapshot.pdf'
        payload={'output_path':str(output),'content':{'paragraphs':['ORIGINAL-4821']}}
        def mutate(event):
            if event['type']=='job.queued':
                payload['output_path']=str(self.base/'outside.pdf')
                payload['content']['paragraphs'][0]='MUTATED'
        result=await self.executor.execute('doc.create_pdf',payload,self.ctx,'snapshot-key',mutate)
        self.assertEqual(result['status'],'completed',result)
        self.assertFalse((self.base/'outside.pdf').exists())
        read=await self.executor.execute('doc.read_pdf',{'path':str(output)},self.ctx)
        self.assertIn('ORIGINAL-4821',json.dumps(read))
    async def test_timeout_is_bounded_and_duplicate_active_job_refused(self):
        class FastRegistry(Registry):
            def get(self,skill):
                item=super().get(skill);item['timeout_seconds']=.15;return item
        self.executor.registry=FastRegistry()
        worker=self.base/'timeout.py';worker.write_text('import time;time.sleep(10)')
        self.executor.worker=worker
        rejected=[]
        async def event(value):
            if value['type']=='job.started':
                try:await self.executor.execute('doc.read_pdf',{'path':str(self.root/'other.pdf')},self.ctx)
                except SkillError as error:rejected.append(error.code)
        result=await self.executor.execute('doc.read_pdf',{'path':str(self.root/'source.pdf')},self.ctx,on_event=event)
        self.assertEqual(rejected,['in_progress'])
        self.assertEqual(result['error']['code'],'timeout')
        self.assertEqual(result['status'],'failed')
    async def test_seo_write_journal_replays_only_artifact_metadata(self):
        from unittest.mock import patch
        output=self.root/'baseline.json';fixture=self.base/'fixture.py'
        fixture.write_text('import json\nfrom pathlib import Path\nPath('+repr(str(output))+').write_text("{}")\nprint(json.dumps({"ok":True,"result":{"status":"completed","snapshot":{"title":"PRIVATE_SEO_TITLE_SENTINEL"}}}))')
        original=asyncio.create_subprocess_exec
        async def spawn(*args,**kwargs):return await original(sys.executable,'-I',str(fixture),**kwargs)
        payload={'url':'https://example.com/','action':'capture','baseline_path':str(output)}
        with patch.object(asyncio,'create_subprocess_exec',side_effect=spawn):result=await self.executor.execute('seo.drift',payload,self.ctx,'seo-journal')
        self.assertEqual(result['status'],'completed',result);self.assertIn('PRIVATE_SEO_TITLE_SENTINEL',json.dumps(result))
        self.assertNotIn(b'PRIVATE_SEO_TITLE_SENTINEL',self.executor.store.path.read_bytes())
        replay=await self.executor.execute('seo.drift',payload,self.ctx,'seo-journal')
        self.assertTrue(replay['replayed']);self.assertEqual(replay['replay_scope'],'artifact_metadata')
        self.assertNotIn('PRIVATE_SEO_TITLE_SENTINEL',json.dumps(replay))
    async def test_readiness_is_actual_not_registry_count(self):
        result=await self.ready()
        self.assertEqual(len([x for x in result['skills'] if x['id'].startswith('doc.')]),9);self.assertFalse(result['public_api'])
        missing=SkillExecutor(self.base/'missing-state',python='/nonexistent/python')
        result=await missing.capabilities()
        self.assertTrue(all(item['readiness']['status']=='unavailable' for item in result['skills'] if item.get('worker') not in ('coding','telco','legal','builder')))
    async def test_create_read_all_formats_and_pdf_edit(self):
        await self.ready()
        specs={'pdf':{'paragraphs':['HARMONIKA-4821']},'docx':{'paragraphs':['HARMONIKA-4821']},'xlsx':{'sheets':[{'name':'Data','rows':[['HARMONIKA-4821','=1+1']]}]},'pptx':{'slides':[{'title':'HARMONIKA-4821','body':['Fixture']} ]}}
        for ext,content in specs.items():
            file=self.root/('report.'+ext)
            result=await self.executor.execute('doc.create_'+ext,{'output_path':str(file),'content':content},self.ctx,'create-'+ext)
            self.assertEqual(result['status'],'completed',result)
            result=await self.executor.execute('doc.read_'+ext,{'path':str(file)},self.ctx)
            self.assertEqual(result['status'],'completed',result);self.assertIn('HARMONIKA-4821',json.dumps(result))
        result=await self.executor.execute('doc.edit_pdf',{'path':str(self.root/'report.pdf'),'output_path':str(self.root/'edited.pdf'),'changes':{'rotation':90,'watermark':'DRAFT'}},self.ctx,'edit-pdf')
        self.assertEqual(result['status'],'completed',result)
    async def test_idempotency_persists_across_executor_restart_and_conflict(self):
        await self.ready();file=self.root/'one.pdf';payload={'output_path':str(file),'content':{'paragraphs':['Private document text']}}
        first=await self.executor.execute('doc.create_pdf',payload,self.ctx,'same-key')
        self.assertEqual(first['status'],'completed',first);before=file.read_bytes()
        second=await SkillExecutor(self.base/'private-state').execute('doc.create_pdf',payload,self.ctx,'same-key')
        self.assertTrue(second['replayed']);self.assertEqual(first['job_id'],second['job_id']);self.assertEqual(before,file.read_bytes())
        with self.assertRaisesRegex(SkillError,'different request'):
            await self.executor.execute('doc.create_pdf',dict(payload,content={'paragraphs':['Different']}),self.ctx,'same-key')
        self.assertNotIn(b'Private document text',self.executor.store.path.read_bytes())
    async def test_permission_and_scope_denials_create_no_job(self):
        payload={'output_path':str(self.root/'no.pdf'),'content':{'paragraphs':['x']}}
        for context in [ExecutionContext('a',str(self.root),frozenset(),True),ExecutionContext('a',str(self.root),frozenset(['doc.create_pdf']),False)]:
            with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',payload,context,'key')
        with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',dict(payload,output_path=str(self.base/'outside.pdf')),self.ctx,'key')
        (self.root/'linked.pdf').symlink_to(self.base/'elsewhere.pdf')
        with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',dict(payload,output_path=str(self.root/'linked.pdf')),self.ctx,'key')
        with self.executor.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
    async def test_write_requires_key_and_unknown_body_identity_rejected(self):
        payload={'output_path':str(self.root/'no.pdf'),'content':{'paragraphs':['x']}}
        with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',payload,self.ctx)
        with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',dict(payload,principal_id='other'),self.ctx,'key')
    async def test_status_and_stop_are_account_and_project_isolated(self):
        await self.ready()
        result=await self.executor.execute('doc.create_pdf',{'output_path':str(self.root/'scope.pdf'),'content':{'paragraphs':['x']}},self.ctx,'scope-key')
        status=await self.executor.status(result['job_id'],self.ctx)
        self.assertEqual([e['sequence'] for e in status['events']],[1,2,3])
        self.assertEqual(status['status'],'completed')
        other=ExecutionContext('account-b',str(self.root),self.ctx.allowed_skills,True)
        with self.assertRaises(SkillError):await self.executor.status(result['job_id'],other)
        with self.assertRaises(SkillError):await self.executor.stop(result['job_id'],other)
        self.assertFalse((await self.executor.stop(result['job_id'],self.ctx))['stop_requested'])
    async def test_existing_output_is_not_overwritten_or_mislabeled_unknown(self):
        file=self.root/'existing.pdf';file.write_bytes(b'unchanged')
        result=await self.executor.execute('doc.create_pdf',{'output_path':str(file),'content':{'paragraphs':['x']}},self.ctx,'exists-key')
        self.assertEqual(result['status'],'failed');self.assertEqual(result['error']['code'],'output_exists')
        self.assertEqual(file.read_bytes(),b'unchanged')
    async def test_external_reference_refused_before_execution(self):
        with self.assertRaises(SkillError):
            await self.executor.execute('doc.edit_pdf',{'path':str(self.root/'a.pdf'),'output_path':str(self.root/'b.pdf'),'changes':{'append_files':[str(self.base/'external.pdf')]}},self.ctx,'ref-key')
    async def test_stop_terminates_only_spawned_worker_and_job_is_recoverable(self):
        worker=self.base/'slow.py';worker.write_text('import time\ntime.sleep(10)\n')
        self.executor.worker=worker
        seen=[]
        async def event(value):
            seen.append(value)
            if value['type']=='job.started':await self.executor.stop(value['job_id'],self.ctx)
        result=await self.executor.execute('doc.read_pdf',{'path':str(self.root/'slow.pdf')},self.ctx,on_event=event)
        self.assertEqual(result['status'],'stopped',result)
        self.assertEqual(seen[-1]['type'],'job.stopped')
    async def test_ambiguous_write_is_unknown_and_retry_is_blocked(self):
        output=self.root/'uncertain.pdf'
        worker=self.base/'ambiguous.py';worker.write_text('from pathlib import Path\nPath('+repr(str(output))+').write_bytes(b"committed")\nimport sys\nsys.exit(1)\n')
        self.executor.worker=worker
        payload={'output_path':str(output),'content':{'paragraphs':['x']}}
        result=await self.executor.execute('doc.create_pdf',payload,self.ctx,'uncertain-key')
        self.assertEqual(result['status'],'unknown',result)
        with self.assertRaises(SkillError):await self.executor.execute('doc.create_pdf',payload,self.ctx,'uncertain-key')
    async def test_output_budget_and_audit_redaction(self):
        worker=self.base/'large.py';worker.write_text('import sys\nsys.stdout.write("X"*1100000)\n')
        self.executor.worker=worker
        result=await self.executor.execute('doc.read_pdf',{'path':str(self.root/'private-customer.pdf')},self.ctx)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['error']['code'],'output_too_large')
        status=await self.executor.status(result['job_id'],self.ctx)
        self.assertNotIn('private-customer',json.dumps(status))
        self.assertEqual(self.executor.store.path.stat().st_mode & 0o777,0o600)

if __name__=='__main__':unittest.main(verbosity=2)
