"""Immutable bridge for one owned staging lab. No arbitrary remote shell."""
import argparse,base64,json,subprocess,sys,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REMOTE='__REMOTE_CONTROLLER__'
p=argparse.ArgumentParser();s=p.add_subparsers(dest='op',required=True)
b=s.add_parser('build');b.add_argument('--release',choices=['app-v1', 'app-v1-r1', 'app-v1-r2', 'app-v1-r3', 'app-v2', 'app-v2-r1', 'app-v2-r2', 'app-v2-r3'],required=True)
a=s.add_parser('activate');a.add_argument('--release',choices=['app-v1', 'app-v1-r1', 'app-v1-r2', 'app-v1-r3', 'app-v2', 'app-v2-r1', 'app-v2-r2', 'app-v2-r3', 'bad-v3'],required=True);a.add_argument('--request',dest='request_id',required=True)
l=s.add_parser('lookup');l.add_argument('--request',dest='request_id',required=True)
f=s.add_parser('fault');f.add_argument('--kind',choices=['drop-ack','database-lock'],required=True)
for n in ['status','ledger','db-audit']:s.add_parser(n)
r={k:v for k,v in vars(p.parse_args()).items() if v is not None};r['origin']='agent'
if r['op']=='fault' and r['kind']=='drop-ack':
 url=json.loads((ROOT/'tools/lab.json').read_text())['url']
 req=urllib.request.Request(url+'/__exam/drop-ack',data=b'{}',method='POST')
 with urllib.request.urlopen(req,timeout=10) as response:print(response.read().decode())
 raise SystemExit(0)
if r['op']=='fault':r={'op':'lock','origin':'agent'}
if r['op']=='build':
 r['files']={}
 for n in ['app.py','web.html','identity_provider.py','Dockerfile','requirements.txt','vendor/harmonika_lab_contracts-1.0.0-py3-none-any.whl']:
  path=ROOT/n
  if path.is_symlink() or not path.is_file():raise SystemExit('invalid_build_file')
  r['files'][n]=base64.b64encode(path.read_bytes()).decode()
p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','vps','python3 '+REMOTE],input=json.dumps(r),capture_output=True,text=True,timeout=180)
if p.stdout:sys.stdout.write(p.stdout)
else:print(json.dumps({'error':'lab_transport_unavailable','outcome':'unknown'}))
raise SystemExit(p.returncode)
