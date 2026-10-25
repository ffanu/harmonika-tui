#!/usr/bin/env python3
"""Isolated master catalog smoke; secrets stdin/private file only, metadata output."""
import json,os,time,urllib.request,urllib.error,uuid
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):raise ValueError('redirect_refused')
def main():
 private=os.environ.get('HARMONIKA_MASTER_SMOKE_PRIVATE')=='1';auth={} if private else json.load(__import__('sys').stdin);base='http://127.0.0.1:18395' if private else 'https://chat.harmonika.id/v1/skills';actor='api-token:900000000000777001'
 headers={'X-Harmonika-Master-Secret':Path('/etc/harmonika-ai/master-engine.token').read_text().strip(),'X-Harmonika-Principal':actor} if private else {'Authorization':'Bearer '+auth['token']};op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 if private:
  with op.open(base+'/healthz',timeout=5) as r:assert json.load(r)['service']=='harmonika-master-skills'
 def call(method,path,body=None,h=None):
  target=(base.removesuffix('/skills')+'/builder/'+path[len('/@builder/'):]) if path.startswith('/@builder/') else base+path
  req=urllib.request.Request(target,method=method,headers={**headers,'Content-Type':'application/json','Idempotency-Key':str(uuid.uuid4()),**(h or {})},data=json.dumps(body).encode() if body is not None else None)
  try:
   with op.open(req,timeout=50) as r:code=r.status;raw=r.read(1048577)
  except urllib.error.HTTPError as e:code=e.code;raw=e.read(65536)
  return code,json.loads(raw)
 code,caps=call('GET','/capabilities');assert code==200 and caps['source_unique']==1200 and caps['existing_registered']==601
 samples=json.loads(Path('/opt/harmonika-ai/master-all-stage/master_engine/smoke_samples.json' if private else '/opt/harmonika-ai/master_engine/smoke_samples.json').read_text());results=[]
 for id,payload in samples.items():
  started=time.monotonic();code,out=call('POST','/invoke',{'skill_id':id,'input':payload});assert code==200 and out.get('status')=='completed' and out.get('analysis_performed') is True,(id,code,out.get('error'));encoded=json.dumps(out)
  if id=='code.secret.scan':assert 'SYNTHETIC_NOT_A_REAL_PASSWORD' not in encoded and out['metrics']['matches_redacted']>0
  if id=='workflow.browser':assert out['data']['actions'][2]['text']=='Halo Harmonika' and out['data']['page_errors']==0
  if id.startswith('binary.inspect.'):assert out['data']['parsed'] is True
  results.append({'id':id,'status':'completed','seconds':round(time.monotonic()-started,3)});print(json.dumps(results[-1]),flush=True)
 total=0;offset=0;seen=set()
 while True:
  code,page=call('GET',('' if not private else '/skills')+'?limit=200&offset='+str(offset));assert code==200
  for r in page['skills']:assert r['id'] not in seen;seen.add(r['id'])
  total+=len(page['skills']);offset+=200
  if total>=page['total']:break
 assert total==caps['catalog_size']
 bad=[('exploit.reverse_shell',403),('proxy.mobile.buy',501),('totally.unknown',404)]
 for id,expected in bad:
  code,out=call('POST','/invoke',{'skill_id':id,'input':{}});assert code==expected and 'error' in out,(id,code)
 for field in ('actor','principal','secret','endpoint'):
  code,_=call('POST','/invoke',{'skill_id':'ip.lookup','input':{'ip':'192.0.2.1'},field:'injected'});assert code==400
 code,out=call('POST','/invoke',{'skill_id':'ip.lookup','input':{'ip':'not-an-ip'}});assert code==400 and out['error']['code']=='invalid_analysis_input'
 if not private:
  for id in ('debug.attach','test.run','evidence.chain.custody'):
   code,out=call('POST','/invoke',{'skill_id':id,'input':{}});assert code==409 and out['error']['code']=='explicit_engine_context_required'
  # Real ownership boundary: nonexistent builder/coding IDs return404, never create side effects.
  for id,ctx in [('scaffold.fastapi',{'engine':'builder','project_id':'00000000-0000-4000-8000-000000000000'}),('fs.read',{'engine':'coding','workspace_id':'00000000-0000-4000-8000-000000000000'})]:
   code,_=call('POST','/invoke',{'skill_id':id,'input':{'project':'demo'} if id.startswith('scaffold') else {'path':'x'},'context':ctx});assert code==404,(id,code)
  code,out=call('POST','/@builder/projects',{'name':'SYNTHETIC MASTER ROUTING'});assert code==201;pid=out['project']['id']
  code,out=call('POST','/invoke',{'skill_id':'scaffold.fastapi','input':{'project':'demo-hub'},'context':{'engine':'builder','project_id':pid}});assert code==202;jid=out['job']['id']
  for _ in range(100):
   code,out=call('GET','/@builder/jobs/'+jid)
   if out['job']['state'] in ('completed','failed'):break
   time.sleep(.1)
  assert out['job']['state']=='completed'
  code,_=call('POST','/invoke',{'skill_id':'scaffold.fastapi','input':{'project':'demo-hub'},'context':{'engine':'builder','project_id':pid}},h={'Authorization':'Bearer '+auth['other_token']});assert code==404
  assert call('GET','/@builder/projects/'+pid+'/artifact',h={'Authorization':'Bearer '+auth['other_token']})[0]==404
 summary={'analyzers_and_browser_tested' :len(results),'passed':len(results),'catalog_pagination':total,'unimplemented_restricted_unknown_errors':True,'credential_identity_injection_rejected':True,'namespace_confirmation_required':not private,'real_engine_ownership_404':not private,'positive_builder_job_route':not private,'capabilities':caps,'results':results}
 Path('/opt/harmonika-ai/master-'+('private' if private else 'public')+'-smoke.json').write_text(json.dumps(summary,indent=2));print(json.dumps({'SUMMARY':{k:v for k,v in summary.items() if k not in ('results','capabilities')}}),flush=True)
if __name__=='__main__':main()
