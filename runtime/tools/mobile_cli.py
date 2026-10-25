"""Fixed internal mobile source interface; never stores or executes returned source."""
import argparse,json,sys
from pathlib import Path
from urllib.parse import quote,urlencode
# -I launch excludes user/project import injection.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from master_cli import request
BASE='https://chat.harmonika.id/v1/mobile-builder'
def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('capabilities');c=sub.add_parser('list');c.add_argument('--status');c.add_argument('--offset',type=int,default=0);c.add_argument('--limit',type=int,default=100)
 c=sub.add_parser('inspect');c.add_argument('skill_id')
 c=sub.add_parser('invoke');c.add_argument('skill_id')
 a=p.parse_args(argv)
 if a.command=='capabilities':return request('GET','/capabilities',base=BASE)
 if a.command=='list':return request('GET','/skills?'+urlencode({k:v for k,v in vars(a).items() if k!='command' and v is not None}),base=BASE)
 if a.command=='inspect':return request('GET','/skills/'+quote(a.skill_id,safe=''),base=BASE)
 raw=sys.stdin.buffer.read(65537)
 if len(raw)>65536:raise ValueError('input_too_large')
 return request('POST','/invoke',{'skill_id':a.skill_id,'input':json.loads(raw)},base=BASE)
if __name__=='__main__':
 try:
  result=main();print(json.dumps(result,ensure_ascii=False));sys.exit(2 if result.get('error') else 0)
 except Exception as e:
  code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'mobile_client_failed'
  print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(2)
