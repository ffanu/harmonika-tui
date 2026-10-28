"""Synthetic coordinator; never retries a failed render automatically."""
import contextlib,importlib.util,io,json,subprocess,time,os
from pathlib import Path
CONTROL=Path(__file__).resolve().parent
BASE=r'C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe'
def read(name):
 try:return json.loads((CONTROL/name).read_text())
 except (OSError,ValueError):return {}
def main():
 spec=importlib.util.spec_from_file_location('linker',CONTROL/'link_models.py');linker=importlib.util.module_from_spec(spec);spec.loader.exec_module(linker)
 start=time.monotonic()
 while time.monotonic()-start<3*3600:
  try:
   with contextlib.redirect_stdout(io.StringIO()):linker.main()
  except (OSError,ValueError):pass
  states={}
  for name in ['wangp','mmaudio','framepack']:
   installed=read(name+'-install.json').get('phase')=='dependencies_installed'
   models=read(name+'-models.json').get('models_sha256_verified',False)
   qual=read(name+'-qualification.json')
   if installed and models and not qual:
    subprocess.run([BASE,'-I',str(CONTROL/'qualify.py'),name],cwd=CONTROL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2450)
    qual=read(name+'-qualification.json')
   states[name]={'installed':installed,'models_verified':models,'inference':qual.get('phase','pending'),'public_route_registered':False,'commercial_enabled':False if name=='mmaudio' else None}
  p=CONTROL/'toolkit-status.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'timestamp':time.time(),'engines':states},indent=2));os.replace(tmp,p)
  if all(v['inference'] in ['verified','failed'] for v in states.values()):return
  time.sleep(10)
if __name__=='__main__':main()
