"""Mux verified video and real TTS, without trimming speech or inventing duration."""
import base64,hashlib,json,shutil,subprocess,tempfile
from pathlib import Path
class NarrationFailure(Exception):pass
def combine(raw,speech,duration):
 if not isinstance(duration,(int,float)) or not 0<duration<=10:raise NarrationFailure('invalid_video_duration')
 if speech.get('duration_seconds',999)>duration+.05:raise NarrationFailure('narration_too_long')
 if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):raise NarrationFailure('audio_mux_unavailable')
 with tempfile.TemporaryDirectory(prefix='harmonika-narration-') as temp:
  root=Path(temp);video=root/'video.mp4';audio=root/'speech.wav';output=root/'result.mp4';video.write_bytes(raw);audio.write_bytes(base64.b64decode(speech['audio_base64'],validate=True))
  cmd=['ffmpeg','-hide_banner','-v','error','-nostdin','-i',str(video),'-i',str(audio),'-filter_complex','[1:a]apad[a]','-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','128k','-t',str(duration),'-map_metadata','-1','-movflags','+faststart',str(output)]
  x=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=45)
  if x.returncode or not output.is_file() or not 100<=output.stat().st_size<=8*1024*1024:raise NarrationFailure('audio_mux_failed')
  x=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height:format=duration','-of','json',str(output)],capture_output=True,timeout=10,check=True)
  meta=json.loads(x.stdout);streams=meta.get('streams',[]);v=next((s for s in streams if s.get('codec_type')=='video'),None)
  if not v or not any(s.get('codec_type')=='audio' for s in streams) or abs(float(meta['format']['duration'])-duration)>.15:raise NarrationFailure('invalid_narrated_artifact')
  result=output.read_bytes()
 return {'ok':True,'status':'ok','provider':'Harmonika Ai','video_base64':base64.b64encode(result).decode(),'bytes':len(result),'sha256':hashlib.sha256(result).hexdigest(),'mime_type':'video/mp4','duration_seconds':duration,'width':v['width'],'height':v['height'],'audio':True,'voice_quality':speech.get('voice_quality','synthetic'),'voice':speech.get('voice'),'style':speech.get('style','normal'),'native_emotion_control':False,'synthetic_audio':True,'disclosure':'Narasi sintetis dibuat oleh Harmonika Ai.'}
