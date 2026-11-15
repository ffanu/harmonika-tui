"""Immutable typed cluster bridge. Target and controller cannot be supplied by agent."""
import argparse,base64,json,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;META=json.loads((ROOT/'tools/lab.json').read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('op',choices=['status','ledger','db-audit','logs','events','build','activate','http','fault','workers','sink','burst','stream','backup-drill']);p.add_argument('--release');p.add_argument('--request',dest='request_id');p.add_argument('--method',default='GET',choices=['GET','POST']);p.add_argument('--path');p.add_argument('--owner',default='alpha',choices=['alpha','beta','anonymous']);p.add_argument('--key');p.add_argument('--cursor',default='0');p.add_argument('--body');p.add_argument('--kind');p.add_argument('--worker',default='worker1');p.add_argument('--action',choices=['stop','start']);p.add_argument('--names');a=p.parse_args();r={k:v for k,v in vars(a).items() if v is not None};r['origin']='agent'
 if a.body is not None:r['body']=json.loads(a.body)
 if a.names:r['names']=a.names.split(',')
 if a.op=='build':
  r['files']={}
  for n in ['engine.py','stream_reader.py','storage_tools.py','Dockerfile','server.py','worker.py','entrypoint.py','effect_connector.py','effect_service.py']:
   f=ROOT/n
   if f.is_symlink() or not f.is_file() or f.stat().st_size>131072:raise ValueError('invalid_build_file')
   r['files'][n]=base64.b64encode(f.read_bytes()).decode()
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+shlex.quote(META['remote_root']+'/controller.py')],input=json.dumps(r),capture_output=True,text=True,timeout=180)
 if not p.stdout:print('{"error":"lab_transport_failed"}');raise SystemExit(2)
 value=json.loads(p.stdout)
 if 'body_b64' in value:
  raw=base64.b64decode(value['body_b64'])
  try:value['body']=json.loads(raw)
  except ValueError:value['body_text']=raw.decode(errors='replace')
 print(json.dumps(value,ensure_ascii=False));raise SystemExit(p.returncode)
if __name__=='__main__':main()
