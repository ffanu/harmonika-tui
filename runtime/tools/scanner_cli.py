"""Licensed debug scanner transport; save verified PDF instead of printing base64."""
import argparse,base64,hashlib,importlib.util,json,os,re,stat,sys,time,uuid
from pathlib import Path
s=importlib.util.spec_from_file_location('scanner_api',Path(__file__).with_name('master_cli.py'));client=importlib.util.module_from_spec(s);s.loader.exec_module(client)
def save_pdf(artifact):
 if type(artifact) is not dict or artifact.get('name')!='harmonika-debug-report.pdf' or artifact.get('mime_type')!='application/pdf':raise ValueError('invalid_pdf_artifact')
 try:raw=base64.b64decode(artifact['base64'],validate=True)
 except (ValueError,KeyError,TypeError):raise ValueError('invalid_pdf_artifact') from None
 if len(raw)>200000 or not raw.startswith(b'%PDF-1.4\n') or not raw.endswith(b'%%EOF\n') or hashlib.sha256(raw).hexdigest()!=artifact.get('sha256'):raise ValueError('pdf_integrity_failed')
 if any(token in raw for token in (b'/JavaScript',b'/JS ',b'/Launch',b'/EmbeddedFile',b'/OpenAction',b'/AA ')):raise ValueError('active_pdf_rejected')
 base=Path(os.environ.get('HARMONIKA_DOWNLOADS_DIR',str(Path.home()/'Downloads/harmonika-ai')))/'pdf'
 if not base.is_absolute() or any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('unsafe_artifact_directory')
 base.mkdir(mode=0o700,parents=True,exist_ok=True);path=base/('harmonika-debug-report-'+uuid.uuid4().hex[:12]+'.pdf');fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as f:f.write(raw)
 return {'name':artifact['name'],'mime_type':artifact['mime_type'],'sha256':artifact['sha256'],'artifact_path':str(path)}
def process_artifacts(result):
 if type(result) is not dict:raise ValueError('invalid_scanner_response')
 def walk(v,depth=0):
  if depth>8:raise ValueError('invalid_scanner_response')
  if type(v) is dict:
   for key,value in list(v.items()):
    if key=='artifact':v[key]=save_pdf(value)
    elif type(value) in (dict,list):walk(value,depth+1)
  elif type(v) is list:
   for value in v:
    if type(value) in (dict,list):walk(value,depth+1)
 walk(result);return result

def main():
 parser=argparse.ArgumentParser();parser.add_argument('operation');args=parser.parse_args();op=args.operation
 if not re.fullmatch(r'[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*',op) or len(op)>80:raise ValueError('invalid_scanner_operation')
 raw=sys.stdin.buffer.read(65537) if op!='catalog' else b'{}'
 if len(raw)>65536:raise ValueError('input_too_large')
 value=json.loads(raw)
 if type(value) is not dict:raise ValueError('invalid_input')
 payload={} if op=='catalog' else {'data':value};key=uuid.uuid4().hex
 for attempt in range(4):
  out=client.request('POST','/invoke',{'skill_id':'debug_scan.'+op,'input':payload},idempotency_key=key)
  if type(out) is not dict:raise ValueError('invalid_scanner_response')
  error=out.get('error')
  if not isinstance(error,dict) or error.get('code')!='analysis_busy' or attempt==3:break
  time.sleep(.25*(2**attempt))
 if out.get('error'):return out
 result=out.get('result',out)
 if type(result) is not dict:raise ValueError('invalid_scanner_response')
 if op=='catalog':
  return {k:result.get(k) for k in ('engine','source_sha256','catalog_size','callable_entrypoints','readiness','features','all_290_operational')}
 return process_artifacts(result)
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));sys.exit(2 if result.get('error') else 0)
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'scanner_client_failed';print(json.dumps({'error':{'code':code}}));sys.exit(2)
