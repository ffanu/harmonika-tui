"""Strict controller for one explicitly registered ephemeral Docker exam lab."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
META=json.loads((ROOT/'lab.json').read_text())
LAB=META['lab_id']
LABEL='id.harmonika.level4'
LEDGER=ROOT/'ledger.json'
BASE=json.loads((ROOT/'base.json').read_text())['base_ref']
FILES={'app.py','storage.py','Dockerfile','requirements.txt','vendor/harmonika_lab_contracts-1.0.0-py3-none-any.whl'}

def command(args,**kwargs):
    return subprocess.run(args,capture_output=True,text=True,timeout=150,**kwargs)
def docker(*args):
    p=command(['docker',*args])
    if p.returncode:raise RuntimeError('lab_docker_operation_failed')
    return p.stdout.strip()
def save(d):
    p=ROOT/'ledger.tmp'
    with p.open('w') as f:json.dump(d,f);f.flush();os.fsync(f.fileno())
    os.replace(p,LEDGER)
def observation(value):
    STATE.setdefault('observations',[]).append({'origin':ORIGIN,**value})
    STATE['observations']=STATE['observations'][-256:];save(STATE)
def owned(cid):
    if cid not in STATE['containers']:raise ValueError('unowned_container')
    d=json.loads(docker('inspect',cid))[0]
    if d['Config'].get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned_container')
    return d
def stop(cid):
    d=owned(cid)
    if d['State']['Running']:docker('stop','--time','5',cid)

HTTP='''import sys,json,urllib.request,urllib.error
r=json.load(sys.stdin);data=json.dumps(r['body']).encode() if r.get('body') is not None else None
req=urllib.request.Request('http://127.0.0.1:18081'+r['path'],data=data,headers={'Content-Type':'application/json'},method=r['method'])
try:response=urllib.request.urlopen(req,timeout=5)
except urllib.error.HTTPError as e:response=e
with response:print(json.dumps({'http_status':response.status,'body':json.load(response)}))
'''
DB='''import sqlite3,json
c=sqlite3.connect('file:/data/items.sqlite?mode=ro',uri=True)
print(json.dumps({'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'rows':[dict(zip(['id','request_id','label','qty'],x)) for x in c.execute('SELECT id,request_id,label,qty FROM items ORDER BY id')]}));c.close()
'''
def http(cid,path,method='GET',body=None):
    owned(cid)
    p=command(['docker','exec','-i',cid,'python','-c',HTTP],input=json.dumps({'path':path,'method':method,'body':body}))
    if p.returncode:return {'http_status':None,'error':'lab_http_unavailable'}
    return json.loads(p.stdout)
def status():
    cid=STATE.get('active')
    if not cid:return {'active_release':None,'state':'absent'}
    d=owned(cid);h=d['HostConfig']
    result={'active_release':STATE['active_release'],'container_id':cid,'image_id':d['Image'],
            'state':d['State']['Status'],'restart_count':d['RestartCount'],
            'isolation':{'user':d['Config']['User'],'network':h['NetworkMode'],'readonly':h['ReadonlyRootfs'],
                         'memory':h['Memory'],'nano_cpus':h['NanoCpus'],'pids_limit':h['PidsLimit'],'cap_drop':h['CapDrop'],'security_opt':h['SecurityOpt']},
            'health':http(cid,'/healthz'),'readiness':http(cid,'/readyz')}
    observation({'kind':'status','release':STATE['active_release'],'health_http':result['health'].get('http_status'),'ready_http':result['readiness'].get('http_status'),'restart_count':d['RestartCount']})
    return result

def build(name,files):
    if name not in ['baseline-v1','candidate-v2']:raise ValueError('invalid_release')
    if set(files)!=FILES:raise ValueError('invalid_build_context')
    raw={n:base64.b64decode(v,validate=True) for n,v in files.items()}
    if sum(map(len,raw.values()))>131072:raise ValueError('build_context_too_large')
    for n,want in META['protected_build'].items():
        if hashlib.sha256(raw[n]).hexdigest()!=want:raise ValueError('protected_build_file_changed')
    text=raw['Dockerfile'].decode()
    lines=[s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith('#')]
    if not lines or lines[0]!='FROM '+BASE:raise ValueError('base_image_must_be_pinned')
    if sum(s.upper().startswith('FROM ') for s in lines)!=1 or any(re.match(r'^(?:ADD|ONBUILD|VOLUME)\b',s,re.I) or '--mount' in s for s in lines):raise ValueError('unsupported_dockerfile_scope')
    digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    if name in STATE['releases']:
        if STATE['releases'][name].get('source_sha256')!=digest:raise ValueError('immutable_release_conflict')
        return STATE['releases'][name]
    folder=ROOT/'contexts'/digest;folder.mkdir(parents=True,exist_ok=True)
    for n,value in raw.items():p=folder/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(value)
    tag=LAB+':'+name+'-'+digest[:12]
    result=command(['docker','build','--network','none','--label',LABEL+'='+LAB,'-q','-t',tag,str(folder)])
    STATE['build_attempts']+=1
    if result.returncode:
        save(STATE);return {'error':'lab_build_failed','diagnostics':result.stderr[-2500:]}
    d=json.loads(docker('image','inspect',tag))[0];STATE['images'].append(tag);save(STATE)
    if d['Config']['User'] not in ['10001','10001:10001'] or d['Config']['Cmd']!=['python','/app/app.py']:
        return {'error':'runtime_image_policy','requires':'USER 10001:10001 and CMD python /app/app.py'}
    record={'image_id':d['Id'],'source_sha256':digest,'tag':tag,'forced_unready':False}
    STATE['releases'][name]=record
    if name=='baseline-v1':STATE['releases']['bad-v3']={**record,'forced_unready':True}
    save(STATE);return record

def activate(name,key):
    if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9-]{1,64}',key):raise ValueError('invalid_request_id')
    attempts=STATE.setdefault('activation_attempts',{});attempts[key]=attempts.get(key,0)+1;save(STATE)
    if key in STATE['requests']:
        old=STATE['requests'][key]
        if old['release']!=name:raise ValueError('request_conflict')
        return {**old,'replayed_receipt':True}
    if name not in STATE['releases']:raise ValueError('release_not_built')
    if STATE.get('active'):stop(STATE['active'])
    release=STATE['releases'][name]
    args=['run','-d','--name',LAB+'-'+key,'--label',LABEL+'='+LAB,'--network','none',
          '--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432',
          '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','--cpus','0.5',
          '--pids-limit','32','--restart','on-failure:3','--mount','type=volume,src='+STATE['volume']+',dst=/data',
          '-e','LAB_RELEASE='+name,'-e','LAB_FORCE_UNREADY='+('1' if release['forced_unready'] else '0'),release['image_id']]
    cid=docker(*args);STATE['containers'].append(cid);STATE['active']=cid;STATE['active_release']=name
    record={'release':name,'container_id':cid,'image_id':release['image_id']}
    STATE['requests'][key]=record;save(STATE)
    if key=='checkpoint-v2':return {'error':'ACK_LOST','request_id':key,'outcome':'unknown','exit_code':75}
    return record

def fault_events(raw):
    """Export only lifecycle mechanics; Docker exec actions can contain argv."""
    events=[]
    for line in raw.splitlines()[-256:]:
        value=json.loads(line);action=value.get('Action')
        if action not in {'die','restart','start'}:continue
        stamp=value.get('time');code=value.get('Actor',{}).get('Attributes',{}).get('exitCode')
        if not isinstance(stamp,int) or isinstance(stamp,bool):continue
        if not isinstance(code,str) or not code.isascii() or not code.isdigit() or len(code)>3 or int(code)>255:code=None
        events.append({'action':action,'time':stamp,'exit_code':code})
    return events[-100:]

def main(req):
    global ORIGIN
    ORIGIN=req.get('origin','examiner')
    op=req.get('op')
    if STATE.get('closed') and op!='ledger':raise ValueError('lab_closed')
    if op=='build':return build(req['release'],req['files'])
    if op=='activate':return activate(req['release'],req['request_id'])
    if op=='lookup':
        STATE['lookups']+=1;save(STATE)
        return STATE['requests'].get(req['request_id'],{'error':'request_not_found'})
    if op=='status':return status()
    if op=='logs':
        cid=STATE.get('active');owned(cid);p=command(['docker','logs','--tail','40',cid]);return {'container_logs':(p.stdout+p.stderr)[-3500:]}
    if op=='http':
        path=req['path'];method=req.get('method','GET');body=req.get('body')
        if not (method=='GET' and path in ['/healthz','/readyz','/items'] or method=='POST' and path=='/items'):raise ValueError('http_scope_denied')
        cid=STATE.get('active')
        if method=='POST':STATE['http_post_count']+=1;save(STATE)
        result=http(cid,path,method,body)
        observation({'kind':'http','release':STATE['active_release'],'path':path,'method':method,'http_status':result.get('http_status')})
        if method=='POST' and isinstance(body,dict) and body.get('request_id')=='session-record' and not STATE['record_ack_lost'] and result.get('http_status') in [200,201]:
            STATE['record_ack_lost']=True;save(STATE);return {'error':'ACK_LOST','request_id':'session-record','outcome':'unknown','exit_code':75}
        return result
    if op=='fault':
        key=req['request_id']
        if key!='crash-once':raise ValueError('invalid_fault_request')
        if key in STATE['faults']:return {'requested':True,'replayed_receipt':True}
        STATE['faults'].append(key);STATE['fault_at']=int(time.time());STATE['fault_container']=STATE['active'];save(STATE)
        return http(STATE['active'],'/__exam/crash','POST',{})
    if op=='fault-evidence':
        cid=STATE.get('fault_container')
        if not cid:return {'events':[]}
        owned(cid)
        raw=docker('events','--since',str(STATE['fault_at']-1),'--until',str(int(time.time())+1),'--filter','container='+cid,'--format','{{json .}}')
        return {'events':fault_events(raw)}
    if op=='db-audit':
        cid=STATE.get('active');owned(cid);return json.loads(docker('exec',cid,'python','-c',DB))
    if op=='ledger':return {k:STATE.get(k,{} if k=='activation_attempts' else []) for k in ['requests','lookups','http_post_count','record_ack_lost','faults','active_release','closed','releases','build_attempts','activation_attempts','observations']}
    if op=='cleanup':
        for cid in STATE['containers']:stop(cid);docker('rm',cid)
        STATE['active']=None;STATE['closed']=True;save(STATE)
        for tag in set(STATE['images']):docker('image','rm',tag)
        volume=json.loads(docker('volume','inspect',STATE['volume']))[0]
        if volume.get('Labels',{}).get(LABEL)!=LAB:raise ValueError('unowned_volume')
        docker('volume','rm',STATE['volume']);return {'closed':True,'owned_containers_removed':len(STATE['containers'])}
    raise ValueError('unknown_operation')

if __name__=='__main__':
    with (ROOT/'control.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        STATE=json.loads(LEDGER.read_text())
        try:
            value=main(json.load(sys.stdin));print(json.dumps(value),flush=True)
            if value.get('exit_code'):raise SystemExit(value['exit_code'])
        except (ValueError,KeyError,RuntimeError,subprocess.SubprocessError):
            print(json.dumps({'error':'lab_operation_rejected'}));raise SystemExit(2)
