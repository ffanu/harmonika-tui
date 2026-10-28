"""Pinned, resumable downloads into the isolated video-toolkit model cache."""
import concurrent.futures,hashlib,http.client,ipaddress,json,os,shutil,time,urllib.request
from pathlib import Path, PureWindowsPath
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
    request_url=url+('\u0026' if '?' in url else '?')+'download=true&hms_offset='+str(a+have)+'&hms_attempt='+str(attempt)+'&hms_nonce='+str(time.time_ns())
    req=urllib.request.Request(request_url,headers={'Range':f'bytes={a+have}-{z}','User-Agent':'Harmonika-Model-Installer/1','Cache-Control':'no-cache'})
    with urllib.request.urlopen(req,timeout=30) as r:
     deadline=time.monotonic()+180
     if r.status!=206 or not r.headers.get('Content-Range','').startswith(f'bytes {a+have}-'):raise ValueError('range_not_supported')
     remain=z-a+1-have
     with p.open('ab',buffering=1024*1024) as f:
      while remain:
       if time.monotonic()>deadline:raise TimeoutError('chunk_deadline')
       b=r.read1(min(remain,1024*1024))
       if not b:raise IOError('incomplete_download')
       f.write(b);remain-=len(b)
   except Exception as exc:
    with (ROOT/'download-errors.jsonl').open('a') as log:log.write(json.dumps({'file':path.name,'part':i,'attempt':attempt,'error_type':type(exc).__name__,'code':getattr(exc,'code',None),'errno':getattr(exc,'errno',None),'winerror':getattr(exc,'winerror',None),'reason':str(exc) if isinstance(exc,ValueError) else None})+'\n')
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
 status('assembling',file=path.name,total_bytes=size)
 temp=path.with_suffix('.assembling')
 with temp.open('wb') as out:
  for i in range((size+chunk-1)//chunk):
   with (parts/str(i)).open('rb') as src:shutil.copyfileobj(src,out,8*1024*1024)
 status('verifying',file=path.name,total_bytes=size)
 if temp.stat().st_size!=size or sha(temp)!=digest:raise RuntimeError('model_checksum_mismatch')
 os.replace(temp,path);shutil.rmtree(parts)

def configure_network():
 interface=Path(r'C:\Users\Kraken\harmonika-h3-worker\download-interface.json')
 if interface.exists():
  source_ip=json.loads(interface.read_text())['source_ip'];address=ipaddress.ip_address(source_ip)
  if address.version!=4 or not address.is_private:raise ValueError('invalid_interface')
  class Connection(http.client.HTTPSConnection):
   def __init__(self,*a,**kw):super().__init__(*a,source_address=(source_ip,0),**kw)
  class Handler(urllib.request.HTTPSHandler):
   def https_open(self,req):return self.do_open(Connection,req,context=self._context)
  urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({}),Handler()))
def main():
 import msvcrt
 guard=(ROOT/'download.lock').open('a+b');guard.seek(0);guard.write(b'0');guard.flush();guard.seek(0);msvcrt.locking(guard.fileno(),msvcrt.LK_NBLCK,1)
 runtime=Path(r'D:\HarmonikaAI\video-toolkit')
 runtime.mkdir(parents=True,exist_ok=True)
 configure_network()
 rows=json.loads((ROOT/'models.json').read_text())
 rows.sort(key=lambda row:({'wangp':0,'mmaudio':1,'framepack':2}[row['group']],row['bytes']))
 missing=[r for r in rows if not (runtime/r['relative']).is_file()]
 if shutil.disk_usage(runtime).free<sum(r['bytes'] for r in missing)+max([r['bytes'] for r in missing]+[0])+45*1024**3:raise RuntimeError('disk_reserve_required')
 verified=[]
 for row in rows:
  relative=PureWindowsPath(row['relative']);assert not relative.is_absolute() and not relative.drive and '..' not in relative.parts
  path=runtime/row['relative']
  status('checking',group=row['group'],file=path.name)
  if row['bytes']<1024*1024:
   path.parent.mkdir(parents=True,exist_ok=True)
   if not path.exists() or sha(path)!=row['sha256']:
    with urllib.request.urlopen(row['url'],timeout=40) as response:data=response.read(row['bytes']+1)
    assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'metadata_checksum_mismatch'
    tmp=path.with_suffix('.download');tmp.write_bytes(data);os.replace(tmp,path)
  else:download(row['url'],path,row['bytes'],row['sha256'])
  verified.append(row)
  (ROOT/'model-receipt.json').write_text(json.dumps({'verified_files':verified,'inference_verified':False},indent=2))
  status('verified',group=row['group'],file=path.name,completed=len(verified),total_files=len(rows))
 # Offline HF lookups resolve main to the pinned, verified local snapshots only.
 for repo,revision in {(r['repo'],r['revision']) for r in rows}:
  ref=runtime/'models/hub'/('models--'+repo.replace('/','--'))/'refs/main';ref.parent.mkdir(parents=True,exist_ok=True);ref.write_text(revision)
 status('completed',files=len(rows),sha256_verified=True,inference_verified=False)
if __name__=='__main__':
 try:main()
 except Exception as exc:status('failed',error_type=type(exc).__name__);raise SystemExit(1)
