import importlib.util, io, json, pathlib, tempfile, unittest
from unittest.mock import patch
from PIL import Image
ROOT=pathlib.Path(__file__).resolve().parents[1]
def module(name,path):
    s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
worker=module('visual_worker',ROOT/'backend/visual_quality/vision_worker.py')
media=module('visual_media',ROOT/'runtime/tools/visual_media.py')

class PrivateJournalTests(unittest.TestCase):
    def test_private_observation_never_written_to_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            j=worker.PrivateResults(pathlib.Path(d).resolve());j.insert('job_test','a'*64)
            sentinel='PRIVATE_IMAGE_SENTINEL_ABC_9182'
            j.state('job_test','completed',sentinel)
            v,_=j.get('job_test');self.assertEqual(v['result']['text'],sentinel)
            self.assertTrue(v['result']['gpu_verified'])
            for p in pathlib.Path(d).iterdir():self.assertNotIn(sentinel.encode(),p.read_bytes())
            with j.connect() as c:self.assertIsNone(c.execute('select text from jobs').fetchone()[0])
            restarted=worker.PrivateResults(pathlib.Path(d).resolve())
            v,_=restarted.get('job_test');self.assertEqual(v['status'],'unknown');self.assertNotIn('result',v)
    def test_expiry_and_bound(self):
        with tempfile.TemporaryDirectory() as d:
            j=worker.PrivateResults(pathlib.Path(d).resolve())
            for i in range(129):
                j.insert(f'job_{i}','b'*64);j.state(f'job_{i}','completed','observation')
            self.assertEqual(len(j.results),128)
            self.assertEqual(j.get('job_0')[0]['status'],'unknown')
            with patch.object(worker.time,'monotonic',return_value=10**12):
                self.assertEqual(j.get('job_128')[0]['status'],'unknown')
    def test_image_provenance_and_dimensions(self):
        raw=io.BytesIO();Image.new('RGB',(320,256),'navy').save(raw,format='PNG')
        r=media.png_result(raw.getvalue(),{'engine':'flux2-klein-4b'},'gw_test')
        self.assertEqual((r['width'],r['height']),(320,256));self.assertEqual(r['model'],'FLUX.2 Klein 4B')
        self.assertEqual(media.png_result(raw.getvalue(),{},'job')['model'],'unreported')
        with self.assertRaises(Exception):media.png_result(b'broken',{},'job')
    def test_missing_file_and_failed_transport_do_not_claim_success(self):
        r=media.analyze({'path':'/no-such-fixture.png'},'x'*32)
        self.assertFalse(r['ok']);self.assertNotIn('text',r)
    def test_http_submission_carries_idempotency_and_no_proxy(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return b'{"status":"queued"}'
        class Opener:
            def open(self,req,timeout):
                self.req=req;return Response()
        op=Opener()
        with patch.object(media.urllib.request,'build_opener',return_value=op):
            media.request('/v1/rtx/jobs','x'*32,b'body','multipart/form-data','job_test')
        self.assertEqual(op.req.get_header('Idempotency-key'),'job_test')
        self.assertEqual(op.req.full_url,'http://127.0.0.1:18188/v1/rtx/jobs')


class VisualContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        class Fake(worker.VisualWorker):
            fault=None
            async def gpu_available(self):return self.fault!='busy'
            async def ready(self):return self.running()
            async def local(self,path,payload=None):
                if path=='/api/ps':return {'models':[{'name':worker.base.MODEL,'digest':worker.DIGEST,'size_vram':1024}]}
                if self.fault=='timeout':raise TimeoutError('PRIVATE_SENTINEL_NEVER_LOG')
                return {'model':'wrong-model' if self.fault=='payload' else worker.base.MODEL,
                        'done':True,'message':{'content':'private visual observation'}}
        self.tmp=tempfile.TemporaryDirectory();self.w=Fake(pathlib.Path(self.tmp.name).resolve(),'x'*48,worker.DIGEST,True)
        self.client=TestClient(TestServer(worker.base.create_app(self.w)));await self.client.start_server()
        self.headers={'Authorization':'Bearer '+'x'*48}
    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()
    async def submit_done(self,jid):
        import asyncio,hashlib
        from aiohttp import FormData
        data=io.BytesIO();Image.new('RGB',(16,16),'navy').save(data,format='PNG');raw=data.getvalue()
        meta={'schema_version':1,'kind':'analyze','job_id':jid,'prompt':'Read this image',
              'request_digest':'a'*64,'reference_files':[{'field':'reference_0',**worker.base.image_meta(raw)}]}
        form=FormData();form.add_field('job',json.dumps(meta),content_type='application/json');form.add_field('reference_0',raw,filename='test.png',content_type='image/png')
        r=await self.client.post('/v1/rtx/jobs',data=form,headers={**self.headers,'Idempotency-Key':jid});self.assertEqual(r.status,202)
        for _ in range(50):
            r=await self.client.get('/v1/rtx/jobs/'+jid,headers=self.headers);v=await r.json()
            if v['status'] in ('failed','completed'):return v
            await asyncio.sleep(.01)
        self.fail('job did not finish')
    async def test_correct_model_and_auth(self):
        self.assertEqual((await self.client.get('/v1/rtx/health')).status,401)
        v=await self.submit_done('job_valid');self.assertTrue(v['result']['gpu_verified']);self.assertEqual(v['result']['model'],'qwen3-vl:8b-instruct')
    async def test_bad_payload_timeout_busy_are_isolated_and_queue_recovers(self):
        for fault,code in [('payload','vision_result_unverified'),('timeout','local_vision_unavailable'),('busy','gpu_busy')]:
            self.w.fault=fault;v=await self.submit_done('job_'+fault)
            self.assertEqual(v['status'],'failed');self.assertEqual(v['error']['code'],code);self.assertNotIn('result',v)
        self.w.fault=None
        self.assertEqual((await self.submit_done('job_after_failures'))['status'],'completed')
        for p in pathlib.Path(self.tmp.name).iterdir():
            if p.is_file():self.assertNotIn(b'PRIVATE_SENTINEL_NEVER_LOG',p.read_bytes())




if __name__=='__main__':unittest.main()
