"""Loopback transport. Only a trusted gateway supplies actor identity."""
import asyncio,hmac,json,logging,os,time
from pathlib import Path
from aiohttp import web,ClientSession,ClientTimeout
from .runtime import Runtime,TERMINAL
from .agent import run_agent
from .common import DomainError,require
LOG=logging.getLogger('harmonika.telco')
ACTOR=getattr(web,'RequestKey',web.AppKey)('actor',str)
def make_app(rt,secret,decide=None):
    require(type(secret) is str and len(secret)>=32,'service_secret_required')
    @web.middleware
    async def boundary(req,handler):
        if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-telco'})
        if not hmac.compare_digest(req.headers.get('X-Harmonika-Telco-Secret',''),secret):return web.json_response({'error':{'code':'forbidden'}},status=403)
        started=time.monotonic()
        try:
            a=req.headers.get('X-Harmonika-Principal','');rt.store.tenant(a);req[ACTOR]=a
            res=await handler(req);res.headers['Cache-Control']='no-store';return res
        except DomainError as e:return web.json_response({'error':{'code':e.code}},status=e.status)
        except (ValueError,KeyError,TypeError):return web.json_response({'error':{'code':'invalid_request'}},status=400)
        except web.HTTPException:raise
        except Exception:
            LOG.error('request_failed type=internal_error duration_ms=%d',int((time.monotonic()-started)*1000))
            return web.json_response({'error':{'code':'telco_unavailable'}},status=503)
    app=web.Application(middlewares=[boundary],client_max_size=262144)
    async def data(req,allowed):
        v=await req.json();require(type(v) is dict and not(set(v)-set(allowed)),'invalid_request');return v
    domain_slots=asyncio.Semaphore(4)
    async def domain_call(fn,*args):
        require(not domain_slots.locked(),'domain_capacity',429)
        async with domain_slots:return await asyncio.to_thread(fn,*args)
    async def caps(req):return web.json_response(await asyncio.to_thread(rt.capabilities))
    async def invoke(req):
        v=await data(req,('skill_id','input','idempotency_key'));key=req.headers.get('Idempotency-Key')
        if key and v.get('idempotency_key'):require(key==v['idempotency_key'],'idempotency_conflict',409)
        out=await domain_call(rt.invoke,req[ACTOR],v.get('skill_id'),v.get('input',{}),key or v.get('idempotency_key'));return web.json_response(out)
    async def status(req):return web.json_response(await asyncio.to_thread(rt.status,req[ACTOR],req.match_info['id']))
    async def approve(req):
        v=await data(req,('decision','input_hash'));require(v.get('decision')=='approve','explicit_approval_required')
        return web.json_response(await domain_call(rt.approve,req[ACTOR],req.match_info['id'],v.get('input_hash')))
    async def stop(req):return web.json_response(await asyncio.to_thread(rt.stop,req[ACTOR],req.match_info['id']))
    async def events(req):
        a=req[ACTOR];rid=req.match_info['id'];after=int(req.query.get('after_sequence','0'));last=req.headers.get('Last-Event-ID','')
        if last:
            prefix,n=last.rsplit(':',1);require(prefix==rid,'invalid_event_id');after=max(after,int(n))
        state=await asyncio.to_thread(rt.status,a,rid)
        if 'text/event-stream' not in req.headers.get('Accept',''):return web.json_response({'request':state,'events':await asyncio.to_thread(rt.events,a,rid,after)})
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await response.prepare(req);deadline=time.monotonic()+180
        try:
            while time.monotonic()<deadline:
                state=await asyncio.to_thread(rt.status,a,rid);rows=await asyncio.to_thread(rt.events,a,rid,after)
                for row in rows:await response.write(('id: '+row['event_id']+'\ndata: '+json.dumps(row)+'\n\n').encode());after=row['sequence']
                if state['status'] in TERMINAL and len(rows)<128:await response.write(b'data: [DONE]\n\n');break
                if not rows:await response.write(b': waiting_for_client_approval\n\n')
                await asyncio.sleep(.5)
        except (ConnectionResetError,asyncio.CancelledError):pass
        return response
    agent_slots=asyncio.Semaphore(2)
    async def agent(req):
        require(decide is not None,'planner_unavailable',503)
        v=await data(req,('messages','stream'));require(type(v.get('stream',False)) is bool,'invalid_stream')
        a=req[ACTOR];key=req.headers.get('Idempotency-Key');require(type(key) is str and 1<=len(key)<=128,'idempotency_required')
        require(not agent_slots.locked(),'agent_capacity',429)
        async with agent_slots:
            if not v.get('stream'):return web.json_response(await run_agent(rt,a,v.get('messages'),key,decide))
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await response.prepare(req)
            async def emit(value):await response.write(('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode())
            task=asyncio.create_task(run_agent(rt,a,v.get('messages'),key,decide,emit))
            try:
                while not task.done():
                    await asyncio.wait({task},timeout=5)
                    if not task.done():await response.write(b': harmonika working\n\n')
                await task;await response.write(b'data: [DONE]\n\n')
            except (ConnectionResetError,asyncio.CancelledError):task.cancel();await asyncio.gather(task,return_exceptions=True)
            except Exception:
                await response.write(b'data: {"type":"response.error","data":{"code":"agent_failed"}}\n\ndata: [DONE]\n\n')
            return response
    app.router.add_post('/agent',agent)
    async def health(req):return web.json_response({'ok':True})
    app.router.add_get('/healthz',health)
    app.router.add_get('/capabilities',caps);app.router.add_post('/invoke',invoke)
    app.router.add_get('/requests/{id}',status);app.router.add_get('/requests/{id}/events',events)
    app.router.add_post('/requests/{id}/approve',approve);app.router.add_post('/requests/{id}/stop',stop)
    return app
async def main():
    import signal
    secret=Path('/etc/harmonika-ai/telco-engine.token').read_text().strip();root=Path(os.environ.get('HARMONIKA_TELCO_ROOT','/var/lib/harmonika-telco'))
    mentor=Path('/etc/harmonika-ai/internal-agent.token').read_text().strip()
    session=ClientSession(timeout=ClientTimeout(total=60))
    async def decide(payload):
        async with session.post('http://127.0.0.1:17890/v1/chat/completions',json=payload,allow_redirects=False,headers={'X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':mentor}) as r:
            raw=await r.content.read(1048577);require(r.status==200 and len(raw)<=1048576,'planner_unavailable',503)
            result=json.loads(raw);return result['choices'][0]['message']
    rt=Runtime(root/'state.sqlite');await asyncio.to_thread(rt.recover)
    app=make_app(rt,secret,decide);app.on_cleanup.append(lambda _:session.close())
    runner=web.AppRunner(app,access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_TELCO_PORT','18189'))).start()
    event=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,event.set)
    try:await event.wait()
    finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
