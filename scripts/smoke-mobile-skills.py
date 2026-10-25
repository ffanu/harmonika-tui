#!/usr/bin/env python3
"""Isolated mobile source smoke. Token stdin only; no real customer/project state."""
import hashlib,json,os,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
PRIVATE=os.environ.get('HARMONIKA_MOBILE_SMOKE_PRIVATE')=='1'
BASE='http://127.0.0.1:18396/mobile' if PRIVATE else 'https://chat.harmonika.id/v1/mobile-builder'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')
def main():
 auth={} if PRIVATE else json.load(sys.stdin);tokens=auth.get('tokens',[]);counter=0
 secret=Path('/etc/harmonika-ai/master-engine.token').read_text().strip() if PRIVATE else ''
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def call(method,path,body=None,raw=None,headers=None):
  nonlocal counter
  # Several explicit isolated actors exercise the shared rate limiter without increasing it.
  actor=counter//25;counter+=1
  h={'X-Harmonika-Master-Secret':secret,'X-Harmonika-Principal':'api-token:'+str(90000000000777000+actor)} if PRIVATE else {'Authorization':'Bearer '+tokens[actor%len(tokens)]}
  data=raw.encode() if raw is not None else json.dumps(body).encode() if body is not None else None
  req=urllib.request.Request(BASE+path,method=method,headers={**h,'Content-Type':'application/json',**(headers or {})},data=data)
  try:
   with op.open(req,timeout=25) as res:code=res.status;out=res.read(262145)
  except urllib.error.HTTPError as e:code=e.code;out=e.read(65536)
  assert len(out)<=262144
  return code,json.loads(out)
 code,caps=call('GET','/capabilities');assert code==200 and caps['catalog_size']==214
 for field in ('android_build','ios_build','code_signing','store_publish','ota_release','device_farm','live_iap','host_filesystem_access'):assert caps['features'][field] is False
 rows=[]
 for offset in (0,100,200):
  code,page=call('GET','/skills?limit=100&offset='+str(offset));assert code==200;rows.extend(page['skills'])
 assert len(rows)==214 and len({r['id'] for r in rows})==214
 results=[];demos={};latency=[]
 for row in rows:
  if row['status']!='available':continue
  payload={}
  props=row['input_schema'].get('properties',{})
  if 'spec' in props:payload={'spec':'Aplikasi Android dan iOS katalog offline, tanpa kamera atau pembayaran.','screens':['Home','Catalog']} if 'screens' in props else {'spec':'Aplikasi Android dan iOS katalog offline.'}
  else:
   for name,field in props.items():
    if name in ('name','identifier','screen_name','api_base_url'):
     payload[name]={'name':'Harmonika Demo','identifier':'id.harmonika.demo','screen_name':'Catalog','api_base_url':'https://example.invalid/api'}[name]
    elif name in row['input_schema'].get('required',[]):
     if 'default' in field:payload[name]=field['default']
     elif 'enum' in field:payload[name]=field['enum'][0]
     else:raise ValueError('sample_missing_'+row['id']+'_'+name)
  start=time.monotonic();code,out=call('POST','/invoke',{'skill_id':row['id'],'input':payload});elapsed=time.monotonic()-start
  assert code==200,(row['id'],code,out.get('error'));assert out['status']=='completed' and out['mode']==row['mode']
  for field in ('runtime_verified','build_performed','tests_performed','signed','deployed','external_actions_performed'):assert out[field] is False
  if row['mode']=='source_generation':assert out['files'];demos[row['id']]=out
  latency.append(elapsed);results.append({'id':row['id'],'mode':row['mode'],'seconds':round(elapsed,4),'files':len(out.get('files',[]))})
 # Missing runtimes, fake IDs and injected principal/credentials never execute.
 for id in ('build.android.apk','build.android.aab','build.ios.ipa','deploy.play_store','deploy.app_store','iap.purchase','ota.release.update','test.device_farm'):
  code,out=call('POST','/invoke',{'skill_id':id,'input':{}});assert code==503 and 'error' in out,(id,code)
 code,_=call('POST','/invoke',{'skill_id':'mobile.gen.screen.login','input':{}});assert code==404
 for field in ('actor','secret','endpoint','context'):
  code,_=call('POST','/invoke',{'skill_id':'spec.read.mobile','input':{'spec':'Android'},field:'injected'});assert code==400
 code,_=call('POST','/invoke',raw='{"skill_id":"spec.read.mobile","input":{"spec":"Android","spec":"iOS"}}');assert code==400
 code,_=call('POST','/invoke',{'skill_id':'spec.read.mobile','input':{'spec':'Android','path':'/etc/passwd'}});assert code==400
 code,out=call('POST','/invoke',{'skill_id':'spec.read.mobile','input':{'spec':'Android tanpa login, gunakan kamera; bukan iOS.'}});assert code==200
 req=out['data']['requirements'];assert req['platform']=='android' and 'camera' in req['features'] and 'login' not in req['features']
 for path in ('/capabilities?url=http://localhost','/skills?limit=201','/skills/scaffold.expo?actor=root'):
  assert call('GET',path)[0]==400
 # curl health/readiness is tested with secret on stdin, never command argv.
 curl_ok=None
 if PRIVATE:
  config='silent\nshow-error\nfail\nurl = "'+BASE+'/capabilities"\nheader = "X-Harmonika-Master-Secret: '+secret+'"\nheader = "X-Harmonika-Principal: api-token:90000000000777999"\n'
  done=subprocess.run(['curl','--config','-'],input=config.encode(),capture_output=True,timeout=10,check=True);curl_ok=json.loads(done.stdout)['catalog_size']==214
 summary={'passed':len(results),'catalog_size':214,'readiness':caps['readiness'],'negative_runtime_and_security':True,'curl_capabilities_passed':curl_ok,'mean_seconds':round(sum(latency)/len(latency),4),'max_seconds':round(max(latency),4),'results':results,'fixtures_only':True,'customer_data_modified':False}
 label='private' if PRIVATE else 'public';Path('/opt/harmonika-ai/mobile-'+label+'-smoke.json').write_text(json.dumps(summary,indent=2));Path('/opt/harmonika-ai/mobile-'+label+'-demo.json').write_text(json.dumps(demos,ensure_ascii=False,indent=2))
 print(json.dumps({k:v for k,v in summary.items() if k!='results'}))
if __name__=='__main__':main()
