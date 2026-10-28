"""Download missing native WanGP support assets into disjoint pinned paths."""
import importlib.util,json,os,time,hashlib,urllib.request
from pathlib import Path
CONTROL=Path(__file__).resolve().parent;ROOT=Path(r'D:\HarmonikaAI\video-toolkit')
def main():
 spec=importlib.util.spec_from_file_location('downloader',CONTROL/'download.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d);d.STATUS=CONTROL/'supplement-download-status.json';d.configure_network()
 rows=json.loads((CONTROL/'supplement.json').read_text());verified=[]
 for row in sorted(rows,key=lambda r:r['bytes']):
  target=ROOT/row['relative']
  if row['bytes']<1024*1024:
   target.parent.mkdir(parents=True,exist_ok=True)
   with urllib.request.urlopen(row['url'],timeout=40) as response:raw=response.read(row['bytes']+1)
   assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
   target.write_bytes(raw)
  else:d.download(row['url'],target,row['bytes'],row['sha256'])
  verified.append(row)
  temp=CONTROL/'supplement-receipt.tmp';temp.write_text(json.dumps({'verified_files':verified}));os.replace(temp,CONTROL/'supplement-receipt.json')
 d.status('completed',files=len(rows),sha256_verified=True)
if __name__=='__main__':
 try:main()
 except Exception as exc:
  (CONTROL/'supplement-download-status.json').write_text(json.dumps({'phase':'failed','error_type':type(exc).__name__,'timestamp':time.time()}));raise SystemExit(1)
