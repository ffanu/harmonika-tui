"""Private educational legal client. Same Harmonika key, no direct mentor SDK."""
import argparse,importlib.util,json,sys
from pathlib import Path
from uuid import UUID,uuid4
spec=importlib.util.spec_from_file_location('harmonika_legal_http',Path(__file__).with_name('coding_cli.py'));http=importlib.util.module_from_spec(spec);spec.loader.exec_module(http);http.BASE='https://chat.harmonika.id/v1/legal'
def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 for name in ('capabilities','sources'):sub.add_parser(name)
 for name in ('create-case','invoke','chat','refresh-source'):
  x=sub.add_parser(name);x.add_argument('--idempotency-key')
  if name=='invoke':x.add_argument('skill_id')
  if name=='chat':x.add_argument('case_id')
  if name=='refresh-source':x.add_argument('source_id')
 for name in ('case','status','events'):
  x=sub.add_parser(name);x.add_argument('id')
  if name=='events':x.add_argument('--after-sequence',type=int,default=0)
 a=p.parse_args()
 if a.command in ('capabilities','sources'):return http.request('GET','/'+a.command)
 if a.command in ('case','status','events'):
  if str(UUID(a.id))!=a.id:raise ValueError('invalid_id')
  route='/cases/'+a.id if a.command=='case' else '/requests/'+a.id
  if a.command=='events':
   if a.after_sequence<0:raise ValueError('invalid_sequence')
   route+='/events?after_sequence='+str(a.after_sequence)
  return http.request('GET',route)
 key=a.idempotency_key or str(uuid4())
 if a.command=='refresh-source':return http.request('POST','/sources/refresh',{'source_id':a.source_id},key)
 raw=sys.stdin.buffer.read(262145)
 if len(raw)>262144:raise ValueError('input_too_large')
 value=json.loads(raw)
 if a.command=='invoke':return http.request('POST','/invoke',{'skill_id':a.skill_id,'input':value},key)
 if a.command=='create-case':return http.request('POST','/cases',value,key)
 if a.command=='chat':
  if str(UUID(a.case_id))!=a.case_id:raise ValueError('invalid_case_id')
  message=value if type(value) is str else value.get('message');return http.request('POST','/chat',{'case_id':a.case_id,'message':message,'stream':False},key)
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));sys.exit(2 if result.get('error') or result.get('status')=='failed' else 0)
 except Exception as e:print(json.dumps({'error':{'code':str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'legal_client_failed'}}),file=sys.stderr);sys.exit(2)
