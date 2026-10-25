#!/usr/bin/env python3
"""Owner-generated synthetic tokens arrive on stdin; never print credentials or model text."""
import json,sys,time,urllib.request,urllib.error,uuid
from pathlib import Path
auth=json.load(sys.stdin);base='https://chat.harmonika.id/v1/unified';checks=[]
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):raise ValueError('redirect_refused')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def call(method,path,body=None,who=0,key=None,headers=None):
 h={'Authorization':'Bearer '+auth['tokens'][who],'Content-Type':'application/json','X-Harmonika-Principal':'api-token:999999999999999999','Idempotency-Key':key or str(uuid.uuid4()),**(headers or {})};target=base+path
 if path.startswith('/@master/'):target='https://chat.harmonika.id/v1/skills/'+path[len('/@master/'):]
 req=urllib.request.Request(target,method=method,data=json.dumps(body).encode() if body is not None else None,headers=h)
 try:
  with op.open(req,timeout=40) as r:code=r.status;raw=r.read(1048577);mime=r.headers.get('Content-Type','')
 except urllib.error.HTTPError as e:code=e.code;raw=e.read(65536);mime=e.headers.get('Content-Type','')
 assert len(raw)<=1048576
 return code,raw.decode() if mime.startswith('text/event-stream') else json.loads(raw)
def invoke(id,v,who=0,key=None):return call('POST','/invoke',{'skill_id':id,'input':v},who,key)
def done(id,who=0):
 for _ in range(160):
  c,v=call('GET','/jobs/'+id,who=who);assert c==200
  if v['status'] not in ('queued','running'):return v
  time.sleep(1)
 raise AssertionError('job_timeout')
c,caps=call('GET','/capabilities');assert c==200 and caps['catalog_size']==70;checks.append('capabilities')
c,catalog=call('GET','/skills');assert c==200 and len(catalog['skills'])==70
ms={'messages':[{'role':'user','content':'Jelaskan apa itu router'}]}
pure_examples={'data.serialize':{'value':{'n':9007199254740993}},'data.deserialize':{'value':'{"n":9007199254740993}'},'data.validate':{'value':{'x':1}},'data.context.pass':{'value':{'x':1}},'data.transform':{'rows':[{'n':1},{'n':2}],'operations':[{'type':'limit','count':1}]},'core.retrieve':{'query':'router','sources':[{'id':'synthetic','text':'Router menghubungkan jaringan','verified':True}]}}
for row in catalog['skills']:
 id=row['id']
 if row['status']!='available':
  c,v=invoke(id,{},who=1);assert c==(409 if row['status']=='internal_only' else 503),(id,c);checks.append('blocked:'+id)
 elif row['mode']=='bounded_analysis':
  val=pure_examples.get(id,{'results':[{'engine':'synthetic','status':'completed','data':{'value':1}}]} if id.startswith('orch.aggregate.') else ms)
  c,v=invoke(id,val);assert c==200,(id,c,v.get('error'));checks.append(id)
c,s=call('POST','/sessions',{});assert c==201;session=s['id']
payload={'messages':[{'role':'user','content':'Jawab singkat: apa fungsi router?'}],'session_id':session};key=str(uuid.uuid4());job=None
for row in catalog['skills']:
 if row['mode']=='async_job':
  c,v=invoke(row['id'],payload,key=key);assert c==202,(row['id'],c,v.get('error'));job=job or v['id'];assert v['id']==job;checks.append(row['id'])
final=done(job);assert final['status']=='completed' and final['result'].get('text');checks.append('real_chat_job')
assert call('GET','/jobs/'+job,who=1)[0]==404
assert call('GET','/sessions/'+session,who=1)[0]==404
c,v=call('GET','/sessions/'+session);assert c==200 and len(v['messages'])==2
c,stream=call('GET','/jobs/'+job+'/events',headers={'Last-Event-ID':job+':1'});assert c==200 and 'event: completed' in stream and 'event: queued' not in stream;checks.append('real_sse_terminal_replay')
for id in ('orch.detect.session','orch.context.load'):
 c,v=invoke(id,{'session_id':session});assert c==200;checks.append(id)
for id in ('orch.exec.monitor','orch.exec.timeout','orch.exec.retry','bus.subscribe.event','bus.dead_letter'):
 c,v=invoke(id,{'job_id':job});assert c==200;checks.append(id)
c,j=call('POST','/jobs',ms,who=2,key=str(uuid.uuid4()));assert c==202
c,v=invoke('orch.exec.cancel',{'job_id':j['id']},who=2);assert c==200 and v['data']['status']=='stopped';checks.append('orch.exec.cancel')
for i,id in enumerate(('core.learn','shared.user_profile')):
 c,v=invoke(id,{'consent':False,'key':'synthetic','value':'v'},who=2);assert c==403
 c,v=invoke(id,{'consent':True,'key':'synthetic','value':'v'},who=2);assert c==200 and v['data']['automatic_training'] is False;checks.append(id)
for id in ('shared.memory','data.shared.memory'):
 c,v=invoke(id,{'operation':'memory.set','input':{'scope':'unified.smoke','key':'x','value':9007199254740993}},who=2);assert c==200
 c,v=invoke(id,{'operation':'memory.get','input':{'scope':'unified.smoke','key':'x'}},who=2);assert c==200 and v['data']['data']['value']==9007199254740993
 c,v=invoke(id,{'operation':'memory.get','input':{'scope':'unified.smoke','key':'x'}},who=1);assert c==200 and not v['data']['data']['found'];checks.append(id)
c,v=call('POST','/@master/invoke',{'skill_id':'core.understand','input':ms},who=1);assert c==200;checks.append('master_route')
c,v=call('POST','/jobs',{'messages':[{'role':'user','content':'Buat website katalog toko, lalu aplikasi Android untuk pelanggan.'}]},who=1,key=str(uuid.uuid4()));assert c==202
final=done(v['id'],who=1);assert final['status']=='completed';engines={x['engine'] for x in final['result'].get('specialist_results',[])};assert {'web_builder','mobile_builder'}<=engines,(engines,final['result'].get('analysis'));checks.append('real_multi_specialist_planning')
summary={'status':'passed','checks':checks,'checks_count':len(checks),'catalog_size':70,'readiness':caps['readiness'],'real_chat':True,'multi_specialist_engines':sorted(engines),'sse_replay':True,'ownership_two_tokens':True,'stop':True,'consent_and_shared_memory':True,'token_ids':auth['actor_ids'],'customer_data_modified':False}
Path('/opt/harmonika-ai/unified-smoke-report.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
