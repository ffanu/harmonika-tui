"""Private analyzer gateway with operator-managed discovery. No customer payloads logged."""
import asyncio,hashlib,hmac,json,logging,math,os,re,signal,time
from collections import deque,OrderedDict
from pathlib import Path
from aiohttp import web
from .index import descriptors,summary,restricted
class RequestError(ValueError):
 def __init__(self,code,status=400):self.code=code;self.status=status

def unique_pairs(pairs):
 out={}
 for k,v in pairs:
  if k in out:raise ValueError('duplicate_json_key')
  out[k]=v
 return out

def check_json(value):
 pending=[(value,0)];nodes=0
 while pending:
  v,d=pending.pop();nodes+=1
  if d>20 or nodes>20000:raise RequestError('input_complexity')
  if type(v) is dict:
   if any(type(k) is not str or len(k)>100 for k in v):raise RequestError('invalid_field')
   pending.extend((x,d+1) for x in v.values())
  elif type(v) is list:pending.extend((x,d+1) for x in v)
  elif type(v) not in (str,int,float,bool,type(None)):raise RequestError('invalid_json')
  elif type(v) is float and not math.isfinite(v):raise RequestError('nonfinite_json')

def make_app(secret,skills=None,dispatcher=None,sandbox=None,mobile_enabled=True,discovery=None,discovery_poll_seconds=5):
 if type(secret) is not str or len(secret)<32:raise ValueError('service_secret_required')
 if skills is None:
  from .analyzers import dispatch
  from .adapters import descriptors as analyzer_descriptors
  skills=analyzer_descriptors();dispatcher=dispatch
 if sandbox is not None:
  from .browser import DESCRIPTOR
  skills={**skills,'workflow.browser':DESCRIPTOR}
 view=descriptors(skills);by_id={x['id']:x for x in view};capability=summary(skills)
 mobile_rows=[];mobile_cap={};mobile_by_id={}
 if mobile_enabled:
  try:from mobile_engine.registry import descriptors as mobile_descriptors,capabilities as mobile_caps
  except ModuleNotFoundError:
   from backend.mobile_engine.registry import descriptors as mobile_descriptors,capabilities as mobile_caps
  mobile_rows=mobile_descriptors();mobile_by_id={x['id']:x for x in mobile_rows};mobile_cap=mobile_caps()
  capability['related_domains']={'mobile_builder':{'capabilities_endpoint':'/v1/mobile-builder/capabilities','skills_endpoint':'/v1/mobile-builder/skills','catalog_size':214}}
 if (Path(__file__).parent/'core_index.json').exists():
  capability.setdefault('related_domains',{})['shared_core']={'capabilities_endpoint':'/v1/core/capabilities','skills_endpoint':'/v1/core/skills','catalog_size':99}
 if (Path(__file__).parent/'unified_index.json').exists():
  capability.setdefault('related_domains',{})['unified']={'capabilities_endpoint':'/v1/unified/capabilities','skills_endpoint':'/v1/unified/skills','catalog_size':70}
 slots=asyncio.Semaphore(2);active=set();rates=OrderedDict();log=logging.getLogger('harmonika.master.audit')
 def live_rows():
  return view + (discovery.descriptors() if discovery else [])
 def live_capability():
  if discovery is None:return capability
  current=json.loads(json.dumps(capability));rows=live_rows()
  current['catalog_size']=len(rows)
  current['readiness']['available']=sum(x['status']=='available' for x in rows)
  current['features']['runtime_discovery']=True
  current['features']['hot_skill_registration']=True
  current['discovery']=discovery.summary()
  return current
 if discovery is not None:
  async def lifecycle(app):
   await discovery.refresh()
   async def polling():
    while True:
     await asyncio.sleep(discovery_poll_seconds)
     try:await discovery.refresh()
     except asyncio.CancelledError:raise
     except Exception:log.warning('discovery_refresh_failed')
   watcher=asyncio.create_task(polling())
   yield
   watcher.cancel()
   await asyncio.gather(watcher,return_exceptions=True)
  # Registered below after app construction.

 @web.middleware
 async def auth(req,handler):
  if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-master-skills'})
  started=time.monotonic();actor=req.headers.get('X-Harmonika-Principal','')
  try:
   if not hmac.compare_digest(req.headers.get('X-Harmonika-Master-Secret',''),secret):raise RequestError('forbidden',403)
   if not re.fullmatch(r'api-token:[1-9][0-9]{0,18}',actor):raise RequestError('invalid_principal',403)
   res=await handler(req)
  except RequestError as e:
   res=web.json_response({'error':{'code':e.code}},status=e.status)
   if e.code=='discovery_scan_busy':res.headers['Retry-After']='1'
  except web.HTTPException as e:res=web.json_response({'error':{'code':'request_too_large' if e.status==413 else 'not_found'}},status=e.status)
  except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):res=web.json_response({'error':{'code':'invalid_input'}},status=400)
  except Exception:res=web.json_response({'error':{'code':'analysis_unavailable'}},status=503)
  res.headers['Cache-Control']='no-store';res.headers['X-Content-Type-Options']='nosniff'
  # Deliberately excludes prompt, result, URLs, query strings, files, headers and secrets.
  if req.method=='POST':log.info(json.dumps({'actor':hmac.new(secret.encode(),actor.encode(),hashlib.sha256).hexdigest()[:20],'status':res.status,'elapsed_ms':round((time.monotonic()-started)*1000)}))
  return res
 app=web.Application(middlewares=[auth],client_max_size=1048576)
 if discovery is not None:app.cleanup_ctx.append(lifecycle)
 async def cap(req):
  if req.query:raise RequestError('unsupported_query')
  return web.json_response(mobile_cap if req.path.startswith('/mobile/') else live_capability())
 async def listing(req):
  if set(req.query)-{'status','engine','offset','limit'}:raise RequestError('unsupported_query')
  offset=int(req.query.get('offset','0'));limit=int(req.query.get('limit','100'))
  if not 0<=offset<=10000 or not 1<=limit<=200:raise RequestError('invalid_pagination')
  rows=mobile_rows if req.path.startswith('/mobile/') else live_rows()
  for field in ('status','engine'):
   if req.query.get(field):rows=[r for r in rows if r.get(field)==req.query[field]]
  return web.json_response({'total':len(rows),'offset':offset,'skills':rows[offset:offset+limit]})
 async def detail(req):
  
  if req.query:raise RequestError('unsupported_query')
  id=req.match_info['sid'];row=(mobile_by_id if req.path.startswith('/mobile/') else {r['id']:r for r in live_rows()}).get(id)
  if row is None:raise RequestError('unknown_skill',404)
  return web.json_response({'skill':row})
 async def discovery_status(req):
  if req.query:raise RequestError('unsupported_query')
  return web.json_response(discovery.summary() if discovery else {'status':'disabled','registered':0})
 async def invoke(req):
  if req.query:raise RequestError('unsupported_query')
  raw=await req.read();body=json.loads(raw,object_pairs_hook=unique_pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')));check_json(body)
  if type(body) is not dict or set(body)-{'skill_id','input'} or type(body.get('input')) is not dict:raise RequestError('invalid_request')
  id=body.get('skill_id')
  if type(id) is not str or len(id)>100:raise RequestError('invalid_skill_id')
  mobile=req.path=='/mobile/invoke'
  if not mobile and discovery is not None and id not in by_id and id not in skills:
   waiter=getattr(discovery,'wait_ready',None)
   if waiter:
    try:await waiter(timeout=10)
    except asyncio.CancelledError:raise
    except Exception as error:
     if getattr(error,'code','')=='E_SCAN_BUSY':raise RequestError('discovery_scan_busy',503) from None
     raise RequestError('discovery_unavailable',503) from None
  discovered=not mobile and discovery is not None and id in {r['id'] for r in discovery.descriptors()}
  if mobile:
   row=mobile_by_id.get(id)
   if row is None:raise RequestError('unknown_skill',404)
   if row['status']!='available':raise RequestError(row['reason'],503)
   if len(raw)>65536:raise RequestError('input_too_large',413)
  if not mobile and restricted(id):raise RequestError('restricted_operation',403)
  if not mobile and not discovered and id not in skills:
   known=by_id.get(id)
   if known and known['status']=='domain_ambiguous':raise RequestError('explicit_engine_context_required',409)
   raise RequestError('engine_context_required' if known and known['status'] in ('context_required','local_only') else 'implementation_missing' if known else 'unknown_skill',409 if known and known['status'] in ('context_required','local_only') else 501 if known else 404)
  actor=req.headers['X-Harmonika-Principal'];now=time.monotonic()
  if actor not in rates and len(rates)>=1000:
   expired=[a for a,d in rates.items() if not d or d[-1]<now-60]
   for a in expired:rates.pop(a,None)
   if len(rates)>=1000:raise RequestError('capacity',429)
  bucket=rates.setdefault(actor,deque())
  while bucket and bucket[0]<now-60:bucket.popleft()
  if len(bucket)>=60:raise RequestError('rate_limit',429)
  if actor in active or len(active)>=2:raise RequestError('analysis_busy',429)
  bucket.append(now);active.add(actor)
  async def perform():
   try:
    async with slots:
     if discovered:
      waiter=getattr(discovery,'wait_ready',None)
      if waiter:
       try:await waiter(timeout=3)
       except asyncio.CancelledError:raise
       except Exception as error:
        if getattr(error,'code','')=='E_SCAN_BUSY':raise RequestError('discovery_scan_busy',503) from None
        raise RequestError('discovery_unavailable',503) from None
      if len(raw)>65536:raise RequestError('input_too_large',413)
      try:from engine_registry.schema import audit as audit_schema,validate as validate_schema,SchemaError
      except ModuleNotFoundError:from backend.engine_registry.schema import audit as audit_schema,validate as validate_schema,SchemaError
      row=next((r for r in discovery.descriptors() if r['id']==id),None)
      if row is None:raise RequestError('skill_changed',409)
      try:validate_schema(body['input'],audit_schema(row['input_schema']))
      except SchemaError:raise RequestError('invalid_skill_input',400) from None
      try:return await discovery.execute(id,body['input'])
      except asyncio.CancelledError:raise
      except Exception as error:
       code=getattr(error,'code','')
       mapped={'E_SCAN_BUSY':('discovery_scan_busy',503),'E_TIMEOUT':('skill_timeout',504),'E_PAYLOAD':('invalid_skill_input',400),'E_NOT_REGISTERED':('skill_changed',409),'E_DATABASE':('discovery_audit_unavailable',503)}
       message,status=mapped.get(code,('skill_execution_failed',422))
       raise RequestError(message,status) from None
     if mobile:
      try:from mobile_engine.registry import dispatch as mobile_dispatch
      except ModuleNotFoundError:
       from backend.mobile_engine.registry import dispatch as mobile_dispatch
      return await asyncio.to_thread(mobile_dispatch,id,body['input'])
     if id=='workflow.browser' and sandbox is not None:
      from .browser import execute
      return await execute(body['input'],sandbox)
     return await asyncio.to_thread(dispatcher,id,body['input'])
   finally:active.discard(actor)
  task=asyncio.create_task(perform())
  task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
  try:result=await asyncio.wait_for(asyncio.shield(task),40 if id=='workflow.browser' else 15)
  except asyncio.TimeoutError:
   # The bounded worker keeps its slot until it really exits; timeout is not cancellation.
   task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
   raise RequestError('analysis_timeout',504) from None
  except RequestError:raise
  except ValueError:raise RequestError('invalid_input') from None
  if type(result) is not dict:raise RequestError('invalid_worker_result',422)
  if not discovered and (result.get('status')!='completed' or (not mobile and result.get('analysis_performed') is not True)):
   raise RequestError('invalid_analysis_input' if result.get('status')=='rejected' else 'analysis_failed',400 if result.get('status')=='rejected' else 422)
  if discovered:result={'status':'completed','skill_id':id,'result':result}
  encoded=json.dumps(result,ensure_ascii=False,allow_nan=False).encode()
  if len(encoded)>262144:raise RequestError('analysis_output_limit',413)
  return web.Response(body=encoded,content_type='application/json')
 app.router.add_get('/capabilities',cap);app.router.add_get('/skills',listing);app.router.add_get('/skills/discovery',discovery_status);app.router.add_get('/skills/{sid}',detail);app.router.add_post('/invoke',invoke)
 if mobile_enabled:
  app.router.add_get('/mobile/capabilities',cap);app.router.add_get('/mobile/skills',listing);app.router.add_get('/mobile/skills/{sid}',detail);app.router.add_post('/mobile/invoke',invoke)
 return app
async def main():
 logging.basicConfig(level=logging.INFO,format='%(message)s')
 secret=Path('/etc/harmonika-ai/master-engine.token').read_text().strip()
 from web_builder.sandbox import WebSandbox
 sandbox=WebSandbox();ready=await sandbox.capabilities()
 from engine_registry.discovery import Discovery
 from .adapters import descriptors as analyzer_descriptors
 from mobile_engine.registry import descriptors as mobile_descriptors
 reserved_ids={r['id'] for r in descriptors(analyzer_descriptors())}|{r['id'] for r in mobile_descriptors()}|{'discovery','capabilities','invoke'}
 discovery=Discovery(os.environ.get('HARMONIKA_SKILLS_DIRECTORY','/opt/harmonika-ai/skills-runtime'),os.environ.get('HARMONIKA_DISCOVERY_DATABASE','/var/lib/harmonika-master/discovery.sqlite3'),reserved_ids=reserved_ids,identity_policy=lambda identity:not restricted(identity))
 runner=web.AppRunner(make_app(secret,sandbox=sandbox if ready.get('ready') else None,discovery=discovery),access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_MASTER_PORT','18193'))).start();stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
