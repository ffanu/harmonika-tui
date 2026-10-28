"""Plan/install isolated runtimes. Never starts/restarts existing services."""
import argparse,hashlib,json,os,platform,secrets,shutil,subprocess,sys,urllib.request,venv
from pathlib import Path
SOURCE=Path(__file__).resolve().parent
PACKAGES={'asr':['faster-whisper==1.2.1','aiohttp==3.14.5'],'upscale':['aura-sr==0.0.4','Pillow==13.0.0'],'browser':['browser-use==0.13.14','playwright==1.56.0'],'tts':['f5-tts==1.1.22','av==19.0.2']}
GROUPS={'asr':'asr','upscale':'upscale','tts_research':'tts_research','vocoder':'vocoder'}
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
 return h.hexdigest()
def download(row,target):
 target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
 if target.is_symlink():raise ValueError('unsafe_model_path')
 if target.is_file() and target.stat().st_size==row['bytes'] and sha(target)==row['sha256']:return
 temp=target.with_name(target.name+'.partial')
 if temp.is_symlink():raise ValueError('unsafe_model_path')
 with urllib.request.urlopen(row['url'],timeout=90) as response,temp.open('wb') as out:
  count=0
  while True:
   data=response.read(1024*1024)
   if not data:break
   count+=len(data)
   if count>row['bytes']:raise ValueError('model_size_mismatch')
   out.write(data)
 if temp.stat().st_size!=row['bytes'] or sha(temp)!=row['sha256']:raise ValueError('model_checksum_mismatch')
 os.replace(temp,target)
def plan(role,include_tts=False):
 envs=['asr','upscale','tts'] if role=='windows' else ['browser']
 rows=[x for x in json.loads((SOURCE/'models.json').read_text()) if x['group'] in (['asr','upscale','tts_research','vocoder'] if role=='windows' and include_tts else ['asr','upscale'] if role=='windows' else [])]
 return {'role':role,'environments':envs,'model_bytes':sum(x['bytes'] for x in rows),'models':rows,'tts_commercial_enabled':False,'real_inference_verified':False}
def finish_browser(root,cfg):
 # Browser-only download: upstream convenience CLI uses --with-deps (global apt).
 # Never use that CLI here. A pinned Playwright obtains an isolated Chromium.
 py=root/'.browser/bin/python';env=os.environ.copy();env['PLAYWRIGHT_BROWSERS_PATH']=str(root/'chromium')
 subprocess.run([str(py),'-m','playwright','install','chromium','--no-shell'],env=env,check=True,timeout=600)
 code='from playwright.sync_api import sync_playwright;p=sync_playwright().start();print(p.chromium.executable_path);p.stop()'
 executable=subprocess.check_output([str(py),'-I','-c',code],env=env,text=True).strip();assert Path(executable).is_file()
 cfg['browser_executable']=executable;(root/'config.json').write_text(json.dumps(cfg,indent=2))
def install_torch(python,cache=None):
 if cache is None:
  subprocess.run([str(python),'-I','-m','pip','install','--no-cache-dir','torch==2.10.0','torchvision==0.25.0','torchaudio==2.10.0','--index-url','https://download.pytorch.org/whl/cu130'],check=True);return
 files=[];base=Path(cache).resolve()
 for row in json.loads((SOURCE/'torch-wheels.json').read_text()):
  assert '/' not in row['file'] and '\\' not in row['file']
  f=base/row['file'];assert f.is_file() and not f.is_symlink() and f.stat().st_size==row['bytes'] and sha(f)==row['sha256'],'cached_wheel_integrity_failed';files.append(str(f))
 subprocess.run([str(python),'-I','-m','pip','install','--no-cache-dir',*files],check=True)
def install(root,role,include_tts=False,torch_cache=None):
 if sys.version_info[:2]!=(3,12):raise RuntimeError('python_3_12_required')
 if (role=='windows' and os.name!='nt') or (role=='linux' and platform.system()!='Linux'):raise RuntimeError('wrong_target_os')
 root=Path(root).absolute()
 if root==SOURCE or root.is_symlink() or any(p.is_symlink() for p in root.parents):raise RuntimeError('unsafe_runtime_root')
 if root.exists() and any(root.iterdir()):raise RuntimeError('runtime_exists_requires_review')
 root.mkdir(parents=True,mode=0o700,exist_ok=True)
 if shutil.disk_usage(root).free<30*1024**3:raise RuntimeError('disk_reserve_required')
 for name in ('contracts.py','engines.py','process.py','service.py','client.py','models.json','README.md','probe.py','qualify.py'):
  shutil.copy2(SOURCE/name,root/name)
 os.umask(0o077);(root/'service.token').write_text(secrets.token_urlsafe(48)+'\n')
 cfg={'role':role,'port':18231 if role=='windows' else 18230,'tts_research_enabled':include_tts,'browser_allowed_hosts':['example.com'],'public_route_registered':False}
 (root/'config.json').write_text(json.dumps(cfg,indent=2));(root/'qualification.json').write_text('{}')
 description=plan(role,include_tts)
 for env in description['environments']:
  dest=root/('.'+env);venv.EnvBuilder(with_pip=True).create(dest);py=dest/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
  # CUDA wheels remain private to this venv. Never downgrade an existing Torch.
  if env in ('upscale','tts'):
   install_torch(py,torch_cache)
  constraint=root/(env+'-constraints.txt')
  constraint.write_text('torch==2.10.0+cu130\ntorchvision==0.25.0+cu130\ntorchaudio==2.10.0+cu130\n' if env in ('upscale','tts') else '')
  subprocess.run([str(py),'-I','-m','pip','install','--no-cache-dir','--report',str(root/(env+'-packages.json')),*PACKAGES[env],'-c',str(constraint)],check=True)
  subprocess.run([str(py),'-I','-m','pip','check'],check=True)
  subprocess.run([str(py),'-I',str(root/'probe.py'),env],check=True)
 for row in description['models']:
  dest=root/'models'/row['group']/Path(row['path']).name;download(row,dest)
  (root/'install-status.json').write_text(json.dumps({'phase':'model_verified','group':row['group'],'name':Path(row['path']).name,'raw_input_logged':False}))
 if role=='linux':finish_browser(root,cfg)
 (root/'model-receipt.json').write_text(json.dumps(description['models'],indent=2))
 (root/'install-status.json').write_text(json.dumps({'phase':'installed_pending_real_qualification','role':role,'real_test_passed':False,'public_route_registered':False}))
 return {'installed':True,'real_test_passed':False,'root':str(root),'port':cfg['port']}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['plan','install']);p.add_argument('--role',choices=['windows','linux'],required=True);p.add_argument('--root');p.add_argument('--include-tts-research',action='store_true');p.add_argument('--torch-cache');a=p.parse_args()
 if a.action=='plan':print(json.dumps(plan(a.role,a.include_tts_research),indent=2))
 else:
  if not a.root:p.error('--root required')
  print(json.dumps(install(a.root,a.role,a.include_tts_research,a.torch_cache)))
