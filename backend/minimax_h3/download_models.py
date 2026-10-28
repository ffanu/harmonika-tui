"""SHA-pinned resumable local H3 model downloads; no inference or third-party data."""
import concurrent.futures,hashlib,http.client,ipaddress,json,os,shutil,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parent
STATUS=ROOT/'model-download-status.json'
def status(phase,**kw):
 temp=STATUS.with_suffix('.tmp');temp.write_text(json.dumps({'phase':phase,'timestamp':time.time(),**kw}),encoding='utf-8');os.replace(temp,STATUS)

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
 interface=ROOT/'download-interface.json'
 if interface.exists():
  source_ip=json.loads(interface.read_text())['source_ip'];address=ipaddress.ip_address(source_ip)
  if address.version!=4 or not address.is_private:raise ValueError('invalid_download_interface')
  class WiredConnection(http.client.HTTPSConnection):
   def __init__(self,*args,**kwargs):super().__init__(*args,source_address=(source_ip,0),**kwargs)
  class WiredHTTPS(urllib.request.HTTPSHandler):
   def https_open(self,req):return self.do_open(WiredConnection,req,context=self._context)
  urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}),WiredHTTPS()))
 manifest=json.loads((ROOT/'models.json').read_text());free=shutil.disk_usage(ROOT).free
 # Account for assembly duplication, own environment and 20GiB OS reserve.
 files=[f for f in manifest['files'] if 'ref2va' not in f['file']]
 prior=0;peak=0
 for item in files:peak=max(peak,prior+2*item['bytes']);prior+=item['bytes']
 if free<peak+32*1024**3:raise RuntimeError('insufficient_space_with_os_reserve')
 for item in files:
  relative=item['file'];path=ROOT/'app/models'/relative
  url='https://huggingface.co/'+manifest['repo']+'/resolve/'+manifest['revision']+'/'+relative
  status('checking',file=path.name)
  download(url,path,item['bytes'],item['sha256'])
  status('verified',file=path.name)
 receipt={'status':'core_models_verified','repo':manifest['repo'],'revision':manifest['revision'],'files':files,'inference_tested':False}
 (ROOT/'model-receipt.json').write_text(json.dumps(receipt,indent=2))
 status('completed',files=len(files),sha256_verified=True)
if __name__=='__main__':
 try:main()
 except Exception as exc:
  status('failed',error_type=type(exc).__name__);raise SystemExit(1)
