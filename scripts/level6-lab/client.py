"""Immutable fixed-operation bridge; no arbitrary target or remote code parameters."""
import argparse,base64,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
META=json.loads((ROOT/'tools/lab.json').read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('op',choices=['status','ledger','db-audit','logs','build','activate','http','fault','restart','events']);p.add_argument('--release');p.add_argument('--request',dest='request_id');p.add_argument('--method',default='GET',choices=['GET','POST']);p.add_argument('--path');p.add_argument('--owner',default='alpha',choices=['alpha','beta','anonymous']);p.add_argument('--key');p.add_argument('--cursor',default='0');p.add_argument('--body');p.add_argument('--kind',choices=['hold','release','crash-after-effect','unknown-ack','lock']);a=p.parse_args();r={k:v for k,v in vars(a).items() if v is not None};r['origin']='agent'
 if a.body is not None:r['body']=json.loads(a.body)
 if a.op=='build':
  r['files']={}
  for n in ['engine.py','server.py','effect_connector.py','Dockerfile']:
   f=ROOT/n
   if f.is_symlink() or not f.is_file() or f.stat().st_size>131072:raise ValueError('invalid_build_file')
   r['files'][n]=base64.b64encode(f.read_bytes()).decode()
 proc=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+META['remote_root']+'/controller.py'],input=json.dumps(r),text=True,capture_output=True,timeout=180)
 if proc.stdout:
  value=json.loads(proc.stdout)
  if a.op=='http' and 'body_b64' in value:
   raw=base64.b64decode(value['body_b64'])
   try:value['body']=json.loads(raw)
   except ValueError:value['body_text']=raw.decode('utf-8',errors='replace')
  print(json.dumps(value,ensure_ascii=False))
 else:print(json.dumps({'error':'lab_transport_failed'}))
 raise SystemExit(proc.returncode)
if __name__=='__main__':main()
