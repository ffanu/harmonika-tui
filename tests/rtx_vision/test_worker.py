import asyncio,hashlib,io,json,sqlite3,tempfile,unittest
from pathlib import Path
from aiohttp import FormData
from aiohttp.test_utils import TestClient,TestServer
from PIL import Image
from backend.rtx_vision.worker import Worker,Journal,VisionError,create_app,image_meta,MODEL
from backend.rtx_queue.router import WorkerRouter

def png():
    out=io.BytesIO();Image.new('RGB',(16,16),'red').save(out,format='PNG');return out.getvalue()
def metadata(jid='job_test',data=None):
    data=data or png();return {'schema_version':1,'job_id':jid,'kind':'analyze','prompt':'Read the image','request_digest':'a'*64,'reference_files':[{'field':'reference_0','file_id':'file_test',**image_meta(data)}]}
class FakeWorker(Worker):
    async def gpu_available(self):return True
    async def ready(self):return self.approved
    async def local(self,path,payload=None):
        self.captured=payload;await asyncio.sleep(.01)
        return {'done':True,'message':{'content':'A red square.'}}
class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.w=FakeWorker(self.root,'x'*48,'a'*64,True);self.client=TestClient(TestServer(create_app(self.w)));await self.client.start_server();self.headers={'Authorization':'Bearer '+'x'*48}
    async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
    async def post(self,meta,data=None):
        form=FormData();form.add_field('job',json.dumps(meta),content_type='application/json');form.add_field('reference_0',data or png(),filename='image.png',content_type='image/png')
        return await self.client.post('/v1/rtx/jobs',data=form,headers={**self.headers,'Idempotency-Key':meta['job_id']})
    async def done(self,jid):
        for _ in range(100):
            r=await self.client.get('/v1/rtx/jobs/'+jid,headers=self.headers);v=await r.json()
            if v.get('status') in {'completed','failed'}:return v
            await asyncio.sleep(.01)
        self.fail('worker did not finish')
    async def test_auth_and_truthful_caps(self):
        self.assertEqual((await self.client.get('/v1/rtx/health')).status,401)
        r=await self.client.get('/v1/rtx/health',headers=self.headers);v=await r.json();self.assertFalse(v['capabilities']['generate']);self.assertTrue(v['capabilities']['analyze'])
    async def test_real_job_contract_uses_only_fixed_local_model(self):
        r=await self.post(metadata());self.assertEqual(r.status,202);v=await self.done('job_test');self.assertEqual(v['result']['text'],'A red square.')
        p=self.w.captured;self.assertEqual(p['model'],MODEL);self.assertEqual(p['keep_alive'],0);self.assertFalse(p['think']);self.assertEqual(len(p['messages'][1]['images']),1)
    async def test_idempotency_no_second_inference(self):
        self.assertEqual((await self.post(metadata())).status,202);await self.done('job_test');self.assertEqual((await self.post(metadata())).status,200)
        meta=metadata();meta['prompt']='changed';self.assertEqual((await self.post(meta)).status,409)
    async def test_reference_hash_and_corrupt_image(self):
        meta=metadata();meta['reference_files'][0]['sha256']='b'*64;self.assertEqual((await self.post(meta)).status,422)
        self.assertEqual((await self.post(metadata(),b'not an image')).status,422)
    async def test_local_model_not_approved_no_job(self):
        self.w.approved=False;self.assertEqual((await self.post(metadata())).status,503)
        with self.assertRaises(VisionError):self.w.db.get('job_test')
    async def test_no_metadata_urls_or_foreign_ids_lookup(self):
        r=await self.client.get('/v1/rtx/jobs/job_missing',headers=self.headers);self.assertEqual(r.status,404)
        self.assertEqual((await self.client.post('/v1/rtx/jobs',json={'image_url':'https://example.org'},headers=self.headers)).status,415)
    async def test_second_process_lock_does_not_rewrite_running_job(self):
        other=FakeWorker(self.root,'x'*48,'a'*64,True)
        with self.assertRaises(VisionError):await other.start()
        await other.close()
    async def test_journal_is_wal_and_reopen_get_does_not_recover(self):
        self.w.db.insert('job_old','b'*64);self.w.db.state('job_old','running');other=Journal(self.root);self.assertEqual(other.get('job_old')[0]['status'],'running')
        with sqlite3.connect(other.path) as c:self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0],'wal');self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
    async def test_failed_consumer_is_not_ready_and_does_not_accept_more_jobs(self):
        self.w.db.state=lambda *args: (_ for _ in ()).throw(sqlite3.DatabaseError('synthetic storage failure'))
        self.assertEqual((await self.post(metadata())).status,202)
        for _ in range(100):
            if self.w.task.done():break
            await asyncio.sleep(.01)
        self.assertTrue(self.w.task.done())
        caps=await (await self.client.get('/v1/rtx/health',headers=self.headers)).json()
        self.assertFalse(caps['capabilities']['analyze'])
        self.assertEqual((await self.post(metadata('job_new'))).status,503)
    async def test_upload_slot_covers_enqueue_wait_not_only_body_parsing(self):
        gate=asyncio.Event();original=self.w.enqueue;active=0;peak=0
        async def held(meta,images):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            try:await gate.wait();return await original(meta,images)
            finally:active-=1
        self.w.enqueue=held
        jobs=[asyncio.create_task(self.post(metadata('job_slot'+str(i)))) for i in range(3)]
        await asyncio.sleep(.3);self.assertEqual(peak,2);gate.set()
        results=await asyncio.gather(*jobs);self.assertEqual(sorted(r.status for r in results),[202,202,429])
    async def test_old_terminal_pruned_but_unknown_and_recent_idempotency_preserved(self):
        import time
        self.w.db.insert('job_old','b'*64);self.w.db.state('job_old','completed','old')
        self.w.db.insert('job_unknown','c'*64);self.w.db.state('job_unknown','unknown')
        self.w.db.insert('job_recent','d'*64);self.w.db.state('job_recent','completed','recent')
        with sqlite3.connect(self.w.db.path) as c:c.execute('UPDATE jobs SET updated=? WHERE id IN (?,?)',(time.time()-8*86400,'job_old','job_unknown'))
        self.w.db.insert('job_added','e'*64)
        with self.assertRaises(VisionError):self.w.db.get('job_old')
        self.assertEqual(self.w.db.get('job_unknown')[0]['status'],'unknown');self.assertEqual(self.w.db.get('job_recent')[0]['status'],'completed')
    async def test_slot_timeouts_do_not_leak_permits_or_waiters(self):
        from backend.rtx_vision.worker import acquire_slot
        slots=asyncio.Semaphore(0)
        for _ in range(5):
            with self.assertRaises(VisionError):await acquire_slot(slots,.001)
        slots.release();await acquire_slot(slots,.1);self.assertEqual(slots._value,0);slots.release();self.assertEqual(slots._value,1)
    async def test_busy_gpu_fails_closed_without_model_call(self):
        async def busy():return False
        self.w.gpu_available=busy
        self.assertEqual((await self.post(metadata())).status,202)
        result=await self.done('job_test');self.assertEqual(result['status'],'failed');self.assertFalse(hasattr(self.w,'captured'))
class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_separate_routes_and_generation_references_unchanged(self):
        class A:
            def __init__(self,name):self.name=name
            async def health(self):return {'ready':True,'generate':self.name=='primary','analyze':self.name=='vision','references':self.name=='vision'}
            async def run(self,j,s):return self.name
            async def recover(self,j):return self.name
        router=WorkerRouter(A('primary'),A('vision'));caps=await router.health();self.assertTrue(caps['analyze']);self.assertTrue(caps['generate']);self.assertFalse(caps['references']);self.assertTrue(caps['vision_local_only']);self.assertEqual(await router.run({'kind':'generate'},None),'primary');self.assertEqual(await router.run({'kind':'analyze'},None),'vision');self.assertEqual(await router.recover({'kind':'analyze'}),'vision')
if __name__=='__main__':unittest.main()
