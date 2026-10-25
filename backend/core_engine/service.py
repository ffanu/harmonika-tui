"""Private shared core HTTP boundary. No caller code/network/host paths are executed."""
import asyncio,hashlib,hmac,json,logging,math,os,re,signal,time
from collections import deque,OrderedDict
from pathlib import Path
from aiohttp import web
from .registry import capabilities,descriptors,local_descriptors,dispatch

class RequestError(ValueError):
 def __init__(self,code,status=400):self.code=code;self.status=status

def unique(pairs):
 out={}
 for key,value in pairs:
  if key in out:raise RequestError('duplicate_json_key')
  out[key]=value
 return out

def bounded(value):
 nodes=0;pending=[(value,0)]
 while pending:
  item,depth=pending.pop();nodes+=1
  if nodes>20000 or depth>20:raise RequestError('input_complexity')
  if type(item) is dict:
   if any(type(k) is not str or len(k)>128 for k in item):raise RequestError('invalid_field')
   pending.extend((v,depth+1) for v in item.values())
  elif type(item) is list:pending.extend((v,depth+1) for v in item)
  elif type(item) is float and not math.isfinite(item):raise RequestError('nonfinite_json')
  elif type(item) is int and item.bit_length()>4096:raise RequestError('numeric_precision')
  elif type(item) not in (str,int,float,bool,type(None)):raise RequestError('invalid_json')

ERROR_STATUS={'not_found':404,'workflow_not_found':404,'memory_not_found':404,'idempotency_required':400,'idempotency_conflict':409,'in_progress':409,'recovery_required':409,'capacity':429,'storage_capacity':429,'quota_exceeded':429,'invalid_actor':403}
def make_app(secret,store):
 if type(secret) is not str or len(secret)<32:raise ValueError('service_secret_required')
 rows=descriptors();by_id={r['id']:r for r in rows};caps=capabilities();local=local_descriptors();active=set();rates=OrderedDict();log=logging.getLogger('harmonika.core.audit')
 @web.middleware
 async def boundary(req,handler):
  if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-shared-core'})
  started=time.monotonic();actor=req.headers.get('X-Harmonika-Principal','')
  try:
   if not hmac.compare_digest(req.headers.get('X-Harmonika-Core-Secret',''),secret) or not re.fullmatch(r'api-token:[1-9][0-9]{0,18}',actor):raise RequestError('forbidden',403)
   result=await handler(req)
  except RequestError as e:result=web.json_response({'error':{'code':e.code}},status=e.status)
  except web.HTTPException as e:result=web.json_response({'error':{'code':'input_too_large' if e.status==413 else 'not_found'}},status=e.status)
  except (ValueError,TypeError,KeyError,UnicodeError,RecursionError) as e:
   code=getattr(e,'code',None)
   if type(code) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',code):code='invalid_input'
   status=getattr(e,'status',ERROR_STATUS.get(code,400))
   if status not in (400,403,404,409,413,429,500,503):status=400
   result=web.json_response({'error':{'code':code}},status=status)
  except Exception:result=web.json_response({'error':{'code':'core_operation_failed'}},status=503)
  result.headers['Cache-Control']='no-store';result.headers['X-Content-Type-Options']='nosniff'
  if req.method=='POST':log.info(json.dumps({'actor':hmac.new(secret.encode(),actor.encode(),hashlib.sha256).hexdigest()[:20],'status':result.status,'elapsed_ms':round((time.monotonic()-started)*1000)}))
  return result
 app=web.Application(middlewares=[boundary],client_max_size=131072)
 async def cap(req):
  if req.query:raise RequestError('unsupported_query')
  return web.json_response(caps)
 async def listing(req):
  if set(req.query)-{'status','offset','limit'}:raise RequestError('unsupported_query')
  limit=int(req.query.get('limit','100'));offset=int(req.query.get('offset','0'))
  if not 1<=limit<=200 or not 0<=offset<=1000:raise RequestError('invalid_pagination')
  data=[r for r in rows if not req.query.get('status') or r['status']==req.query['status']]
  return web.json_response({'total':len(data),'offset':offset,'skills':data[offset:offset+limit]})
 async def detail(req):
  if req.query:raise RequestError('unsupported_query')
  row=by_id.get(req.match_info['sid'])
  if row is None:raise RequestError('unknown_skill',404)
  return web.json_response({'skill':row})
 async def invoke(req):
  if req.query:raise RequestError('unsupported_query')
  raw=await req.read();body=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(RequestError('nonfinite_json')));bounded(body)
  if type(body) is not dict or set(body)-{'skill_id','input'} or type(body.get('input')) is not dict or type(body.get('skill_id')) is not str:raise RequestError('invalid_request')
  id=body['skill_id'];row=by_id.get(id)
  if row is None:raise RequestError('unknown_skill',404)
  if id not in local:raise RequestError('local_runtime_required' if row['status']=='local_only' else 'gateway_delegation_required' if row['status'] in ('context_required','native_endpoint') else row['reason'],409 if row['status'] in ('local_only','context_required','native_endpoint') else 503)
  key=req.headers.get('Idempotency-Key')
  if key is not None and not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',key):raise RequestError('invalid_idempotency_key')
  actor=req.headers['X-Harmonika-Principal'];now=time.monotonic()
  if actor not in rates and len(rates)>=1000:
   for a in list(rates):
    if not rates[a] or rates[a][-1]<now-60:rates.pop(a,None)
   if len(rates)>=1000:raise RequestError('capacity',429)
  bucket=rates.setdefault(actor,deque())
  while bucket and bucket[0]<now-60:bucket.popleft()
  if len(bucket)>=60:raise RequestError('rate_limit',429)
  if actor in active or len(active)>=2:raise RequestError('core_busy',429)
  active.add(actor);bucket.append(now)
  async def perform():
   try:return await asyncio.to_thread(dispatch,id,body['input'],actor,store,key)
   finally:active.discard(actor)
  task=asyncio.create_task(perform());task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
  try:out=await asyncio.wait_for(asyncio.shield(task),20)
  except asyncio.TimeoutError:raise RequestError('operation_timeout',504) from None
  if type(out) is not dict:raise RequestError('invalid_worker_result',502)
  code=409 if out.get('status') in ('in_progress','recovery_required') else 200
  data=json.dumps({'schema_version':1,**out},ensure_ascii=False,allow_nan=False).encode()
  if len(data)>262144:raise RequestError('output_limit',413)
  return web.Response(body=data,status=code,content_type='application/json')
 app.router.add_get('/capabilities',cap);app.router.add_get('/skills',listing);app.router.add_get('/skills/{sid}',detail);app.router.add_post('/invoke',invoke)
 return app
async def main():
 from .state import Store
 secret=Path('/etc/harmonika-ai/core-engine.token').read_text().strip();storage=Path('/etc/harmonika-ai/core-storage.key').read_bytes()
 if len(storage)!=32:raise ValueError('storage_key_required')
 store=Store(Path(os.environ.get('HARMONIKA_CORE_DATABASE','/var/lib/harmonika-core/core.sqlite3')),storage)
 logging.basicConfig(level=logging.INFO,format='%(message)s')
 runner=web.AppRunner(make_app(secret,store),access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_CORE_PORT','18195'))).start()
 stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
