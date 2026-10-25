"""Explicit execution modes. Template generation never implies external deployment."""
import asyncio,base64,json,re,shlex
from .common import BuilderError,require,safe_path
from .constants import READERS,EXEC_IDS,snapshot_check
READ_SCHEMA={'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':240}},'required':['path'],'additionalProperties':False}
EXEC_SCHEMA={'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':240},'cwd':{'type':'string','minLength':1,'maxLength':240},'database_path':{'type':'string','minLength':1,'maxLength':240},'timeout_seconds':{'type':'integer','minimum':1,'maximum':120}},'additionalProperties':False}
SSH_SCHEMA={'type':'object','properties':{'target':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,40}$'},'action':{'type':'string','enum':['health','disk']},'path':{'type':'string','maxLength':240}},'required':['target'],'additionalProperties':False}
SCHEMAS={s:READ_SCHEMA for s in READERS}
SCHEMAS['spec.read.notation']={'type':'object','properties':{'page_id':{'type':'string','maxLength':40}},'required':['page_id'],'additionalProperties':False}
SCHEMAS.update({s:EXEC_SCHEMA for s in EXEC_IDS})
SCHEMAS.update({s:SSH_SCHEMA for s in ('ssh.connect','ssh.exec','ssh.upload','ssh.download')})
SCHEMAS['ssh.tunnel']={'type':'object','properties':{'target':{'type':'string','pattern':'^[A-Za-z0-9_-]{1,40}$'},'local_port':{'type':'integer','minimum':1024,'maximum':65535},'remote_port':{'type':'integer','minimum':1024,'maximum':65535}},'required':['target','local_port','remote_port'],'additionalProperties':False}
def text_file(files,path):
 p=safe_path(path);require(p in files,'file_not_found',404)
 try:return base64.b64decode(files[p],validate=True).decode('utf-8')
 except Exception:raise BuilderError('text_file_required') from None
async def sandbox_run(rt,files,j,command,timeout):
 require(rt.sandbox is not None,'sandbox_unavailable',503)
 async def output(v):
  # Output may contain project secrets. Persist only encrypted job result, not logs/events.
  pass
 command='export PATH=/opt/harmonika/tools-venv/bin:/opt/harmonika/web/node_modules/.bin:/opt/harmonika/node/node_modules/.bin:/usr/local/bin:/usr/bin:/bin NODE_PATH=/opt/harmonika/web/node_modules:/opt/harmonika/node/node_modules; '+command
 out=await rt.sandbox.run(files,'exec.shell',{'command':command,'timeout_seconds':timeout},j,output,deadline_seconds=140)
 require(out.get('status')=='completed' and out.get('exit_code')==0,'sandbox_execution_failed',422)
 require(type(out.get('snapshot')) is dict,'sandbox_export_failed',502)
 return {'status':'completed','execution_kind':'sandbox_execution','data':{'stdout':out.get('stdout','')[:20000],'stderr':out.get('stderr','')[:20000],'exit_code':out['exit_code'],'network_access':False,'host_execution':False},'_snapshot':snapshot_check(out['snapshot']),'limitations':['Isolated snapshot execution; no host services or external deployment were changed.']}
async def dispatch(rt,skill,args,files,j,actor):
 if skill in READERS:
  if skill=='spec.read.notation':
   require(rt.connectors is not None and rt.connectors.notion,'notion_connector_not_configured',503)
   from .notion import read
   return await asyncio.to_thread(read,rt.connectors,actor,args['page_id'])
  path=safe_path(args['path']);require(path in files,'file_not_found',404)
  if skill=='spec.read.voice':
   from legal_engine.local_media import LocalMedia
   media=await asyncio.to_thread(LocalMedia);require(media.asr_ready,'asr_unavailable',503);raw=base64.b64decode(files[path]);mime={'wav':'audio/wav','mp3':'audio/mpeg','ogg':'audio/ogg','mp4':'video/mp4'}.get(path.rsplit('.',1)[-1].lower());require(mime is not None,'unsupported_audio');v=await asyncio.to_thread(media.transcribe,raw,mime,'id');return {'status':'completed','execution_kind':'media_transcription','data':{'text':v['transcript'],'requires_human_confirmation':True},'limitations':['Transcription may miss words, names or figures. Confirm the specification.']}
  text=text_file(files,path)
  if skill=='spec.read.json':
   try:parsed=json.loads(text,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non_finite_json')))
   except ValueError:raise BuilderError('invalid_spec_json') from None
  else:parsed=None
  return {'status':'completed','execution_kind':'workspace_read','data':{'path':path,'text':text,'parsed':parsed},'limitations':['Read only the explicitly selected project file, not a local computer vault.']}
 if skill in EXEC_IDS:
  path=args.get('path');timeout=args.get('timeout_seconds',60);cwd=args.get('cwd');prefix=''
  if cwd:safe_path(cwd);prefix=cwd+'/'
  if path:safe_path(path);require(prefix+path in files or any(p.startswith(prefix+path+'/') for p in files),'file_not_found',404)
  if skill in ('db.run.migration','db.run.seed'):
   require(path and path.endswith('.sql'),'sql_path_required');text_file(files,prefix+path)
   # SQL executes inside the networkless container; arbitrary SQLite extension/file access
   # therefore cannot reach host secrets or another tenant. Persistent DB is exported.
   dbpath=safe_path(args.get('database_path','data/app.sqlite'))
   code="import sqlite3,pathlib\np=pathlib.Path("+repr(dbpath)+");p.parent.mkdir(parents=True,exist_ok=True)\nc=sqlite3.connect(str(p));c.enable_load_extension(False)\nc.executescript(pathlib.Path("+repr(path)+").read_text());c.commit();print('sqlite_script_applied')"
   cmd='python3 -c '+shlex.quote(code)
  elif skill in ('test.run.unit','test.run.integration'):
   target=path or 'tests';require(any(p.endswith('.py') and (p.startswith(prefix+target+'/') or p==prefix+target) for p in files),'tests_not_found',422)
   cmd='python3 -m unittest discover -s '+shlex.quote(target)+' -v'
  elif skill=='test.run.e2e':
   require(path and path.endswith(('.js','.mjs','.cjs')),'e2e_test_path_required');cmd='node '+shlex.quote(path)
  elif skill=='test.coverage.report':cmd='python3 -m coverage run --branch -m unittest discover -s '+shlex.quote(path or 'tests')+' && python3 -m coverage json -o coverage.json'
  elif skill=='test.lint.run':cmd='ruff check '+shlex.quote(path or '.')
  elif skill=='test.type.check':cmd='tsc --noEmit --project '+shlex.quote(path or 'tsconfig.json')
  else:
   require(path and path.endswith(('.js','.mjs','.cjs')),'performance_test_path_required');cmd='node '+shlex.quote(path)
  if cwd:cmd='cd '+shlex.quote(cwd)+' && '+cmd
  out=await sandbox_run(rt,files,j,cmd,timeout)
  if skill in ('test.run.unit','test.run.integration','test.coverage.report'):require(not re.search(r'Ran 0 tests',out['data']['stderr']+out['data']['stdout']),'zero_tests_not_success',422)
  return out
 if skill.startswith('ssh.'):
  from .connectors import dispatch as ssh_dispatch
  return await ssh_dispatch(rt.connectors,skill,args,files,j,actor)
 import importlib
 number=next(x['number'] for x in __import__('json').loads(__import__('pathlib').Path(__file__).with_name('catalog_source.json').read_text())['skills'] if x['id']==skill)
 module=importlib.import_module('.planning' if number<=46 or 134<=number<=151 else '.generation',__package__)
 return await asyncio.to_thread(module.dispatch,skill,args)
