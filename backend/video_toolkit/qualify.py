"""Synthetic-only serial qualification. Installed != importable != verified inference."""
import argparse,hashlib,importlib.util,json,os,shutil,subprocess,sys,time,uuid
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ROOT=Path(r'D:\HarmonikaAI\video-toolkit');ENGINES=Path(r'C:\HarmonikaVideo')
JOBS=Path(r'C:\HarmonikaVideo\jobs')
def main():
 p=argparse.ArgumentParser();p.add_argument('engine',choices=['framepack','wangp','mmaudio']);args=p.parse_args();name=args.engine
 assert (CONTROL/(name+'-models.json')).is_file(),'models_not_verified'
 assert json.loads((CONTROL/(name+'-install.json')).read_text())['phase']=='dependencies_installed','dependencies_not_installed'
 import msvcrt
 guard=(CONTROL/'gpu.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
 job=JOBS/uuid.uuid4().hex;job.mkdir(parents=True)
 prompts={'framepack':'A white wireless router on a wooden desk. Subtle slow camera movement. Natural lighting, realistic colors, no colorful light effects.','wangp':'A white wireless router on a wooden desk in a bright modern home. Static product shot, natural daylight, realistic neutral colors, subtle movement.','mmaudio':'Quiet room ambience, soft keyboard tapping, no speech, no music.'}
 payload={'prompt':prompts[name],'duration_seconds':8 if name=='mmaudio' else 1,'steps':25 if name=='framepack' else 50}
 (job/'input.json').write_text(json.dumps(payload))
 # Owned, synthetic fixtures only. Never pull customer attachments/history.
 if name=='framepack':
  fixture=CONTROL/'fixture-router.png';assert fixture.is_file();shutil.copy2(fixture,job/'input.png')
 if name=='mmaudio':
  fixture=CONTROL/'fixture-video.mp4';assert fixture.is_file();shutil.copy2(fixture,job/'input.mp4')
 state=CONTROL/(name+'-qualification.json')
 def status(phase,**extra):
  tmp=state.with_suffix('.tmp');tmp.write_text(json.dumps({'engine':name,'phase':phase,'timestamp':time.time(),'job_id':job.name,'customer_data_used':False,'public_route_registered':False,**extra},indent=2));os.replace(tmp,state)
 status('running')
 env={k:v for k,v in os.environ.items() if k not in ['PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','HF_TOKEN','OPENAI_API_KEY','ANTHROPIC_API_KEY']}
 env.update(HARMONIKA_MMAUDIO_RESEARCH='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
 spec=importlib.util.spec_from_file_location('owned_job',CONTROL/'owned_job.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 start=time.monotonic();child=None;container=None
 try:
  child=subprocess.Popen([str(ENGINES/name/'.venv/Scripts/python.exe'),'-I',str(CONTROL/(name+'_runner.py')),str(job)],cwd=CONTROL,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x4)
  container=module.WindowsJob(child)
  while child.poll() is None:
   if time.monotonic()-start>2400:raise TimeoutError('qualification_timeout')
   time.sleep(2)
  result=json.loads((job/'result.json').read_text());assert result['status']=='completed','inference_failed'
  status('verified',latency_seconds=round(time.monotonic()-start,2),result=result,private_only=True)
 except Exception as exc:
  details={}
  if (job/'result.json').is_file():details=json.loads((job/'result.json').read_text())
  status('failed',error_type=type(exc).__name__,latency_seconds=round(time.monotonic()-start,2),result=details)
 finally:
  if container:container.close()
  if child is not None and child.poll() is None:child.kill();child.wait(10)
  guard.close()
if __name__=='__main__':main()
