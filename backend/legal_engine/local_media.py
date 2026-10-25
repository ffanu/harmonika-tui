"""Tool-free isolated local media adapter. Private Unix socket, never an external model SDK."""
import base64,http.client,json,socket
from pathlib import Path
from .common import DomainError,require
SOCKET='/run/harmonika-legal-media/media.sock'
class UnixHTTP(http.client.HTTPConnection):
 def connect(self):self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(SOCKET)
class LocalMedia:
 public_safe=True
 def __init__(self):
  self.ready=False;self.asr_ready=False;self.video_ready=False;self.revision=''
  try:
   v=self._call('GET','/healthz');self.ready=v.get('ready') is True and v.get('isolated') is True and v.get('cpu_only') is True;self.asr_ready=self.ready and v.get('asr') is True;self.video_ready=self.ready and v.get('video') is True;self.revision=v.get('model_manifest_sha256','')+v.get('worker_revision','')
  except Exception:pass
 def _call(self,method,path,payload=None,probe=False):
  conn=UnixHTTP('localhost',timeout=90 if method=='POST' else 2)
  try:
   secret=Path('/etc/harmonika-ai/legal-media.token').read_text().strip();headers={'X-Harmonika-Media-Secret':secret,'Content-Type':'application/json'};body=json.dumps(payload).encode() if payload is not None else None
   if probe:headers['X-Harmonika-Media-Probe']='1'
   conn.request(method,path,body=body,headers=headers);res=conn.getresponse();raw=res.read(262145);require(len(raw)<=262144,'media_output_limit',502);v=json.loads(raw)
   if res.status!=200:raise DomainError(v.get('error',{}).get('code','media_unavailable'),res.status)
   require(type(v) is dict,'media_invalid_response',502);return v
  except DomainError:raise
  except Exception:raise DomainError('media_unavailable',503) from None
  finally:conn.close()
 def analyze(self,raw,mime):
  require(self.ready,'vision_unavailable',503);v=self._call('POST','/vision',{'bytes_base64':base64.b64encode(raw).decode(),'mime_type':mime});require(v.get('analysis_performed') is True and type(v.get('observations')) is dict,'vision_unavailable',503);return {**v,'source':'isolated_local_observer','legal_fact_verified':False,'forensic_authenticity_verified':False,'requires_human_confirmation':True}
 def transcribe(self,raw,mime,language=None):
  require(self.asr_ready,'asr_unavailable',503);v=self._call('POST','/asr',{'bytes_base64':base64.b64encode(raw).decode(),'mime_type':mime,'language':language});require(v.get('analysis_performed') is True and type(v.get('transcript')) is str and v['transcript'].strip(),'asr_unavailable',503);return {**v,'source':'isolated_local_transcriber','legal_fact_verified':False,'speaker_identity_verified':False,'requires_human_confirmation':True}
