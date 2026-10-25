"""Private Unix-socket media service. Dynamic user, no network, credentials, or agent tools."""
import asyncio,hashlib,hmac,json,os,socket,sys,signal,tempfile
from pathlib import Path
from aiohttp import web
ROOT=Path('/var/lib/harmonika-legal-models');MAX_INPUT=14000000

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def verified_models():
 manifest=ROOT/'manifest.json';m=json.loads(manifest.read_text())
 if type(m) is not dict or set(m)!={'vision','asr'}:return None
 for name,data in m.items():
  if type(data) is not dict or len(data.get('revision',''))!=40 or type(data.get('files')) is not dict or not data['files']:return None
  for rel,digest in data['files'].items():
   p=Path(rel)
   if p.is_absolute() or '..' in p.parts or not (ROOT/name/p).is_file() or sha(ROOT/name/p)!=digest:return None
 return sha(manifest)
def ready(model_hash):
 try:
  v=json.loads((ROOT/'ready.json').read_text());return v.get('manifest_sha256')==model_hash and v.get('worker_sha256')==sha(Path(__file__).with_name('worker.py')) and v.get('vision_smoke') is True and v.get('asr_smoke') is True and v.get('video_smoke') is True
 except Exception:return False
async def execute_child(raw,operation,*,temp_root='/run/harmonika-legal-media',worker_path=None,timeout=85):
 with tempfile.TemporaryDirectory(prefix='job-',dir=temp_root) as job:
  env=dict(os.environ,TMPDIR=job);env.pop('CREDENTIALS_DIRECTORY',None);env.pop('NOTIFY_SOCKET',None)
  proc=await asyncio.create_subprocess_exec(sys.executable,'-I',str(worker_path or Path(__file__).with_name('worker.py')),operation,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,env=env,start_new_session=True)
  try:
   out,_=await asyncio.wait_for(proc.communicate(raw),timeout=timeout);return proc.returncode,out
  except (asyncio.TimeoutError,asyncio.CancelledError):
   if proc.returncode is None:
    try:os.killpg(proc.pid,signal.SIGKILL) # Only this job's freshly-created process group.
    except ProcessLookupError:pass
   await proc.communicate();raise
async def serve():
 if os.geteuid()==0:raise RuntimeError('isolated_user_required')
 if os.access('/etc/harmonika-ai/internal-agent.token',os.R_OK):raise RuntimeError('host_credentials_must_be_unreadable')
 try:
  probe=socket.socket(socket.AF_INET,socket.SOCK_STREAM);probe.close();raise RuntimeError('ip_network_must_be_disabled')
 except OSError:pass
 secret=(Path(os.environ['CREDENTIALS_DIRECTORY'])/'legal-media.token').read_text().strip()
 if len(secret)<32:raise RuntimeError('media_credential_required')
 model_hash=await asyncio.to_thread(verified_models)
 if not model_hash:raise RuntimeError('model_integrity_failed')
 slots=asyncio.Semaphore(1)
 @web.middleware
 async def boundary(req,handler):
  if req.path=='/healthz':return web.json_response({'ok':True,'ready':ready(model_hash),'cpu_only':True,'isolated':True,'vision':ready(model_hash),'asr':ready(model_hash),'video':ready(model_hash),'model_manifest_sha256':model_hash,'worker_revision':sha(Path(__file__).with_name('worker.py'))})
  if not hmac.compare_digest(req.headers.get('X-Harmonika-Media-Secret',''),secret):return web.json_response({'error':{'code':'forbidden'}},status=403)
  try:return await handler(req)
  except (ValueError,KeyError,TypeError,json.JSONDecodeError):return web.json_response({'error':{'code':'invalid_media_request'}},status=400)
  except web.HTTPException:raise
  except Exception:return web.json_response({'error':{'code':'media_unavailable'}},status=503)
 async def analyze(req):
  operation=req.match_info['operation']
  if operation not in ('vision','asr'):return web.json_response({'error':{'code':'not_found'}},status=404)
  if not ready(model_hash) and req.headers.get('X-Harmonika-Media-Probe')!='1':return web.json_response({'error':{'code':'media_validation_required'}},status=503)
  if slots.locked():return web.json_response({'error':{'code':'media_busy'}},status=429)
  raw=await req.read()
  if len(raw)>MAX_INPUT:return web.json_response({'error':{'code':'media_input_too_large'}},status=413)
  v=json.loads(raw)
  if type(v) is not dict or set(v)-{'bytes_base64','mime_type','language'}:raise ValueError()
  async with slots:
   try:code,out=await execute_child(raw,operation)
   except asyncio.TimeoutError:return web.json_response({'error':{'code':'media_timeout'}},status=504)
   if len(out)>262144:return web.json_response({'error':{'code':'media_output_limit'}},status=502)
   try:result=json.loads(out)
   except Exception:return web.json_response({'error':{'code':'media_worker_failed'}},status=502)
   return web.json_response(result,status=200 if code==0 else 422)
 app=web.Application(middlewares=[boundary],client_max_size=MAX_INPUT);app.router.add_get('/healthz',lambda r:web.json_response({}));app.router.add_post('/{operation}',analyze)
 runner=web.AppRunner(app,access_log=None);await runner.setup();socket_path=Path('/run/harmonika-legal-media/media.sock');await web.UnixSite(runner,str(socket_path)).start();os.chmod(socket_path,0o600)
 notify=os.environ.get('NOTIFY_SOCKET')
 if notify:
  channel=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM)
  try:channel.connect('\0'+notify[1:] if notify.startswith('@') else notify);channel.sendall(b'READY=1\nSTATUS=Isolated CPU media socket ready')
  finally:channel.close()
 import signal
 stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(serve())
