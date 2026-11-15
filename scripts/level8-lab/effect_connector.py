"""Immutable private Unix-HTTP client; lost connection is an uncertain outcome."""
import http.client,json,os,socket,time
from pathlib import Path
class OutcomeUnknown(Exception):pass
class Unavailable(OutcomeUnknown):pass
class StaleFence(Exception):pass
class ReceiptConflict(Exception):pass
class UnixConnection(http.client.HTTPConnection):
 def __init__(self,path):super().__init__('localhost',timeout=1);self.path=str(path)
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(1);self.sock.connect(self.path)
class EffectConnector:
 def __init__(self,root,worker_id='local'):self.root=Path(root);self.worker_id=worker_id
 def rpc(self,op,data):
  c=UnixConnection(self.root/'effect.sock')
  try:
   c.request('POST','/'+op,json.dumps({**data,'worker_id':self.worker_id}).encode(),{'Content-Type':'application/json'});r=c.getresponse();body=r.read(16385)
   if len(body)>16384:raise OutcomeUnknown('invalid_receipt')
   value=json.loads(body)
   if r.status==409:
    if isinstance(value,dict) and value.get('error')=='stale_fence':raise StaleFence('stale_fence')
    raise ReceiptConflict('receipt_conflict')
   if r.status!=200:raise Unavailable('sink_unavailable')
   if r.getheader('X-Lab-Crash-Worker')=='42':os._exit(42)
   return value
  except (StaleFence,ReceiptConflict):raise
  except (OSError,http.client.HTTPException,ValueError):raise OutcomeUnknown('transport_unknown') from None
  finally:c.close()
 def authorize(self,job_id,owner,fence):return self.rpc('authorize',{'job_id':job_id,'owner':owner,'fence':fence})
 def lookup(self,job_id):return self.rpc('lookup',{'job_id':job_id})
 def execute(self,job_id,payload,fence):
  while (self.root/'faults/hold-all').exists() or (self.root/('faults/hold-'+self.worker_id)).exists():time.sleep(.025)
  return self.rpc('execute',{'job_id':job_id,'payload':payload,'fence':fence})
 def health(self):return self.rpc('health',{})
