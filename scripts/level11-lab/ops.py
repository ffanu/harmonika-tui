"""Typed operator transport. No credentials returned to the model."""
import argparse,base64,hashlib,hmac,json,os,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];META=json.loads((ROOT/'tools/lab.json').read_text())
def call(r):
 origin=os.environ.get('LEVEL11_ORIGIN','agent');payload={**r,'origin':origin}
 if origin=='examiner':payload['_operator_auth']=hmac.new(Path(os.environ['LEVEL11_KEY_FILE']).read_bytes(),json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
 p=subprocess.run(['ssh','-o','BatchMode=yes','vps','python3 '+META['remote_root']+'/controller.py'],input=json.dumps(payload),capture_output=True,text=True,timeout=150)
 if p.returncode:raise RuntimeError('lab_operation_rejected')
 return json.loads(p.stdout)
def main():
 p=argparse.ArgumentParser();p.add_argument('op',choices=['build','activate','status','ledger','audit','events','resources','logs','http','batch','lock','browser']);p.add_argument('--release');p.add_argument('--request');p.add_argument('--owner',choices=['alpha','beta']);p.add_argument('--project',choices=['one','two']);p.add_argument('--method',choices=['GET','POST','PATCH'],default='GET');p.add_argument('--path');p.add_argument('--key');p.add_argument('--body');p.add_argument('--file');p.add_argument('--lost-ack',action='store_true');p.add_argument('--action',choices=['start','release','state']);p.add_argument('--cursor');p.add_argument('--auth-mode',choices=['missing','invalid','nonascii','forged']);a=p.parse_args();r={'op':a.op}
 if a.op=='browser':
  q=subprocess.run(['node',str(ROOT/'tools/browser.cjs')],cwd=ROOT);raise SystemExit(q.returncode)
 if a.op=='build':r.update(release=a.release,files={n:base64.b64encode((ROOT/n).read_bytes()).decode() for n in META['build_files']})
 elif a.op=='activate':r.update(release=a.release,request_id=a.request)
 elif a.op=='http':
  r.update(owner=a.owner,project=a.project,method=a.method,path=a.path)
  if a.key is not None:r['key']=a.key
  if a.body is not None:r['body']=json.loads(a.body)
  if a.lost_ack:r['lost_ack']=True
  if a.auth_mode:r['auth_mode']=a.auth_mode
  if a.cursor:r['headers']={'Last-Event-ID':a.cursor}
 elif a.op=='batch':
  f=(ROOT/a.file).resolve();assert f.is_relative_to(ROOT.resolve()) and f.is_file();r['requests']=json.loads(f.read_text())
 elif a.op=='lock':r['action']=a.action
 result=call(r)
 def expand(v):
  if isinstance(v,dict) and 'body_b64' in v:
   try:v['body']=json.loads(base64.b64decode(v['body_b64']))
   except (ValueError,UnicodeDecodeError):pass
  return v
 result=[expand(x) for x in result] if isinstance(result,list) else expand(result);print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
