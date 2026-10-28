"""Read-only mechanical diagnostics from the private media gateway. No user data."""
import asyncio,json,math,re,time
from pathlib import Path
import aiohttp
STATES={'online','offline','busy','vram_full','warming','timeout','bad_payload','auth_failed','recovery_required','unavailable','unknown','not_checked'}
ERRORS={'skill_exception','skill_timeout','invalid_input','invalid_output','cancelled','graphics_offline','graphics_busy','graphics_vram_full','graphics_timeout','graphics_bad_payload','graphics_auth_failed','graphics_warming','graphics_recovery_required','graphics_failed','logging_unavailable'}
ID=re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$')
STAMP=re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$')
def errors_view(raw):
 out={'status':'logging_unavailable','count':0,'recent':[]}
 if not isinstance(raw,dict):return out
 if raw.get('status',raw.get('logging')) in ('ok','available'):out['status']='ok'
 if type(raw.get('count')) is int and 0<=raw['count']<=1000000:out['count']=raw['count']
 rows=raw.get('recent',[])
 if not isinstance(rows,list):return out
 for row in rows[:5]:
  if not isinstance(row,dict):continue
  sid=row.get('skill_id');code=row.get('error_type');stamp=row.get('timestamp');latency=row.get('latency')
  if not isinstance(sid,str) or len(sid)>100 or not ID.fullmatch(sid) or not isinstance(code,str) or code not in ERRORS or not isinstance(stamp,str) or not STAMP.fullmatch(stamp):continue
  if type(latency) not in (float,int) or not math.isfinite(latency) or not 0<=latency<=3600000:continue
  out['recent'].append({'timestamp':stamp,'skill_id':sid,'error_type':code,'latency':round(latency,3)})
 return out

def graphics_view(raw):
 if not isinstance(raw,dict):return {'status':'unavailable'}
 value={'status':raw.get('status') if isinstance(raw.get('status'),str) and raw.get('status') in STATES else 'unknown'}
 stamp=raw.get('checked_at');latency=raw.get('latency_ms')
 if isinstance(stamp,str) and STAMP.fullmatch(stamp):value['checked_at']=stamp
 if type(latency) in (int,float) and math.isfinite(latency) and 0<=latency<=600000:value['latency_ms']=round(latency,3)
 return value

class GraphicsProbe:
 def __init__(self,secret_path='/etc/harmonika-ai/rtx-queue-client.token',url='http://127.0.0.1:18186/health'):
  self.secret_path=Path(secret_path);self.url=url;self.lock=asyncio.Lock();self.cached=None;self.observed=0
 async def __call__(self):
  if self.cached is not None and time.monotonic()-self.observed<1:return self.cached
  if self.lock.locked():return self.cached or {'graphics':{'status':'unavailable'},'errors':errors_view(None)}
  async with self.lock:
   value={'graphics':{'status':'unavailable'},'errors':errors_view(None)}
   try:
    token=self.secret_path.read_text().strip()
    if len(token)<32:raise ValueError('credential_unavailable')
    timeout=aiohttp.ClientTimeout(total=4,connect=.5)
    async with aiohttp.ClientSession(timeout=timeout,trust_env=False,cookie_jar=aiohttp.DummyCookieJar()) as client:
     async with client.get(self.url,headers={'Authorization':'Bearer '+token},allow_redirects=False) as response:
      if response.status!=200:raise ValueError('diagnostics_unavailable')
      body=bytearray()
      async for part in response.content.iter_chunked(8192):
       body.extend(part)
       if len(body)>65536:raise ValueError('diagnostics_limit')
      raw=json.loads(body);d=raw.get('diagnostics',{})
      value={'graphics':graphics_view(d.get('graphics')),'errors':errors_view(d.get('errors'))}
      if d.get('logging_degraded') is True:value['errors']['status']='logging_unavailable'
   except Exception:pass
   self.cached=value;self.observed=time.monotonic();return value
