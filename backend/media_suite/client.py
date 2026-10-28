"""Internal async adapter; TUI/API callers do not import vendor media SDKs."""
import asyncio,ipaddress,json,re,time
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
class AdapterError(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)
class MediaAdapter:
 def __init__(self,endpoint,token_file):
  p=urlsplit(endpoint)
  try:address=ipaddress.ip_address(p.hostname)
  except ValueError:raise AdapterError('invalid_worker_endpoint') from None
  if p.scheme!='http' or not address.is_loopback or not p.port or p.port==3000 or p.path not in ('','/') or p.query or p.fragment or p.username or p.password:raise AdapterError('invalid_worker_endpoint')
  self.base=endpoint.rstrip('/');self.token=Path(token_file).read_text().strip()
  if len(self.token)<32:raise AdapterError('invalid_worker_credentials')
 async def request(self,method,path,body=None,idem=None):
  headers={'Authorization':'Bearer '+self.token}
  if idem:headers['Idempotency-Key']=idem
  try:
   async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=5)) as s:
    async with s.request(method,self.base+path,json=body,headers=headers,allow_redirects=False) as r:
     raw=bytearray()
     async for chunk in r.content.iter_chunked(65536):
      raw.extend(chunk)
      if len(raw)>36*1024*1024:raise AdapterError('invalid_worker_payload')
     try:data=json.loads(raw)
     except ValueError:raise AdapterError('invalid_worker_payload') from None
     if r.status not in (200,202):raise AdapterError(data.get('error',{}).get('code','worker_rejected'))
     return data
  except (aiohttp.ClientError,asyncio.TimeoutError):raise AdapterError('worker_unavailable') from None
 async def capabilities(self):return await self.request('GET','/health')
 async def submit(self,skill_id,payload,idempotency_key):
  try:return await self.request('POST','/v1/jobs',{'skill_id':skill_id,'input':payload},idempotency_key)
  except AdapterError as e:
   # If POST transport breaks, it may already be accepted. Never blind retry.
   if e.code=='worker_unavailable':raise AdapterError('submission_uncertain') from None
   raise
 async def result(self,job_id):
  if not re.fullmatch('[a-f0-9]{32}',job_id):raise AdapterError('invalid_job_id')
  return await self.request('GET','/v1/jobs/'+job_id)
 async def wait(self,job_id,timeout=400):
  started=time.monotonic()
  while time.monotonic()-started<timeout:
   value=await self.result(job_id)
   if value['state'] in ('completed','failed','unknown'):return value
   await asyncio.sleep(.5)
  raise AdapterError('result_pending')
