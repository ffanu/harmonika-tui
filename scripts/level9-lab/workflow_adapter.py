"""Protected fixed-target TCP child client; no host/address parameter from model."""
import http.client,json,os,re,socket
import fault_store as faults
class Unavailable(Exception):pass
class OutcomeUnknown(Exception):pass
class ChildClient:
 def __init__(self,root='/data',coordinator_id=None):self.identity=coordinator_id or os.environ.get('COORDINATOR_ID','api')
 def _request(self,owner,path,method='GET',key=None,body=None,binary=False):
  if owner not in ['alpha','beta'] or not re.fullmatch(r'/(?:jobs(?:/[a-f0-9]{32}(?:/cancel)?)?|requests/[A-Za-z0-9_-]{1,64}|artifacts/[a-f0-9]{32}|queue-readyz)',path):raise Unavailable()
  c=http.client.HTTPConnection('proxy',18082,timeout=50 if self.identity.startswith('coordinator') else 2)
  headers={'X-Lab-Owner':owner,'X-Lab-Child':'1','X-Lab-Coordinator':self.identity,'Content-Type':'application/json'}
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
