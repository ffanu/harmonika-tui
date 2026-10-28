"""Loopback ComfyUI adapter. Preserve established /health and /generate contract."""
import asyncio, base64, io, json, re, secrets, time
from pathlib import Path
import aiohttp
from aiohttp import web
from PIL import Image
ROOT=Path(__file__).resolve().parent
MODEL='black-forest-labs/FLUX.2-klein-4B'
COMFY='http://127.0.0.1:18190'
PROMPT_LIMIT=12000

class RenderFailure(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)

def comfy_failure(history):
    for item in history.get('status',{}).get('messages',[]):
        if isinstance(item,(list,tuple)) and len(item)==2 and item[0]=='execution_error' and isinstance(item[1],dict):
            kind=item[1].get('exception_type')
            if isinstance(kind,str) and kind.rsplit('.',1)[-1] in {'OutOfMemoryError','CUDAOutOfMemoryError'}:return 'gpu_memory_full'
    return 'image_render_failed'

def workflow(prompt,width,height,seed,prefix):
    return {
      '1':{'class_type':'UNETLoader','inputs':{'unet_name':'flux-2-klein-4b-fp8.safetensors','weight_dtype':'default'}},
      '2':{'class_type':'CLIPLoader','inputs':{'clip_name':'qwen_3_4b_fp4_flux2.safetensors','type':'flux2','device':'default'}},
      '3':{'class_type':'VAELoader','inputs':{'vae_name':'flux2-vae.safetensors'}},
      '4':{'class_type':'CLIPTextEncode','inputs':{'text':prompt,'clip':['2',0]}},
      '5':{'class_type':'ConditioningZeroOut','inputs':{'conditioning':['4',0]}},
      '6':{'class_type':'EmptyFlux2LatentImage','inputs':{'width':width,'height':height,'batch_size':1}},
      '7':{'class_type':'RandomNoise','inputs':{'noise_seed':seed}},
      '8':{'class_type':'CFGGuider','inputs':{'model':['1',0],'positive':['4',0],'negative':['5',0],'cfg':1.0}},
      '9':{'class_type':'KSamplerSelect','inputs':{'sampler_name':'euler'}},
      '10':{'class_type':'Flux2Scheduler','inputs':{'steps':4,'width':width,'height':height}},
      '11':{'class_type':'SamplerCustomAdvanced','inputs':{'noise':['7',0],'guider':['8',0],'sampler':['9',0],'sigmas':['10',0],'latent_image':['6',0]}},
      '12':{'class_type':'VAEDecode','inputs':{'samples':['11',0],'vae':['3',0]}},
      '13':{'class_type':'SaveImage','inputs':{'images':['12',0],'filename_prefix':prefix}},
    }
def validate(data):
    if not isinstance(data,dict):raise ValueError('invalid_request')
    prompt=data.get('prompt')
    if not isinstance(prompt,str) or not 1<=len(prompt.encode('utf-8'))<=PROMPT_LIMIT or not prompt.strip():raise ValueError('invalid_prompt')
    width=data.get('width',1024);height=data.get('height',1024)
    if any(type(x) is not int or x<512 or x>1024 or x%64 for x in [width,height]):raise ValueError('invalid_dimensions')
    # The previous 512/768 defaults were speed presets, not user quality choices.
    # Render at model-native quality; report actual dimensions in returned artifact.
    width=1024 if width==height and width in (512,768) else width
    height=width if data.get('width',1024)==data.get('height',1024) else height
    seed=data.get('seed')
    if seed is None:seed=secrets.randbits(48)
    if type(seed) is not int or not 0<=seed<2**53:raise ValueError('invalid_seed')
    if data.get('reference_files') or data.get('reference_images'):raise ValueError('references_unavailable')
    return prompt,width,height,seed

class Worker:
    def __init__(self,root=ROOT):self.root=root;self.session=None;self.active=False;self.tasks=set();self.approved=False;self.warmed=False;self.uncertain_pid=None;self.last_failure=None;self.last_failure_time=0
    async def start(self,app):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),trust_env=False,cookie_jar=aiohttp.DummyCookieJar())
        try:self.approved=json.loads((self.root/'approval.json').read_text()).get('approved') is True
        except (OSError,ValueError):pass
        if self.approved:
            task=asyncio.create_task(self.warmup());self.tasks.add(task)
            task.add_done_callback(self.tasks.discard);task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
    def refresh_approval(self):
        try:self.approved=json.loads((self.root/'approval.json').read_text()).get('approved') is True
        except (OSError,ValueError):self.approved=False
    async def warmup(self):
        for attempt in range(90):
            try:
                stats=await self.request('GET','/system_stats')
                if stats.get('devices'):break
            except Exception:pass
            await asyncio.sleep(2)
        else:return
        for attempt in range(3):
            if self.uncertain_pid:
                for _ in range(90):
                    try:
                        h=await self.request('GET','/history/'+self.uncertain_pid)
                        if self.uncertain_pid in h:self.uncertain_pid=None;break
                    except Exception:pass
                    await asyncio.sleep(2)
                if self.uncertain_pid:return
            if self.active:return
            self.active=True
            try:
                await self.render({'prompt':'A blue geometric sphere on a white background, high quality studio lighting','width':1024,'height':1024,'seed':20261025})
                return
            except Exception:await asyncio.sleep(3)
    async def stop(self,app):
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.session.close()
    async def request(self,method,route,**kw):
        async with self.session.request(method,COMFY+route,allow_redirects=False,**kw) as r:
            if r.status!=200:raise ValueError('comfy_request_failed')
            data=await r.read()
            if len(data)>8*1024*1024:raise ValueError('comfy_response_limit')
            return json.loads(data)
    async def health(self,req):
        self.refresh_approval()
        ready=False;memory_full=False;comfy_busy=False
        try:
            v=await asyncio.wait_for(self.request('GET','/system_stats'),1.5)
            devices=[d for d in v.get('devices',[]) if d.get('type')=='cuda']
            ready=bool(devices)
            memory_full=any(type(d.get('vram_free')) is int and d['vram_free']<256*1024*1024 for d in devices)
            q=await asyncio.wait_for(self.request('GET','/queue'),1)
            comfy_busy=bool(q.get('queue_running') or q.get('queue_pending'))
        except Exception:ready=False
        if self.uncertain_pid:
            try:
                h=await asyncio.wait_for(self.request('GET','/history/'+self.uncertain_pid),1)
                if self.uncertain_pid in h:
                    await asyncio.wait_for(self.request('POST','/history',json={'delete':[self.uncertain_pid]}),1)
                    self.uncertain_pid=None
            except Exception:pass
        return web.json_response({'ok':ready and self.approved and self.warmed and not self.uncertain_pid and not memory_full,'model':MODEL,'device':'cuda','cuda':ready,'loaded':ready and self.warmed,'busy':self.active or comfy_busy,'memory_full':memory_full,'quality':'1024','approved':self.approved,'recovery_required':bool(self.uncertain_pid)})
    async def render(self,data):
        prompt,width,height,seed=validate(data);prefix='harmonika-flux-'+secrets.token_hex(12)
        start=time.monotonic();pid=None
        try:
            submitted=await self.request('POST','/prompt',json={'prompt':workflow(prompt,width,height,seed,prefix),'client_id':prefix})
            pid=submitted.get('prompt_id')
            if not isinstance(pid,str) or not re.fullmatch(r'[a-f0-9-]{36}',pid):raise ValueError('invalid_comfy_job')
            async with asyncio.timeout(95):
                while True:
                    result=await self.request('GET','/history/'+pid)
                    if pid in result:
                        item=result[pid]
                        if item.get('status',{}).get('status_str')=='error':raise RenderFailure(comfy_failure(item))
                        outputs=item.get('outputs',{}).get('13',{}).get('images',[])
                        if outputs:break
                    await asyncio.sleep(.3)
            artifact=outputs[0];filename=artifact.get('filename','')
            if artifact.get('subfolder') or artifact.get('type')!='output' or not re.fullmatch(prefix+r'_[0-9]+_\.png',filename):raise ValueError('invalid_artifact')
            p=self.root/'ComfyUI/output'/filename
            if p.is_symlink() or p.stat().st_size>16*1024*1024:raise ValueError('invalid_artifact')
            binary=await asyncio.to_thread(p.read_bytes)
            with Image.open(io.BytesIO(binary)) as im:
                if im.format!='PNG' or im.mode not in ('RGB','RGBA') or im.size!=(width,height):raise ValueError('invalid_image')
                im.verify()
            self.warmed=True
            return {'ok':True,'id':prefix,'model':MODEL,'device':'cuda','seconds':round(time.monotonic()-start,2),'width':width,'height':height,'seed':seed,'b64_png':base64.b64encode(binary).decode()}
        finally:
            # Fail closed on uncertain GPU execution. Never admit a second job
            # merely because the HTTP client timed out. Health can recover only
            # after the exact job has a terminal history record.
            if pid:
                terminal=False
                try:
                    async with asyncio.timeout(8):
                        while True:
                            h=await self.request('GET','/history/'+pid)
                            if pid in h:terminal=True;break
                            await asyncio.sleep(.5)
                except Exception:pass
                if terminal:
                    try:
                        async with asyncio.timeout(4):
                            await self.request('POST','/history',json={'delete':[pid]})
                            await self.request('POST','/free',json={'unload_models':True,'free_memory':True})
                    except Exception:pass
                else:self.uncertain_pid=pid
            self.active=False
    async def generate(self,req):
        self.refresh_approval()
        try:data=await req.json();validate(data)
        except Exception:return web.json_response({'ok':False,'error':'invalid_request'},status=422)
        if (not self.approved or not self.warmed) and req.headers.get('X-Harmonika-Qualification')!='local-test':return web.json_response({'ok':False,'error':'model_not_approved'},status=503)
        if self.uncertain_pid:return web.json_response({'ok':False,'error':'gpu_job_recovery_required'},status=503)
        if self.active:return web.json_response({'ok':False,'error':'worker_busy'},status=429)
        self.active=True;task=asyncio.create_task(self.render(data));self.tasks.add(task);task.add_done_callback(self.tasks.discard);task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
        response=web.StreamResponse(status=200,headers={'Content-Type':'application/json'})
        try:
            await response.prepare(req)
            while not task.done():
                await asyncio.wait([task],timeout=4)
                if not task.done():await response.write(b' ')
            try:result=task.result()
            except Exception as exc:
                code=exc.code if isinstance(exc,RenderFailure) else 'render_timeout' if isinstance(exc,TimeoutError) else 'image_render_failed'
                self.last_failure=code;self.last_failure_time=time.monotonic()
                result={'ok':False,'error':code,'uncertain':bool(self.uncertain_pid),'retry_safe':not bool(self.uncertain_pid)}
            await response.write(json.dumps(result).encode());await response.write_eof()
        except (ConnectionError,asyncio.CancelledError):
            # Shielded inference remains in self.tasks and holds the admission gate.
            pass
        return response

WORKER_KEY=web.AppKey('worker',Worker)

def create_app(root=ROOT):
    w=Worker(root);app=web.Application(client_max_size=32768);app[WORKER_KEY]=w;app.on_startup.append(w.start);app.on_cleanup.append(w.stop)
    app.router.add_get('/health',w.health);app.router.add_post('/generate',w.generate);return app
if __name__=='__main__':web.run_app(create_app(),host='127.0.0.1',port=18191,access_log=None,print=None)
