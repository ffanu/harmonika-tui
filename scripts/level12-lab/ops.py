"""Typed exam operations. Credentials and private operator state never returned."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def call(request):
 meta=json.loads((ROOT/'tools/lab.json').read_text());payload={**request,'origin':'agent'};p=subprocess.run([sys.executable,meta['controller']],input=json.dumps(payload),capture_output=True,text=True,timeout=180)
 try:
  if len(p.stdout)>1048576:raise ValueError()
  result=json.loads(p.stdout)
 except ValueError:raise RuntimeError('exam_operation_response_invalid') from None
 if p.returncode:
  safe={'tls_verification_failed','tls_handshake_failed','dns_unavailable','connection_timeout','endpoint_not_listening','endpoint_unavailable','fixture_login_failed','operation_incomplete','operation_conflict','phase_denied','path_invalid','release_invalid','immutable_release_conflict','helper_source_invalid','upload_hash_mismatch','port_busy_foreign','application_not_ready','restart_readiness_failed','listener_identity_failed','backup_deadline','backup_exists','backup_hash_mismatch','backup_integrity_failed','label_invalid','fault_holder_not_ready','writer_holder_not_ready','fault_already_started','state_incomplete','release_integrity_failed','release_file_set_invalid','protected_file_changed','operation_denied','design_only'}
  error=result.get('error') if isinstance(result,dict) else None
  raise RuntimeError(error if isinstance(error,str) and error in safe else 'exam_operation_rejected')
 return result
def main():
 p=argparse.ArgumentParser();p.add_argument('op',choices=['mockup','status','capabilities','build','deploy','api','browser','fault','backup','restore','audit','logs','resources','history']);p.add_argument('--release');p.add_argument('--operation');p.add_argument('--method',choices=['GET','POST','PATCH','DELETE'],default='GET');p.add_argument('--path');p.add_argument('--role',choices=['admin','staff-a','staff-b','anonymous'],default='admin');p.add_argument('--key');p.add_argument('--body');p.add_argument('--auth-mode',choices=['missing','invalid','forged','missing_csrf','invalid_csrf','foreign_origin']);p.add_argument('--kind',choices=['lock','port','bad-config','restart','writer']);p.add_argument('--action',choices=['start','release','state']);p.add_argument('--label');a=p.parse_args()
 if a.op=='mockup':raise SystemExit(subprocess.run(['node',str(ROOT/'tools/render_mockup.cjs')],cwd=ROOT).returncode)
 if a.op=='browser':raise SystemExit(subprocess.run(['node',str(ROOT/'tools/browser.cjs')],cwd=ROOT).returncode)
 request={'op':a.op}
 for k in ['release','operation','method','path','role','key','auth_mode','kind','action','label']:
  value=getattr(a,k,None)
  if value is not None:request[k]=value
 if a.body is not None:request['body']=json.loads(a.body)
 if a.op=='build':
  import base64
  meta=json.loads((ROOT/'tools/lab.json').read_text());request['files']={name:base64.b64encode((ROOT/name).read_bytes()).decode() for name in meta['build_files']}
 print(json.dumps(call(request),ensure_ascii=False))
if __name__=='__main__':main()
