import importlib.util,json,os,secrets,subprocess,time,traceback
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;JOBS=Path(r'C:\HarmonikaVideo\jobs');STATE=CONTROL/'wangp-10s-qualification.json'
def status(phase,**extra):
 p=STATE.with_suffix('.tmp');p.write_text(json.dumps({'phase':phase,'timestamp':time.time(),'customer_data_used':False,'profile':{'duration_seconds':10,'resolution':'480x272','steps':20},**extra}));os.replace(p,STATE)
def main():
 import msvcrt
 guard=(CONTROL/'gpu.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
 try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:status('failed',error_type='gpu_busy');return
 job=JOBS/secrets.token_hex(16);job.mkdir(parents=True)
 (job/'input.json').write_text(json.dumps({'prompt':'A white wireless router on a wooden desk in a bright modern home. Slow subtle cinematic camera movement. Natural daylight, realistic neutral colors, no text, no logos.','duration_seconds':10,'resolution':'480x272','steps':20}))
 status('running',job_id=job.name);start=time.monotonic();child=None;box=None
 try:
  spec=importlib.util.spec_from_file_location('owned',CONTROL/'owned_job.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  env={k:v for k,v in os.environ.items() if k not in ['PYTHONPATH','PYTHONHOME','PYTHONSTARTUP','HF_TOKEN'] and not any(x in k.upper() for x in ['API_KEY','ACCESS_TOKEN','PASSWORD','SECRET'])};env.update(HF_HUB_OFFLINE='1',DO_NOT_TRACK='1')
  child=subprocess.Popen([r'C:\HarmonikaVideo\wangp\.venv\Scripts\python.exe','-I',str(CONTROL/'wangp_runner.py'),str(job)],cwd=CONTROL,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=0x4);box=m.WindowsJob(child)
  while child.poll() is None:
   if time.monotonic()-start>1800:raise TimeoutError()
   time.sleep(2)
  result=json.loads((job/'result.json').read_text());assert result.get('status')=='completed'
  assert result['duration_seconds']==10 and result['frames']>=160
  status('verified',job_id=job.name,latency_seconds=round(time.monotonic()-start,2),result=result)
 except Exception as e:status('failed',job_id=job.name,error_type=type(e).__name__,latency_seconds=round(time.monotonic()-start,2))
 finally:
  if box:box.close()
  if child and child.poll() is None:child.kill();child.wait(10)
  (job/'input.json').unlink(missing_ok=True);guard.close()
if __name__=='__main__':main()
