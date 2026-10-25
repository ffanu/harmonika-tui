"""Private loopback HTTP boundary. Public keys are verified by the Go gateway."""
import asyncio
import hmac
import json
import os
import signal
import re
from pathlib import Path
import sys
import time
from aiohttp import web,ClientSession,ClientTimeout
from .core import Runtime,CodingError,actor
from .agent import run_agent

def make_app(runtime,secret,decide=None):
    if not isinstance(secret,str) or len(secret)<32:raise ValueError('strong_service_secret_required')
    @web.middleware
    async def boundary(request,handler):
        if request.path=='/healthz':
            return web.json_response({'ok':True,'service':'harmonika-coding-runtime'})
        if not hmac.compare_digest(request.headers.get('X-Harmonika-Coding-Secret',''),secret):
            return web.json_response({'error':{'code':'forbidden'}},status=403)
        try:
            request['actor']=actor(request.headers.get('X-Harmonika-Principal',''))
            response=await handler(request)
            response.headers['Cache-Control']='no-store'
            return response
        except CodingError as e:return web.json_response({'error':{'code':e.code,'message':e.code}},status=e.status)
        except (ValueError,TypeError,KeyError):return web.json_response({'error':{'code':'invalid_request'}},status=400)
        except web.HTTPException:raise
        except asyncio.TimeoutError:return web.json_response({'error':{'code':'operation_timeout'}},status=504)
        except Exception:return web.json_response({'error':{'code':'coding_unavailable'}},status=503)
    app=web.Application(middlewares=[boundary],client_max_size=1024*1024)
    async def body(req,keys):
        data=await req.json()
        if type(data) is not dict or set(data)-set(keys):raise CodingError('invalid_request')
        return data
    async def caps(req):return web.json_response(await runtime.capabilities())
    async def spaces(req):
        if req.method=='GET':return web.json_response({'workspaces':await runtime.list_workspaces(req['actor'])})
        data=await body(req,('name','permissions'));return web.json_response({'workspace':await runtime.create(req['actor'],data.get('name','Project'),data.get('permissions',{}))},status=201)
    async def space(req):return web.json_response({'workspace':runtime.view_workspace(await runtime.workspace(req['actor'],req.match_info['wid']))})
    async def invoke(req):
        data=await body(req,('skill_id','input','idempotency_key'))
        header=req.headers.get('Idempotency-Key')
        if header and data.get('idempotency_key') and header!=data['idempotency_key']:raise CodingError('idempotency_conflict',409)
        if header:data['idempotency_key']=header
        return web.json_response(await runtime.invoke_result(req['actor'],req.match_info['wid'],data.get('skill_id'),data.get('input',{}),data.get('idempotency_key')),status=200)
    async def job(req):return web.json_response(runtime.view_job(await runtime.job(req['actor'],req.match_info['jid'])))
    async def approve(req):
        data=await body(req,('decision','input_hash'))
        if data.get('decision')!='approve':raise CodingError('explicit_approval_required')
        return web.json_response(await runtime.approve(req['actor'],req.match_info['jid'],data.get('input_hash')))
    async def stop(req):return web.json_response(await runtime.stop(req['actor'],req.match_info['jid']))
    async def events(req):
        jid=req.match_info['jid'];a=req['actor']
        last=req.headers.get('Last-Event-ID','')
        after=int(req.query.get('after_sequence','0'))
        if last:
            prefix,seq=last.rsplit(':',1)
            if prefix!=jid:raise CodingError('invalid_event_id')
            after=max(after,int(seq))
        await runtime.job(a,jid)
        if 'text/event-stream' not in req.headers.get('Accept',''):
            return web.json_response({'events':await runtime.events(a,jid,after),'job':runtime.view_job(await runtime.job(a,jid))})
        res=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'})
        await res.prepare(req);started=time.monotonic()
        try:
            while time.monotonic()-started<180:
                state=(await runtime.job(a,jid))['state']
                rows=await runtime.events(a,jid,after)
                for row in rows:
                    await res.write(('id: '+row['event_id']+'\ndata: '+json.dumps(row,ensure_ascii=False)+'\n\n').encode());after=row['sequence']
                if state in ('completed','failed','stopped','timeout','expired','interrupted') and len(rows)<128:
                    await res.write(b'data: [DONE]\n\n');break
                if not rows:await res.write(b': keepalive\n\n')
                await asyncio.sleep(.5)
        except (ConnectionResetError,asyncio.CancelledError):pass
        return res
    def debugger():
        if runtime.debug is None:raise CodingError('debug_unavailable',503)
        return runtime.debug
    async def debug_session(req):return web.json_response(debugger().view(await debugger().get(req['actor'],req.match_info['sid'])))
    async def debug_stop(req):return web.json_response(await debugger().stop(req['actor'],req.match_info['sid']))
    async def debug_action(req):
        data=await body(req,('action',))
        return web.json_response(await debugger().action_once(req['actor'],req.match_info['sid'],data.get('action'),req.headers.get('Idempotency-Key')))
    async def debug_events(req):
        mgr=debugger();sid=req.match_info['sid'];a=req['actor'];await mgr.get(a,sid)
        after=int(req.query.get('after_sequence','0'));last=req.headers.get('Last-Event-ID','')
        if last:
            prefix,n=last.rsplit(':',1)
            if prefix!=sid:raise CodingError('invalid_event_id')
            after=max(after,int(n))
        if 'text/event-stream' not in req.headers.get('Accept',''):
            return web.json_response({'events':await mgr.events(a,sid,after),'session':mgr.view(await mgr.get(a,sid))})
        res=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await res.prepare(req);started=time.monotonic()
        try:
            while time.monotonic()-started<180:
                state=(await mgr.get(a,sid))['state'];rows=await mgr.events(a,sid,after)
                for row in rows:
                    await res.write(('id: '+row['event_id']+'\ndata: '+json.dumps(row,ensure_ascii=False)+'\n\n').encode());after=row['sequence']
                if state in ('closed','expired','interrupted','failed') and len(rows)<128:
                    await res.write(b'data: [DONE]\n\n');break
                if not rows:await res.write(b': keepalive\n\n')
                await asyncio.sleep(.5)
        except (ConnectionResetError,asyncio.CancelledError):pass
        return res
    async def agent(req):
        if decide is None:raise CodingError('planner_unavailable',503)
        data=await body(req,('messages','stream'));wid=req.match_info['wid'];a=req['actor']
        if type(data.get('stream',False)) is not bool:raise CodingError('invalid_stream')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',req.headers.get('Idempotency-Key','')):raise CodingError('idempotency_required')
        if type(data.get('messages')) is not list or not 1<=len(data['messages'])<=40:raise CodingError('invalid_messages')
        if not data.get('stream'):
            result=await runtime.agent_turn(a,wid,req.headers.get('Idempotency-Key'),data.get('messages'),lambda:run_agent(runtime,a,wid,data.get('messages'),decide,turn_key=req.headers.get('Idempotency-Key')))
            return web.json_response(result)
        # Headers are sent immediately; event data never claims token streaming.
        await runtime.workspace(a,wid)
        res=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store','X-Accel-Buffering':'no'});await res.prepare(req)
        async def emit(value):await res.write(('data: '+json.dumps(value,ensure_ascii=False)+'\n\n').encode())
        async def agent_request():
            result=await runtime.agent_turn(a,wid,req.headers.get('Idempotency-Key'),data.get('messages'),lambda:run_agent(runtime,a,wid,data.get('messages'),decide,emit,turn_key=req.headers.get('Idempotency-Key')))
            if result.get('replayed'):await emit({'type':'response.completed','data':result})
            return result
        task=asyncio.create_task(agent_request())
        try:
            while not task.done():
                await asyncio.wait({task},timeout=5)
                if not task.done():await res.write(b': harmonika working\n\n')
            await task;await res.write(b'data: [DONE]\n\n')
        except (ConnectionResetError,asyncio.CancelledError):
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        except Exception:
            await res.write(b'data: {"type":"error","data":{"code":"agent_failed"}}\n\ndata: [DONE]\n\n')
        return res
    async def health(req):return web.json_response({'ok':True})
    app.router.add_get('/healthz',health)
    app.router.add_get('/capabilities',caps)
    app.router.add_get('/workspaces',spaces);app.router.add_post('/workspaces',spaces)
    app.router.add_get('/workspaces/{wid}',space)
    app.router.add_post('/workspaces/{wid}/invoke',invoke);app.router.add_post('/workspaces/{wid}/agent',agent)
    app.router.add_get('/jobs/{jid}',job);app.router.add_get('/jobs/{jid}/events',events)
    app.router.add_post('/jobs/{jid}/approve',approve);app.router.add_post('/jobs/{jid}/stop',stop)
    app.router.add_get('/debug/sessions/{sid}',debug_session)
    app.router.add_get('/debug/sessions/{sid}/events',debug_events)
    app.router.add_post('/debug/sessions/{sid}/actions',debug_action)
    app.router.add_post('/debug/sessions/{sid}/stop',debug_stop)
    async def janitor():
        while True:
            await asyncio.sleep(60)
            try:await runtime.prune()
            except Exception:pass
    async def startup(_):
        await runtime.initialize();app['janitor']=asyncio.create_task(janitor())
    async def close(_):
        app['janitor'].cancel();await asyncio.gather(app['janitor'],return_exceptions=True)
        await runtime.close()
    app.on_startup.append(startup);app.on_cleanup.append(close)
    return app

async def main():
    if sys.version_info<(3,11):raise RuntimeError('coding_backend_requires_python_3_11_or_newer')
    from .phase2_files import EngineeringFileStore
    from .sandbox import DockerSandbox
    module_dir=Path(os.environ.get('HARMONIKA_SHARED_TOOLS','/opt/harmonika-ai/shared-skills/versions/2026.10.24.19/runtime/tools'))
    sys.path.insert(0,str(module_dir))
    from engineering_evidence import redact
    secret=Path('/etc/harmonika-ai/coding-runtime.token').read_text().strip()
    mentor=Path('/etc/harmonika-ai/internal-agent.token').read_text().strip()
    session=ClientSession(timeout=ClientTimeout(total=95))
    async def decide(payload):
        async with session.post('http://127.0.0.1:17890/v1/chat/completions',json=payload,headers={'X-Harmonika-Client':'internal-agent','X-Harmonika-Agent-Secret':mentor}) as res:
            raw=await res.content.read(2*1024*1024+1)
            if res.status!=200 or len(raw)>2*1024*1024:raise CodingError('planner_unavailable',503)
            value=json.loads(raw)
            return value['choices'][0]['message']
    image=os.environ.get('HARMONIKA_CODING_IMAGE','harmonika-coding:20261024')
    runtime=Runtime(os.environ.get('HARMONIKA_CODING_ROOT','/var/lib/harmonika-coding'),DockerSandbox(image=image,phase2_runtime=os.environ.get('HARMONIKA_CODING_PHASE2_ENABLED','false').lower()=='true'),EngineeringFileStore,redact)
    if runtime.phase2_enabled:
        from .debug_adapter import DockerDebugAdapter
        from .debug_sessions import DebugSessions
        runtime.debug=DebugSessions(runtime,DockerDebugAdapter(image=image))
    app=make_app(runtime,secret,decide)
    app.on_cleanup.append(lambda _:session.close())
    runner=web.AppRunner(app,access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_CODING_PORT','18187'))).start()
    shutdown=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,shutdown.set)
    try:await shutdown.wait()
    finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
