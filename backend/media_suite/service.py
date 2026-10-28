"""Private async media API. Durable mechanics only; payload/results stay in RAM."""
import asyncio,hashlib,hmac,json,os,re,secrets,sqlite3,sys,time
from pathlib import Path
from aiohttp import web
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from contracts import Failure,MAX_INPUT,OPS,validate,validate_result
from process import execute
LIMITS={'audio.transcribe':180,'image.upscale':180,'audio.synthesize':360,'browser.inspect':90}
ENV_NAMES={'audio.transcribe':'asr','image.upscale':'upscale','audio.synthesize':'tts','browser.inspect':'browser'}
CODES={'invalid_input','invalid_media','invalid_image','unsupported_audio','invalid_audio','audio_duration_limit','image_dimensions_limit','output_limit','gpu_offline','gpu_busy','gpu_memory_full','engine_unavailable','engine_timeout','invalid_engine_payload','engine_failed','reference_audio_too_short','license_restricted','language_unqualified','target_not_registered','private_target_denied','target_offline','invalid_url','invalid_output'}
class Suite:
 def __init__(self,root=ROOT,runner=execute):
  self.root=Path(root);self.runner=runner;self.queue=asyncio.Queue(maxsize=8);self.results={};self.inputs={};self.task=None
  self.db=sqlite3.connect(self.root/'mechanics.sqlite');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT,idem TEXT,digest TEXT,skill_id TEXT,state TEXT,error_type TEXT,latency REAL,created REAL,UNIQUE(owner,idem))')
  self.db.execute("UPDATE jobs SET state='unknown',error_type='interrupted' WHERE state IN ('queued','running')");self.db.commit()
 def config(self):return json.loads((self.root/'config.json').read_text())
 def enabled(self,op):
  try:v=json.loads((self.root/'qualification.json').read_text());return v.get(op,{}).get('real_test_passed') is True
  except (OSError,ValueError):return False
 def owner(self,req):
  token=(self.root/'service.token').read_text().strip();provided=req.headers.get('Authorization','')
  if len(token)<32 or not hmac.compare_digest(provided,'Bearer '+token):raise web.HTTPUnauthorized()
  return hashlib.sha256(token.encode()).hexdigest()
 async def startup(self,app):self.task=asyncio.create_task(self.work())
 async def stop(self,app):
  # Never cancel to_thread while its engine is still running. Bounded deadline remains enforced.
  await self.queue.put(None)
  if self.task:await self.task
  self.db.close()
 async def health(self,req):
  self.owner(req);cfg=self.config()
  return web.json_response({'ok':True,'ready':any(self.enabled(x) for x in OPS),'capabilities':{x:{'ready':self.enabled(x),'research_only':x=='audio.synthesize'} for x in OPS},'pending':self.queue.qsize(),'role':cfg['role']})
 async def submit(self,req):
  owner=self.owner(req)
  try:
   body=await req.json();op=body.get('skill_id');v=body.get('input');await asyncio.wait_for(asyncio.to_thread(validate,op,v,self.config()),timeout=5)
   if not self.enabled(op):raise Failure('engine_unavailable')
  except asyncio.TimeoutError:return web.json_response({'status':'degraded','error':{'code':'target_offline'}},status=422)
  except Failure as e:return web.json_response({'status':'degraded','error':{'code':e.code}},status=422)
  except (ValueError,AttributeError):return web.json_response({'error':{'code':'invalid_input'}},status=400)
  idem=req.headers.get('Idempotency-Key','')
  if not re.fullmatch('[A-Za-z0-9_-]{8,128}',idem):return web.json_response({'error':{'code':'invalid_idempotency_key'}},status=400)
  raw=json.dumps(body,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest()
  old=self.db.execute('SELECT id,digest,state FROM jobs WHERE owner=? AND idem=?',(owner,idem)).fetchone()
  if old:
   if old[1]!=digest:return web.json_response({'error':{'code':'idempotency_conflict'}},status=409)
   return web.json_response({'job_id':old[0],'state':old[2]},status=200)
  if self.queue.full():return web.json_response({'error':{'code':'queue_busy'}},status=429)
  if self.db.execute('SELECT count(*) FROM jobs').fetchone()[0]>=1000:return web.json_response({'error':{'code':'history_capacity'}},status=429)
  jid=secrets.token_hex(16);self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',(jid,owner,idem,digest,op,'queued',None,None,time.time()));self.db.commit()
  self.inputs[jid]=(op,v);self.queue.put_nowait(jid);return web.json_response({'job_id':jid,'state':'queued'},status=202)
 async def get(self,req):
  owner=self.owner(req);jid=req.match_info['job_id'];row=self.db.execute('SELECT skill_id,state,error_type,latency FROM jobs WHERE id=? AND owner=?',(jid,owner)).fetchone()
  if row is None:raise web.HTTPNotFound()
  result=self.results.get(jid)
  if result and time.time()-result[0]>=1800:self.results.pop(jid,None);result=None
  return web.json_response({'job_id':jid,'skill_id':row[0],'state':row[1],'error_type':row[2],'latency':row[3],'result':result[1] if result else None,'result_expired':row[1]=='completed' and result is None})
 async def work(self):
  while True:
   jid=await self.queue.get()
   if jid is None:return
   op,v=self.inputs.pop(jid);start=time.monotonic();code=None;state='failed'
   self.db.execute("UPDATE jobs SET state='running' WHERE id=?",(jid,));self.db.commit()
   try:
    env=self.root/('.'+ENV_NAMES[op]);python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.is_file():raise Failure('engine_unavailable')
    result=await asyncio.to_thread(self.runner,python,self.root/'engines.py',{'skill_id':op,'input':v},LIMITS[op],self.root)
    if result.get('status')=='completed':
     validated=validate_result(op,result.get('result'));state='completed';self.results[jid]=(time.time(),validated)
    else:code=result.get('error',{}).get('code');code=code if code in CODES else 'engine_failed'
   except Failure as e:code=e.code if e.code in CODES else 'engine_failed'
   except Exception:code='engine_unavailable'
   finally:v=None
   self.db.execute('UPDATE jobs SET state=?,error_type=?,latency=? WHERE id=?',(state,code,round(time.monotonic()-start,3),jid));self.db.commit()
   now=time.time();self.results={k:x for k,x in self.results.items() if now-x[0]<1800}
   # At most 8 bounded responses in memory, in addition to the bounded pending queue.
   while len(self.results)>8:self.results.pop(next(iter(self.results)))
 def app(self):
  app=web.Application(client_max_size=MAX_INPUT);app.router.add_get('/health',self.health);app.router.add_post('/v1/jobs',self.submit);app.router.add_get('/v1/jobs/{job_id}',self.get);app.on_startup.append(self.startup);app.on_cleanup.append(self.stop);return app
if __name__=='__main__':
 os.umask(0o077);s=Suite();cfg=s.config();port=cfg['port'];assert type(port) is int and 1024<=port<=65535 and port!=3000
 web.run_app(s.app(),host='127.0.0.1',port=port,access_log=None,print=None)
