"""Operator CLI. No credential arguments or plaintext transcript exports by default."""
import argparse
import json
from pathlib import Path
import urllib.request


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=('summary','traces','consent','revoke','purge','metrics'))
    parser.add_argument('--config',type=Path,default=Path('/etc/harmonika-ai/quality-audit/config.json'))
    parser.add_argument('--token-id',type=int)
    parser.add_argument('--include-content',action='store_true')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    token=Path(config['admin_token_file']).read_text().strip()
    path='/'+args.operation;data=None
    if args.operation in ('consent','revoke','purge'):
        if args.token_id is None:
            parser.error('--token-id required')
        path='/purge' if args.operation=='purge' else '/consent'
        value={'token_id':args.token_id}
        if path=='/consent':value.update(granted=args.operation=='consent',basis='operator_confirmed')
        data=json.dumps(value).encode()
    else:
        if args.operation=='traces' and args.token_id is None:
            parser.error('--token-id required')
        if args.token_id is not None:path+='?token_id='+str(args.token_id)
        if args.include_content:
            if args.operation!='traces':parser.error('--include-content only for traces')
            path+='&include_content=true'
            path+='&limit=3'
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    req=urllib.request.Request('http://127.0.0.1:'+str(config.get('port',18208))+path,data=data,
        headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    with opener.open(req,timeout=8) as response:
        raw=response.read(4194305)
        if len(raw)>4194304:raise ValueError('operator_response_limit')
        result=json.loads(raw)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
