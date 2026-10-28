"""Private research adapter for MIT MMAudio code / CC-BY-NC official weights.
Uses pinned offline weights, no prompt logging and no native CLI prompt filenames.
"""
import json,os,sys,time,traceback
from pathlib import Path
ROOT=Path(r'D:\HarmonikaAI\video-toolkit');APP=Path(r'C:\HarmonikaVideo\mmaudio\app')
os.environ.update(HF_HOME=str(ROOT/'models'),HF_HUB_CACHE=str(ROOT/'models/hub'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
JOBS=Path(r'C:\HarmonikaVideo\jobs')
def run(job):
 assert os.environ.get('HARMONIKA_MMAUDIO_RESEARCH')=='1','license_restricted'
 import torch,numpy as np,soundfile as sf
 sys.path.insert(0,str(APP));os.chdir(APP)
 from mmaudio.eval_utils import all_model_cfg,generate,load_video,make_video
 from mmaudio.model.flow_matching import FlowMatching
 from mmaudio.model.networks import get_my_mmaudio
 from mmaudio.model.utils.features_utils import FeaturesUtils
 cfg=json.loads((job/'input.json').read_text());prompt=cfg['prompt'];duration=cfg.get('duration_seconds',8)
 assert isinstance(prompt,str) and 0<len(prompt.encode())<=8000 and type(duration) is int and 1<=duration<=10
 torch.set_num_threads(4);device='cuda';dtype=torch.bfloat16
 model=all_model_cfg['large_44k_v2'];seq=model.seq_cfg
 # All expected model files are linked from SHA-verified manifests. No download_if_needed.
 for path in [model.model_path,model.vae_path,model.synchformer_ckpt]:assert Path(path).is_file()
 net=get_my_mmaudio(model.model_name).to(device,dtype).eval();net.load_weights(torch.load(model.model_path,map_location=device,weights_only=True))
 features=FeaturesUtils(tod_vae_ckpt=model.vae_path,synchformer_ckpt=model.synchformer_ckpt,enable_conditions=True,mode=model.mode,bigvgan_vocoder_ckpt=model.bigvgan_16k_path,need_vae_encoder=False).to(device,dtype).eval()
 video=None
 if (job/'input.mp4').is_file():
  video=load_video(job/'input.mp4',duration);duration=video.duration_sec;clip=video.clip_frames.unsqueeze(0);sync=video.sync_frames.unsqueeze(0)
 else:clip=sync=None
 seq.duration=duration;net.update_seq_lengths(seq.latent_seq_len,seq.clip_seq_len,seq.sync_seq_len)
 rng=torch.Generator(device=device).manual_seed(20261027);fm=FlowMatching(min_sigma=0,inference_mode='euler',num_steps=25)
 audios=generate(clip,sync,[prompt],negative_text=['distortion, clipping, speech'],feature_utils=features,net=net,fm=fm,rng=rng,cfg_strength=4.5)
 audio=audios.float().cpu()[0];sf.write(str(job/'result.wav'),audio.numpy().T,seq.sampling_rate,subtype='PCM_16')
 if video is not None:make_video(video,job/'result.mp4',audio,sampling_rate=seq.sampling_rate)
 data,rate=sf.read(job/'result.wav');assert np.isfinite(data).all() and np.sqrt(np.mean(data**2))>1e-5 and abs(len(data)/rate-duration)<.2
 result={'status':'completed','sample_rate':rate,'duration_seconds':len(data)/rate,'channels':data.shape[1] if data.ndim>1 else 1,'research_only':True,'commercial_enabled':False,'video_composited':video is not None}
 (job/'result.json').write_text(json.dumps(result,indent=2));return result
if __name__=='__main__':
 job=Path(sys.argv[1]);assert job.parent==JOBS and len(job.name)==32
 try:
  import torch
  with torch.inference_mode():run(job)
 except Exception as exc:
  frames=traceback.extract_tb(exc.__traceback__);last=frames[-1] if frames else None
  (job/'result.json').write_text(json.dumps({'status':'failed','error_type':type(exc).__name__,'function':last.name if last else None,'line':last.lineno if last else None,'missing_module':getattr(exc,'name',None) if isinstance(exc,ModuleNotFoundError) else None,'research_only':True}));raise SystemExit(1)
