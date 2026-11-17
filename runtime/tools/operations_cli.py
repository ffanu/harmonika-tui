"""Harmonika operations client. Existing license authentication; no raw key arguments."""
import argparse,json,sys,re,urllib.error,base64,hashlib,os,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from master_cli import request
for stream in (sys.stdin,sys.stdout,sys.stderr):
 if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')

def download(project,artifact_id,output,fetch=request):
 target=Path(output).expanduser().absolute()
 if target.suffix.lower() not in ('.zip','.png') or any(part.startswith('.') for part in target.parts if part not in ('.','..')) or any(p.is_symlink() for p in [target,*target.parents]):raise ValueError('unsafe_output_path')
 if not target.parent.is_dir():raise ValueError('output_directory_missing')
 owned=False;offset=0;expected=None;size=None;digest=hashlib.sha256()
 try:
  with target.open('xb') as out:
   owned=True
   if os.name!='nt':target.chmod(0o600)
   for attempt in range(257):
    if attempt:time.sleep(1.05)
    result=fetch('POST','/invoke',{'skill_id':'operations.artifact.read','input':{'project':project,'artifact_id':artifact_id,'offset':offset,'limit':65536}},timeout=48)
    if result.get('ok') is not True:raise ValueError('artifact_download_failed')
    row=result.get('result',{});data=base64.b64decode(row.get('data_base64',''),validate=True)
    if row.get('offset')!=offset or row.get('next_offset')!=offset+len(data) or hashlib.sha256(data).hexdigest()!=row.get('chunk_sha256'):raise ValueError('artifact_chunk_invalid')
    if attempt==0:
     size=row.get('bytes');expected=row.get('sha256')
     if type(size) is not int or not 0<=size<=16*1024**2 or type(expected) is not str or not re.fullmatch('[a-f0-9]{64}',expected):raise ValueError('artifact_metadata_invalid')
    if row.get('bytes')!=size or len(data)>65536 or offset+len(data)>size:raise ValueError('artifact_chunk_invalid')
    out.write(data);digest.update(data);offset+=len(data)
    if row.get('complete') is True:
     if offset!=size or digest.hexdigest()!=expected:raise ValueError('artifact_checksum_mismatch')
     return {'ok':True,'output_file':str(target),'bytes':size,'sha256':expected,'private':True}
    if not data:raise ValueError('artifact_chunk_empty')
   raise ValueError('artifact_download_limit')
 except Exception:
  if owned:target.unlink(missing_ok=True)
  raise

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
 sub.add_parser('catalog');invoke=sub.add_parser('invoke');invoke.add_argument('skill_id')
 download_parser=sub.add_parser('download');download_parser.add_argument('--project',required=True);download_parser.add_argument('--artifact-id',required=True);download_parser.add_argument('--output',required=True)
 args=parser.parse_args(argv)
 if args.command=='download':return download(args.project,args.artifact_id,args.output)
 catalog=json.loads(Path(__file__).with_name('operations_catalog.json').read_text())
 if args.command=='catalog':return catalog
 sid=args.skill_id if args.skill_id.startswith('operations.') else 'operations.'+args.skill_id
 if sid not in {row['id'] for row in catalog['skills']}:raise ValueError('unknown_operations_skill')
 raw=sys.stdin.buffer.read(1048577)
 if len(raw)>1048576:raise ValueError('input_too_large')
 value=json.loads(raw or b'{}')
 if type(value) is not dict:raise ValueError('invalid_input')
 try:return request('POST','/invoke',{'skill_id':sid,'input':value},timeout=48)
 except urllib.error.HTTPError as error:
  raw=error.read(262145)
  if len(raw)>262144:raise ValueError('operations_response_limit') from None
  try:code=json.loads(raw).get('error',{}).get('code')
  except (ValueError,AttributeError):code=None
  return {'ok':False,'error':{'code':code if type(code) is str and re.fullmatch('[a-z_]{1,80}',code) else 'operations_http_failed'},'http_status':error.code,'retry_safe':False}
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));sys.exit(0 if result.get('ok',True) is True and not result.get('error') else 2)
 except Exception:
  print(json.dumps({'ok':False,'error':{'code':'operations_client_failed'},'retry_safe':False}),file=sys.stderr);sys.exit(2)
