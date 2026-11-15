"""Immutable, scoped bridge to the lab controller; no credentials in payloads."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
REMOTE='__REMOTE_CONTROLLER__'
p=argparse.ArgumentParser();s=p.add_subparsers(dest='op',required=True)
b=s.add_parser('build');b.add_argument('--release',choices=['candidate-v2'],required=True)
a=s.add_parser('activate');a.add_argument('--release',choices=['candidate-v2','bad-v3'],required=True);a.add_argument('--request',dest='request_id',required=True)
l=s.add_parser('lookup');l.add_argument('--request',dest='request_id',required=True)
h=s.add_parser('http');h.add_argument('--method',choices=['GET','POST'],default='GET');h.add_argument('--path',choices=['/healthz','/readyz','/items'],required=True);h.add_argument('--body',type=json.loads)
f=s.add_parser('fault');f.add_argument('--request',dest='request_id',choices=['crash-once'],required=True)
for name in ['status','logs','db-audit','ledger']:s.add_parser(name)
req={k:v for k,v in vars(p.parse_args()).items() if v is not None}
req['origin']='agent'
if req['op']=='build':
    req['files']={}
    for name in ['app.py','storage.py','Dockerfile','requirements.txt','vendor/harmonika_lab_contracts-1.0.0-py3-none-any.whl']:
        file=ROOT/name
        if file.is_symlink() or not file.is_file():raise SystemExit('invalid_lab_build_file')
        req['files'][name]=base64.b64encode(file.read_bytes()).decode()
result=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+REMOTE],input=json.dumps(req),text=True,capture_output=True)
if result.stdout:sys.stdout.write(result.stdout)
if result.returncode and not result.stdout:print(json.dumps({'error':'lab_transport_unavailable','outcome':'unknown'}))
raise SystemExit(result.returncode)
