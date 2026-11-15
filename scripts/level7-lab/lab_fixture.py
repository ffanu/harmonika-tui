"""Immutable synthetic local sink for native regression tests; no network/secrets."""
import base64,hashlib,json,threading
from effect_connector import OutcomeUnknown,StaleFence
class MemorySink:
 def __init__(self):self.lock=threading.RLock();self.fences={};self.effects={};self.calls=[];self.hold={};self.entered={}
 def authorize(self,id,owner,fence):
  with self.lock:
   previous=self.fences.get(id)
   if previous and (previous[0]!=owner or previous[1]>fence):raise StaleFence()
   self.fences[id]=(owner,fence);self.calls.append(('authorize',id,fence));return {'authorized':True,'fence':fence}
 def lookup(self,id):
  with self.lock:self.calls.append(('lookup',id));return self.effects.get(id)
 def execute(self,id,payload,fence):
  gate=self.hold.get(fence);self.entered.setdefault(fence,threading.Event()).set()
  if gate is not None:gate.wait(5)
  with self.lock:
   self.calls.append(('execute',id,fence));owner,current=self.fences[id]
   if current!=fence:self.calls.append(('stale_rejected',id,fence));raise StaleFence()
   raw=json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode();value={'job_id':id,'owner':owner,'fence':fence,'sha256':hashlib.sha256(raw).hexdigest(),'content_b64':base64.b64encode(raw).decode()}
   if id not in self.effects:self.effects[id]=value
   return self.effects[id]
