"""Private builder transport; principal only from the authenticated gateway."""
import asyncio,base64,hmac,json,os,re,signal,time
from pathlib import Path
from aiohttp import web
from .core import Runtime,MAX_FILE,snapshot_check,TERMINAL
from .common import BuilderError,require,safe_path
from .connectors import Connectors

def make_app(rt,secret):
 require(type(secret) is str and len(secret)>=32,'service_secret_required')
 @web.middleware
 async def auth(req,handler):
  if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-web-builder'})
  if not hmac.compare_digest(req.headers.get('X-Harmonika-Builder-Secret',''),secret):return web.json_response({'error':{'code':'forbidden'}},status=403)
  try:
   from .common import principal
   req['actor']=principal(req.headers.get('X-Harmonika-Principal',''));out=await handler(req);out.headers['Cache-Control']='no-store';out.headers['X-Content-Type-Options']='nosniff';return out
  except BuilderError as e:return web.json_response({'error':{'code':e.code}},status=e.status)
  except (ValueError,TypeError,KeyError,UnicodeError):return web.json_response({'error':{'code':'invalid_request'}},status=400)
  except web.HTTPException:raise
  except Exception:return web.json_response({'error':{'code':'builder_unavailable'}},status=503)
 app=web.Application(middlewares=[auth],client_max_size=1048576);artifact_slots=asyncio.Semaphore(2)
 async def body(req,keys):
  raw=await req.read();require(len(raw)<=1048576,'input_too_large',413);v=json.loads(raw);require(type(v) is dict and not set(v)-set(keys),'unknown_fields');return v
 async def caps(req):return web.json_response(await rt.capabilities(req['actor']))
 async def projects(req):
  if req.method=='GET':return web.json_response({'projects':rt.projects(req['actor'])})
  v=await body(req,('name',));return web.json_response({'project':rt.create(req['actor'],v.get('name'),req.headers.get('Idempotency-Key'))},status=201)
 async def project(req):return web.json_response({'project':rt.project_view(req['actor'],req.match_info['pid'])})
 async def invoke(req):
  v=await body(req,('skill_id','input','expected_revision','overwrite'));out=await rt.invoke(req['actor'],req.match_info['pid'],v.get('skill_id'),v.get('input',{}),req.headers.get('Idempotency-Key'),v.get('expected_revision'),v.get('overwrite',False));return web.json_response({'job':out},status=202)
 async def build(req):
  v=await body(req,('spec','stack','run_checks','name','expected_revision','overwrite'));revision=v.pop('expected_revision',None);overwrite=v.pop('overwrite',False);out=await rt.invoke(req['actor'],req.match_info['pid'],'builder.workflow',v,req.headers.get('Idempotency-Key'),revision,overwrite,_workflow=True);return web.json_response({'job':out},status=202)
 async def deploy(req):
  v=await body(req,('target','environment','expected_revision'));revision=v.pop('expected_revision',None);require(type(revision) is int,'expected_revision_required');out=await rt.invoke(req['actor'],req.match_info['pid'],'builder.deploy',v,req.headers.get('Idempotency-Key'),revision,_workflow=True);return web.json_response({'job':out},status=202)
 async def deployment(req):
  from .deployment import status as deployment_status
  return web.json_response(await deployment_status(rt,req['actor'],req.match_info['jid']))
 async def status(req):return web.json_response({'job':rt.job(req['actor'],req.match_info['jid'])})
 async def approve(req):
  v=await body(req,('input_hash','decision'));require(v.get('decision')=='approve','explicit_approval_required');return web.json_response({'job':await rt.approve(req['actor'],req.match_info['jid'],v.get('input_hash'))})
 async def stop(req):return web.json_response({'job':await rt.stop(req['actor'],req.match_info['jid'])})
 async def files(req):
  a=req['actor'];pid=req.match_info['pid'];v=await body(req,('path','content','bytes_base64','expected_revision','overwrite'));path=safe_path(v.get('path'));require(('content' in v)!=('bytes_base64' in v),'file_content_required')
  raw=v.get('bytes_base64') if 'bytes_base64' in v else base64.b64encode(v['content'].encode()).decode();require(type(raw) is str,'invalid_file');snapshot_check({path:raw});require(type(v.get('expected_revision')) is int,'expected_revision_required')
  async with rt.lock(pid):
   w=rt.project(a,pid);require(w['revision']==v['expected_revision'],'revision_conflict',409);require(path not in w['data']['files'] or v.get('overwrite') is True,'file_exists',409);fs={**w['data']['files'],path:raw};snapshot_check(fs)
   with rt.store.db() as d:
    d.execute('BEGIN IMMEDIATE');rt.store.quota(d,a,max(0,len(json.dumps({**w['data'],'files':fs},ensure_ascii=False,separators=(',',':')).encode())+28-w['_stored_bytes']))
    n=d.execute('UPDATE projects SET revision=revision+1,data=? WHERE id=? AND actor=? AND revision=?',(rt.store.seal({**w['data'],'files':fs},'project:'+a+':'+pid),pid,a,w['revision'])).rowcount;require(n==1,'revision_conflict',409)
  return web.json_response({'project':rt.project_view(a,pid)},status=201)
 async def artifact(req):
  require(not artifact_slots.locked(),'builder_capacity',429)
  async with artifact_slots:raw=await asyncio.to_thread(rt.artifact,req['actor'],req.match_info['pid'])
  return web.Response(body=raw,headers={'Content-Type':'application/zip','Content-Disposition':'attachment; filename="harmonika-project.zip"'})
 async def events(req):
  a=req['actor'];jid=req.match_info['jid'];after=int(req.query.get('after_sequence','0'));last=req.headers.get('Last-Event-ID')
  if last:
   prefix,n=last.rsplit(':',1);require(prefix==jid,'invalid_event_id');after=max(after,int(n))
  state=rt.job(a,jid)
  if 'text/event-stream' not in req.headers.get('Accept',''):return web.json_response({'job':state,'events':rt.events(a,jid,after)})
  res=web.StreamResponse(headers={'Content-Type':'text/event-stream','X-Accel-Buffering':'no','Cache-Control':'no-store'});await res.prepare(req);started=time.monotonic()
  try:
   while time.monotonic()-started<180:
    rows=rt.events(a,jid,after)
    for row in rows:await res.write(('id: '+row['event_id']+'\ndata: '+json.dumps(row)+'\n\n').encode());after=row['sequence']
    if rt.job(a,jid)['state'] in TERMINAL and len(rows)<128:await res.write(b'data: [DONE]\n\n');break
    if not rows:await res.write(b': harmonika builder working\n\n')
    await asyncio.sleep(.5)
  except (ConnectionResetError,asyncio.CancelledError):pass
  return res
 async def health(req):return web.json_response({'ok':True})
 app.router.add_get('/healthz',health);app.router.add_get('/capabilities',caps);app.router.add_get('/projects',projects);app.router.add_post('/projects',projects);app.router.add_get('/projects/{pid}',project);app.router.add_post('/projects/{pid}/invoke',invoke);app.router.add_post('/projects/{pid}/build',build);app.router.add_post('/projects/{pid}/deploy',deploy);app.router.add_post('/projects/{pid}/files',files);app.router.add_get('/projects/{pid}/artifact',artifact);app.router.add_get('/jobs/{jid}',status);app.router.add_get('/jobs/{jid}/deployment',deployment);app.router.add_get('/jobs/{jid}/events',events);app.router.add_post('/jobs/{jid}/approve',approve);app.router.add_post('/jobs/{jid}/stop',stop)
 async def startup(app):await rt.recover()
 async def close(app):await rt.close()
 app.on_startup.append(startup);app.on_cleanup.append(close);return app
async def main():
 from .sandbox import WebSandbox
 root=Path(os.environ.get('HARMONIKA_BUILDER_ROOT','/var/lib/harmonika-builder'));rt=Runtime(root,Path('/etc/harmonika-ai/builder-master.key').read_bytes(),WebSandbox(),Connectors());secret=Path('/etc/harmonika-ai/builder-engine.token').read_text().strip()
 runner=web.AppRunner(make_app(rt,secret),access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_BUILDER_PORT','18191'))).start();stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
