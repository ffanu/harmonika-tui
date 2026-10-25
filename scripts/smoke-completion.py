#!/usr/bin/env python3
"""Isolated external HTTPS/WSS acceptance. Keys only stdin; output contains no credentials."""
import asyncio,json,sys,uuid
from pathlib import Path
from aiohttp import ClientSession,ClientTimeout,WSServerHandshakeError
BASE='https://chat.harmonika.id/v1/unified';WS='wss://chat.harmonika.id/v1/unified/ws';ORIGIN='https://chat.harmonika.id'
stage='initial';checks=[]
async def main(auth):
 global stage
 async with ClientSession(trust_env=False,timeout=ClientTimeout(total=40)) as client:
  if auth.get('revoked_ticket'):
   try:
    async with client.ws_connect(WS+'?ticket='+auth['revoked_ticket'],origin=ORIGIN):raise AssertionError('revoked_ticket_accepted')
   except WSServerHandshakeError as e:assert e.status==401
   print(json.dumps({'revoked_ticket_rejected':True}));return
  async def call(method,path,body=None,who=0,key=None,spoof=False):
   headers={'Authorization':'Bearer '+auth['tokens'][who],'Idempotency-Key':key or str(uuid.uuid4()),'Origin':ORIGIN}
   if spoof:headers['X-Harmonika-Unified-Scopes']='chat,secrets:read,secrets:write'
   async with client.request(method,BASE+path,json=body,headers=headers,allow_redirects=False) as r:
    data=bytearray()
    while True:
     chunk=await r.content.read(8192)
     if not chunk:break
     data.extend(chunk);assert len(data)<=1048576
    return r.status,json.loads(data)
  def ok(name):checks.append(name)
  stage='capabilities';code,caps=await call('GET','/capabilities');assert code==200 and caps['readiness']=={'available':57,'internal_only':12,'native_endpoint':1,'unavailable':0};assert caps['features']['graphql'] and caps['features']['websocket_ticket_auth'];assert not caps['token_permissions']['secret_write'];ok(stage)
  stage='graphql';code,v=await call('POST','/graphql',{'query':'{capabilities skills{id status} knowledge(query:"idempotency status stopped")}'});assert code==200 and len(v['data']['skills'])==70 and v['data']['knowledge']['results'];ok(stage)
  stage='knowledge';code,v=await call('POST','/knowledge/query',{'query':'idempotency status stopped'});assert code==200 and v['method']=='local_lexical_bm25';assert all(len(r['citation']['sha256'])==64 and r['citation']['source'].startswith('https://chat.harmonika.id/') for r in v['results']);ok(stage)
  stage='scope_spoofing';secret={'operation':'set','name':'synthetic.connector','value':'SYNTHETIC_NOT_A_REAL_SECRET'};code,v=await call('POST','/secrets',secret,spoof=True);assert code==403 and v['error']['code']=='secret_scope_required';ok(stage)
  stage='encrypted_connector';key_a=str(uuid.uuid4());key_b=str(uuid.uuid4());key_del=str(uuid.uuid4());name='synthetic.connector'
  code,a=await call('POST','/secrets',{'operation':'set','name':name,'value':'SYNTHETIC_A_NOT_REAL'},who=1,key=key_a);assert code==200 and 'value' not in a and 'ciphertext' not in a
  code,b=await call('POST','/secrets',{'operation':'set','name':name,'value':'SYNTHETIC_B_NOT_REAL'},who=1,key=key_b);assert code==200
  code,replay=await call('POST','/secrets',{'operation':'set','name':name,'value':'SYNTHETIC_A_NOT_REAL'},who=1,key=key_a);assert code==200 and replay['replayed']
  code,_=await call('POST','/secrets',{'operation':'set','name':name,'value':'SYNTHETIC_CHANGED'},who=1,key=key_a);assert code==409
  code,_=await call('POST','/secrets',{'operation':'metadata','name':name},who=2);assert code==404
  # Root-only assertion checks actual rotation without ever exposing the secret over REST.
  from unified_engine.state import State
  from unified_engine.extensions import Extensions
  state=State('/var/lib/harmonika-unified/state.sqlite3',Path('/etc/harmonika-ai/unified-storage.key').read_bytes());ext=Extensions(state);actor='api-token:'+str(auth['actor_ids'][1]);assert ext.secret_resolve(actor,name,'internal_connector')=='SYNTHETIC_B_NOT_REAL'
  code,_=await call('POST','/secrets',{'operation':'delete','name':name},who=1,key=key_del);assert code==200
  code,_=await call('POST','/secrets',{'operation':'set','name':name,'value':'SYNTHETIC_C_NOT_REAL'},who=1);assert code==200
  code,r=await call('POST','/secrets',{'operation':'delete','name':name},who=1,key=key_del);assert code==200 and r['replayed'];assert ext.secret_resolve(actor,name,'internal_connector')=='SYNTHETIC_C_NOT_REAL';ok(stage)
  topic='completion.'+uuid.uuid4().hex;pubkey=str(uuid.uuid4())
  stage='event_bus';code,event=await call('POST','/bus/events',{'topic':topic,'payload':{'n':1}},key=pubkey);assert code==200 and event['sequence']==1
  code,e=await call('POST','/bus/events',{'topic':topic,'payload':{'n':1}},key=pubkey);assert code==200 and e['replayed']
  code,r=await call('POST','/bus/replay',{'topic':topic},who=2);assert code==200 and r['events']==[];ok(stage)
  stage='native_wss_live_pubsub'
  async with client.ws_connect(WS,headers={'Authorization':'Bearer '+auth['tokens'][0]},origin=ORIGIN) as ws:
   connected=await ws.receive_json(timeout=10);assert connected['type']=='connected' and connected['read_only']
   await ws.send_json({'type':'hello'});assert (await ws.receive_json(timeout=5))['type']=='hello_ack'
   await ws.send_json({'type':'resume','topic':topic,'after_sequence':1});assert (await ws.receive_json(timeout=5))['type']=='resume.accepted'
   code,_=await call('POST','/bus/events',{'topic':topic,'payload':{'n':2}});assert code==200
   e=await ws.receive_json(timeout=5);assert e['type']=='event' and e['data']['payload']['n']==2
   # Expire ONLY this synthetic actor/topic to prove durable sequence does not reset.
   owner=state.owner('api-token:'+str(auth['actor_ids'][0]));topic_hash=state.store._hash('unified_event_topic',owner,topic)
   with state.store._transaction() as db:db.execute('UPDATE unified_ext_events SET expires=0 WHERE actor=? AND topic=?',(owner,topic_hash))
   code,third=await call('POST','/bus/events',{'topic':topic,'payload':{'n':3}});assert code==200 and third['sequence']==3
   e=await ws.receive_json(timeout=5);assert e['data']['sequence']==3
   await ws.send_json({'type':'create_job'});assert (await ws.receive_json(timeout=5))['error']['code']=='read_only_websocket'
  ok(stage)
  stage='ticket_wss';code,t=await call('POST','/ticket',{});assert code==200 and len(t['ticket'])==43
  async with client.ws_connect(WS+'?ticket='+t['ticket'],origin=ORIGIN) as ws:assert (await ws.receive_json(timeout=5))['type']=='connected'
  try:
   async with client.ws_connect(WS+'?ticket='+t['ticket'],origin=ORIGIN):raise AssertionError('ticket_reused')
  except WSServerHandshakeError as e:assert e.status==401
  ok(stage)
  stage='rag_chat_job';code,s=await call('POST','/sessions',{});assert code==201
  code,j=await call('POST','/jobs',{'session_id':s['id'],'messages':[{'role':'user','content':'Bagaimana Idempotency-Key bekerja pada API Harmonika? Jawab singkat.'}]});assert code==202
  async with client.ws_connect(WS,headers={'Authorization':'Bearer '+auth['tokens'][2]},origin=ORIGIN) as other:
   await other.receive_json(timeout=5);await other.send_json({'type':'resume','job_id':j['id'],'after_sequence':0});assert (await other.receive_json(timeout=5))['error']['code']=='not_found'
  for _ in range(160):
   code,final=await call('GET','/jobs/'+j['id']);assert code==200
   if final['status'] not in ('queued','running'):break
   await asyncio.sleep(1)
  assert final['status']=='completed' and final['result'].get('text');assert any(r['engine']=='knowledge' and r['data']['results'] for r in final['result']['specialist_results']);ok(stage)
  stage='wss_job_replay'
  async with client.ws_connect(WS,headers={'Authorization':'Bearer '+auth['tokens'][0]},origin=ORIGIN) as ws:
   await ws.receive_json(timeout=5);await ws.send_json({'type':'resume','job_id':j['id'],'after_sequence':1});assert (await ws.receive_json(timeout=5))['type']=='resume.accepted';terminal=False
   for _ in range(4):
    frame=await ws.receive_json(timeout=5)
    if frame['type']=='event' and frame['data']['type']=='completed':terminal=True;break
   assert terminal
  ok(stage)
  stage='stop_compatibility';code,j=await call('POST','/jobs',{'messages':[{'role':'user','content':'Jelaskan router'}]},who=3);assert code==202
  code,v=await call('POST','/jobs/'+j['id']+'/stop',{},who=3);assert code==200 and v['status']=='stopped';ok(stage)
  report={'status':'passed','checks':checks,'readiness':caps['readiness'],'real_https':True,'real_wss':True,'single_use_ticket':True,'rotated_secret_not_rolled_back':True,'expired_events_preserve_cursor':True,'scopes_spoof_rejected':True,'live_model_rag':True,'actor_ids':auth['actor_ids'],'customer_data_modified':False}
  Path('/opt/harmonika-ai/unified-completion-smoke.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
if __name__=='__main__':
 try:asyncio.run(main(json.load(sys.stdin)))
 except Exception as e:print(json.dumps({'status':'failed','stage':stage,'exception':type(e).__name__}));sys.exit(1)
