"""Private async media job API. Start independently from the chat gateway."""
import asyncio
import fcntl
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import time
from aiohttp import web
from .adapter import RTXAdapter
from .diagnostics import error_type,STATE_CODES,observed
from .core import Store,RTXError,FILE_LIMIT,RESULT_LIMIT,ACTIVE

class Queue:
    def __init__(self,store,adapter):self.store=store;self.adapter=adapter;self.task=None;self.lock=None;self.recovery_lock=asyncio.Lock();self.last_error=None;self.failures=None;self.failure_logging_degraded=False;self.last_graphics_state=None
    async def record_failure(self,kind,code,latency):
        if self.failures is None:self.failure_logging_degraded=True;return
        try:
            result=await asyncio.to_thread(self.failures.record,'image.generate' if kind=='generate' else 'vision.analyze',error_type(code),latency)
            self.failure_logging_degraded=result not in (True,'ok')
        except Exception:self.failure_logging_degraded=True
    async def failure_summary(self):
        if self.failures is None:return {'status':'logging_unavailable','count':0,'recent':[]}
        try:
            value=await asyncio.to_thread(self.failures.summary)
            return {'status':value.get('status',value.get('logging','logging_unavailable')),'count':value.get('count',0),'recent':value.get('recent',[])}
        except Exception:return {'status':'logging_unavailable','count':0,'recent':[]}
    async def start(self):
        path=self.store.root/'worker.lock'
        if path.is_symlink():raise RTXError('unsafe_lock_path')
        self.lock=open(path,'a');os.chmod(path,0o600)
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:self.lock.close();self.lock=None;raise RTXError('queue_already_running',409) from None
        await self.store.call('startup')
        try:
            try:from engine_registry.failure_log import FailureLog
            except ModuleNotFoundError:from backend.engine_registry.failure_log import FailureLog
            self.failures=FailureLog(self.store.db)
        except Exception:self.failure_logging_degraded=True
        await self.adapter.start();self.task=asyncio.create_task(self.run())
    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        await self.adapter.close()
        if self.lock:fcntl.flock(self.lock,fcntl.LOCK_UN);self.lock.close();self.lock=None
    async def save_result(self,job,result):
        current=await self.store.call('get',job['owner'],job['id'],True)
        if current['cancel_requested']:
            await self.store.call('set_state',job['id'],'cancelled');return
        artifacts=[]
        for index,data in enumerate(result['images']):
            artifacts.append(await self.store.call('put_file',job['owner'],'result-'+str(index),data,RESULT_LIMIT))
        await self.store.call('set_state',job['id'],'completed',None,{'text':result['text'],'artifacts':artifacts})
    async def run(self):
        while True:
            try:job=await self.store.call('claim');self.last_error=None
            except Exception:
                self.last_error='queue_storage_unavailable';await asyncio.sleep(.5);continue
            if job is None:await asyncio.sleep(.2);continue
            started=time.monotonic()
            try:
                result=await self.adapter.run(job,self.store);await self.save_result(job,result)
            except asyncio.CancelledError:
                await self.store.call('set_state',job['id'],'unknown','service_interrupted');raise
            except RTXError as error:
                await self.record_failure(job['kind'],error.code,(time.monotonic()-started)*1000)
                if error.code=='remote_job_cancelled':state='cancelled'
                else:state='unknown' if error.uncertain else 'failed'
                await self.store.call('set_state',job['id'],state,error.code)
            except Exception:
                await self.record_failure(job['kind'],'internal_worker_error',(time.monotonic()-started)*1000)
                await self.store.call('set_state',job['id'],'unknown','internal_worker_error')
    async def recover(self,owner,jid):
        async with self.recovery_lock:
            job=await self.store.call('get',owner,jid,True)
            if job['status'] in {'completed','cancelled','failed'}:return await self.store.call('get',owner,jid)
            if job['status'] not in {'unknown','cancel_requested'}:raise RTXError('job_not_recoverable',409)
            try:result=await self.adapter.recover(job);await self.save_result(job,result)
            except RTXError as error:
                if not error.uncertain:await self.store.call('set_state',jid,'cancelled' if error.code=='remote_job_cancelled' else 'failed',error.code)
                raise
            return await self.store.call('get',owner,jid)

STORE=web.AppKey('store',Store)
QUEUE=web.AppKey('queue',Queue)
CLIENTS=web.AppKey('clients',dict)
BRIDGE_SECRET=web.AppKey('bridge_secret',str)
UPLOAD_SLOTS=web.AppKey('upload_slots',asyncio.Semaphore)
OWNER=web.RequestKey('owner',str)

@web.middleware
async def errors(request,handler):
    try:return await handler(request)
    except RTXError as error:return web.json_response({'error':{'code':error.code}},status=error.status)
    except (json.JSONDecodeError,UnicodeError,ValueError):return web.json_response({'error':{'code':'invalid_request'}},status=400)
    except web.HTTPException as error:return web.json_response({'error':{'code':{404:'not_found',405:'method_not_allowed',413:'request_too_large'}.get(error.status,'http_error')}},status=error.status)
    except Exception:return web.json_response({'error':{'code':'internal_error'}},status=500)

@web.middleware
async def auth(request,handler):
    header=request.headers.get('Authorization','');token=header[7:] if header.startswith('Bearer ') else ''
    digest=hashlib.sha256(token.encode()).hexdigest();principal=None
    for owner,expected in request.app[CLIENTS].items():
        if token and hmac.compare_digest(digest,expected):principal=owner
    bridge=request.app[BRIDGE_SECRET]
    if principal is None and bridge and token and hmac.compare_digest(token,bridge):
        claimed=request.headers.get('X-Harmonika-Media-Principal','')
        if re.fullmatch(r'api_[1-9][0-9]{0,18}',claimed):principal=claimed
    if principal is None:return web.json_response({'error':{'code':'invalid_token'}},status=401)
    request[OWNER]=principal
    return await handler(request)

async def health(request):
    queue=request.app[QUEUE];caps=await queue.adapter.health()
    connection=getattr(queue.adapter,'connection',observed('unknown'))
    state=connection.get('status','unknown')
    if state!=queue.last_graphics_state and state in STATE_CODES:
        await queue.record_failure('generate',STATE_CODES[state],connection.get('latency_ms',0))
    queue.last_graphics_state=state
    uncertain=await request.app[STORE].call('uncertain')
    if uncertain:connection={**connection,'status':'recovery_required'}
    diagnostics={'graphics':connection,'errors':await queue.failure_summary(),'logging_degraded':queue.failure_logging_degraded}
    return web.json_response({'ok':True,'provider':'Harmonika Ai','worker':caps,'queue':await request.app[STORE].call('counts',request[OWNER]),'concurrency':1,'queue_ready':not queue.task.done() and queue.last_error is None and not uncertain,'blocked_by_uncertain_job':uncertain,'diagnostics':diagnostics})
async def submit(request):
    if request.content_type!='application/json':raise RTXError('json_required',415)
    body=bytearray()
    async for part in request.content.iter_chunked(16384):
        body.extend(part)
        if len(body)>65536:raise RTXError('request_too_large',413)
    value=json.loads(body);job,new=await request.app[STORE].call('submit',request[OWNER],value,request.headers.get('Idempotency-Key',''))
    return web.json_response({'job':job},status=202 if new else 200)
async def status(request):return web.json_response({'job':await request.app[STORE].call('get',request[OWNER],request.match_info['jid'])})
async def cancel(request):return web.json_response({'job':await request.app[STORE].call('cancel',request[OWNER],request.match_info['jid'])})
async def recover(request):return web.json_response({'job':await request.app[QUEUE].recover(request[OWNER],request.match_info['jid'])})
async def upload(request):
    async with request.app[UPLOAD_SLOTS]:
        if request.content_type!='multipart/form-data':raise RTXError('multipart_required',415)
        reader=await request.multipart();part=await reader.next()
        if part is None or part.name!='file':raise RTXError('file_required')
        chunks=[];size=0
        while True:
            chunk=await part.read_chunk(65536)
            if not chunk:break
            size+=len(chunk)
            if size>FILE_LIMIT:raise RTXError('file_too_large',413)
            chunks.append(chunk)
        if await reader.next() is not None:raise RTXError('one_file_per_upload')
        meta=await request.app[STORE].call('put_file',request[OWNER],part.filename or 'image',b''.join(chunks))
        return web.json_response({'file':meta},status=201)
async def download(request):
    data,meta=await request.app[STORE].call('file_bytes',request[OWNER],request.match_info['fid'])
    return web.Response(body=data,content_type=meta['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

def create_app(root,adapter,clients,bridge_secret=""):
    if not isinstance(clients,dict) or not clients or len(set(clients.values()))!=len(clients) or any(not re.fullmatch('[A-Za-z0-9_-]{1,64}',k) or not re.fullmatch('[a-f0-9]{64}',v) for k,v in clients.items()):raise RTXError('invalid_client_keys')
    if bridge_secret and (not isinstance(bridge_secret,str) or not 32<=len(bridge_secret)<=200):raise RTXError('invalid_bridge_secret')
    app=web.Application(middlewares=[errors,auth],client_max_size=FILE_LIMIT+65536)
    app[BRIDGE_SECRET]=bridge_secret
    app[STORE]=Store(root);app[QUEUE]=Queue(app[STORE],adapter);app[CLIENTS]=clients;app[UPLOAD_SLOTS]=asyncio.Semaphore(2)
    app.add_routes([web.get('/health',health),web.post('/v1/rtx/jobs',submit),web.get('/v1/rtx/jobs/{jid}',status),web.post('/v1/rtx/jobs/{jid}/cancel',cancel),web.post('/v1/rtx/jobs/{jid}/recover',recover),web.post('/v1/rtx/files',upload),web.get('/v1/rtx/files/{fid}',download)])
    async def context(app):
        try:await app[QUEUE].start();yield
        finally:await app[QUEUE].close()
    app.cleanup_ctx.append(context)
    return app

def main():
    credentials=Path(os.environ.get('CREDENTIALS_DIRECTORY','/etc/harmonika-ai/rtx-queue'))
    config=json.loads(Path(os.environ.get('HARMONIKA_RTX_CONFIG',str(credentials/'config.json'))).read_text())
    clients=json.loads((credentials/'client-keys.json').read_text())
    token=(credentials/'worker.token').read_text().strip() if (credentials/'worker.token').exists() else ''
    adapter=RTXAdapter(config['endpoint'],config.get('mode','v1'),token,config.get('connect_timeout',3),config.get('job_timeout',120))
    if config.get('vision_endpoint'):
        from .router import WorkerRouter
        vision_token=(credentials/'vision.token').read_text().strip()
        vision=RTXAdapter(config['vision_endpoint'],'v1',vision_token,config.get('connect_timeout',3),config.get('job_timeout',120))
        adapter=WorkerRouter(adapter,vision)
    bridge=(credentials/'bridge.token').read_text().strip() if (credentials/'bridge.token').exists() else ''
    app=create_app(config.get('data_root','/var/lib/harmonika-rtx-queue'),adapter,clients,bridge)
    logging.basicConfig(level=logging.WARNING)
    web.run_app(app,host='127.0.0.1',port=config.get('port',18186),access_log=None,print=None,shutdown_timeout=10)
if __name__=='__main__':main()
