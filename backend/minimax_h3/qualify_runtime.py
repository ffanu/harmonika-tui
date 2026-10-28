"""Owned synthetic render qualification on a private isolated ComfyUI instance."""
import ctypes,json,os,re,socket,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'qualification-status.json'
class QualificationError(Exception):pass

def state(phase,**data):
 value={'phase':phase,'timestamp':time.time(),'public_route_registered':False,'customer_data_used':False,**data};tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2));os.replace(tmp,STATE)
def request(port,path,body=None,timeout=5):
 assert type(port) is int and 1024<=port<=65535 and port!=3000
 raw=json.dumps(body).encode() if body is not None else None
 req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=raw,headers={'Content-Type':'application/json'})
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=timeout) as response:data=response.read(16*1024*1024+1)
 if len(data)>16*1024*1024:raise QualificationError('local_response_limit')
 return json.loads(data)
def memory_safe():
 class Memory(ctypes.Structure):
  _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),('physical_total',ctypes.c_ulonglong),('physical_free',ctypes.c_ulonglong),('commit_total',ctypes.c_ulonglong),('commit_free',ctypes.c_ulonglong),('virtual_total',ctypes.c_ulonglong),('virtual_free',ctypes.c_ulonglong),('extended_free',ctypes.c_ulonglong)]
 m=Memory();m.length=ctypes.sizeof(m)
 if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):raise QualificationError('memory_probe_failed')
 return m.physical_free>=2*1024**3 and m.commit_free>=4*1024**3

def artifact_paths(history):
 base=(ROOT/'ComfyUI/output').resolve();paths=[]
 for row in history.get('outputs',{}).values():
  for key in ('videos','gifs','images'):
   for value in row.get(key,[]):
    if value.get('type','output')!='output':continue
    path=(base/value.get('subfolder','')/value['filename']).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() not in ('.mp4','.webm','.mkv'):raise QualificationError('invalid_output_artifact')
    if path not in paths:paths.append(path)
 return paths

def main():
 receipt=json.loads((ROOT/'model-receipt.json').read_text());assert receipt['status']=='core_models_verified'
 for row in receipt['files']:
  file=ROOT/'app/models'/row['file'];assert file.is_file() and not file.is_symlink() and file.stat().st_size==row['bytes']
 s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
 if port==3000:raise QualificationError('forbidden_port')
 config={'h3':{'base_path':str(ROOT/'app/models').replace('\\','/'),'diffusion_models':'diffusion_models','text_encoders':'text_encoders','vae':'vae'}}
 # JSON is a valid YAML subset consumed by ComfyUI's safe YAML loader.
 (ROOT/'extra-model-paths.yaml').write_text(json.dumps(config))
 env=os.environ.copy()
 for key in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP'):env.pop(key,None)
 env.update(PYTHONNOUSERSITE='1',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TRANSFORMERS_OFFLINE='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
 command=[str(ROOT/'.venv/Scripts/python.exe'),'-u',str(ROOT/'ComfyUI/main.py'),'--listen','127.0.0.1','--port',str(port),'--extra-model-paths-config',str(ROOT/'extra-model-paths.yaml'),'--offline','--disable-all-custom-nodes','--lowvram','--reserve-vram','7','--cpu-vae','--cache-none','--disable-smart-memory','--disable-pinned-memory','--disable-metadata']
 log=(ROOT/'comfy-runtime.log').open('ab');child=subprocess.Popen(command,cwd=ROOT/'ComfyUI',env=env,stdout=log,stderr=subprocess.STDOUT)
 state('starting_runtime',port=port,owned_pid=child.pid,technical_render_verified=False)
 try:
  for _ in range(180):
   if child.poll() is not None:raise QualificationError('comfy_start_failed')
   try:request(port,'/system_stats');break
   except Exception:time.sleep(1)
  else:raise QualificationError('comfy_start_timeout')
  info=request(port,'/object_info');workflow=json.loads((ROOT/'smoke-workflow.json').read_text())
  missing=sorted({node['class_type'] for node in workflow.values()}-set(info))
  if missing:raise QualificationError('native_h3_nodes_missing')
  if not memory_safe():raise QualificationError('insufficient_memory_for_qualification')
  result=request(port,'/prompt',{'prompt':workflow,'client_id':'harmonika-h3-owned-qualification'})
  prompt_id=result.get('prompt_id')
  if not prompt_id or result.get('node_errors'):raise QualificationError('workflow_validation_failed')
  state('rendering',port=port,owned_pid=child.pid,prompt_id=prompt_id,technical_render_verified=False)
  started=time.monotonic();terminal=None
  while time.monotonic()-started<1200:
   if child.poll() is not None:raise QualificationError('owned_runtime_exited')
   if not memory_safe():raise QualificationError('memory_guard_triggered')
   stats=request(port,'/system_stats')
   if any(type(d.get('vram_free')) is int and d['vram_free']<512*1024**2 for d in stats.get('devices',[])):raise QualificationError('gpu_headroom_guard_triggered')
   history=request(port,'/history/'+prompt_id)
   if prompt_id in history:
    terminal=history[prompt_id]
    if terminal.get('status',{}).get('status_str')=='error':raise QualificationError('generation_failed')
    break
   time.sleep(3)
  if terminal is None:raise QualificationError('qualification_timeout')
  paths=artifact_paths(terminal)
  if not paths:raise QualificationError('video_artifact_missing')
  import av,hashlib
  artifacts=[]
  for path in paths:
   with av.open(str(path)) as media:
    video=media.streams.video;audio=media.streams.audio
    if not video or not audio:raise QualificationError('video_or_audio_stream_missing')
    first=next(media.decode(video=0));first.to_image().save(ROOT/'qualification-frame.png')
    artifacts.append({'file':str(path),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'width':video[0].width,'height':video[0].height,'audio_channels':len(audio[0].codec_context.layout.channels),'audio_rate':audio[0].codec_context.sample_rate,'duration_seconds':float(media.duration/av.time_base) if media.duration else None})
  state('installed_and_render_tested',port=port,owned_pid=child.pid,technical_render_verified=True,runtime_ready=True,visual_review_pending=True,artifacts=artifacts,render_seconds=round(time.monotonic()-started,2))
  child.wait()
  state('runtime_stopped',technical_render_verified=True,runtime_ready=False,artifacts=artifacts)
 finally:
  if child.poll() is None:
   child.terminate()
   try:child.wait(timeout=30)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=30)
  log.close()
if __name__=='__main__':
 try:main()
 except Exception as exc:
  code=str(exc) if isinstance(exc,QualificationError) else type(exc).__name__
  if not re.fullmatch(r'[A-Za-z_]{1,100}',code):code='qualification_failed'
  state('degraded',error_type=code,technical_render_verified=False)
  raise SystemExit(1)
