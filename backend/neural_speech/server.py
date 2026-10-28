"""Local neural CPU voice service: authenticated, serial, bounded and no text logs."""
import base64,hashlib,hmac,io,json,logging,os,secrets,threading,time,wave
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
os.environ.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1')
logging.disable(logging.CRITICAL)
ROOT=Path('/opt/harmonika-ai/neural-speech');VOICES={'wanita':'F1','pria':'M1','wanita-2':'F2','pria-2':'M2'}
LOCK=threading.Lock();MAX=8192

def validate(data):
 if type(data) is not dict or set(data)-{'text','language','voice','speed'}:raise ValueError('invalid_request')
 text=data.get('text');language=data.get('language','id');voice=data.get('voice','wanita');speed=data.get('speed',1.05)
 if not isinstance(text,str) or not text.strip() or len(text.encode())>1200 or any(ord(c)<32 and c not in '\n\t' for c in text):raise ValueError('invalid_speech_text')
 if language not in ['id','en'] or voice not in VOICES or type(speed) not in [int,float] or not .8<=speed<=1.4:raise ValueError('invalid_speech_options')
 # Bundled presets only. Reference voice files/URLs and user speaker identities are unsupported.
 return text,language,voice,float(speed)

def synthesize(tts,data):
 import numpy as np,soundfile as sf
 text,language,voice,speed=validate(data)
 np.random.seed(20261028)
 wav,duration=tts.synthesize(text,voice_style=tts.get_voice_style(VOICES[voice]),lang=language,speed=speed,total_steps=12,silence_duration=.22,verbose=False)
 audio=wav[0];duration=len(audio)/tts.sample_rate
 if not np.isfinite(audio).all() or not 0<duration<=55 or float(np.sqrt(np.mean(audio**2)))<1e-5:raise ValueError('invalid_audio_artifact')
 out=io.BytesIO();sf.write(out,audio,tts.sample_rate,format='WAV',subtype='PCM_16');raw=out.getvalue()
 if len(raw)>5*1024*1024:raise ValueError('audio_output_limit')
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','audio_base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'mime_type':'audio/wav','duration_seconds':round(duration,3),'sample_rate':tts.sample_rate,'channels':1,'language':language,'voice':voice,'voice_quality':'neural_synthetic','synthetic_audio':True,'disclosure':'Suara sintetis dibuat oleh Harmonika Ai.'}

def main():
 from supertonic import TTS
 token=Path('/etc/harmonika-ai/neural-speech.token').read_text().strip();assert len(token)==64
 manifest=json.loads((ROOT/'models-manifest.json').read_text())
 for row in manifest['files']:
  file=ROOT/'assets'/row['path'];assert file.is_file() and file.stat().st_size==row['bytes'] and hashlib.sha256(file.read_bytes()).hexdigest()==row['sha256'],'model_integrity_failed'
 tts=TTS(model='supertonic-3',model_dir=ROOT/'assets',auto_download=False,intra_op_num_threads=4,inter_op_num_threads=1)
 class Handler(BaseHTTPRequestHandler):
  timeout=15
  def log_message(self,*args):pass
  def response(self,status,data):
   raw=json.dumps(data,separators=(',',':')).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
  def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token)
  def do_GET(self):
   if not self.authorized():return self.response(401,{'ok':False,'error':{'code':'unauthorized'}})
   if self.path!='/health':return self.response(404,{'ok':False})
   return self.response(200,{'ok':True,'ready':True,'speech_synthesis':True,'languages':['id','en'],'voices':list(VOICES),'voice_quality':'neural_synthetic','sample_rate':tts.sample_rate,'busy':LOCK.locked(),'local_cpu':True})
  def do_POST(self):
   if not self.authorized():return self.response(401,{'ok':False,'error':{'code':'unauthorized'}})
   if self.path!='/speech':return self.response(404,{'ok':False})
   try:
    n=int(self.headers.get('Content-Length','0'))
    if not 0<n<=MAX:raise ValueError('invalid_request')
    data=json.loads(self.rfile.read(n));validate(data)
   except Exception:return self.response(400,{'ok':False,'error':{'code':'invalid_speech_input'}})
   if not LOCK.acquire(blocking=False):return self.response(429,{'ok':False,'error':{'code':'speech_busy'}})
   start=time.monotonic()
   try:out=synthesize(tts,data)
   except Exception:out={'ok':False,'error':{'code':'neural_speech_failed'}}
   finally:LOCK.release()
   # Only system mechanics: no text, language content, audio, names or payloads.
   print(json.dumps({'event':'speech_terminal','ok':out['ok'],'latency_ms':round((time.monotonic()-start)*1000)}),flush=True)
   return self.response(200 if out['ok'] else 503,out)
 class Server(ThreadingHTTPServer):
  daemon_threads=True
  def handle_error(self,*args):print('{"event":"speech_transport_error"}',flush=True)
 print('{"event":"neural_speech_ready","local_cpu":true}',flush=True)
 Server(('127.0.0.1',18256),Handler).serve_forever()
if __name__=='__main__':main()
