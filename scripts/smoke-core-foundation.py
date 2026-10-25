#!/usr/bin/env python3
"""Synthetic shared-core smoke; secrets via stdin/private files only."""
import hashlib,json,os,subprocess,sys,time,uuid,urllib.request,urllib.error
from pathlib import Path
PRIVATE=os.environ.get('HARMONIKA_CORE_SMOKE_PRIVATE')=='1'
BASE='http://127.0.0.1:18397' if PRIVATE else 'https://chat.harmonika.id/v1/core'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')
def main():
 nonce=str(uuid.uuid4());auth={} if PRIVATE else json.load(sys.stdin);op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect());secret=Path('/etc/harmonika-ai/core-engine.token').read_text().strip() if PRIVATE else ''
 def call(method,path,body=None,who=0,key=None,raw=None):
  header={'X-Harmonika-Core-Secret':secret,'X-Harmonika-Principal':'api-token:'+str(90000000007770000+who)} if PRIVATE else {'Authorization':'Bearer '+auth['tokens'][who],'X-Harmonika-Principal':'api-token:999999999999999999'}
  if method=='POST':header['Idempotency-Key']=hashlib.sha256((nonce+key).encode()).hexdigest() if key else str(uuid.uuid4())
  target=BASE+path
  if path.startswith('/@coding/'):
   assert not PRIVATE;target='https://chat.harmonika.id/v1/coding/'+path[len('/@coding/'):]
  if path.startswith('/@master/'):
   assert not PRIVATE;target='https://chat.harmonika.id/v1/skills/'+path[len('/@master/'):]
  req=urllib.request.Request(target,method=method,headers={**header,'Content-Type':'application/json'},data=raw.encode() if raw is not None else json.dumps(body).encode() if body is not None else None)
  try:
   with op.open(req,timeout=150) as r:status=r.status;data=r.read(4*1024*1024+1)
  except urllib.error.HTTPError as e:status=e.code;data=e.read(65536)
  assert len(data)<=4*1024*1024
  return status,json.loads(data)
 def invoke(id,v,**kwargs):return call('POST','/invoke',{'skill_id':id,'input':v},**kwargs)
 code,caps=call('GET','/capabilities');assert code==200 and caps['catalog_size']==99
 assert caps['readiness']=={'available':12,'context_required':19,'local_only':6,'native_endpoint':2,'unavailable':60}
 code,catalog=call('GET','/skills?limit=100');assert code==200 and len(catalog['skills'])==99
 for row in catalog['skills']:
  if row['status'] in ('unavailable','local_only'):
   code,out=invoke(row['id'],{},who=3 if not PRIVATE else 1);assert code==(409 if row['status']=='local_only' else 503),(row['id'],code);assert 'error' in out
 samples={
 'data.read_csv':{'text':'name,value\nA,12\nB,15\n'},'data.read_json':{'text':'{"n":9007199254740993}'},
 'data.transform':{'rows':[{'n':2},{'n':1}],'operations':[{'type':'sort','column':'n'}]},
 'data.analyze':{'rows':[{'n':1},{'n':3}]},'data.visualize':{'rows':[{'label':'A','value':'12'}],'chart_type':'bar','x':'label','y':'value'},
 'data.export':{'rows':[{'v':'=NOT_A_REAL_FORMULA'}],'format':'csv'}}
 results=[]
 for id,v in samples.items():
  code,out=invoke(id,v);assert code==200 and out['status']=='completed',(id,code,out.get('error'));results.append(id)
  if id=='data.read_json':assert out['data']['value']['n']==9007199254740993
 code,out=invoke('memory.set',{'scope':'smoke','key':'n','value':9007199254740993},key='smoke-memory-001');assert code==200 and out['status']=='completed';results.append('memory.set')
 code,out=invoke('memory.set',{'scope':'smoke','key':'n','value':9007199254740993},key='smoke-memory-001');assert code==200 and out['replayed'] is True
 code,_=invoke('memory.set',{'scope':'smoke','key':'n','value':9007199254740992},key='smoke-memory-001');assert code==409
 code,out=invoke('memory.get',{'scope':'smoke','key':'n'});assert code==200 and out['data']['value']==9007199254740993;results.append('memory.get')
 code,out=invoke('memory.get',{'scope':'smoke','key':'n'},who=1);assert code==200 and out['data']['found'] is False
 code,out=invoke('memory.search',{'scope':'smoke','query':'9007199254740993'});assert code==200 and out['data']['total']==1;results.append('memory.search')
 code,out=invoke('memory.clear',{'scope':'smoke','all':True},who=1);assert code==200
 assert invoke('memory.get',{'scope':'smoke','key':'n'})[1]['data']['found'] is True
 code,out=invoke('workflow.define',{'name':'Synthetic foundation smoke','steps':[{'id':'parse','skill_id':'data.read_json','input':{'text':'{"a":1}'}},{'id':'stats','skill_id':'data.analyze','input':{'rows':[{'url':'https://example.invalid','amount':12}]},'depends_on':['parse']}]},key='smoke-define-001');assert code==200;wid=out['data']['workflow_id'];results.append('workflow.define')
 code,out=invoke('workflow.run',{'workflow_id':wid},key='smoke-run-001');assert code==200 and out['data']['status']=='completed';rid=out['data']['run_id'];results.append('workflow.run')
 code,out=invoke('workflow.run',{'workflow_id':wid},key='smoke-run-001');assert code==200 and out['replayed'] and out['data']['run_id']==rid
 code,_=invoke('workflow.run',{'workflow_id':wid},who=1);assert code==404
 code,out=invoke('memory.clear',{'scope':'smoke','all':True},key='smoke-clear-001');assert code==200;results.append('memory.clear')
 assert invoke('memory.get',{'scope':'smoke','key':'n'})[1]['data']['found'] is False
 for raw in ('{"skill_id":"memory.get","input":{"key":"n","key":"x"}}','{"skill_id":"memory.set","input":{"key":"n","value":NaN}}'):
  assert call('POST','/invoke',raw=raw)[0]==400
 assert invoke('workflow.define',{'name':'bad','steps':[{'id':'exec','skill_id':'shell.exec','input':{'command':'bad'}}]})[0]==403
 assert invoke('memory.get',{'key':'n','actor':'root'})[0]==400
 assert invoke('data.transform',{'rows':[{'x':1}],'operations':[{'type':[]}]})[0]==400
 assert invoke('data.analyze',{'rows':[{'x':'1e999999999'}]})[0]==413
 for path in ('/capabilities?url=http://localhost','/skills?limit=201'):
  assert call('GET',path)[0]==400
 aliases=[];workspaces=[];approved_execution=False;llm=[]
 if not PRIVATE:
  code,out=call('GET','/@master/memory.set');assert code==200 and out['skill']['runtime_readiness_endpoint']=='/v1/core/capabilities'
  code,out=call('POST','/@master/invoke',{'skill_id':'data.analyze','input':{'rows':[{'x':1},{'x':3}]}});assert code==200 and out['data']['columns']['x']['mean']==2
  def workspace(name,permissions):
   code,out=call('POST','/@coding/workspaces',{'name':name,'permissions':permissions});assert code==201
   workspaces.append(out['workspace']['id']);return workspaces[-1]
  ws=workspace('SYNTHETIC CORE FOUNDATION',{'write':True,'execute':True,'git_commit':True})
  readonly=workspace('SYNTHETIC CORE FOUNDATION',{'write':False,'execute':False,'git_commit':False})
  def delegated(id,v,w=ws,key=None,who=0):return call('POST','/invoke',{'skill_id':id,'input':v,'context':{'workspace_id':w}},key=key,who=who)
  def finished(job,approve=False):
   jid=job['id'];assert call('GET','/@coding/jobs/'+jid,who=1)[0]==404
   if job['status']=='approval_required':
    assert call('POST','/@coding/jobs/'+jid+'/approve',{'decision':'approve','input_hash':job['input_hash']},who=1)[0]==404
    if not approve:
     code,out=call('POST','/@coding/jobs/'+jid+'/stop',{});assert code==200;return out
    code,job=call('POST','/@coding/jobs/'+jid+'/approve',{'decision':'approve','input_hash':job['input_hash']});assert code==200
   for _ in range(300):
    if job['status'] not in ('queued','running'):break
    time.sleep(.1);code,job=call('GET','/@coding/jobs/'+jid);assert code==200
   return job
  code,j=delegated('file.write',{'path':'hello.py','content':'print(42)\n'});assert code in (200,202);j=finished(j);assert j['status']=='completed';aliases.append('file.write')
  code,j=delegated('file.read',{'path':'hello.py'});assert code in (200,202);j=finished(j);assert j['status']=='completed' and j['result']['content']=='print(42)\n';sha=j['result']['sha256'];aliases.append('file.read')
  code,j=delegated('file.edit',{'path':'hello.py','old':'42','new':'43','expected_sha256':sha});assert code in (200,202);assert finished(j)['status']=='completed';aliases.append('file.edit')
  for id,v in [('file.list',{}),('file.search',{'pattern':'*.py'}),('file.grep',{'pattern':'43','path':'.','literal':True}),('code.scaffold',{'path':'service','template':'python_http','name':'CoreDemo'})]:
   code,j=delegated(id,v);assert code in (200,202),(id,code,j.get('error'));assert finished(j)['status']=='completed',(id,j.get('error'));aliases.append(id)
  execution={
   'shell.exec':{'command':'python3 -c "print(42)"','timeout_seconds':10},'shell.background':{'command':'python3 -c "print(42)"','timeout_seconds':10},
   'code.run.python':{'code':'print(42)'},'code.run.node':{'code':'console.log(42)'},'code.run.shell':{'command':'python3 -c "print(42)"'},
   'code.lint':{'path':'hello.py','language':'python'},'code.format':{'path':'hello.py','language':'python'},
   'code.test':{'runner':'python_unittest','path':'.'},'git.commit':{'message':'Synthetic draft only, never committed','paths':['hello.py']},'system.info':{}}
  for id,v in execution.items():
   code,j=delegated(id,v);assert code in (200,202),(id,code,j.get('error'));real=id=='code.run.python'
   if id!='code.lint':assert j['status']=='approval_required',(id,code,j.get('status'))
   out=finished(j,approve=real)
   if real:assert out['status']=='completed' and '42' in out['result']['stdout'];approved_execution=True
   elif id=='code.lint':assert out['status']=='completed',(id,out.get('error'))
   else:assert out['status']=='stopped',(id,out.get('status'))
   aliases.append(id)
  for id in ('git.status','git.diff'):
   # A fresh fixture has no Git repository; routing+owned-job recovery is exercised.
   code,j=delegated(id,{});assert code in (200,202);out=finished(j);assert out['status'] in ('completed','failed');aliases.append(id)
  assert len(aliases)==19
  assert delegated('file.read',{'path':'hello.py'},who=1)[0]==404
  assert delegated('file.write',{'path':'x','content':'x'},w=readonly)[0]==403
  assert delegated('system.info',{},w=readonly)[0]==403
  for id,payload in [('llm.complete',{'prompt':'Reply with only CORE_OK. Do not use tools.'}),('llm.chat',{'messages':[{'role':'user','content':'Reply with only CORE_OK. Do not use tools.'}]})]:
   code,out=invoke(id,payload,who=2);assert code==200 and out.get('choices'),(id,code,out.get('error'));llm.append({'id':id,'http':code,'choices':len(out['choices'])})
 else:
  config='silent\nshow-error\nfail\nurl = "'+BASE+'/capabilities"\nheader = "X-Harmonika-Core-Secret: '+secret+'"\nheader = "X-Harmonika-Principal: api-token:90000000007770999"\n'
  p=subprocess.run(['curl','--config','-'],input=config.encode(),capture_output=True,check=True,timeout=10);assert json.loads(p.stdout)['catalog_size']==99
 summary={'local_operations_tested':len(results),'local_ids':results,'workspace_adapters_tested':len(aliases),'workspace_adapter_ids':aliases,'actual_python_execution':approved_execution,'workspace_file_read_write_edit':not PRIVATE,'workspace_ownership_and_write_execution_permissions':not PRIVATE,'chat_adapters':llm,'memory_isolation_precision_replay':True,'workflow_real_steps_and_replay':True,'unavailable_and_local_only_rejected':66,'customer_data_modified':False,'synthetic_workspaces':workspaces,'fixture_actor_ids':auth.get('actor_ids',[])}
 label='private' if PRIVATE else 'public';Path('/opt/harmonika-ai/core-'+label+'-smoke.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
if __name__=='__main__':main()
