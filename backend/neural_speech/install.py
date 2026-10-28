"""Isolated, pinned local CPU voice install. Never modifies other Python runtimes."""
import hashlib,json,os,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path('/opt/harmonika-ai/neural-speech');REV='aafc6e32416a594460b32413efc49d7fe4ce6d46';REPO='supertone-oss-archive/supertonic-3'
def fetch(url):
 with urllib.request.urlopen(url,timeout=90) as f:return f.read()
def main():
 ROOT.mkdir(parents=True,exist_ok=True);os.chmod(ROOT,0o700)
 def state(phase,**kw):
  p=ROOT/'install.next';p.write_text(json.dumps({'phase':phase,'timestamp':time.time(),**kw}));p.replace(ROOT/'install.json')
 state('dependencies')
 py=ROOT/'.venv/bin/python'
 if not py.exists():subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True)
 # CPU wheels only; no Torch/CUDA or system packages are installed.
 subprocess.run([str(py),'-m','pip','install','--disable-pip-version-check','--report',str(ROOT/'pip-report.json'),'supertonic==1.3.1','onnxruntime==1.24.4','numpy==2.2.6','soundfile==0.13.1','huggingface-hub==0.36.2'],check=True)
 info=json.loads(fetch('https://huggingface.co/api/models/'+REPO+'/revision/'+REV+'?blobs=true'));assert info['sha']==REV
 assets=ROOT/'assets';manifest=[];state('models')
 for item in info['siblings']:
  name=item['rfilename']
  if not (name.startswith(('onnx/','voice_styles/')) or name in ['LICENSE','README.md','config.json']):continue
  target=assets/name;target.parent.mkdir(parents=True,exist_ok=True)
  raw=fetch('https://huggingface.co/'+REPO+'/resolve/'+REV+'/'+name);assert len(raw)==item['size']
  sha=hashlib.sha256(raw).hexdigest();lfs=item.get('lfs',{})
  if lfs:assert sha==lfs['sha256']
  elif item.get('blobId'):assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\x00'+raw).hexdigest()==item['blobId']
  tmp=target.with_suffix(target.suffix+'.partial');tmp.write_bytes(raw);tmp.replace(target);manifest.append({'path':name,'bytes':len(raw),'sha256':sha})
 (ROOT/'models-manifest.json').write_text(json.dumps({'repository':REPO,'revision':REV,'files':manifest},indent=2));state('installed',model_files=len(manifest),model_bytes=sum(x['bytes'] for x in manifest),revision=REV)
 print(json.dumps({'phase':'installed','files':len(manifest)}),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:
  (ROOT/'install-error.json').write_text(json.dumps({'error_type':type(e).__name__}));raise
