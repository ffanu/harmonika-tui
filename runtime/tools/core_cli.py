"""Harmonika Shared Core interface. Source/token/workspace scopes stay explicit."""
import argparse,json,sys
from pathlib import Path
from urllib.parse import quote,urlencode
sys.path.insert(0,str(Path(__file__).resolve().parent))
from master_cli import request
BASE='https://chat.harmonika.id/v1/core'
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('capabilities');a=sub.add_parser('list');a.add_argument('--status');a.add_argument('--offset',type=int,default=0);a.add_argument('--limit',type=int,default=100)
 a=sub.add_parser('inspect');a.add_argument('skill_id')
 a=sub.add_parser('invoke');a.add_argument('skill_id');a.add_argument('--workspace-id');a.add_argument('--engine');a.add_argument('--idempotency-key')
 args=p.parse_args(argv)
 if args.command=='capabilities':return request('GET','/capabilities',base=BASE)
 if args.command=='list':return request('GET','/skills?'+urlencode({k:v for k,v in vars(args).items() if k!='command' and v is not None}),base=BASE)
 if args.command=='inspect':return request('GET','/skills/'+quote(args.skill_id,safe=''),base=BASE)
 raw=sys.stdin.buffer.read(1048577)
 if len(raw)>1048576:raise ValueError('input_too_large')
 body={'skill_id':args.skill_id,'input':json.loads(raw)};ctx={k:getattr(args,k) for k in ('workspace_id','engine') if getattr(args,k) is not None}
 if ctx:body['context']=ctx
 return request('POST','/invoke',body,args.idempotency_key,base=BASE)
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));failed=bool(result.get('error')) or result.get('status') in ('in_progress','recovery_required','failed') or (result.get('skill_id')=='workflow.run' and result.get('data',{}).get('status')=='failed');sys.exit(2 if failed else 0)
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'core_client_failed'
  print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(2)
