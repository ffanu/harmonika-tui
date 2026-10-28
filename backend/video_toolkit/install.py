"""Provision new sources/environments; no existing service restart or inference."""
import argparse,hashlib,json,os,shutil,stat,subprocess,sys,time,urllib.request,venv,zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
CONTROL=Path(__file__).resolve().parent
ROOT=Path(r'D:\HarmonikaAI\video-toolkit')
ENGINES=Path(r'C:\HarmonikaVideo')
CACHE=Path(r'C:\Users\Kraken\harmonika-h3-worker\wheels')
def state(name,phase,**data):
 p=CONTROL/(name+'-install.json');tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps({'engine':name,'phase':phase,'timestamp':time.time(),**data},indent=2));os.replace(tmp,p)
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def command(cmd,folder,timeout=1800):
 env=os.environ.copy()
 for k in ['PYTHONPATH','PYTHONHOME','PYTHONSTARTUP']:env.pop(k,None)
 env.update(PIP_DISABLE_PIP_VERSION_CHECK='1',PIP_NO_CACHE_DIR='1',PIP_DEFAULT_TIMEOUT='40',TEMP=str(CONTROL/'temp'),TMP=str(CONTROL/'temp'),HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4')
 with (CONTROL/'install-packages.log').open('ab') as out:subprocess.run([str(x) for x in cmd],cwd=folder,env=env,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=timeout)
def source(name,row):
 target=ENGINES/name/'app';receipt=ENGINES/name/'source-receipt.json'
 if target.exists():
  assert receipt.exists() and json.loads(receipt.read_text())['revision']==row['revision'],'unverified_existing_source'
  return target
 archive=CONTROL/(name+'.zip')
 if not archive.exists():
  with urllib.request.urlopen(row['url'],timeout=90) as r,archive.open('wb') as out:shutil.copyfileobj(r,out,1024*1024)
 assert archive.stat().st_size==row['archive_bytes'] and sha(archive)==row['archive_sha256'],'source_checksum_mismatch'
 extract=ENGINES/name/'extract';extract.mkdir(parents=True,exist_ok=True);assert not any(extract.iterdir()),'nonempty_extract_requires_review'
 with zipfile.ZipFile(archive) as z:
  assert len(z.infolist())<=50000 and sum(i.file_size for i in z.infolist())<600*1024*1024
  for entry in z.infolist():
   part=PurePosixPath(entry.filename)
   assert not part.is_absolute() and '..' not in part.parts and not PureWindowsPath(entry.filename).drive and '\\' not in entry.filename and not stat.S_ISLNK(entry.external_attr>>16),'unsafe_archive'
  z.extractall(extract)
 children=list(extract.iterdir());assert len(children)==1;children[0].rename(target);extract.rmdir();receipt.write_text(json.dumps(row,indent=2));return target
def install(name):
 assert os.name=='nt' and sys.version_info[:2]==(3,12)
 assert shutil.disk_usage(ENGINES).free>22*1024**3,'system_disk_reserve_required'
 row=json.loads((CONTROL/'sources.json').read_text())[name];state(name,'source_provisioning');app=source(name,row)
 envroot=ENGINES/name/'.venv';py=envroot/'Scripts/python.exe'
 if not py.exists():state(name,'creating_environment');venv.EnvBuilder(with_pip=True).create(envroot)
 constraint=ENGINES/name/'torch-constraints.txt';constraint.write_text('torch==2.10.0+cu130\ntorchvision==0.25.0+cu130\ntorchaudio==2.10.0+cu130\n')
 wheels=[]
 for wheel in json.loads((CONTROL/'torch-wheels.json').read_text()):
  path=CACHE/wheel['file'];assert path.stat().st_size==wheel['bytes'] and sha(path)==wheel['sha256'],'cached_torch_integrity_failed';wheels.append(path)
 state(name,'installing_torch');command([py,'-I','-m','pip','install','--no-cache-dir',*wheels],app)
 state(name,'installing_dependencies');command([py,'-I','-m','pip','install','--no-cache-dir','-c',constraint,'--report',ENGINES/name/'package-receipt.json','-r',CONTROL/(name+'-requirements.txt')],app)
 if name=='mmaudio':command([py,'-I','-m','pip','install','--no-deps',app],app)
 command([py,'-I','-m','pip','check'],app);command([py,'-I',CONTROL/'probe.py',name],app,timeout=120)
 state(name,'dependencies_installed',source_revision=row['revision'],inference_verified=False,public_route_registered=False)
def main():
 ROOT.mkdir(parents=True,exist_ok=True);ENGINES.mkdir(exist_ok=True);(CONTROL/'temp').mkdir(exist_ok=True)
 import msvcrt
 guard=(CONTROL/'install.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0)
 try:msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
 except OSError:raise SystemExit('installation_already_running')
 p=argparse.ArgumentParser();p.add_argument('engine',choices=['wangp','framepack','mmaudio','all']);args=p.parse_args()
 for name in (['mmaudio','framepack','wangp'] if args.engine=='all' else [args.engine]):
  try:install(name)
  except Exception as exc:state(name,'failed',error_type=type(exc).__name__)
 guard.close()
if __name__=='__main__':main()
