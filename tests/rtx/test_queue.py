import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from aiohttp import web,FormData
from aiohttp.test_utils import TestClient,TestServer
from PIL import Image
from backend.rtx_queue.core import Store,RTXError,image_info
from backend.rtx_queue.adapter import RTXAdapter
from backend.rtx_queue.service import create_app,STORE

def png():
    out=io.BytesIO();Image.new('RGB',(16,12),'navy').save(out,format='PNG');return out.getvalue()
TOKENS={'alpha':'fixture-alpha-key','beta':'fixture-beta-key'}
CLIENTS={k:hashlib.sha256(v.encode()).hexdigest() for k,v in TOKENS.items()}
def headers(owner='alpha',idem=None):
    value={'Authorization':'Bearer '+TOKENS[owner]}
    if idem:value['Idempotency-Key']=idem
    return value

class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.store=Store(self.root)
    def submit(self,owner='a',idem='same'):return self.store.submit(owner,{'kind':'generate','prompt':'private fixture'},idem)[0]
    def test_idempotency_owner_and_conflict(self):
        first=self.submit();second=self.submit();self.assertEqual(first['job_id'],second['job_id']);self.assertNotEqual(first['job_id'],self.submit('b')['job_id'])
        with self.assertRaisesRegex(RTXError,'idempotency_conflict'):self.store.submit('a',{'kind':'generate','prompt':'different'},'same')
        with self.assertRaisesRegex(RTXError,'job_not_found'):self.store.get('b',first['job_id'])
    def test_unknown_blocks_next_dispatch_and_restart_does_not_duplicate(self):
        first=self.submit();self.submit(idem='second');self.store.claim();self.store.set_state(first['job_id'],'running');Store(self.root).startup()
        self.assertEqual(self.store.get('a',first['job_id'])['status'],'unknown');self.assertIsNone(self.store.claim())
    def test_bounds_and_cancel_queued(self):
        store=Store(self.root,max_pending=1,max_owner_pending=1);job=store.submit('a',{'kind':'generate','prompt':'hi'},'one')[0]
        with self.assertRaisesRegex(RTXError,'queue_full'):store.submit('b',{'kind':'generate','prompt':'hi'},'two')
        store.cancel('a',job['job_id']);self.assertEqual(store.submit('b',{'kind':'generate','prompt':'hi'},'two')[0]['status'],'queued')
    def test_cancel_running_keeps_remote_slot_and_discards_result(self):
        job=self.submit();self.submit(idem='two');self.store.claim();self.store.begin_remote(job['job_id']);self.store.cancel('a',job['job_id'])
        self.assertIsNone(self.store.claim());self.store.set_state(job['job_id'],'completed',None,{'text':'must not show'})
        self.assertEqual(self.store.get('a',job['job_id'])['status'],'cancelled');self.assertIsNotNone(self.store.claim())
    def test_foreign_reference_and_magic_bytes_rejected(self):
        meta=self.store.put_file('a','image.png',png())
        with self.assertRaisesRegex(RTXError,'file_not_found'):self.store.submit('b',{'kind':'analyze','prompt':'read','reference_file_ids':[meta['file_id']]},'read')
        for data in [b'fake.png',b'\x89PNG\r\n\x1a\ncorrupt']:
            with self.assertRaises(RTXError):image_info(data)
    def test_queued_deadline_and_invalid_reference_shape(self):
        job=self.store.submit('a',{'kind':'generate','prompt':'hi'},'expires',ttl=-1)[0];self.assertIsNone(self.store.claim());self.assertEqual(self.store.get('a',job['job_id'])['error']['code'],'queue_deadline_exceeded')
        with self.assertRaisesRegex(RTXError,'invalid_references'):self.store.submit('a',{'kind':'analyze','prompt':'read','reference_file_ids':[{}]},'bad')
    def test_transactional_claim_across_instances(self):
        from concurrent.futures import ThreadPoolExecutor
        self.submit();self.submit(idem='two');other=Store(self.root)
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda s:s.claim(),[self.store,other]))
        self.assertEqual(sum(r is not None for r in rows),1)
    def test_private_files_no_public_url_and_integrity(self):
        meta=self.store.put_file('a','../../file.png',png());self.assertNotIn('url',meta);self.assertEqual(meta['name'],'file.png')
        self.assertEqual((self.store.files/meta['file_id']).stat().st_mode & 0o777,0o600)
        (self.store.files/meta['file_id']).write_bytes(b'tampered')
        with self.assertRaisesRegex(RTXError,'file_integrity_failed'):self.store.file_bytes('a',meta['file_id'])

class AsyncQueue(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.worker=None;self.client=None;self.posts=0;self.remote={};self.slow=False;self.kind_seen=None;self.header_seen=None
    async def asyncTearDown(self):
        if self.client:await self.client.close()
        if self.worker:await self.worker.close()
        self.tmp.cleanup()
    async def setup_service(self,mode='v1',timeout=2,endpoint=None):
        if endpoint is None:
            app=web.Application()
            async def health(req):return web.json_response({'ok':True,'cuda':True,'device':'cuda','model':'fixture','capabilities':{'generate':True,'analyze':True,'references':True}})
            async def legacy(req):
                self.posts+=1
                if self.slow:await asyncio.sleep(.3)
                return web.json_response({'ok':True,'device':'cuda','b64_png':base64.b64encode(png()).decode()})
            async def post(req):
                self.posts+=1;self.header_seen=req.headers.get('Idempotency-Key')
                reader=await req.multipart();part=await reader.next();metadata=json.loads(await part.text());self.kind_seen=metadata['kind']
                for ref in metadata['reference_files']:
                    part=await reader.next();data=await part.read();self.assertEqual(hashlib.sha256(data).hexdigest(),ref['sha256'])
                jid=metadata['job_id'];self.remote[jid]=metadata
                if self.slow:await asyncio.sleep(.3)
                return web.json_response({'job_id':jid,'status':'running'},status=202)
            async def get(req):
                jid=req.match_info['jid'];meta=self.remote.get(jid)
                if meta is None:return web.json_response({},status=404)
                result={'text':'fixture image analysis' if meta['kind']=='analyze' else '','artifacts':[]}
                if meta['kind']=='generate':result['artifacts']=[{'artifact_id':'artifact-one','sha256':hashlib.sha256(png()).hexdigest(),'size_bytes':len(png())}]
                return web.json_response({'job_id':jid,'status':'completed','result':result})
            async def artifact(req):return web.Response(body=png(),content_type='image/png')
            app.add_routes([web.get('/health',health),web.get('/v1/rtx/health',health),web.post('/generate',legacy),web.post('/v1/rtx/jobs',post),web.get('/v1/rtx/jobs/{jid}',get),web.get('/v1/rtx/jobs/{jid}/artifacts/{aid}',artifact)])
            self.worker=TestServer(app);await self.worker.start_server();endpoint=str(self.worker.make_url('')).rstrip('/')
        adapter=RTXAdapter(endpoint,mode,'fixture-worker',connect_timeout=.1,job_timeout=timeout,poll_interval=.01)
        app=create_app(self.root,adapter,CLIENTS);self.client=TestClient(TestServer(app));await self.client.start_server()
    async def submit(self,kind='generate',refs=None,key='job-one'):
        payload={'kind':kind,'prompt':'fixture request'}
        if refs:payload['reference_file_ids']=refs
        response=await self.client.post('/v1/rtx/jobs',json=payload,headers=headers(idem=key));self.assertIn(response.status,[200,202]);return (await response.json())['job']
    async def wait(self,jid,states=('completed','failed','unknown','cancelled'),timeout=3):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            r=await self.client.get('/v1/rtx/jobs/'+jid,headers=headers());job=(await r.json())['job']
            if job['status'] in states:return job
            await asyncio.sleep(.01)
        self.fail('job did not reach expected state')
    async def test_v1_generation_and_private_download(self):
        await self.setup_service();job=await self.submit();final=await self.wait(job['job_id']);self.assertEqual(final['status'],'completed');self.assertEqual(self.posts,1);self.assertEqual(self.header_seen,job['job_id'])
        fid=final['result']['artifacts'][0]['file_id'];r=await self.client.get('/v1/rtx/files/'+fid,headers=headers());self.assertEqual(await r.read(),png())
        r=await self.client.get('/v1/rtx/files/'+fid,headers=headers('beta'));self.assertEqual(r.status,404)
    async def test_v1_upload_and_analyze(self):
        await self.setup_service();form=FormData();form.add_field('file',png(),filename='sample.png',content_type='image/png')
        r=await self.client.post('/v1/rtx/files',data=form,headers=headers());self.assertEqual(r.status,201);fid=(await r.json())['file']['file_id']
        job=await self.submit('analyze',[fid]);final=await self.wait(job['job_id']);self.assertEqual(final['result']['text'],'fixture image analysis');self.assertEqual(self.kind_seen,'analyze')
    async def test_offline_fails_fast_without_blocking_submit(self):
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        await self.setup_service(endpoint='http://127.0.0.1:'+str(port));start=time.monotonic();job=await self.submit();self.assertLess(time.monotonic()-start,.3)
        final=await self.wait(job['job_id']);self.assertEqual(final['error']['code'],'rtx_offline');self.assertEqual(final['status'],'failed')
    async def test_legacy_generation_and_unavailable_vision(self):
        await self.setup_service('legacy');job=await self.submit();final=await self.wait(job['job_id']);self.assertEqual(final['status'],'completed')
        meta=await self.client.app[STORE].call('put_file','alpha','test.png',png());job=await self.submit('analyze',[meta['file_id']],key='vision');final=await self.wait(job['job_id']);self.assertEqual(final['error']['code'],'vision_unavailable');self.assertEqual(self.posts,1)
    async def test_ambiguous_timeout_holds_slot_and_recovery_get_only(self):
        await self.setup_service(timeout=.05);self.slow=True;job=await self.submit();final=await self.wait(job['job_id']);self.assertEqual(final['status'],'unknown')
        second=await self.submit(key='second');await asyncio.sleep(.05);self.assertEqual(self.posts,1)
        await self.client.post('/v1/rtx/jobs/'+second['job_id']+'/cancel',headers=headers())
        await asyncio.sleep(.3);r=await self.client.post('/v1/rtx/jobs/'+job['job_id']+'/recover',headers=headers());self.assertEqual(r.status,200);self.assertEqual((await r.json())['job']['status'],'completed');self.assertEqual(self.posts,1)
    async def test_legacy_unknown_cannot_be_reposted(self):
        await self.setup_service('legacy',timeout=.05);self.slow=True;job=await self.submit();final=await self.wait(job['job_id']);self.assertEqual(final['status'],'unknown')
        r=await self.client.post('/v1/rtx/jobs/'+job['job_id']+'/recover',headers=headers());self.assertEqual(r.status,409);self.assertEqual(self.posts,1)
    async def test_auth_owner_and_idempotency(self):
        await self.setup_service();r=await self.client.get('/health');self.assertEqual(r.status,401)
        job=await self.submit();again=await self.submit();self.assertEqual(job['job_id'],again['job_id'])
        r=await self.client.get('/v1/rtx/jobs/'+job['job_id'],headers=headers('beta'));self.assertEqual(r.status,404)
        r=await self.client.get('/health',headers=headers('beta'));self.assertEqual((await r.json())['queue'],{})
    async def test_only_one_queue_process_holds_lock(self):
        await self.setup_service();from backend.rtx_queue.service import Queue
        other=Queue(Store(self.root),RTXAdapter(str(self.worker.make_url('')).rstrip('/'),token='fixture'))
        with self.assertRaisesRegex(RTXError,'queue_already_running'):await other.start()
        await other.close()
    async def test_invalid_kinds_and_sizes_are_customer_safe_400(self):
        await self.setup_service()
        for key,value in [('kind',[]),('kind',{}),('size',[]),('size',{})]:
            payload={'kind':'generate','prompt':'hi'};payload[key]=value
            r=await self.client.post('/v1/rtx/jobs',json=payload,headers=headers(idem='invalid'))
            self.assertEqual(r.status,400);self.assertIn((await r.json())['error']['code'],['invalid_kind','invalid_size'])
        self.assertEqual(self.posts,0)
    async def test_typed_worker_errors_preserved(self):
        app=web.Application()
        async def error(req):return web.json_response({},status=int(req.match_info['status']))
        app.add_routes([web.get('/{status}',error)]);self.worker=TestServer(app);await self.worker.start_server()
        adapter=RTXAdapter(str(self.worker.make_url('')).rstrip('/'),token='fixture');await adapter.start()
        try:
            for status,code in [(401,'worker_auth_failed'),(404,'remote_job_not_found'),(429,'worker_busy')]:
                with self.subTest(status=status),self.assertRaises(RTXError) as raised:await adapter.json_request('GET','/'+str(status))
                self.assertEqual(raised.exception.code,code)
        finally:await adapter.close()
    async def test_foreign_owner_cannot_cancel_or_recover(self):
        await self.setup_service();job=await self.submit()
        for action in ['cancel','recover']:
            r=await self.client.post('/v1/rtx/jobs/'+job['job_id']+'/'+action,headers=headers('beta'));self.assertEqual(r.status,404)

    async def test_unknown_is_explicitly_reported_as_queue_blocked(self):
        await self.setup_service('legacy',timeout=.05);self.slow=True
        job=await self.submit();await self.wait(job['job_id'])
        r=await self.client.get('/health',headers=headers());value=await r.json()
        self.assertTrue(value['blocked_by_uncertain_job']);self.assertFalse(value['queue_ready'])
