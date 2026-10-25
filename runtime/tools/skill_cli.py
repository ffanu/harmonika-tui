"""Trusted local CLI facade over the same shared registry/executor interface."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from skill_registry import Registry, SkillError
from skill_executor import SkillExecutor, ExecutionContext

async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    listing=sub.add_parser('list');listing.add_argument('--prefix',default='');listing.add_argument('--summary',action='store_true')
    run=sub.add_parser('execute');run.add_argument('skill_id');run.add_argument('--root',required=True);run.add_argument('--idempotency-key')
    for name in ('status','stop'):
        p=sub.add_parser(name);p.add_argument('job_id');p.add_argument('--root',required=True)
        if name=='status':p.add_argument('--after-sequence',type=int,default=0)
    args=parser.parse_args()
    state=(Path(os.environ.get('HARMONIKA_TERMINAL_HOME',str(Path.home()/'.config/harmonika-terminal')))/'skill-state').resolve()
    executor=SkillExecutor(state)
    if args.command=='list':
        if len(args.prefix)>64:raise SkillError('invalid_prefix','Prefix too long')
        result=await executor.capabilities()
        if args.prefix:result['skills']=[item for item in result['skills'] if item['id'].startswith(args.prefix)]
        if args.summary:
            result['skills']=[{k:item[k] for k in ('id','readiness','execution_target','permissions')} for item in result['skills']]
            result['summary']=True
        return result
    # This is an explicit local-user command, not identity accepted from network JSON.
    context=ExecutionContext('local:'+str(os.getuid()),args.root,frozenset(item['id'] for item in Registry().list()),True,'tui')
    if args.command=='status':return await executor.status(args.job_id,context,args.after_sequence)
    if args.command=='stop':return await executor.stop(args.job_id,context)
    raw=sys.stdin.buffer.read(1024*1024+1)
    if len(raw)>1024*1024:raise SkillError('input_too_large','Input exceeds 1 MiB')
    try:payload=json.loads(raw)
    except (ValueError,UnicodeError):raise SkillError('invalid_json','Expected JSON input on stdin') from None
    return await executor.execute(args.skill_id,payload,context,args.idempotency_key)

if __name__=='__main__':
    try:
        result=asyncio.run(main())
        print(json.dumps(result,ensure_ascii=False,allow_nan=False,default=str))
        if result.get('status') in ('failed','unknown','stopped','unavailable'):sys.exit(2)
    except Exception as error:
        code=error.code if isinstance(error,SkillError) else 'skill_internal_error'
        message=error.message if isinstance(error,SkillError) else 'Shared skill operation failed'
        print(json.dumps({'schema_version':1,'status':'error','error':{'code':code,'message':message}}),file=sys.stderr)
        sys.exit(2)
