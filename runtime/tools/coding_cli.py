"""Authenticated remote coding workspace client. Keys never enter argv or output."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.request
from uuid import uuid4

BASE='https://chat.harmonika.id/v1/coding'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect_refused')

def request(method,path,payload=None,key=None):
    file=Path.home()/'.config/harmonika/harmonika-api-key'
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)
    fd=os.open(file,flags)
    with os.fdopen(fd) as src:
        info=os.fstat(src.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:raise ValueError('unsafe_key_file')
        token=src.read(256).strip()
    if not token.startswith('hai_') or len(token)>200:raise ValueError('invalid_key_file')
    raw=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
    if raw and len(raw)>1024*1024:raise ValueError('input_too_large')
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if key:headers['Idempotency-Key']=key
    req=urllib.request.Request(BASE+path,data=raw,method=method,headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=195) as res:
            data=res.read(4*1024*1024+1)
    except urllib.error.HTTPError as e:data=e.read(65536)
    if len(data)>4*1024*1024:raise ValueError('response_too_large')
    return json.loads(data)

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('capabilities');sub.add_parser('list')
    c=sub.add_parser('create');c.add_argument('name');c.add_argument('--write',action='store_true');c.add_argument('--execute',action='store_true');c.add_argument('--git-commit',action='store_true')
    for name in ('invoke','chat'):
        p=sub.add_parser(name);p.add_argument('workspace_id');p.add_argument('--idempotency-key',default=None)
        if name=='invoke':p.add_argument('skill_id')
    for name in ('status','events','stop','approve'):
        p=sub.add_parser(name);p.add_argument('job_id')
        if name=='approve':p.add_argument('--input-hash',required=True);p.add_argument('--confirm',action='store_true',required=True)
        if name=='events':p.add_argument('--after-sequence',type=int,default=0)
    for name in ('debug-status','debug-events','debug-action','debug-stop'):
        p=sub.add_parser(name);p.add_argument('session_id')
        if name=='debug-action':p.add_argument('action',choices=('status','continue','next','inspect'));p.add_argument('--idempotency-key')
        if name=='debug-events':p.add_argument('--after-sequence',type=int,default=0)
    a=parser.parse_args()
    for field in ('workspace_id','job_id','session_id'):
        if hasattr(a,field) and str(__import__('uuid').UUID(getattr(a,field)))!=getattr(a,field):raise ValueError('invalid_identifier')
    if a.command=='capabilities':return request('GET','/capabilities')
    if a.command=='list':return request('GET','/workspaces')
    if a.command=='create':return request('POST','/workspaces',{'name':a.name,'permissions':{'write':a.write,'execute':a.execute,'git_commit':a.git_commit}})
    if a.command in ('invoke','chat'):
        raw=sys.stdin.buffer.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('input_too_large')
        value=json.loads(raw);key=a.idempotency_key or str(uuid4())
        body={'skill_id':a.skill_id,'input':value} if a.command=='invoke' else {'messages':value,'stream':False}
        result=request('POST','/workspaces/'+a.workspace_id+('/invoke' if a.command=='invoke' else '/agent'),body,key)
        result['client_idempotency_key']=key;return result
    if a.command.startswith('debug-'):
        path='/debug/sessions/'+a.session_id
        if a.command=='debug-status':return request('GET',path)
        if a.command=='debug-stop':return request('POST',path+'/stop',{})
        if a.command=='debug-events':
            if a.after_sequence<0:raise ValueError('invalid_sequence')
            return request('GET',path+'/events?after_sequence='+str(a.after_sequence))
        key=a.idempotency_key or str(uuid4());value=request('POST',path+'/actions',{'action':a.action},key);value['client_idempotency_key']=key;return value
    path='/jobs/'+a.job_id
    if a.command=='status':return request('GET',path)
    if a.command=='events':
        if a.after_sequence<0:raise ValueError('invalid_sequence')
        return request('GET',path+'/events?after_sequence='+str(a.after_sequence))
    if a.command=='stop':return request('POST',path+'/stop',{})
    if a.command=='approve':return request('POST',path+'/approve',{'decision':'approve','input_hash':a.input_hash})

if __name__=='__main__':
    try:
        value=main();print(json.dumps(value,ensure_ascii=False));sys.exit(2 if value.get('error') else 0)
    except (Exception,KeyboardInterrupt) as e:
        code=str(e) if isinstance(e,ValueError) and str(e).replace('_','').isalnum() else 'coding_client_failed'
        print(json.dumps({'error':{'code':code}}),file=sys.stderr);sys.exit(2)
