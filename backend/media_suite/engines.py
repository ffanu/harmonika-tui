"""Executed in one isolated environment per engine; local model files only."""
import asyncio,base64,io,json,os,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from contracts import Failure,require,binary,validate

def audio_array(encoded,max_seconds):
 import av,numpy as np
 data=binary(encoded)
 # Reject playlists / arbitrary file protocols before decoder selection.
 fmt=None
 if data[:4]==b'RIFF' and data[8:12]==b'WAVE':fmt='wav'
 elif data[:4]==b'fLaC':fmt='flac'
 elif data[:4]==b'OggS':fmt='ogg'
 elif data[:3]==b'ID3' or data[:2] in (b'\xff\xfb',b'\xff\xf3',b'\xff\xf2'):fmt='mp3'
 require(fmt is not None,'unsupported_audio')
 with av.open(io.BytesIO(data),format=fmt,options={'protocol_whitelist':'file,pipe'}) as media:
  require(len(media.streams.audio)==1,'invalid_audio');resampler=av.AudioResampler(format='fltp',layout='mono',rate=16000);chunks=[];samples=0
  for frame in media.decode(audio=0):
   for out in resampler.resample(frame):
    row=out.to_ndarray().reshape(-1);samples+=len(row);require(samples<=max_seconds*16000,'audio_duration_limit');chunks.append(row)
  for out in resampler.resample(None):
   row=out.to_ndarray().reshape(-1);samples+=len(row);require(samples<=max_seconds*16000,'audio_duration_limit');chunks.append(row)
 require(samples>=1600,'invalid_audio');return np.concatenate(chunks),16000

def transcribe(v):
 from faster_whisper import WhisperModel
 samples,rate=audio_array(v['audio_base64'],120)
 model=WhisperModel(str(ROOT/'models/asr'),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
 segments,info=model.transcribe(samples,language=None if v.get('language','auto')=='auto' else v['language'],beam_size=3,vad_filter=True)
 text=' '.join(x.text.strip() for x in segments).strip()
 return {'transcript':text,'language':info.language,'confidence':None,'language_probability':float(info.language_probability),'duration_seconds':round(len(samples)/rate,3),'warnings':[] if text else ['no_speech_detected']}

def upscale(v):
 import torch
 from PIL import Image,ImageOps
 from aura_sr import AuraSR
 require(torch.cuda.is_available(),'gpu_offline')
 free,_=torch.cuda.mem_get_info();require(free>=4*1024**3,'gpu_busy')
 Image.MAX_IMAGE_PIXELS=1048576
 with Image.open(io.BytesIO(binary(v['image_base64']))) as im:
  require(im.format in ('PNG','JPEG','WEBP') and not getattr(im,'is_animated',False),'invalid_image')
  require(64<=im.width<=1024 and 64<=im.height<=1024 and im.width*im.height<=1048576,'image_dimensions_limit');im.verify()
 with Image.open(io.BytesIO(binary(v['image_base64']))) as im:source=ImageOps.exif_transpose(im).convert('RGB')
 model=AuraSR.from_pretrained(str(ROOT/'models/upscale/model.safetensors'))
 with torch.inference_mode():result=model.upscale_4x_overlapped(source,max_batch_size=1)
 require(result.size==(source.width*4,source.height*4),'invalid_output')
 out=io.BytesIO();result.save(out,format='PNG');raw=out.getvalue();require(len(raw)<=24*1024*1024,'output_limit')
 return {'image_base64':base64.b64encode(raw).decode(),'mime_type':'image/png','width':result.width,'height':result.height,'scale':4,'warnings':['Upscaling may synthesize detail; do not use as payment evidence.']}

def synthesize(v):
 import soundfile as sf
 from f5_tts.api import F5TTS
 samples,rate=audio_array(v['reference_audio_base64'],15)
 require(len(samples)/rate>=3,'reference_audio_too_short')
 # Lower-level tensor API avoids TorchCodec/FFmpeg DLL coupling and extra
 # ASR/downloads in the upstream convenience reference preprocessing path.
 import torch
 from f5_tts.infer.utils_infer import infer_batch_process
 torch.manual_seed(20261027)
 model=F5TTS(ckpt_file=str(ROOT/'models/tts_research/model_1250000.safetensors'),vocab_file=str(ROOT/'models/tts_research/vocab.txt'),vocoder_local_path=str(ROOT/'models/vocoder'),device='cpu')
 wav,sr,_=next(infer_batch_process((torch.from_numpy(samples).unsqueeze(0),rate),v['reference_text'],[v['text']],model.ema_model,model.vocoder,mel_spec_type=model.mel_spec_type,progress=None,nfe_step=16,device='cpu'))
 require(0<len(wav)<=sr*60,'audio_duration_limit');out=io.BytesIO();sf.write(out,wav,sr,format='WAV',subtype='PCM_16');raw=out.getvalue()
 return {'audio_base64':base64.b64encode(raw).decode(),'mime_type':'audio/wav','sample_rate':sr,'duration_seconds':round(len(wav)/sr,3),'language':v.get('language','en'),'research_only':True,'warnings':['CC-BY-NC-4.0 weights; commercial/public use disabled.','Indonesian speech is not qualified.']}

async def inspect_browser(v,config):
 # Browser Use owns a new temporary profile. No pre-existing login/cookies/CDP.
 from browser_use import BrowserSession,BrowserProfile
 from browser_use.browser.events import NavigateToUrlEvent
 with tempfile.TemporaryDirectory(prefix='harmonika-browser-') as profile:
  browser=BrowserSession(browser_profile=BrowserProfile(headless=True,chromium_sandbox=True,executable_path=config.get('browser_executable'),args=['--disable-gpu','--disable-software-rasterizer'],user_data_dir=profile,use_cloud=False,allowed_domains=config['browser_allowed_hosts'],block_ip_addresses=True,enable_default_extensions=False,captcha_solver=False,keep_alive=False,disable_security=False))
  try:
   await browser.start()
   await browser.navigate_to(v['url'])
   page=await browser.must_get_current_page()
   for attempt in range(40):
    value=await page.evaluate('() => ({url:location.href,title:document.title,text:(document.body?.innerText||" ").slice(0,10000),ready:document.readyState})')
    document=json.loads(value)
    if document.get('ready')=='complete' and document.get('title') and document.get('url','').startswith(v['url']):break
    await asyncio.sleep(.25)
   else:raise Failure('target_offline')
   tabs=[{'url':document['url'][:2048],'title':document['title'][:500]}]
   return {'pages':tabs,'page_text':document['text'],'read_only':True,'existing_profile_used':False,'agentic_actions_enabled':False,'warnings':['Navigation inspection only; autonomous clicks/forms require a separately audited policy.']}
  finally:await browser.kill()

def main():
 config=json.loads((ROOT/'config.json').read_text());v=json.load(sys.stdin);op=v['skill_id'];payload=validate(op,v['input'],config)
 # No telemetry, automatic model downloads or raw library output in logs.
 os.environ.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TRANSFORMERS_OFFLINE='1',ANONYMIZED_TELEMETRY='false',WANDB_MODE='disabled',WANDB_DISABLED='true',BROWSER_USE_LOGGING_LEVEL='critical',BROWSER_USE_DISABLE_EXTENSIONS='1',BROWSER_USE_DISABLE_UPDATE_CHECK='true')
 import contextlib
 with contextlib.redirect_stdout(sys.stderr):
  if op=='audio.transcribe':result=transcribe(payload)
  elif op=='image.upscale':result=upscale(payload)
  elif op=='audio.synthesize':result=synthesize(payload)
  else:result=asyncio.run(inspect_browser(payload,config))
 print(json.dumps({'status':'completed','result':result}))
if __name__=='__main__':
 try:main()
 except Failure as e:print(json.dumps({'status':'failed','error':{'code':e.code}}));raise SystemExit(1)
 except Exception as e:
  code='gpu_memory_full' if type(e).__name__ in ('OutOfMemoryError','CUDAOutOfMemoryError') else 'engine_unavailable'
  print(json.dumps({'status':'failed','error':{'code':code}}));raise SystemExit(1)
