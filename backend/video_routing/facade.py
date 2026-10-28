"""Owner-only video RPC. Caller data never enters audit logs or the replay DB."""
import base64,hashlib,hmac,http.client,importlib.util,json,os,re,secrets,sqlite3,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CONFIG=Path('/etc/harmonika-ai/video-routing-owners.json')
REPLAY=Path('/var/lib/harmonika-video-routing/replays.sqlite3')
BASE='http://127.0.0.1:18252'
LEGACY='http://127.0.0.1:18240'
TOKEN=Path('/etc/harmonika-ai/video-toolkit.token')
OPS={'video_capabilities','generate_video','video_job_status','video_request_status','download_video','speech_capabilities','speech_generate','narrate_video','animate_video','seo_capabilities','seo_invoke','seo_job','seo_request','seo_artifact','backend_tools_capabilities','backend_tools_self_test','pro_cs_capabilities','pro_cs_invoke','pro_cs_plan','pro_cs_validate','pro_cs_takeover','pro_cs_close'}
ID=re.compile(r'^[a-f0-9]{32}$')
MAX_VIDEO=8*1024*1024
class Failure(Exception):pass

def canonical(data):return json.dumps(data,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def authorize(data,config=None,now=None,replay=True):
 cfg=json.loads(CONFIG.read_text()) if config is None else config;now=int(time.time()) if now is None else now
 proof=data.get('_video_auth');unsigned={k:v for k,v in data.items() if k!='_video_auth'}
 if not isinstance(proof,dict) or set(proof)!={'client','issued','nonce','signature'}:raise Failure('video_owner_required')
 client=proof.get('client');secret=cfg.get('clients',{}).get(client,{}).get('secret') if isinstance(client,str) else None
 if not isinstance(secret,str) or not re.fullmatch('[a-f0-9]{64}',secret):raise Failure('video_owner_required')
 if type(proof.get('issued')) is not int or abs(now-proof['issued'])>90 or not ID.fullmatch(str(proof.get('nonce',''))):raise Failure('video_auth_expired')
 context=unsigned.get('owner_context')
 if not isinstance(context,str) or not re.fullmatch('[A-Za-z0-9_-]{16,96}',context):raise Failure('invalid_request')
 signed={'input':unsigned,'client':client,'issued':proof['issued'],'nonce':proof['nonce']}
 expected=hmac.new(bytes.fromhex(secret),canonical(signed),hashlib.sha256).hexdigest()
 if not isinstance(proof.get('signature'),str) or not hmac.compare_digest(expected,proof['signature']):raise Failure('video_owner_required')
 if replay:
  REPLAY.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
  con=sqlite3.connect(REPLAY,timeout=5)
  try:
   con.execute('PRAGMA journal_mode=WAL');con.execute('CREATE TABLE IF NOT EXISTS nonces(client TEXT,nonce TEXT,expires INTEGER,PRIMARY KEY(client,nonce))')
   con.execute('DELETE FROM nonces WHERE expires<?',(now,))
   if con.execute('SELECT count(*) FROM nonces').fetchone()[0]>=10000:raise Failure('rate_limited')
   try:con.execute('INSERT INTO nonces VALUES(?,?,?)',(client,proof['nonce'],now+180));con.commit()
   except sqlite3.IntegrityError:raise Failure('video_auth_replayed') from None
  finally:con.close()
 owner=hmac.new(bytes.fromhex(secret),('video-owner:'+context).encode(),hashlib.sha256).hexdigest()
 return unsigned,owner

def call(method,path,owner,payload=None,idem=None,binary=False,_legacy=False):
 headers={'Authorization':'Bearer '+TOKEN.read_text().strip(),'X-Resource-Owner':owner,'Content-Type':'application/json'}
 if idem:headers['Idempotency-Key']=idem
 req=urllib.request.Request((LEGACY if _legacy else BASE)+path,method=method,headers=headers,data=canonical(payload) if payload is not None else None)
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=15) as r:
   raw=r.read((MAX_VIDEO if binary else 65536)+1)
   if len(raw)>(MAX_VIDEO if binary else 65536):raise Failure('video_output_limit')
   if binary:
    if r.headers.get('Content-Type')!='video/mp4':raise Failure('invalid_video_artifact')
    return raw
   value=json.loads(raw)
   if not isinstance(value,dict):raise Failure('invalid_video_response')
   return value
 except urllib.error.HTTPError as e:
  if e.code==404 and method=='GET' and not _legacy:return call(method,path,owner,payload,idem,binary,True)
  try:code=json.loads(e.read(4096)).get('error','video_unavailable')
  except Exception:code='video_unavailable'
  raise Failure(code if code in {'not_found','engine_not_qualified','queue_full','daily_limit','storage_full','engine_recovery_required','idempotency_conflict','unsupported_profile'} else 'video_unavailable') from None
 except (OSError,ValueError,http.client.HTTPException):raise Failure('video_submission_uncertain' if method=='POST' else 'video_unavailable') from None

def project(row):
 jid=row.get('id')
 if not isinstance(jid,str) or not ID.fullmatch(jid):raise Failure('invalid_video_response')
 state=row.get('state');allowed={'queued','running','completed','failed','unknown'}
 if state not in allowed:raise Failure('invalid_video_response')
 result=row.get('result') or {};safe={k:result.get(k) for k in ['width','height','duration_seconds','frames','audio','sha256']} if state=='completed' and isinstance(result,dict) else None
 error=row.get('error_type');error=error if isinstance(error,str) and re.fullmatch('[a-z_]{1,64}',error) else None
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','job_id':jid,'state':state,'error_code':error,'result':safe,'poll_after_seconds':10}

def execute(data,owner):
 op=data.get('operation')
 if op in ('pro_cs_capabilities','pro_cs_invoke','pro_cs_plan','pro_cs_validate','pro_cs_takeover','pro_cs_close'):
  spec=importlib.util.spec_from_file_location('harmonika_pro_cs',ROOT/'pro_cs_proxy.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.dispatch(data,owner)
 if op in ('backend_tools_capabilities','backend_tools_self_test'):
  spec=importlib.util.spec_from_file_location('harmonika_backend_tools',ROOT/'toolchain_proxy.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.dispatch(data,owner)
 if op in ('seo_capabilities','seo_invoke','seo_job','seo_request','seo_artifact'):
  spec=importlib.util.spec_from_file_location('harmonika_seo_proxy',ROOT/'seo_proxy.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.dispatch(data,owner)
 if op in ('speech_capabilities','speech_generate'):
  spec=importlib.util.spec_from_file_location('harmonika_local_speech',ROOT/'speech.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  if op=='speech_capabilities':
   if set(data)-{'operation','owner_context'}:raise Failure('invalid_request')
   return module.capabilities()
  try:return module.generate(data)
  except module.SpeechFailure as e:raise Failure(str(e)) from None
 common={'operation','owner_context'}
 fields={'video_capabilities':set(),'generate_video':{'prompt','duration_seconds','request_id'},'animate_video':{'prompt','duration_seconds','request_id','reference_image_base64'},'video_job_status':{'job_id'},'video_request_status':{'request_id'},'download_video':{'job_id'},'narrate_video':{'job_id','text','language','speed','voice','quality','style'}}
 if op not in fields or set(data)-fields[op]-common:raise Failure('invalid_request')
 if op=='video_capabilities':
  cap=call('GET','/capabilities',owner);engine=cap.get('engines',{}).get('wangp',{})
  spec=importlib.util.spec_from_file_location('harmonika_local_speech',ROOT/'speech.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);speech=module.capabilities()
  return {'ok':True,'status':'ok','provider':'Harmonika Ai','video_generation':engine.get('operator_ready') is True,'image_to_video':cap.get('engines',{}).get('framepack',{}).get('operator_ready') is True,'image_to_video_duration_seconds':[1],'duration_seconds':engine.get('qualified_duration_seconds',[1]),'audio':bool(speech.get('speech_synthesis') and __import__('shutil').which('ffmpeg')),'native_video_audio':False,'audio_requires_narration_step':True,'speech_synthesis':speech.get('speech_synthesis',False),'voice_quality':speech.get('voice_quality'),'voices':speech.get('voices',[]),'default_voice':speech.get('default_voice'),'can_add_speech':bool(speech.get('speech_synthesis') and __import__('shutil').which('ffmpeg')),'audience':'internal_owner','public_enabled':False}
 if op in ('generate_video','animate_video'):
  if Path('/etc/harmonika-ai/video-maintenance.flag').exists():raise Failure('engine_maintenance')
  prompt=data.get('prompt');duration=data.get('duration_seconds',1);rid=data.get('request_id')
  if not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode())>8000 or type(duration) is not int or duration not in (1,10) or not isinstance(rid,str) or not ID.fullmatch(rid):raise Failure('unsupported_video_profile')
  payload={'kind':'video.generate','prompt':prompt,'duration_seconds':duration}
  if op=='animate_video':
   if duration!=1:raise Failure('image_video_duration_unsupported')
   encoded=data.get('reference_image_base64')
   if not isinstance(encoded,str) or len(encoded)>12*1024*1024:raise Failure('invalid_reference_image')
   try:
    raw=base64.b64decode(encoded,validate=True)
    if not 100<=len(raw)<=8*1024*1024:raise ValueError()
    import io
    from PIL import Image
    with Image.open(io.BytesIO(raw)) as image:
     if image.format not in ('PNG','JPEG','WEBP') or max(image.size)>4096 or image.width*image.height>16777216 or getattr(image,'n_frames',1)!=1:raise ValueError()
     image.verify()
   except Exception:raise Failure('invalid_reference_image') from None
   payload.update(kind='video.animate_image',image_base64=encoded)
  if duration==10:
   cap=call('GET','/capabilities',owner)
   if 10 not in cap.get('engines',{}).get('wangp',{}).get('qualified_duration_seconds',[]):raise Failure('video_profile_not_qualified')
  try:row=call('POST','/jobs',owner,payload,rid)
  except Failure as e:
   if str(e)!='video_submission_uncertain':raise
   try:row=call('GET','/requests/'+rid,owner)
   except Failure:return {'ok':True,'status':'pending','state':'uncertain','request_id':rid,'retry_submission':False,'provider':'Harmonika Ai'}
  return {**project(row),'request_id':rid,'retry_submission':False}
 key='request_id' if op=='video_request_status' else 'job_id';value=data.get(key)
 if not isinstance(value,str) or not ID.fullmatch(value):raise Failure('invalid_request')
 row=call('GET',('/requests/' if op=='video_request_status' else '/jobs/')+value,owner)
 if op not in ('download_video','narrate_video'):return project(row)
 info=project(row)
 if info['state']!='completed':raise Failure('video_not_ready')
 digest=(info['result'] or {}).get('sha256','')
 if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise Failure('invalid_video_artifact')
 raw=call('GET','/jobs/'+value+'/file',owner,binary=True)
 if hashlib.sha256(raw).hexdigest()!=digest:raise Failure('video_checksum_mismatch')
 if op=='narrate_video':
  def load(name):
   spec=importlib.util.spec_from_file_location('harmonika_'+name,ROOT/(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
  speech=load('speech');mux=load('narrate')
  try:
   sound=speech.generate({'operation':'speech_generate',**{k:data[k] for k in ('text','language','speed','voice','quality','style') if k in data}})
   return mux.combine(raw,sound,info['result']['duration_seconds'])
  except (speech.SpeechFailure,mux.NarrationFailure) as e:raise Failure(str(e)) from None
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','mime_type':'video/mp4','video_base64':base64.b64encode(raw).decode(),'sha256':digest,'bytes':len(raw),**{k:v for k,v in info['result'].items() if k!='sha256'}}

def dispatch(data):
 try:
  if not isinstance(data,dict):raise Failure('invalid_request')
  unsigned,owner=authorize(data);return execute(unsigned,owner)
 except Failure as e:return {'ok':False,'status':'degraded','provider':'Harmonika Ai','error':{'code':str(e)}}
 except Exception:return {'ok':False,'status':'degraded','provider':'Harmonika Ai','error':{'code':'video_unavailable'}}
