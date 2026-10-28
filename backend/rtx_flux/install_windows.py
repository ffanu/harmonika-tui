"""Pinned, resumable ComfyUI + model provisioning, isolated from legacy worker."""
import concurrent.futures, hashlib, http.client, ipaddress, json, os, shutil, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
COMMIT='4281eb0a1478ce445dc32c4b0895f14c9fc3c1be'
REV='533c4cbc3d3b89af1743d9466c434e89810391c0'
FILES=[('diffusion_models/flux-2-klein-4b-fp8.safetensors',4070624520,'97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6'),('text_encoders/qwen_3_4b_fp4_flux2.safetensors',3848213998,'3eab03a77adb0ee5304a4e677d5c10ac22f9049c1d7c894adca4f8bb39206ca8'),('vae/flux2-vae.safetensors',336211292,'868fe7b343cc8f3a19dbcfcafbc3d5f888802be3f89bd81b65b3621a066ce8f3')]
STATUS=ROOT/'install-status.json'
def status(phase,**kw):
 tmp=STATUS.with_suffix('.tmp');tmp.write_text(json.dumps({'phase':phase,'timestamp':time.time(),**kw}),encoding='utf-8');os.replace(tmp,STATUS)
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def download(url,path,size,digest):
 path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists() and path.stat().st_size==size and sha(path)==digest:return
 parts=path.parent/(path.name+'.parts');parts.mkdir(exist_ok=True);chunk=64*1024*1024
 def get(i):
  a=i*chunk;z=min(size,a+chunk)-1;p=parts/str(i)
  for attempt in range(15):
   have=p.stat().st_size if p.exists() else 0
   if have==z-a+1:return
   if have>z-a+1:p.unlink();have=0
   try:
    request_url=url+('\u0026' if '?' in url else '?')+'download=true&hms_offset='+str(a+have)
    req=urllib.request.Request(request_url,headers={'Range':f'bytes={a+have}-{z}','User-Agent':'Harmonika-Model-Installer/1','Cache-Control':'no-cache'})
    with urllib.request.urlopen(req,timeout=90) as r:
     if r.status!=206 or not r.headers.get('Content-Range','').startswith(f'bytes {a+have}-'):raise ValueError('range_not_supported')
     remain=z-a+1-have
     with p.open('ab') as f:
      while remain:
       b=r.read(min(remain,1024*1024))
       if not b:raise IOError('incomplete_download')
       f.write(b);remain-=len(b)
   except Exception as exc:
    with (ROOT/'download-errors.jsonl').open('a') as log:log.write(json.dumps({'file':path.name,'part':i,'attempt':attempt,'error_type':type(exc).__name__,'code':getattr(exc,'code',None),'reason':str(exc) if isinstance(exc,ValueError) else None})+'\n')
    if attempt==14:raise
    time.sleep(min(30,2+attempt*2))
  raise RuntimeError('download_failed')
 with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
  futures=[pool.submit(get,i) for i in range((size+chunk-1)//chunk)]
  while futures:
   _,pending=concurrent.futures.wait(futures,timeout=15,return_when=concurrent.futures.FIRST_EXCEPTION)
   for f in futures:
    if f.done() and f.exception():raise f.exception()
   downloaded=sum(p.stat().st_size for p in parts.iterdir() if p.is_file())
   status('downloading',file=path.name,bytes=downloaded,total_bytes=size)
   futures=list(pending)
 temp=path.with_suffix('.assembling')
 with temp.open('wb') as out:
  for i in range((size+chunk-1)//chunk):
   with (parts/str(i)).open('rb') as src:shutil.copyfileobj(src,out,8*1024*1024)
 if temp.stat().st_size!=size or sha(temp)!=digest:raise RuntimeError('model_checksum_mismatch')
 os.replace(temp,path);shutil.rmtree(parts)
def main():
 status('preflight')
 interface=ROOT/'download-interface.json'
 if interface.exists():
  source_ip=json.loads(interface.read_text()).get('source_ip')
  address=ipaddress.ip_address(source_ip)
  if address.version!=4 or not address.is_private:raise ValueError('invalid_download_interface')
  class WiredConnection(http.client.HTTPSConnection):
   def __init__(self,*args,**kwargs):super().__init__(*args,source_address=(source_ip,0),**kwargs)
  class WiredHTTPS(urllib.request.HTTPSHandler):
   def https_open(self,req):return self.do_open(WiredConnection,req,context=self._context)
  urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}),WiredHTTPS()))
 if shutil.disk_usage(ROOT).free<40*1024**3:raise RuntimeError('requires_40gb_free')
 comfy=ROOT/'ComfyUI'
 if not comfy.exists():
  z=ROOT/'comfy-pinned.zip'
  if not z.exists():urllib.request.urlretrieve(f'https://codeload.github.com/comfyanonymous/ComfyUI/zip/{COMMIT}',z)
  with zipfile.ZipFile(z) as archive:
   for member in archive.infolist():
    if not (ROOT/member.filename).resolve().is_relative_to(ROOT):raise ValueError('unsafe_archive')
   archive.extractall(ROOT)
  (ROOT/('ComfyUI-'+COMMIT)).rename(comfy)
  (ROOT/'source-receipt.json').write_text(json.dumps({'commit':COMMIT,'archive_sha256':sha(z)}))
 status('installing_dependencies')
 py=ROOT/'.venv/Scripts/python.exe'
 if not py.exists():subprocess.run([sys.executable,'-m','venv',str(ROOT/'.venv')],check=True)
 # Read legacy site packages for working CUDA torch only; pip installs into own venv.
 site=ROOT/'.venv/Lib/site-packages';legacy=Path.home()/'harmonika-image-worker/.venv/Lib/site-packages'
 if not (legacy/'torch').is_dir():raise RuntimeError('cuda_base_missing')
 (site/'harmonika-cuda-base.pth').write_text(str(legacy)+'\n')
 with (ROOT/'dependency-install.log').open('a',encoding='utf-8') as log:
  subprocess.run([str(py),'-m','pip','install','--disable-pip-version-check','--timeout','30','--retries','3','-r',str(comfy/'requirements.txt'),'fastapi','uvicorn'],stdout=log,stderr=subprocess.STDOUT,check=True)
 base=f'https://huggingface.co/Comfy-Org/Flux-2-Klein-4B/resolve/{REV}/split_files/'
 source=ROOT/'download-source.json'
 if source.exists():
  candidate=json.loads(source.read_text()).get('base')
  if candidate!='http://127.0.0.1:18205/':raise ValueError('invalid_download_source')
  base=candidate
 subprocess.run([str(py),'-c','import torch; assert torch.cuda.is_available(); print(torch.__version__, torch.version.cuda)'],check=True)
 for rel,size,digest in FILES:
  url=base+rel
  if rel.startswith('diffusion_models/'):
   url='https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/5b4408e59397a4a37ccb46afe426d8ed86379441/flux-2-klein-4b-fp8.safetensors'
  download(url,comfy/'models'/rel,size,digest)
 subprocess.run([str(py),'-m','pip','freeze','--local'],stdout=(ROOT/'installed-requirements.lock').open('w'),check=True)
 (ROOT/'model-receipt.json').write_text(json.dumps({'model':'FLUX.2-klein-4B','revision':REV,'diffusion_revision':'5b4408e59397a4a37ccb46afe426d8ed86379441','precision':'official FP8 diffusion + Comfy FP4 text encoder','license':'Apache-2.0','files':[{'file':f,'bytes':n,'sha256':h} for f,n,h in FILES]},indent=2))
 status('installed_verified',model='FLUX.2-klein-4B',revision=REV)
if __name__=='__main__':
 try:main()
 except Exception as e:status('failed',error_type=type(e).__name__);raise
