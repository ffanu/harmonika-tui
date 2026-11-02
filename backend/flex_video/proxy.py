"""Called only after signed owner authorization by the existing gateway."""
import json,urllib.request
from pathlib import Path

def call(action,owner,**values):
 try:
  req=urllib.request.Request('http://127.0.0.1:18258/rpc',method='POST',headers={'Content-Type':'application/json','Authorization':'Bearer '+Path('/etc/harmonika-ai/video-toolkit.token').read_text().strip()},data=json.dumps({'action':action,'owner':owner,**values}).encode())
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=10) as r:return json.loads(r.read(1024**2))
 except Exception:return {'ok':False,'error':{'code':'flex_video_unavailable'}}

def route(data,owner):
 op=data.get('operation');jid=data.get('job_id','')
 if op=='video_capabilities':return None
 if op in ('generate_video','animate_video'):
  seconds=data.get('duration_seconds',1)
  if type(seconds) is not int or not 1<=seconds<=2**31-1:return {'ok':False,'error':{'code':'invalid_duration'}}
  # Keep qualified legacy text profiles and animation-scene behavior intact.
  if seconds==1 or (op=='generate_video' and seconds==10):return None
  if op=='generate_video' and any(k in data for k in ('scenes','narration')):return {'ok':False,'error':{'code':'flex_video_scene_options_unsupported'}}
  payload={'kind':'video.animate_image' if op=='animate_video' else 'video.generate','duration_seconds':seconds,'prompt':data.get('prompt')}
  if op=='animate_video':payload['image_base64']=data.get('reference_image_base64')
  out=call('submit',owner,request_id=data.get('request_id'),input=payload)
  if out.get('ok'):out.update(request_id=data.get('request_id'),retry_submission=False)
  return out
 if op=='video_request_status':
  out=call('request',owner,request_id=data.get('request_id'))
  if out.get('error',{}).get('code') in ('not_found','flex_video_unavailable'):return None
  return out
 if isinstance(jid,str) and jid.startswith('d04a'):
  status=call('job',owner,job_id=jid)
  if status.get('error',{}).get('code')=='not_found':return None
  if not status.get('ok') or op=='video_job_status':return status
  if op=='download_video':return call('download',owner,job_id=jid,offset=data.get('offset',0))
  if op=='narrate_video':return {'ok':False,'error':{'code':'flex_video_narration_requires_studio'}}
 return None
