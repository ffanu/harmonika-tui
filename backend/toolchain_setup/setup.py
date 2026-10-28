"""Isolated CPU toolchain provisioner. No host upgrade, firewall, browser/GPU or daemon changes."""
import argparse,hashlib,json,os,subprocess,sys,time,venv
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def install(base):
 base=base.resolve();base.mkdir(parents=True,exist_ok=True,mode=0o700)
 env=base/'venv'
 if not (env/'bin/python').exists():venv.EnvBuilder(with_pip=True).create(env)
 py=str(env/'bin/python');cache=base/'wheels';cache.mkdir(exist_ok=True)
 lock=base/'requirements.lock'
 # First resolution is captured; all subsequent installs reuse immutable local wheels.
 if not lock.exists():
  run([py,'-m','pip','download','--only-binary=:all:','-r',str(ROOT/'requirements.in'),'-d',str(cache)],stdout=sys.stdout,stderr=sys.stderr)
  run([py,'-m','pip','install','--no-index','--find-links',str(cache),'-r',str(ROOT/'requirements.in')])
  raw=subprocess.check_output([py,'-m','pip','freeze']);lock.write_bytes(raw);lock.chmod(0o600)
  (base/'wheel-sha256.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.iterdir() if p.is_file()},sort_keys=True,indent=2))
 else:
  expected=json.loads((base/'wheel-sha256.json').read_text())
  for name,digest in expected.items():
   if Path(name).name!=name or hashlib.sha256((cache/name).read_bytes()).hexdigest()!=digest:raise ValueError('wheel_integrity_failed')
  run([py,'-m','pip','install','--no-index','--find-links',str(cache),'-r',str(lock)])
 run([py,'-m','pip','check']);run([py,str(ROOT/'verify.py'),'--output',str(base/'verification.json')])
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--install',action='store_true');p.add_argument('--root',type=Path,default=Path('/opt/harmonika-ai/toolchain-runtime'));a=p.parse_args()
 if a.install:install(a.root)
 else:print(json.dumps({'dry_run':True,'root':str(a.root),'requirements':(ROOT/'requirements.in').read_text().splitlines(),'host_mutations':False}))
