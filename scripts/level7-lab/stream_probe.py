"""Actual continuous chunked HTTP, later writes, mid-frame loss and reconnect."""
import hashlib,http.client,json,os,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,'/app')
checks={};accepted=[];events=[];label=os.environ['LEVEL7_LABEL']
def req(path,owner='alpha',body=None,key=None):
 headers={'X-Lab-Owner':owner,'Content-Type':'application/json'}
 if key:headers['Idempotency-Key']=key
 q=urllib.request.Request('http://127.0.0.1:18081'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None,method='POST' if body is not None else 'GET')
 try:r=urllib.request.urlopen(q,timeout=3)
 except urllib.error.HTTPError as e:r=e
 with r:return r.status,json.loads(r.read())
def cursor(owner):
 last=0
 for _ in range(16):
  status,value=req('/events',owner);assert status==200
  # Initial list only used when small; query durable max separately below.
  break
 import sqlite3
 with sqlite3.connect('file:/data/jobs.sqlite?mode=ro',uri=True) as c:return c.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE owner=?',(owner,)).fetchone()[0]
def connect(last,cut=0):
 c=http.client.HTTPConnection('127.0.0.1',18081,timeout=3);c.request('GET','/events/live',headers={'X-Lab-Owner':'alpha','Last-Event-ID':str(last),'X-Lab-Disconnect-After':str(cut)});r=c.getresponse();return c,r
try:
 from stream_reader import EventReader
 initial=cursor('alpha');reader=EventReader(last_event_id=initial);c,r=connect(initial,1)
 try:
  checks['chunked_live_headers']=r.status==200 and r.getheader('Transfer-Encoding')=='chunked' and r.getheader('Content-Length') is None
  # Read heartbeat bytes before any new job exists. Same connection remains open.
  warm=r.read(7);checks['open_before_write']=bool(warm);events.extend(reader.feed(warm));opened=time.monotonic()
  for owner in ['alpha','beta']:
   status,value=req('/jobs',owner,{'kind':'report','text':'stream fixture '+owner},'stream-'+label+'-'+owner);assert status==202;accepted.append({'id':value['job']['id'],'owner':owner})
  disconnected=False
  for _ in range(3000):
   if time.monotonic()-opened>5:break
   try:raw=r.read(13)
   except http.client.IncompleteRead as e:
    if e.partial:events.extend(reader.feed(e.partial))
    disconnected=True;break
   if not raw:break
   events.extend(reader.feed(raw))
  checks['later_committed_event_on_existing_connection']=any(e['id']>initial for e in events);checks['forced_midframe_disconnect']=disconnected
 finally:c.close()
 checkpoint=reader.last_event_id;checks['accepted_cursor_saved']=checkpoint>initial
 reader=EventReader(last_event_id=checkpoint);c,r=connect(checkpoint)
 try:
  checks['reconnect_with_cursor']=r.status==200;deadline=time.monotonic()+6;new=[];alpha=next(x['id'] for x in accepted if x['owner']=='alpha');beta=next(x['id'] for x in accepted if x['owner']=='beta')
  while time.monotonic()<deadline:
   raw=r.read(17)
   if not raw:break
   new.extend(reader.feed(raw))
   if any(x['job_id']==alpha and x['state']=='completed' for x in new):break
  checks['no_replayed_accepted_event']=all(x['id']>checkpoint for x in new)
  checks['completed_after_reconnect']=any(x['job_id']==alpha and x['state']=='completed' for x in new)
  checks['foreign_owner_not_streamed']=all(x['job_id']!=beta for x in events+new)
  checks['mechanical_event_schema']=all(set(x)=={'id','job_id','state'} for x in events+new)
 finally:c.close()
 for item in accepted:
  for _ in range(20):
   status,value=req('/jobs/'+item['id'],item['owner'])
   if status==200 and value['job']['state']=='completed':break
   time.sleep(.1)
  checks['job_completed_'+item['owner']]=status==200 and value['job']['state']=='completed'
 print(json.dumps({'passed':all(checks.values()),'checks':checks,'accepted_jobs':accepted,'stream_reader_sha256':hashlib.sha256(Path('/app/stream_reader.py').read_bytes()).hexdigest(),'saved_cursor':checkpoint,'final_cursor':reader.last_event_id,'actual_network':'loopback_http_chunked'}))
except Exception as e:print(json.dumps({'passed':False,'checks':checks,'error_type':type(e).__name__,'accepted_jobs':accepted}));raise SystemExit(1)
