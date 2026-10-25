"""Private loopback legal transport: gateway identity, encrypted cases and originals."""
import asyncio,hmac,json,logging,os,time,re,collections
from urllib.parse import quote
from pathlib import Path
from aiohttp import web
from .runtime import Runtime
from .sources import Sources,MANIFEST
from .common import DomainError,require
from .vault import Vault,MAX_FILE
from .chat import answer
from .vision import VisionAdapter
LOG=logging.getLogger('harmonika.legal');ACTOR=getattr(web,'RequestKey',web.AppKey)('actor',str)
OBSERVER=getattr(web,'RequestKey',web.AppKey)('observer',bool)
def make_app(rt,secret):
 require(type(secret) is str and len(secret)>=32,'service_secret_required')
 @web.middleware
 async def boundary(req,handler):
  if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-legal'})
  if not hmac.compare_digest(req.headers.get('X-Harmonika-Legal-Secret',''),secret):return web.json_response({'error':{'code':'forbidden'}},status=403)
  try:
   actor=req.headers.get('X-Harmonika-Principal','');rt.store.tenant(actor);req[ACTOR]=actor;req[OBSERVER]=req.headers.get('X-Harmonika-Legal-Observer')=='1';res=await handler(req);res.headers['Cache-Control']='no-store';res.headers['X-Content-Type-Options']='nosniff';return res
  except DomainError as e:return web.json_response({'error':{'code':e.code}},status=e.status)
  except (ValueError,KeyError,TypeError,UnicodeError):return web.json_response({'error':{'code':'invalid_request'}},status=400)
  except web.HTTPException:raise
  except Exception:
   LOG.error('legal_request_failed code=internal_error');return web.json_response({'error':{'code':'legal_unavailable'}},status=503)
 app=web.Application(middlewares=[boundary],client_max_size=MAX_FILE+65536);slots=asyncio.Semaphore(2);source_slots=asyncio.Semaphore(1)
 async def data(req,allowed):
  raw=await req.read();require(len(raw)<=262144,'input_too_large',413);v=json.loads(raw);require(type(v) is dict and not(set(v)-set(allowed)),'invalid_request');return v
 async def call(fn,*args):
  require(not slots.locked(),'legal_capacity',429)
  async with slots:return await asyncio.to_thread(fn,*args)
 async def caps(req):
  v=await asyncio.to_thread(rt.capabilities);v['features']['legal_observer_internal_only']=not getattr(rt.vision,'public_safe',False)
  if not req[OBSERVER] and not getattr(rt.vision,'public_safe',False):
   v['features']['legal_vision']=False;v['features']['legal_ocr']=False
   for entry in v['skills']:
    if entry['id'] in ('evidence.ocr.extract','evidence.vlm.analyze'):entry['readiness']={'status':'unavailable','reason':'isolated_public_vision_adapter_required'}
   v['implemented_handlers']=sum(x['readiness']['status']=='available' for x in v['skills'])
  return web.json_response(v)
 async def invoke(req):
  v=await data(req,('skill_id','input'))
  if v.get('skill_id') in ('evidence.ocr.extract','evidence.vlm.analyze','evidence.asr.transcribe'):require(req[OBSERVER] or getattr(rt.vision,'public_safe',False),'isolated_public_vision_adapter_required',503)
  return web.json_response(await call(rt.invoke,req[ACTOR],v.get('skill_id'),v.get('input',{}),req.headers.get('Idempotency-Key')))
 async def create(req):
  v=await data(req,('jurisdiction','occurred_on','perspective','topic','facts','questions'));return web.json_response(await call(rt.case_create,req[ACTOR],v,req.headers.get('Idempotency-Key')))
 async def case(req):return web.json_response(await asyncio.to_thread(rt.case_context,req[ACTOR],req.match_info['id']))
 upload_rates=collections.OrderedDict()
 async def upload(req):
  now=time.monotonic();actor=req[ACTOR]
  while upload_rates and next(iter(upload_rates.values()))[0]<now-60:upload_rates.popitem(last=False)
  started,count=upload_rates.pop(actor,(now,0));require(count<30,'upload_rate_limited',429);require(len(upload_rates)<10000,'legal_capacity',429);upload_rates[actor]=(started,count+1)
  reader=await req.multipart();case_id=None;name=None;raw=None;seen=set()
  while True:
   part=await reader.next()
   if part is None:break
   require(part.name in ('case_id','file') and part.name not in seen,'invalid_multipart');seen.add(part.name)
   if part.name=='case_id':
    chunks=[];size=0
    while True:
     b=await part.read_chunk(1024)
     if not b:break
     size+=len(b);require(size<=100,'invalid_case_id');chunks.append(b)
    case_id=b''.join(chunks).decode()
   else:
    name=part.filename;chunks=[];size=0
    while True:
     chunk=await part.read_chunk(65536)
     if not chunk:break
     size+=len(chunk);require(size<=MAX_FILE,'file_too_large',413);chunks.append(chunk)
    raw=b''.join(chunks)
  require(case_id is not None and raw is not None,'missing_upload_fields');meta=await call(Vault(rt.store,rt.root/'files',req[ACTOR]).upload,case_id,name,raw);return web.json_response({'file':meta},status=201)
 async def file(req):
  meta,raw=await asyncio.to_thread(rt.file_read,req[ACTOR],req.match_info['id']);return web.Response(body=raw,headers={'Content-Type':meta['mime_type'],'Content-Disposition':'attachment; filename="evidence"; filename*=UTF-8\'\''+quote(meta['name'],safe=''),'Cache-Control':'no-store'})
 async def status(req):return web.json_response(await asyncio.to_thread(rt.status,req[ACTOR],req.match_info['id']))
 async def events(req):
  id=req.match_info['id'];after=int(req.query.get('after_sequence','0'));last=req.headers.get('Last-Event-ID')
  if last:
   prefix,seq=last.rsplit(':',1);require(prefix==id,'invalid_event_id');after=max(after,int(seq))
  state=await asyncio.to_thread(rt.status,req[ACTOR],id);rows=await asyncio.to_thread(rt.events,req[ACTOR],id,after)
  if 'text/event-stream' not in req.headers.get('Accept',''):return web.json_response({'request':state,'events':rows})
  response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await response.prepare(req)
  try:
   for row in rows:await response.write(('id: '+row['event_id']+'\ndata: '+json.dumps(row)+'\n\n').encode())
   await response.write(b'data: [DONE]\n\n')
  except ConnectionResetError:pass
  return response
 async def sources(req):
  return web.json_response({'sources':[{k:v for k,v in x.items()}|{'id':id} for id,x in MANIFEST.items()],'currentness_certified':False})
 async def refresh(req):
  v=await data(req,('source_id',));require(not source_slots.locked(),'source_capacity',429)
  async with source_slots:return web.json_response({'source':await asyncio.to_thread(rt.sources.refresh,v.get('source_id'))})
 async def chat(req):
  require(re.fullmatch('[A-Za-z0-9_-]{1,128}',req.headers.get('Idempotency-Key','')) is not None,'idempotency_required')
  v=await data(req,('case_id','message','stream'));stream=v.pop('stream',False);require(type(stream) is bool,'invalid_stream')
  async def respond():
   if req[OBSERVER] or getattr(rt.vision,'public_safe',False):await asyncio.to_thread(rt.prepare_evidence,req[ACTOR],v.get('case_id'))
   return await call(answer,rt,req[ACTOR],v,req.headers.get('Idempotency-Key'))
  if not stream:return web.json_response(await respond())
  # Working state is emitted before source/case processing; tokens are not faked.
  response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await response.prepare(req)
  task=asyncio.create_task(respond())
  try:
   await response.write(b'data: {"type":"response.created","data":{"status":"processing_case_evidence_and_sources"}}\n\n')
   while not task.done():
    await asyncio.wait({task},timeout=3)
    if not task.done():await response.write(b': harmonika processing private case evidence and sources\n\n')
   out=await task;await response.write(('data: '+json.dumps({'type':'response.error' if out.get('status')=='failed' else 'response.completed','data':out},ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode())
  except (ConnectionResetError,asyncio.CancelledError):await asyncio.gather(task,return_exceptions=True)
  except Exception as e:
   code=e.code if isinstance(e,DomainError) else 'legal_chat_failed'
   try:await response.write(('data: '+json.dumps({'type':'response.error','data':{'code':code}})+'\n\ndata: [DONE]\n\n').encode())
   except ConnectionResetError:pass
  return response
 async def watch_poll(req):
  require(req[ACTOR]=='api-token:900000000000400000','internal_scheduler_required',403)
  return web.json_response(await call(rt.poll_watches))
 async def health(req):return web.json_response({'ok':True})
 app.router.add_post('/_internal/poll-watches',watch_poll);app.router.add_get('/healthz',health);app.router.add_get('/capabilities',caps);app.router.add_post('/invoke',invoke);app.router.add_post('/cases',create);app.router.add_get('/cases/{id}',case);app.router.add_post('/files',upload);app.router.add_get('/files/{id}/download',file);app.router.add_get('/requests/{id}',status);app.router.add_get('/requests/{id}/events',events);app.router.add_get('/sources',sources);app.router.add_post('/sources/refresh',refresh);app.router.add_post('/chat',chat)
 return app
async def main():
 import signal
 root=Path(os.environ.get('HARMONIKA_LEGAL_ROOT','/var/lib/harmonika-legal'));key=Path('/etc/harmonika-ai/legal-master.key').read_bytes();secret=Path('/etc/harmonika-ai/legal-engine.token').read_text().strip();from .local_media import LocalMedia
 media=LocalMedia();rt=Runtime(root,key,Sources(root/'sources.sqlite'),media if media.ready else VisionAdapter(os.environ.get('HARMONIKA_LEGAL_VISION_READY')=='1'));rt.recover()
 runner=web.AppRunner(make_app(rt,secret),access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_LEGAL_PORT','18190'))).start();stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
