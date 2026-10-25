"""Short-lived single-use tickets; authenticated read-only WebSocket replay/subscriptions."""
import asyncio,hashlib,hmac,json,secrets,time
from aiohttp import web,WSMsgType,ClientTimeout
from .state import Error,require,encoded
try:from core_engine.service import unique,bounded
except ModuleNotFoundError:from backend.core_engine.service import unique,bounded

class Tickets:
 def __init__(self):self.items={}
 def issue(self,actor,bearer,scopes,origin):
  require(type(origin) is str and len(origin)<=512,'invalid_origin');now=time.monotonic();self.items={k:v for k,v in self.items.items() if v['deadline']>now}
  require(len(self.items)<1000 and sum(v['actor']==actor for v in self.items.values())<4,'ticket_capacity',429)
  require(type(bearer) is str and bearer.startswith('Bearer hai_') and len(bearer)<=220,'authenticated_bearer_required',401)
  raw=secrets.token_urlsafe(32);self.items[hashlib.sha256(raw.encode()).hexdigest()]={'actor':actor,'bearer':bearer,'scopes':scopes,'origin':origin,'deadline':now+60}
  return {'ticket':raw,'expires_at':time.time()+60,'ws_url':'wss://chat.harmonika.id/v1/unified/ws','single_use':True}
 def consume(self,raw,origin):
  require(type(raw) is str and len(raw)==43,'invalid_ticket',401);v=self.items.pop(hashlib.sha256(raw.encode()).hexdigest(),None)
  require(v is not None and v['deadline']>time.monotonic(),'invalid_or_expired_ticket',401)
  require(hmac.compare_digest(v['origin'],origin),'ticket_origin_mismatch',403);return v

async def bearer_active(runtime,bearer):
 checker=getattr(runtime.adapters,'auth_check',None)
 if checker:return await checker(bearer)
 try:
  async with runtime.adapters.client.get('http://127.0.0.1:3210/v1/public-ai/capabilities',headers={'Authorization':bearer},timeout=ClientTimeout(total=3),allow_redirects=False) as r:
   if r.status in (401,403):return False
   require(r.status==200,'authentication_unavailable',503);return True
 except Error:raise
 except Exception:raise Error('authentication_unavailable',503) from None

def attach(app,runtime,state,extensions,body,actor,scopes):
 tickets=Tickets();connections={};sockets=set()
 async def ticket(req):
  require(await body(req)=={},'invalid_input');return web.json_response(tickets.issue(actor(req),req.headers.get('X-Harmonika-Delegated-Authorization',''),scopes(req),req.headers.get('Origin','')))
 async def websocket(req):
  require(set(req.query)<= {'ticket'},'unsupported_query');origin=req.headers.get('Origin','')
  if req.query.get('ticket'):
   record=tickets.consume(req.query['ticket'],origin);a=record['actor'];bearer=record['bearer']
  else:a=actor(req);bearer=req.headers.get('X-Harmonika-Delegated-Authorization','')
  state.owner(a);require(await bearer_active(runtime,bearer),'revoked_or_invalid_token',401)
  require(sum(connections.values())<16 and connections.get(a,0)<2,'websocket_capacity',429)
  ws=web.WebSocketResponse(heartbeat=20,max_msg_size=16384,compress=False);await ws.prepare(req);sockets.add(ws);connections[a]=connections.get(a,0)+1;subscription=None;expiry=time.monotonic()+120
  async def send(value):
   require(len(encoded(value))<=196608,'websocket_output_limit',413);await ws.send_json(value)
  async def follow(job_id=None,topic=None,after=0):
   cursor=after
   try:
    while not ws.closed and time.monotonic()<expiry:
     if job_id:
      v=state.get(a,job_id)
      for e in v['events']:
       if e['sequence']>cursor:
        out={**e,'event_id':job_id+':'+str(e['sequence']),'job_id':job_id}
        if e['type'] in ('completed','failed','stopped','recovery_required'):out['result']=v['result']
        await send({'type':'event','data':out});cursor=e['sequence']
      if v['status'] not in ('queued','running'):
       await send({'type':'resume.state','job_id':job_id,'status':v['status'],'sequence':v['sequence']});return
     else:
      replay=extensions.replay(a,topic,cursor,100)
      for e in replay:await send({'type':'event','data':e});cursor=e['sequence']
     await asyncio.sleep(.25)
   except asyncio.CancelledError:raise
   except Exception:await send({'type':'error','error':{'code':'subscription_failed'}})
  async def monitor():
   while not ws.closed:
    await asyncio.sleep(min(30,max(.01,expiry-time.monotonic())))
    if time.monotonic()>=expiry:await ws.close(code=1000,message=b'session expired');return
    try:active=await bearer_active(runtime,bearer)
    except Error:await ws.close(code=1013,message=b'authentication unavailable');return
    if not active:await ws.close(code=1008,message=b'authorization expired');return
  watchdog=asyncio.create_task(monitor())
  try:
   await send({'type':'connected','protocol':'harmonika.unified.v1','read_only':True,'expires_at':time.time()+120})
   while not ws.closed and time.monotonic()<expiry:
    try:frame=await ws.receive(timeout=min(30,max(.01,expiry-time.monotonic())))
    except asyncio.TimeoutError:
     await send({'type':'ping'});continue
    if frame.type==WSMsgType.TEXT:
     try:
      runtime.limit(a);v=json.loads(frame.data,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(Error('nonfinite_json')));bounded(v)
      require(type(v) is dict and type(v.get('type')) is str,'invalid_frame');kind=v['type']
      if kind in ('hello','ping','pong'):
       require(set(v)=={'type'},'invalid_frame');await send({'type':'hello_ack' if kind=='hello' else 'pong' if kind=='ping' else 'pong_ack'})
      elif kind=='resume':
       require(set(v)<= {'type','job_id','topic','after_sequence'} and ('job_id' in v)!=('topic' in v),'invalid_frame');after=v.get('after_sequence',0);require(type(after) is int and 0<=after<=10**8,'invalid_sequence')
       if 'job_id' in v:require(after<=state.get(a,v['job_id'])['sequence'],'invalid_sequence')
       else:extensions.replay(a,v['topic'],after,1)
       if subscription:subscription.cancel();await asyncio.gather(subscription,return_exceptions=True)
       await send({'type':'resume.accepted','after_sequence':after});subscription=asyncio.create_task(follow(v.get('job_id'),v.get('topic'),after))
      else:raise Error('read_only_websocket')
     except (Error,ValueError,TypeError,KeyError,UnicodeError) as e:
      code=e.code if isinstance(e,Error) else 'invalid_frame';await send({'type':'error','error':{'code':code}})
    elif frame.type in (WSMsgType.CLOSE,WSMsgType.CLOSED,WSMsgType.ERROR):break
   await ws.close()
  finally:
   watchdog.cancel();await asyncio.gather(watchdog,return_exceptions=True)
   if subscription:subscription.cancel();await asyncio.gather(subscription,return_exceptions=True)
   connections[a]-=1
   sockets.discard(ws)
   if connections[a]==0:connections.pop(a,None)
  return ws
 app.router.add_post('/ticket',ticket);app.router.add_get('/ws',websocket)
 async def shutdown(app):
  tickets.items.clear();await asyncio.gather(*(ws.close(code=1001,message=b'service restart') for ws in list(sockets)),return_exceptions=True)
 app.on_shutdown.append(shutdown)
 return tickets
