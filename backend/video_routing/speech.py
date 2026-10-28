"""Local CPU speech. Input text stays in RAM; private temporary WAV is removed."""
import importlib.util,base64,hashlib,io,json,shutil,subprocess,tempfile,wave,urllib.request,urllib.error
from pathlib import Path
class SpeechFailure(Exception):pass
POLICY=Path('/etc/harmonika-ai/speech-policy.json')
NEURAL_TOKEN=Path('/etc/harmonika-ai/neural-speech.token')
def policy():
 try:
  cfg=json.loads(POLICY.read_text())
  if cfg.get('default_quality') not in ['basic','neural'] or cfg.get('default_voice') not in ['wanita','pria','wanita-2','pria-2']:raise ValueError()
  return cfg
 except FileNotFoundError:return {'default_quality':'basic','default_voice':'wanita'}
 except Exception:raise SpeechFailure('speech_policy_invalid') from None

def neural_call(path,payload=None):
 try:
  req=urllib.request.Request('http://127.0.0.1:18256'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Authorization':'Bearer '+NEURAL_TOKEN.read_text().strip(),'Content-Type':'application/json'})
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=16) as response:raw=response.read(8*1024*1024+1)
  if len(raw)>8*1024*1024:raise SpeechFailure('audio_output_limit')
  data=json.loads(raw)
  if not isinstance(data,dict) or not data.get('ok'):raise SpeechFailure('neural_speech_unavailable')
  return data
 except urllib.error.HTTPError as e:raise SpeechFailure('speech_busy' if e.code==429 else 'neural_speech_unavailable') from None
 except (OSError,ValueError):raise SpeechFailure('neural_speech_unavailable') from None

def normalize_neural(result):
 raw=base64.b64decode(result['audio_base64'],validate=True)
 if len(raw)!=result.get('bytes') or hashlib.sha256(raw).hexdigest()!=result.get('sha256') or not raw.startswith(b'RIFF'):raise SpeechFailure('invalid_audio_artifact')
 with tempfile.TemporaryDirectory(prefix='harmonika-neural-level-') as temp:
  source=Path(temp)/'source.wav';output=Path(temp)/'result.wav';source.write_bytes(raw)
  x=subprocess.run(['ffmpeg','-v','error','-nostdin','-i',str(source),'-af','loudnorm=I=-16:TP=-1.5:LRA=11','-ar','44100','-ac','1','-c:a','pcm_s16le','-metadata','comment=Suara sintetis Harmonika Ai',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
  if x.returncode or not output.is_file() or output.stat().st_size>5*1024*1024:raise SpeechFailure('speech_normalization_failed')
  normalized=output.read_bytes()
 return {**result,'audio_base64':base64.b64encode(normalized).decode(),'bytes':len(normalized),'sha256':hashlib.sha256(normalized).hexdigest(),'loudness_normalized':True}

def capabilities():
 cfg=policy();ready=False
 try:ready=neural_call('/health').get('ready',False)
 except SpeechFailure:pass
 return {'ok':True,'provider':'Harmonika Ai','speech_synthesis':ready if cfg['default_quality']=='neural' else bool(shutil.which('espeak-ng')),'neural_speech_ready':ready,'languages':['id','en'],'voice_quality':'neural_synthetic' if cfg['default_quality']=='neural' else 'synthetic','default_quality':cfg['default_quality'],'default_voice':cfg['default_voice'],'voices':['wanita','wanita-2','pria','pria-2'],'voice_labels':{'wanita':'Wanita 1','wanita-2':'Wanita 2','pria':'Pria 1','pria-2':'Pria 2'},'approved_voices':cfg.get('approved_voices',[]),'voice_selection':'follow_request_or_context_keep_consistent_within_narration','audio_formats':['wav'],'local_cpu':True,'basic_fallback_automatic':False,'styles':['normal','promosi_ceria'],'native_emotion_control':False}
def generate(data):
 if set(data)-{'operation','owner_context','text','language','speed','voice','quality','style'}:raise SpeechFailure('invalid_request')
 text=data.get('text');language=data.get('language','id');style=data.get('style','normal');speed=data.get('speed',160 if style=='promosi_ceria' else 155)
 if style not in ['normal','promosi_ceria']:raise SpeechFailure('invalid_speech_style')
 if not isinstance(text,str) or not text.strip() or len(text.encode())>1200 or any(ord(c)<32 and c not in '\n\t' for c in text):raise SpeechFailure('invalid_speech_text')
 if language not in ['id','en'] or type(speed) is not int or not 120<=speed<=220:raise SpeechFailure('invalid_speech_options')
 cfg=policy();quality=data.get('quality',cfg['default_quality']);voice=data.get('voice',cfg['default_voice'])
 if quality not in ['neural','basic'] or voice not in ['wanita','pria','wanita-2','pria-2']:raise SpeechFailure('invalid_speech_options')
 if quality=='neural':
  pace=max(.8,min(1.4,speed/155*1.05))
  if style=='promosi_ceria':
   spec=importlib.util.spec_from_file_location('harmonika_prosody',Path(__file__).resolve().parent/'prosody.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   try:return normalize_neural(module.promotional(text,language,voice,pace,neural_call))
   except ValueError as e:raise SpeechFailure(str(e) if str(e) in ['promotional_text_too_long','invalid_audio_artifact','audio_output_limit'] else 'speech_style_failed') from None
  return {**normalize_neural(neural_call('/speech',{'text':text,'language':language,'voice':voice,'speed':pace})),'style':'normal','native_emotion_control':False}
 if style!='normal':raise SpeechFailure('speech_style_requires_neural')
 # The basic voice is available only when explicitly selected or prior to migration.
 binary=shutil.which('espeak-ng')
 if not binary:raise SpeechFailure('speech_unavailable')
 with tempfile.TemporaryDirectory(prefix='harmonika-speech-') as temp:
  file=Path(temp)/'speech.wav'
  result=subprocess.run([binary,'-v',language,'-s',str(speed),'-w',str(file),'--stdin'],input=text.encode(),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15,check=False)
  if result.returncode!=0 or not file.is_file() or not 44<file.stat().st_size<=5*1024*1024:raise SpeechFailure('speech_failed')
  raw=file.read_bytes()
 with wave.open(io.BytesIO(raw)) as audio:
  duration=audio.getnframes()/audio.getframerate();rate=audio.getframerate();channels=audio.getnchannels();frames=audio.readframes(audio.getnframes())
 if not 0<duration<=90 or not any(frames):raise SpeechFailure('invalid_audio_artifact')
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','audio_base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'duration_seconds':round(duration,3),'sample_rate':rate,'channels':channels,'language':language,'mime_type':'audio/wav','voice_quality':'synthetic'}
