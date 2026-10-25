#!/usr/bin/env python3
"""All-161 isolated builder smoke; credentials stdin/private file, metadata-only output."""
import base64,io,json,os,sys,time,urllib.request,urllib.error,uuid,zipfile
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(BASE))
try:from backend.web_builder import planning,generation
except ModuleNotFoundError:
 sys.path.insert(0,'/opt/harmonika-ai/builder-all-stage');from web_builder import planning,generation

def sample(s,k=''):
 if 'default' in s:return s['default']
 if s.get('enum'):return s['enum'][0]
 if s.get('type')=='object':return {x:sample(v,x) for x,v in s.get('properties',{}).items() if x in s.get('required',[])}
 if s.get('type')=='array':return [sample(s.get('items',{}),k) for _ in range(s.get('minItems',0))]
 if s.get('type')=='integer':return s.get('minimum',1)
 if s.get('type')=='boolean':return False
 return {'project':'demo-builder','site_name':'Harmonika Demo','route':'about','name':'demo','title':'Demo','tagline':'Demo workflow','hero_body':'Synthetic test project','body':'Synthetic test text','key':'name','label':'Name'}.get(k,'demo')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):raise ValueError('redirect_refused')
def main():
 private=os.environ.get('HARMONIKA_BUILDER_SMOKE_PRIVATE')=='1';secret={} if private else json.load(sys.stdin);actor='api-token:900000000000'+str(int(time.time())%1000000).zfill(6);base='http://127.0.0.1:18393' if private else 'https://chat.harmonika.id/v1/builder';op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 if private:
  with op.open(base+'/healthz',timeout=5) as r:assert json.load(r).get('service')=='harmonika-web-builder'
 auth={'X-Harmonika-Builder-Secret':Path('/etc/harmonika-ai/builder-engine.token').read_text().strip(),'X-Harmonika-Principal':actor} if private else {'Authorization':'Bearer '+secret['token']};other={'X-Harmonika-Principal':'api-token:900000000000999999'} if private else {'Authorization':'Bearer '+secret['other_token']}
 def call(method,path,body=None,headers=None,key=None,raw=False):
  h={**auth,'Content-Type':'application/json','Idempotency-Key':key or uuid.uuid4().hex,**(headers or {})};req=urllib.request.Request(base+path,method=method,headers=h,data=json.dumps(body).encode() if body is not None else None)
  try:
   with op.open(req,timeout=160) as r:status=r.status;data=r.read(25*1024*1024)
  except urllib.error.HTTPError as e:status=e.code;data=e.read(65536)
  return status,data if raw else json.loads(data)
 def project(p):return call('GET','/projects/'+p)[1]['project']
 def upload(p,path,data):
  status,out=call('POST','/projects/'+p+'/files',{'path':path,'bytes_base64':base64.b64encode(data).decode(),'expected_revision':project(p)['revision'],'overwrite':True});assert status==201,out
 def invoke(p,id,args,expect=None):
  status,out=call('POST','/projects/'+p+'/invoke',{'skill_id':id,'input':args,'expected_revision':project(p)['revision'],'overwrite':True});assert status==202,(id,status,out);job=out['job'];jid=job['id']
  if job['state']=='awaiting_approval':
   status,out=call('POST','/jobs/'+jid+'/approve',{'decision':'approve','input_hash':job['input_hash']});assert status==200,(id,out)
  deadline=time.monotonic()+150
  while time.monotonic()<deadline:
   status,out=call('GET','/jobs/'+jid);job=out['job']
   if job['state'] in ('completed','failed','stopped','interrupted','timeout'):return job
   time.sleep(.15)
  raise AssertionError('job_timeout:'+id)
 status,caps=call('GET','/capabilities');assert status==200 and len(caps['skills'])==161 and caps['implemented_handlers']==161
 projects={};report=[];last=None
 for n,item in enumerate(caps['skills']):
  id=item['id'];group=n//12
  if group not in projects:
   status,out=call('POST','/projects',{'name':'SYNTHETIC builder catalog '+str(group)});assert status==201,out;projects[group]=out['project']['id']
  p=projects[group];number=n+1
  if number<=46 or 134<=number<=151:args=planning._demo_args_for_schema(planning.schema(id))
  else:args=sample(item['input_schema'])
  if id.startswith('spec.read.'):
   if id=='spec.read.notation':args={'page_id':'00000000-0000-4000-8000-000000000000'}
   else:
    path='fixtures/input.'+('wav' if id.endswith('voice') else 'json' if id.endswith('json') else 'md');upload(p,path,Path('/opt/harmonika-ai/legal-audio-fixture.wav').read_bytes() if path.endswith('wav') else b'{"spec":"Synthetic private inventory"}' if path.endswith('json') else b'# Synthetic inventory\nCRUD without payments.');args={'path':path}
  if id.startswith('ssh.'):
   args={'target':'unregistered'} if id in ('ssh.connect','ssh.exec','ssh.upload','ssh.download') else {'target':'unregistered','local_port':18401,'remote_port':3000} if id=='ssh.tunnel' else {}
  if id.startswith('test.run.') or id in ('db.run.migration','db.run.seed','test.coverage.report','test.lint.run','test.type.check','test.performance'):
   fixtures={'tests/test_math.py':b'import unittest\nclass Math(unittest.TestCase):\n def test_sum(self):self.assertEqual(1+1,2)\n','schema.sql':b'CREATE TABLE IF NOT EXISTS demo(id INTEGER PRIMARY KEY, value TEXT);','seed.sql':b'CREATE TABLE IF NOT EXISTS demo(id INTEGER PRIMARY KEY, value TEXT); INSERT OR IGNORE INTO demo VALUES(1,"fixture");','src/check.ts':b'const answer: number = 42; console.log(answer);','tsconfig.json':b'{"compilerOptions":{"strict":true,"noEmit":true,"target":"ES2020","types":[]},"include":["src/**/*.ts"]}','tests/browser.cjs':b'const {chromium}=require("playwright");(async()=>{const b=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH||undefined,args:["--disable-gpu","--no-sandbox","--disable-dev-shm-usage"]});try{const p=await b.newPage();await p.goto("data:text/html,<h1>Harmonika</h1>");if(await p.locator("h1").innerText()!=="Harmonika")throw Error("wrong_dom");console.log("browser_real_ok");}finally{await b.close();}})().catch(e=>{console.error(e.message);process.exitCode=1;});','tests/perf.cjs':b'const http=require("node:http"),assert=require("node:assert/strict");(async()=>{const s=http.createServer((q,r)=>r.end("ok"));await new Promise(r=>s.listen(0,"127.0.0.1",r));try{let start=performance.now();for(let i=0;i<10;i++){const r=await fetch("http://127.0.0.1:"+s.address().port);assert.equal(await r.text(),"ok");}console.log(JSON.stringify({requests:10,elapsed_ms:performance.now()-start}));}finally{s.closeAllConnections();s.close();}})().catch(e=>{console.error(e.message);process.exitCode=1;});'}
   for path,data in fixtures.items():upload(p,path,data)
   path='schema.sql' if id=='db.run.migration' else 'seed.sql' if id=='db.run.seed' else 'tests/browser.cjs' if id=='test.run.e2e' else 'tests/perf.cjs' if id=='test.performance' else 'tests' if id in ('test.run.unit','test.run.integration','test.coverage.report') else 'tests/test_math.py' if id=='test.lint.run' else 'tsconfig.json';args={'path':path,'timeout_seconds':90}
  started=time.monotonic()
  try:
   j=invoke(p,id,args);last=j;row={'skill_id':id,'state':j['state'],'error_code':j.get('error',{}).get('code'),'execution_kind':j.get('result',{}).get('execution_kind'),'seconds':round(time.monotonic()-started,2)}
   row['expected_unavailable']=item['readiness']['status']=='unavailable';row['passed']=j['state']=='completed' if not row['expected_unavailable'] else j['state']=='failed' and row['error_code'] in ('ssh_connector_not_configured','notion_connector_not_configured')
   if id=='spec.read.voice':row['transcript_checked']=all(x in j.get('result',{}).get('data',{}).get('text','').lower() for x in ('dokumen','senin','bukti'));row['passed'] &= row['transcript_checked']
  except Exception as e:row={'skill_id':id,'passed':False,'failure_type':type(e).__name__}
  report.append(row);print(json.dumps(row),flush=True)
 p=projects[0];assert call('GET','/projects/'+p,headers=other)[0]==404;assert call('GET','/projects/'+p+'/artifact',headers=other)[0]==404
 assert call('GET','/jobs/'+last['id'],headers=other)[0]==404
 status,raw=call('GET','/projects/'+p+'/artifact',raw=True);assert status==200
 with zipfile.ZipFile(io.BytesIO(raw)) as z:assert z.namelist() and all(not x.startswith('/') and '..' not in x.split('/') for x in z.namelist())
 status,raw=call('GET','/jobs/'+last['id']+'/events',headers={'Accept':'text/event-stream'},raw=True);assert status==200 and b'job.completed' in raw and raw.endswith(b'data: [DONE]\n\n')
 events=call('GET','/jobs/'+last['id']+'/events')[1]['events'];assert call('GET','/jobs/'+last['id']+'/events?after_sequence='+str(events[-1]['sequence']))[1]['events']==[]
 key=uuid.uuid4().hex;body={'name':'Idempotent fixture'};one=call('POST','/projects',body,key=key);assert call('POST','/projects',body,key=key)[1]['project']['id']==one[1]['project']['id'];assert call('POST','/projects',{'name':'different'},key=key)[0]==409
 # Stale approval never runs; Stop is deterministic while awaiting approval.
 status,out=call('POST','/projects/'+p+'/invoke',{'skill_id':'test.run.unit','input':{}});j=out['job'];upload(p,'approval-revision.txt',b'new snapshot');assert call('POST','/jobs/'+j['id']+'/approve',{'decision':'approve','input_hash':j['input_hash']})[0]==409;assert call('POST','/jobs/'+j['id']+'/stop')[1]['job']['state']=='stopped'

 # Convenience workflow really generates source and runs its syntax check in Docker.
 status,out=call('POST','/projects',{'name':'SYNTHETIC workflow'});assert status==201;wp=out['project']['id']
 status,out=call('POST','/projects/'+wp+'/build',{'name':'demo-flow','spec':'Buat inventory Python FastAPI tanpa payment, jangan NextJS.','run_checks':True});assert status==202;j=out['job'];assert j['state']=='awaiting_approval'
 assert call('POST','/jobs/'+j['id']+'/approve',{'decision':'approve','input_hash':j['input_hash']})[0]==200
 for _ in range(300):
  j=call('GET','/jobs/'+j['id'])[1]['job']
  if j['state'] in ('completed','failed'):break
  time.sleep(.1)
 assert j['state']=='completed' and j['result']['data']['stack']=='fastapi' and j['result']['data']['framework_tests_executed'] is False,j.get('error')
 # Unregistered deployment stays fail-closed even after explicit local approval.
 status,out=call('POST','/projects/'+wp+'/deploy',{'target':'unregistered','expected_revision':project(wp)['revision']});assert status==202;j=out['job'];assert j['state']=='awaiting_approval';call('POST','/jobs/'+j['id']+'/approve',{'decision':'approve','input_hash':j['input_hash']})
 for _ in range(100):
  j=call('GET','/jobs/'+j['id'])[1]['job']
  if j['state']=='failed':break
  time.sleep(.05)
 assert j['state']=='failed' and j['error']['code']=='deployment_target_not_authorized'
 # Pending approvals count toward the tenant's max-active-jobs limit.
 pending=[]
 for _ in range(2):
  status,out=call('POST','/projects/'+wp+'/invoke',{'skill_id':'test.run.unit','input':{}});assert status==202;pending.append(out['job']['id'])
 assert call('POST','/projects/'+wp+'/invoke',{'skill_id':'test.run.unit','input':{}})[0]==429
 for jid in pending:assert call('POST','/jobs/'+jid+'/stop')[1]['job']['state']=='stopped'
 summary={'tested':len(report),'passed':sum(x['passed'] for x in report),'completed':sum(x.get('state')=='completed' for x in report),'expected_unavailable':sum(x.get('expected_unavailable',False) for x in report),'failed':[x for x in report if not x['passed']],'cross_tenant_404':True,'artifact_bearer_ownership':True,'sse_terminal_and_resume':True,'idempotency_replay_conflict':True,'stale_approval_and_stop':True,'actor':actor if private else None,'workflow_syntax_real':True,'unregistered_deployment_rejected':True,'active_job_limit_enforced':True,'projects':list(projects.values())+[one[1]['project']['id'],wp]}
 Path('/opt/harmonika-ai/builder-'+('private' if private else 'public')+'-smoke-report.json').write_text(json.dumps({'summary':summary,'skills':report},indent=2));print(json.dumps({'SUMMARY':summary}),flush=True);return bool(summary['failed'])
if __name__=='__main__':raise SystemExit(main())
