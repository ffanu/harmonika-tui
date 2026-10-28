"""Harmonika sequential-loading adapter for FramePack-F1.
Sampling/history follows lllyasviel/FramePack demo_gradio_f1.py @97fe5dbe.
Modified: no UI/network; release text/image encoders before loading transformer.
Upstream source and license are preserved unmodified in the engine app directory.
"""
import gc,json,math,os,sys,time
from pathlib import Path
ROOT=Path(r'D:\HarmonikaAI\video-toolkit')
os.environ.update(HF_HOME=str(ROOT/'models'),HF_HUB_CACHE=str(ROOT/'models/hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')

JOBS=Path(r'C:\HarmonikaVideo\jobs')
def snapshot(repo):
 rows=json.loads((Path(__file__).with_name('models.json')).read_text());revisions={r['revision'] for r in rows if r['repo']==repo};assert len(revisions)==1
 rev=revisions.pop();hub=ROOT/'models/hub'/('models--'+repo.replace('/','--'));assert len(rev)==40
 return hub/'snapshots'/rev

def mechanical_error_code(exc):
 if isinstance(exc,AssertionError) and str(exc) in ['gpu_busy','system_memory_low']:return str(exc)
 if type(exc).__name__=='OutOfMemoryError':return 'insufficient_vram'
 return 'inference_failed'

def run(job):
 import torch,numpy as np
 from PIL import Image
 sys.path.insert(0,str(Path(r'C:\HarmonikaVideo\framepack\app')))
 from diffusers import AutoencoderKLHunyuanVideo
 from transformers import LlamaModel,CLIPTextModel,LlamaTokenizerFast,CLIPTokenizer,SiglipImageProcessor,SiglipVisionModel
 from diffusers_helper.hunyuan import encode_prompt_conds,vae_encode,vae_decode
 from diffusers_helper.clip_vision import hf_clip_vision_encode
 from diffusers_helper.bucket_tools import find_nearest_bucket
 from diffusers_helper.utils import crop_or_pad_yield_mask,resize_and_center_crop,soft_append_bcthw,save_bcthw_as_mp4
 from diffusers_helper.models.hunyuan_video_packed import HunyuanVideoTransformer3DModelPacked
 from diffusers_helper.pipelines.k_diffusion_hunyuan import sample_hunyuan
 from diffusers_helper.memory import DynamicSwapInstaller,fake_diffusers_current_device,move_model_to_device_with_memory_preservation,offload_model_from_device_for_memory_preservation
 cfg=json.loads((job/'input.json').read_text());prompt=cfg['prompt'];seconds=cfg.get('duration_seconds',1);steps=cfg.get('steps',25)
 assert isinstance(prompt,str) and 0<len(prompt.encode())<=8000 and type(seconds) is int and 1<=seconds<=10 and type(steps) is int and 8<=steps<=30
 import psutil
 assert psutil.virtual_memory().available>=30*1024**3,'system_memory_low'
 assert torch.cuda.is_available() and torch.cuda.mem_get_info()[0]>=6*1024**3,'gpu_busy'
 torch.set_num_threads(4);torch.manual_seed(20261027);torch.cuda.manual_seed_all(20261027);gpu=torch.device('cuda');dtype=torch.bfloat16
 def stage(name,**extra):
  path=job/'progress.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps({'stage':name,**extra}));os.replace(tmp,path)
 def release():gc.collect();torch.cuda.empty_cache()
 base=snapshot('hunyuanvideo-community/HunyuanVideo');redux=snapshot('lllyasviel/flux_redux_bfl')
 stage('text_encoding')
 te=LlamaModel.from_pretrained(base,subfolder='text_encoder',torch_dtype=torch.float16,local_files_only=True).cpu().eval().requires_grad_(False)
 te2=CLIPTextModel.from_pretrained(base,subfolder='text_encoder_2',torch_dtype=torch.float16,local_files_only=True).to(gpu).eval().requires_grad_(False)
 tok=LlamaTokenizerFast.from_pretrained(base,subfolder='tokenizer',local_files_only=True);tok2=CLIPTokenizer.from_pretrained(base,subfolder='tokenizer_2',local_files_only=True)
 DynamicSwapInstaller.install_model(te,device=gpu);fake_diffusers_current_device(te,gpu)
 vec,pool=encode_prompt_conds(prompt,te,te2,tok,tok2)
 vec,mask=crop_or_pad_yield_mask(vec,length=512)
 vec,pool,mask=vec.cpu(),pool.cpu(),mask.cpu()
 del te,te2,tok,tok2;release()
 stage('image_encoding')
 pil_image=Image.open(job/'input.png');assert pil_image.width*pil_image.height<=4_000_000 and pil_image.format in ['PNG','JPEG','WEBP']
 image=np.array(pil_image.convert('RGB'));h,w=find_nearest_bucket(*image.shape[:2],resolution=640)
 image=resize_and_center_crop(image,target_width=w,target_height=h)
 vae=AutoencoderKLHunyuanVideo.from_pretrained(base,subfolder='vae',torch_dtype=torch.float16,local_files_only=True).to(gpu).eval().requires_grad_(False)
 vae.enable_slicing();vae.enable_tiling()
 pixels=torch.from_numpy(image).float()/127.5-1;pixels=pixels.permute(2,0,1)[None,:,None]
 start=vae_encode(pixels,vae).cpu();vae.to('cpu');del pixels;release()
 feature=SiglipImageProcessor.from_pretrained(redux,subfolder='feature_extractor',local_files_only=True)
 ie=SiglipVisionModel.from_pretrained(redux,subfolder='image_encoder',torch_dtype=torch.float16,local_files_only=True).to(gpu).eval().requires_grad_(False)
 image_out=hf_clip_vision_encode(image,feature,ie);image_embed=image_out.last_hidden_state.cpu().to(dtype)
 del ie,image_out,feature;release()
 stage('loading_video_model')
 transformer=HunyuanVideoTransformer3DModelPacked.from_pretrained(snapshot('lllyasviel/FramePack_F1_I2V_HY_20250503'),torch_dtype=dtype,local_files_only=True).cpu().eval().requires_grad_(False)
 transformer.high_quality_fp32_output_for_inference=True
 DynamicSwapInstaller.install_model(transformer,device=gpu)
 vec,pool,image_embed=vec.to(gpu,dtype),pool.to(gpu,dtype),image_embed.to(gpu,dtype);mask=mask.to(gpu)
 negative=torch.zeros_like(vec);negative_pool=torch.zeros_like(pool)
 window=9;sections=math.ceil((seconds*30-1)/(window*4));history=torch.zeros((1,16,19,h//8,w//8),dtype=torch.float32)
 history=torch.cat([history,start.to(history)],dim=2);total=1;history_pixels=None;rng=torch.Generator('cpu').manual_seed(20261027)
 for index in range(sections):
  vae.to('cpu');release();move_model_to_device_with_memory_preservation(transformer,target_device=gpu,preserved_memory_gb=8)
  transformer.initialize_teacache(enable_teacache=False)
  indices=torch.arange(1+16+2+1+window).unsqueeze(0)
  i0,i4,i2,i1,latent_indices=indices.split([1,16,2,1,window],dim=1)
  l4,l2,l1=history[:,:,-19:].split([16,2,1],dim=2)
  clean=torch.cat([start.to(history),l1],dim=2)
  def callback(data):stage('sampling',section=index+1,sections=sections,step=int(data['i'])+1,steps=steps)
  generated=sample_hunyuan(transformer=transformer,sampler='unipc',width=w,height=h,frames=window*4-3,real_guidance_scale=1,distilled_guidance_scale=10,guidance_rescale=0,num_inference_steps=steps,generator=rng,prompt_embeds=vec,prompt_embeds_mask=mask,prompt_poolers=pool,negative_prompt_embeds=negative,negative_prompt_embeds_mask=mask,negative_prompt_poolers=negative_pool,device=gpu,dtype=dtype,image_embeddings=image_embed,latent_indices=latent_indices,clean_latents=clean,clean_latent_indices=torch.cat([i0,i1],dim=1),clean_latents_2x=l2,clean_latent_2x_indices=i2,clean_latents_4x=l4,clean_latent_4x_indices=i4,callback=callback)
  total+=int(generated.shape[2]);history=torch.cat([history,generated.to(history)],dim=2);del generated
  stage('decoding',section=index+1,sections=sections)
  offload_model_from_device_for_memory_preservation(transformer,target_device=gpu,preserved_memory_gb=8);vae.to(gpu)
  real=history[:,:,-total:]
  if history_pixels is None:history_pixels=vae_decode(real,vae).cpu()
  else:history_pixels=soft_append_bcthw(history_pixels,vae_decode(real[:,:,-window*2:],vae).cpu(),window*4-3)
  vae.to('cpu');release()
 stage('encoding')
 assert history_pixels.shape[2]>=seconds*30
 save_bcthw_as_mp4(history_pixels[:,:,:seconds*30],str(job/'result.mp4'),fps=30,crf=19)
 import av,hashlib
 with av.open(str(job/'result.mp4')) as media:
  video=media.streams.video[0];count=sum(1 for _ in media.decode(video=0));duration=float(video.duration*video.time_base) if video.duration is not None else count/30
 assert count==seconds*30 and abs(duration-seconds)<.05
 result={'status':'completed','frames':count,'duration_seconds':duration,'width':w,'height':h,'audio':False,'steps':steps,'sha256':hashlib.sha256((job/'result.mp4').read_bytes()).hexdigest()}
 (job/'result.json').write_text(json.dumps(result,indent=2));stage('completed');return result

if __name__=='__main__':
 import traceback
 job=Path(sys.argv[1]);assert job.parent==JOBS and len(job.name)==32
 # Suppress native model prompts/traces. Output only controlled mechanical metadata.
 try:
  import torch
  with torch.inference_mode():run(job)
 except Exception as exc:
  frames=traceback.extract_tb(exc.__traceback__);last=frames[-1] if frames else None
  (job/'result.json').write_text(json.dumps({'status':'failed','error_code':mechanical_error_code(exc),'error_type':type(exc).__name__,'function':last.name if last else None,'line':last.lineno if last else None,'missing_module':getattr(exc,'name',None) if isinstance(exc,ModuleNotFoundError) else None}))
  raise SystemExit(1)
