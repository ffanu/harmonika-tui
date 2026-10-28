"""Private in-house adapter to pinned WanGP. Not enabled as a paid/public API.
WanGP Community License2.0 is preserved in its source; model = Wan1.3B Apache2.
"""
import json,math,os,sys,traceback,hashlib,subprocess,shutil
from pathlib import Path
ROOT=Path(r'D:\HarmonikaAI\video-toolkit');APP=Path(r'C:\HarmonikaVideo\wangp\app');CONTROL=Path(__file__).resolve().parent
os.environ.update(HF_HOME=str(ROOT/'models'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
JOBS=Path(r'C:\HarmonikaVideo\jobs')
def run(job):
 import torch,psutil,av
 assert psutil.virtual_memory().available>=12*1024**3,'system_memory_low'
 assert torch.cuda.is_available() and torch.cuda.mem_get_info()[0]>=5*1024**3,'gpu_busy'
 torch.set_num_threads(4);sys.path.insert(0,str(APP));os.chdir(APP)
 from shared.api import WanGPSession
 data=json.loads((job/'input.json').read_text());prompt=data['prompt'];seconds=data.get('duration_seconds',1);steps=data.get('steps',50)
 assert isinstance(prompt,str) and 0<len(prompt.encode())<=8000 and type(seconds) is int and 1<=seconds<=10 and type(steps) is int and 8<=steps<=50
 config=CONTROL/'wgp_config.json'
 values=json.loads(config.read_text()) if config.exists() else {}
 values.update(transformer_quantization='bf16',text_encoder_quantization='int8',attention_mode='sdpa',profile=4,compile='disabled',vae_precision='16',checkpoints_paths=[str(APP/'ckpts')],save_queue_if_crash=0,metadata_type='none')
 temp=config.with_suffix('.tmp');temp.write_text(json.dumps(values));os.replace(temp,config)
 session=WanGPSession(root=APP,config_path=config,output_dir=job/'native',cli_args=['--offline','--attention','sdpa','--profile','4'],console_output=False,console_isatty=False)
 settings=session.get_default_settings('t2v_1.3B')
 settings.update(model_type='t2v_1.3B',prompt=prompt,prompt_enhancer='',negative_prompt='rainbow, neon colors, color bleeding, distorted objects, text, watermark',resolution=data.get('resolution','832x480'),video_length=4*math.ceil((seconds*16-1)/4)+1,num_inference_steps=steps,seed=20261027,repeat_generation=1)
 result=session.run_task(settings)
 if not result.success:
  # Only the fixed synthetic fixture may carry a bounded technical diagnostic.
  fixture='A white wireless router on a wooden desk in a bright modern home. Static product shot, natural daylight, realistic neutral colors, subtle movement.'
  if prompt==fixture:
   import re
   messages=[re.sub(r'https?://\S+','[url]',str(e.message).replace(prompt,'[fixture]'))[:600] for e in result.errors]
   (job/'synthetic-diagnostic.json').write_text(json.dumps({'technical_errors':messages,'customer_data_used':False}))
  raise RuntimeError('generation_failed')
 files=[Path(f) for f in result.generated_files if str(f).lower().endswith('.mp4')];assert len(files)==1
 source=files[0];assert source.is_relative_to(job/'native')
 ffmpeg=shutil.which('ffmpeg');assert ffmpeg
 # Exact requested duration and strip prompt/model metadata from user artifact.
 subprocess.run([ffmpeg,'-v','error','-i',str(source),'-t',str(seconds),'-an','-map_metadata','-1','-c:v','libx264','-crf','19','-movflags','+faststart','-y',str(job/'result.mp4')],check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 with av.open(str(job/'result.mp4')) as f:
  stream=f.streams.video[0];count=sum(1 for _ in f.decode(video=0));duration=float(f.duration/av.time_base);w,h=stream.width,stream.height
 assert abs(duration-seconds)<.1 and count>=seconds*16
 out={'status':'completed','width':w,'height':h,'duration_seconds':duration,'frames':count,'audio':False,'steps':steps,'private_in_house_only':True,'public_route_enabled':False,'sha256':hashlib.sha256((job/'result.mp4').read_bytes()).hexdigest()}
 (job/'result.json').write_text(json.dumps(out,indent=2));return out
if __name__=='__main__':
 job=Path(sys.argv[1]);assert job.parent==JOBS and len(job.name)==32
 try:run(job)
 except Exception as exc:
  frames=traceback.extract_tb(exc.__traceback__);last=frames[-1] if frames else None
  (job/'result.json').write_text(json.dumps({'status':'failed','error_type':type(exc).__name__,'function':last.name if last else None,'line':last.lineno if last else None,'missing_module':getattr(exc,'name',None) if isinstance(exc,ModuleNotFoundError) else None}));raise SystemExit(1)
