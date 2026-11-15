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
LABEL='id.harmonika.level5'
LEDGER=ROOT/'ledger.json'
BASE=json.loads((ROOT/'base.json').read_text())['base_ref']
FILES={'app.py','web.html','identity_provider.py','Dockerfile','requirements.txt','vendor/harmonika_lab_contracts-1.0.0-py3-none-any.whl'}

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

HTTP="import sys,json,base64,urllib.request,urllib.error,time\nr=json.load(sys.stdin);data=base64.b64decode(r['body_b64']) if r.get('body_b64') is not None else None\nheaders=r.get('headers',{})\nclass NoRedirect(urllib.request.HTTPRedirectHandler):\n def redirect_request(self,*a,**k):return None\nreq=urllib.request.Request('http://127.0.0.1:18081'+r['path'],data=data,headers=headers,method=r['method'])\nstarted=time.monotonic()\ntry:response=urllib.request.build_opener(NoRedirect).open(req,timeout=8)\nexcept urllib.error.HTTPError as e:response=e\nwith response:\n body=response.read(2097153)\n if len(body)>2097152:raise ValueError('response_too_large')\n print(json.dumps({'http_status':response.status,'headers':{k:v for k,v in response.headers.items() if k.lower() in ['content-type','set-cookie','cache-control','content-disposition','x-content-type-options','retry-after']},'body_b64':base64.b64encode(body).decode(),'latency_ms':round((time.monotonic()-started)*1000)}))\n"
DB="""import sqlite3,json,base64
c=sqlite3.connect('file:/data/app.sqlite?mode=ro',uri=True)
columns=[r[1] for r in c.execute('PRAGMA table_info(tickets)')]
keys=['id','owner','title','description','status']+[x for x in ['priority','version'] if x in columns]
rows=[dict(zip(keys,r)) for r in c.execute('SELECT '+','.join(keys)+' FROM tickets ORDER BY id')]
attachments=[dict(zip(['id','ticket_id','filename','mime','content_b64'],r[:-1]+(base64.b64encode(r[-1]).decode(),))) for r in c.execute('SELECT id,ticket_id,filename,mime,content FROM attachments ORDER BY id')]
print(json.dumps({'integrity':c.execute('PRAGMA integrity_check').fetchone()[0],'schema_version':c.execute('PRAGMA user_version').fetchone()[0],'rows':rows,'attachments':attachments,'request_count':c.execute('SELECT count(*) FROM requests').fetchone()[0]}));c.close()
"""
def raw_http(cid,req):
    owned(cid)
    p=command(['docker','exec','-i',cid,'python','-c',HTTP],input=json.dumps(req))
    if p.returncode:return {'http_status':502,'headers':{'Content-Type':'application/json'},'body_b64':base64.b64encode(b'{"error":"lab_transport_unavailable"}').decode()}
    return json.loads(p.stdout)
def http(cid,path,method='GET',body=None):
    r=raw_http(cid,{'path':path,'method':method,'headers':{'Content-Type':'application/json'},'body_b64':base64.b64encode(json.dumps(body).encode()).decode() if body is not None else None})
    try:value=json.loads(base64.b64decode(r['body_b64']))
    except ValueError:value={}
    return {'http_status':r['http_status'],'body':value}
def status():
    cid=STATE.get('active')
    if not cid:return {'active_release':None,'state':'absent'}
    d=owned(cid);h=d['HostConfig']
    result={'active_release':STATE['active_release'],'container_id':cid,'image_id':d['Image'],
            'state':d['State']['Status'],'restart_count':d['RestartCount'],
            'isolation':{'user':d['Config']['User'],'network':h['NetworkMode'],'readonly':h['ReadonlyRootfs'],
                         'memory':h['Memory'],'nano_cpus':h['NanoCpus'],'pids_limit':h['PidsLimit'],'cap_drop':h['CapDrop'],'security_opt':h['SecurityOpt']},
            'health':http(cid,'/healthz'),'readiness':http(cid,'/readyz')}
    if ORIGIN=='agent' and STATE['active_release']=='bad-v3' and result['health'].get('http_status')==200 and result['readiness'].get('http_status')==503:STATE['bad_readiness_observed']=True
    observation({'kind':'status','release':STATE['active_release'],'health_http':result['health'].get('http_status'),'ready_http':result['readiness'].get('http_status'),'restart_count':d['RestartCount']})
    return result

def build(name,files):
    if not re.fullmatch(r'app-v[12](?:-r[1-3])?',name):raise ValueError('invalid_release')
    if set(files)!=FILES:raise ValueError('invalid_build_context')
    raw={n:base64.b64decode(v,validate=True) for n,v in files.items()}
    if sum(map(len,raw.values()))>524288:raise ValueError('build_context_too_large')
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
    if name.startswith('app-v2') and 'bad-v3' not in STATE['releases']:STATE['releases']['bad-v3']={**record,'forced_unready':True}
    save(STATE);return record

def activate(name,key):
    if not isinstance(key,str) or not re.fullmatch(r'[a-z0-9-]{1,64}',key):raise ValueError('invalid_request_id')
    attempts=STATE.setdefault('activation_attempts',{});attempts[key]=attempts.get(key,0)+1;save(STATE)
    if key in STATE['requests']:
        old=STATE['requests'][key]
        if old['release']!=name:raise ValueError('request_conflict')
        return {**old,'replayed_receipt':True,'active_release_now':STATE.get('active_release')}
    if name not in STATE['releases']:raise ValueError('release_not_built')
    if STATE.get('active'):stop(STATE['active'])
    release=STATE['releases'][name]
    args=['run','-d','--name',LAB+'-'+key,'--label',LABEL+'='+LAB,'--network','none',
          '--user','10001:10001','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=33554432',
          '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','192m','--cpus','0.5',
          '--pids-limit','32','--restart','on-failure:3','--mount','type=volume,src='+STATE['volume']+',dst=/data',
          '-e','LAB_RELEASE='+name,'-e','LAB_FORCE_UNREADY='+('1' if release['forced_unready'] else '0'),release['image_id']]
    cid=docker(*args);STATE['containers'].append(cid);STATE['active']=cid;STATE['active_release']=name
    record={'release':name,'container_id':cid,'image_id':release['image_id']}
    STATE['requests'][key]=record;save(STATE)
    if key=='stage-v1':return {'error':'ACK_LOST','request_id':key,'outcome':'unknown','exit_code':75}
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
        value=STATE['requests'].get(req['request_id'],{'error':'request_not_found'})
        if req['request_id']=='stage-v1' and value.get('release')=='app-v1':
            STATE['activation_ack_reconciled']=ORIGIN=='agent' or STATE.get('activation_ack_reconciled',False)
            observation({'kind':'activation_ack_lookup','release':'app-v1','confirmed':True})
        return value
    if op=='status':return status()
    if op=='logs':
        cid=STATE.get('active');owned(cid);p=command(['docker','logs','--tail','40',cid]);return {'container_logs':(p.stdout+p.stderr)[-3500:]}
    if op=='browser':
        path=req.get('path','');method=req.get('method','GET')
        if method not in ['GET','POST','PATCH','DELETE'] or len(path)>2048 or not re.fullmatch(r'/(?:|healthz|readyz|api/(?:login|logout|me|tickets(?:/\d+(?:/attachments)?)?|requests/[A-Za-z0-9_-]{1,80}|attachments/[A-Za-z0-9_-]{1,100}))(?:\?q=[^\r\n]*)?',path):raise ValueError('invalid_http_scope')
        headers={k:v for k,v in req.get('headers',{}).items() if k.lower() in ['content-type','cookie','idempotency-key','if-match'] and isinstance(v,str) and len(v)<4096 and '\r' not in v and '\n' not in v}
        body=req.get('body_b64')
        if body is not None and (not isinstance(body,str) or len(body)>140000):raise ValueError('request_too_large')
        response=raw_http(STATE['active'],{'path':path,'method':method,'headers':headers,'body_b64':body})
        if ORIGIN=='agent' and path=='/api/tickets' and method=='POST' and response['http_status']==503 and time.time()<STATE.get('lock_until',0)+1:
            try:
                if json.loads(base64.b64decode(response['body_b64'])).get('error')=='database_busy':STATE['db_busy_observed']=True
            except (ValueError,AttributeError):pass
        category=path.split('?',1)[0];category=re.sub(r'/\d+', '/:id',category)
        if category.startswith('/api/requests/'):category='/api/requests/:key'
        if category.startswith('/api/attachments/'):category='/api/attachments/:id'
        observation({'kind':'http','release':STATE['active_release'],'path':category,'method':method,'http_status':response['http_status']})
        return response
    if op=='lock':
        cid=STATE['active'];owned(cid)
        code="import sqlite3,time; c=sqlite3.connect('/data/app.sqlite',timeout=1); c.execute('BEGIN IMMEDIATE'); print('locked',flush=True); time.sleep(8); c.rollback(); c.close()"
        child=subprocess.Popen(['docker','exec','-i',cid,'python','-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
        if child.stdout.readline().strip()!='locked':raise ValueError('lock_not_acquired')
        child.stdout.close();STATE['lock_until']=time.time()+8;observation({'kind':'fault','fault':'database_lock','seconds':8})
        return {'locked':True,'release_after_seconds':8}
    if op=='fault':raise ValueError('unsupported_fault')
    if op=='fault-evidence':
        cid=STATE.get('fault_container')
        if not cid:return {'events':[]}
        owned(cid)
        raw=docker('events','--since',str(STATE['fault_at']-1),'--until',str(int(time.time())+1),'--filter','container='+cid,'--format','{{json .}}')
        return {'events':fault_events(raw)}
    if op=='db-audit':
        cid=STATE.get('active');owned(cid);return json.loads(docker('exec',cid,'python','-c',DB))
    if op=='ledger':return {k:STATE.get(k,{} if k=='activation_attempts' else []) for k in ['requests','lookups','active_release','closed','releases','build_attempts','activation_attempts','observations','activation_ack_reconciled','bad_readiness_observed','db_busy_observed']}
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
