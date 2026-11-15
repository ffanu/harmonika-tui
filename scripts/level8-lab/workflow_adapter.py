"""Protected real queue HTTP client over private Unix IPC."""
import http.client,json,os,socket
from pathlib import Path
import fault_store as faults
class Unavailable(Exception):pass
class OutcomeUnknown(Exception):pass
class Connection(http.client.HTTPConnection):
 def __init__(self,path):super().__init__('lab',timeout=1);self.path=str(path)
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)
class ChildClient:
 def __init__(self,root='/data'):self.path=Path(root)/'queue.sock'
 def _request(self,owner,path,method='GET',key=None,body=None,binary=False):
  c=Connection(self.path);headers={'X-Lab-Owner':owner,'X-Lab-Child':'1','Content-Type':'application/json'}
  if key is not None:headers['Idempotency-Key']=key
  try:
   c.request(method,path,body=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,headers=headers);r=c.getresponse();raw=r.read(65537)
   if len(raw)>65536:raise Unavailable()
   return r.status,raw if binary else json.loads(raw)
  except (OSError,http.client.HTTPException,ValueError):raise (OutcomeUnknown() if method=='POST' else Unavailable()) from None
  finally:c.close()
 def submit(self,owner,key,payload):
  status,value=self._request(owner,'/jobs','POST',key,payload)
  if status in [200,202] and faults.take('workflow-crash-after-submit',owner,key,value['job']['id']):faults.audit('coordinator_exit42',owner,key,value['job']['id'],42);os._exit(42)
  return status,value
 def lookup_request(self,owner,key):return self._request(owner,'/requests/'+key)
 def get(self,owner,id):return self._request(owner,'/jobs/'+id)
 def cancel(self,owner,id):return self._request(owner,'/jobs/'+id+'/cancel','POST',body={})
 def artifact(self,owner,id):return self._request(owner,'/artifacts/'+id,binary=True)
 def health(self):
  status,_=self._request('alpha','/queue-readyz')
  if status!=200:raise Unavailable()
