"""Authenticated Harmonika tenant-local telecom client; no live company access."""
import argparse,importlib.util,json,sys
from pathlib import Path
from uuid import UUID,uuid4
spec=importlib.util.spec_from_file_location('harmonika_coding_http',Path(__file__).with_name('coding_cli.py'))
http=importlib.util.module_from_spec(spec);spec.loader.exec_module(http);http.BASE='https://chat.harmonika.id/v1/telco'
def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True);sub.add_parser('capabilities')
 for name in ('invoke','chat'):
  t=sub.add_parser(name);t.add_argument('--idempotency-key')
  if name=='invoke':t.add_argument('skill_id')
 for name in ('status','events','approve','stop'):
  t=sub.add_parser(name);t.add_argument('request_id')
  if name=='events':t.add_argument('--after-sequence',type=int,default=0)
  if name=='approve':t.add_argument('--input-hash',required=True);t.add_argument('--confirm',required=True,action='store_true')
 a=p.parse_args()
 if a.command=='capabilities':return http.request('GET','/capabilities')
 if a.command in ('invoke','chat'):
  raw=sys.stdin.buffer.read(262145)
  if len(raw)>262144:raise ValueError('input_too_large')
  value=json.loads(raw);key=a.idempotency_key or str(uuid4())
  body={'skill_id':a.skill_id,'input':value} if a.command=='invoke' else {'messages':value,'stream':False}
  result=http.request('POST','/invoke' if a.command=='invoke' else '/agent',body,key);result['client_idempotency_key']=key;return result
 if str(UUID(a.request_id))!=a.request_id:raise ValueError('invalid_request_id')
 path='/requests/'+a.request_id
 if a.command=='status':return http.request('GET',path)
 if a.command=='events':
  if a.after_sequence<0:raise ValueError('invalid_sequence')
  return http.request('GET',path+'/events?after_sequence='+str(a.after_sequence))
 if a.command=='approve':return http.request('POST',path+'/approve',{'decision':'approve','input_hash':a.input_hash})
 if a.command=='stop':return http.request('POST',path+'/stop',{})
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));sys.exit(2 if result.get('error') or result.get('status')=='failed' else 0)
 except Exception as e:
  print(json.dumps({'error':{'code':str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'telco_client_failed'}}),file=sys.stderr);sys.exit(2)
