"""Licensed public RPC facade. No worker keys, private job IDs or research TTS escape."""
import base64,http.client,json,secrets,sys,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from contracts import Failure,validate,validate_result
OPS={'transcribe_audio':'audio.transcribe','upscale_image':'image.upscale','browse_page':'browser.inspect'}
ALLOWED={'transcribe_audio':{'audio_base64','language'},'upscale_image':{'image_base64'},'browse_page':{'url'}}
MAX_OUTPUT=23*1024*1024
class RPCError(Exception):
 def __init__(self,code):self.code=code;super().__init__(code)
def request(endpoint,path,body=None,idem=None):
 key=Path('/etc/harmonika-ai/media-suite-rtx.token') if endpoint==18232 else ROOT/'service.token'
 headers={'Authorization':'Bearer '+key.read_text().strip(),'Content-Type':'application/json'}
 if idem:headers['Idempotency-Key']=idem
 r=urllib.request.Request(f'http://127.0.0.1:{endpoint}'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r,timeout=5) as f:
   raw=f.read(MAX_OUTPUT+1)
   if len(raw)>MAX_OUTPUT:raise RPCError('media_output_limit')
   return json.loads(raw)
 except urllib.error.HTTPError as e:raise RPCError('media_submission_uncertain' if body is not None and e.code>=500 else 'media_request_rejected') from None
 except (OSError,ValueError,http.client.HTTPException):raise RPCError('media_submission_uncertain' if body is not None else 'media_unavailable') from None

def capabilities():
 flags={}
 for port in (18230,18232):
  try:flags.update({k:v['ready'] for k,v in request(port,'/health')['capabilities'].items() if v.get('ready')})
  except Exception:pass
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','features':{'audio_transcription':flags.get('audio.transcribe',False),'image_upscale':flags.get('image.upscale',False),'browser_inspection':flags.get('browser.inspect',False),'speech_synthesis':False,'speech_research_private':flags.get('audio.synthesize',False)},'browser_scope':'operator_registered_public_hosts_only','speech_public_status':'license_restricted'}

def dispatch(data):
 if type(data) is not dict:raise RPCError('invalid_request')
 op=data.get('operation')
 if op=='media_capabilities':
  if set(data)!={'operation'}:raise RPCError('invalid_request')
  return capabilities()
 if op in ('synthesize_speech','audio.synthesize'):raise RPCError('license_restricted')
 if op not in OPS or set(data)-ALLOWED[op]-{'operation'}:raise RPCError('invalid_request')
 payload={k:v for k,v in data.items() if k!='operation'};cfg=json.loads((ROOT/'config.json').read_text());validate(OPS[op],payload,cfg)
 port=18230 if op=='browse_page' else 18232
 # Readiness checked before POST. After ambiguous submission never retry/fallback.
 cap=request(port,'/health')['capabilities'].get(OPS[op],{})
 if not cap.get('ready'):raise RPCError('media_unavailable')
 job=request(port,'/v1/jobs',{'skill_id':OPS[op],'input':payload},secrets.token_hex(24));jid=job.get('job_id')
 if not isinstance(jid,str) or len(jid)!=32 or any(x not in '0123456789abcdef' for x in jid):raise RPCError('media_submission_uncertain')
 start=time.monotonic()
 while time.monotonic()-start<195:
  try:value=request(port,'/v1/jobs/'+jid)
  except RPCError:raise RPCError('media_job_pending') from None
  if value.get('state')=='completed':
   result=validate_result(OPS[op],value.get('result'))
   if op=='upscale_image' and len(base64.b64decode(result['image_base64']))>16*1024*1024:raise RPCError('media_output_limit')
   return {'ok':True,'status':'ok','provider':'Harmonika Ai',**result}
  if value.get('state') in ('failed','unknown'):
   code=value.get('error_type');raise RPCError(code if isinstance(code,str) and code.isidentifier() and len(code)<80 else 'media_failed')
  time.sleep(.25)
 raise RPCError('media_job_pending')

def safe_dispatch(data):
 try:return dispatch(data)
 except (RPCError,Failure) as e:
  uncertain=e.code in ('media_submission_uncertain','media_job_pending')
  return {'ok':False,'status':'degraded','error':{'code':e.code},'retry_safe':not uncertain,'retryable':False,'operation_state':'uncertain' if uncertain else 'failed'}
 except Exception:return {'ok':False,'status':'degraded','error':{'code':'media_unavailable'},'retry_safe':False,'retryable':False,'operation_state':'uncertain'}
