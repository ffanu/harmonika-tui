"""Loopback-only SSH REST API. Dedicated owner credential; no public AI tokens."""
import asyncio, hashlib, hmac, json, os, re, shlex, signal, time
from pathlib import Path
from aiohttp import web
from .control import Control, ControlError, require
from .service import execute

READ_ONLY={'uname','id','pwd','df','uptime','whoami','hostname','ls','free'}
BLOCKED={'sh','bash','zsh','fish','sudo','su','rm','mkfs','dd','shutdown','reboot','halt','poweroff','init','python','python3','node','perl','ruby','env','eval','exec','ssh','scp','sftp','curl','wget'}

class Runtime:
 def __init__(self,config,token,approval_key,control,python=None):
  self.config=Path(config);self.token=token;self.approval_key=approval_key;self.control=control;self.python=python;self.slots=asyncio.Semaphore(4);self.enabled=True
 def registry(self):
  from .connector import ssh
  try:c=ssh.load_config(str(self.config))
  except ssh.SSHError as exc:raise ControlError(exc.code,503) from None
  require(c.get('enabled',True) is True,'ssh_disabled',503);return c
 def prepare(self,v,operation):
  from .connector import ssh
  fields={'target','host','user','port','command','argv','timeout','timeout_seconds','project_root','local_path','remote_path','tunnel'}
  require(type(v) is dict and not set(v)-fields,'invalid_input')
  c=self.registry();alias=v.get('target')
  if alias is None:
   require(type(v.get('host')) is str,'target_required')
   matches=[a for a,t in c['targets'].items() if t['host']==v['host'] and t['username']==v.get('user',t['username']) and t.get('port',22)==v.get('port',t.get('port',22))]
   require(len(matches)==1,'target_not_registered',403);alias=matches[0]
  try:alias=ssh.resolve_alias(c,alias);t=ssh.target(c,alias)
  except ssh.SSHError as exc:raise ControlError(exc.code,403) from None
  for field,expected in [('host',t['host']),('user',t['username']),('port',t.get('port',22))]:
   require(field not in v or v[field]==expected,'target_mismatch',403)
  allowed={'connect':{'target','host','user','port'},'exec':{'target','host','user','port','argv','command','timeout','timeout_seconds'},'upload':{'target','host','user','port','project_root','local_path','remote_path'},'download':{'target','host','user','port','project_root','local_path','remote_path'},'tunnel':{'target','host','user','port','tunnel'}}
  require(not set(v)-allowed[operation],'invalid_input')
  args={'target':alias};risky=operation=='upload'
  if operation=='exec':
   require(('command' in v)!=('argv' in v),'command_or_argv_required')
   if 'command' in v:
    command=v['command'];require(type(command) is str and 0<len(command)<=16384 and not re.search(r'[\n\r\x00;|&<>`$]',command),'ssh_command_not_allowed',403)
    try:argv=shlex.split(command)
    except ValueError:raise ControlError('invalid_input') from None
   else:argv=v['argv']
   require(type(argv) is list and 1<=len(argv)<=64 and all(type(x) is str and '\x00' not in x and len(x)<=8192 for x in argv),'invalid_input')
   name=Path(argv[0]).name
   require(name not in BLOCKED,'ssh_command_not_allowed',403)
   candidates=[x for x in t.get('allowed_executables',[]) if x==argv[0] or ('/' not in argv[0] and Path(x).name==argv[0])]
   require(len(candidates)==1,'ssh_command_not_allowed',403);argv=[candidates[0],*argv[1:]]
   timeout=v.get('timeout_seconds',v.get('timeout',30));require(type(timeout) is int and 1<=timeout<=120,'invalid_input')
   args.update(argv=argv,timeout_seconds=timeout);risky=name not in READ_ONLY
  elif operation in ('upload','download'):
   args.update({k:v.get(k) for k in ('project_root','local_path','remote_path')})
   try:ssh.validate('ssh.'+operation,args);ssh.local_path(c,args);ssh.relative(args['remote_path'])
   except ssh.SSHError as exc:raise ControlError(exc.code,400) from None
   require(t.get('allow_'+operation) is True,'ssh_transfer_not_allowed',403)
  elif operation=='tunnel':
   profile=t.get('tunnels',{}).get(v.get('tunnel'))
   require(type(profile) is dict and profile.get('host') in ('127.0.0.1','::1','localhost') and type(profile.get('port')) is int and 1<=profile['port']<=65535,'tunnel_not_registered',403)
   args['tunnel']=v['tunnel']
  return args,risky

def make_app(runtime):
 rt=runtime
 @web.middleware
 async def boundary(req,handler):
  started=time.monotonic();skill='ssh.api';record=False
  try:
   if req.path=='/healthz':return web.json_response({'ok':True,'service':'harmonika-remote-access'})
   supplied=req.headers.get('Authorization','');require(supplied.startswith('Bearer ') and hmac.compare_digest(supplied[7:],rt.token),'unauthorized',401)
   require(os.environ.get('SSH_ENABLED','true').lower() not in ('false','0','no') and rt.enabled,'ssh_disabled',503)
   req['actor']='owner';record=True;skill={'/api/v1/ssh/execute':'ssh.exec','/api/v1/ssh/upload':'ssh.upload','/api/v1/ssh/download':'ssh.download','/api/v1/ssh/test':'ssh.connect','/api/v1/ssh/tunnel':'ssh.tunnel'}.get(req.path,'ssh.api')
   response=await handler(req)
   if not req.get('audit_recorded') and req.path not in ('/metrics','/api/v1/ssh/audit/verify'):rt.control.audit(skill,'completed' if response.status<400 else 'error','',int((time.monotonic()-started)*1000))
  except ControlError as exc:
   if record:rt.control.audit(skill,'error',exc.code,int((time.monotonic()-started)*1000))
   response=web.json_response({'ok':False,'error':{'code':exc.code}},status=exc.status)
   if exc.retry:response.headers['Retry-After']=str(exc.retry)
  except web.HTTPException as exc:response=web.json_response({'ok':False,'error':{'code':'input_too_large' if exc.status==413 else 'invalid_request'}},status=exc.status)
  except (ValueError,TypeError,KeyError,UnicodeError):response=web.json_response({'ok':False,'error':{'code':'invalid_input'}},status=400)
  except Exception:response=web.json_response({'ok':False,'error':{'code':'remote_backend_unavailable'}},status=503)
  response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});return response
 app=web.Application(middlewares=[boundary],client_max_size=32768)
 async def body(req):
  require(req.content_type=='application/json','json_required',415);raw=await req.read();require(len(raw)<=32768,'input_too_large',413);v=json.loads(raw);require(type(v) is dict,'invalid_input');return v
 async def sessions(req):
  require(not await body(req),'invalid_input');return web.json_response({'ok':True,'session_id':rt.control.session(req['actor']),'expires_in':86400})
 async def targets(req):
  from .connector import targets
  return web.json_response({'ok':True,**targets(rt.registry(),req.query.get('query',''))})
 async def approval(req):
  require(hmac.compare_digest(req.headers.get('X-Harmonika-Approval-Key',''),rt.approval_key),'approval_operator_required',403)
  v=await body(req);require(set(v)=={'operation','input'} and v['operation'] in ('exec','upload'),'invalid_input')
  args,risky=rt.prepare(v['input'],v['operation']);require(risky,'approval_not_required')
  token=rt.control.approval(req['actor'],{'operation':v['operation'],'input':args})
  return web.json_response({'ok':True,'approval_token':token,'expires_in':60,'single_use':True})
 async def action(req):
  operation={'execute':'exec','test':'connect'}.get(req.match_info['operation'],req.match_info['operation'])
  require(operation in ('connect','exec','upload','download','tunnel'),'operation_not_found',404)
  v=await body(req)
  bucket=v.get('target',v.get('host','unknown'));bucket=bucket if type(bucket) is str else 'invalid'
  rt.control.consume(req['actor'],req.headers.get('X-Harmonika-Session',''),bucket,req.remote or 'loopback')
  args,risky=rt.prepare(v,operation)
  if risky:rt.control.use_approval(req['actor'],{'operation':operation,'input':args},req.headers.get('X-Harmonika-Approval',''))
  require(not rt.slots.locked(),'remote_capacity',429)
  # Persist started before any network call: audit failure is fail-closed.
  rt.control.audit('ssh.'+operation,'started','',0)
  call_started=time.monotonic()
  async with rt.slots:
   result=await execute('ssh.'+operation,args,python=rt.python,timeout=180)
  result['success']=bool(result.get('ok'))
  code=result.get('error',{}).get('code')
  if result.get('ok'):status=200
  elif code in ('ssh_command_not_allowed','path_denied','tunnel_not_registered'):status=403
  elif code in ('invalid_input','target_not_registered'):status=400
  elif code in ('ssh_timeout','remote_timeout'):status=504
  elif code=='destination_exists':status=409
  else:status=502
  # Actual worker failure belongs in the backend ledger as well as worker audit.
  rt.control.audit('ssh.'+operation,result.get('status','error'),code or '',int((time.monotonic()-call_started)*1000));req['audit_recorded']=True
  return web.json_response(result,status=status)
 async def metrics(req):return web.Response(text=rt.control.metrics(),content_type='text/plain')
 async def verify(req):return web.json_response({'ok':rt.control.verify(),'integrity':'hmac_chain','content_logged':False})
 async def health(req):return web.json_response({'ok':True})
 app.router.add_get('/healthz',health)
 app.router.add_post('/api/v1/ssh/sessions',sessions);app.router.add_get('/api/v1/ssh/targets',targets);app.router.add_post('/api/v1/ssh/approvals',approval)
 app.router.add_get('/api/v1/ssh/audit/verify',verify);app.router.add_get('/metrics',metrics);app.router.add_post('/api/v1/ssh/{operation}',action)
 return app

async def main():
 from .connector import ssh
 root=Path(os.environ.get('HARMONIKA_REMOTE_ROOT','/var/lib/harmonika-remote-access'))
 config=os.environ.get('HARMONIKA_SSH_CONFIG','/etc/harmonika-ai/remote-access/targets.json')
 token=ssh.private_file(os.environ.get('HARMONIKA_REMOTE_TOKEN_FILE','/etc/harmonika-ai/remote-access/api.token')).read_text().strip()
 approval=ssh.private_file(os.environ.get('HARMONIKA_REMOTE_APPROVAL_FILE','/etc/harmonika-ai/remote-access/approval.token')).read_text().strip()
 require(len(token)>=32 and len(approval)>=32 and token!=approval,'invalid_service_secret')
 root.mkdir(parents=True,exist_ok=True,mode=0o700);pepper_file=root/'audit-integrity.key'
 if not pepper_file.exists():
  fd=os.open(pepper_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as file:file.write(os.urandom(32))
 pepper=ssh.private_file(pepper_file).read_bytes()
 control=Control(root,pepper);require(control.verify(),'audit_integrity_failed',503);rt=Runtime(config,token,approval,control)
 runner=web.AppRunner(make_app(rt),access_log=None);await runner.setup();await web.TCPSite(runner,'127.0.0.1',int(os.environ.get('HARMONIKA_REMOTE_PORT','18231'))).start()
 stop=asyncio.Event();loop=asyncio.get_running_loop()
 for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
 try:await stop.wait()
 finally:await runner.cleanup()
if __name__=='__main__':asyncio.run(main())
