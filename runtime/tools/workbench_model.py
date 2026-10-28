"""Read-only terminal telemetry. Never query message bodies or log customer data."""
from contextlib import closing
import ctypes
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

STATES={'online','offline','busy','vram_full','warming','timeout','bad_payload','auth_failed','recovery_required','unavailable','unknown','not_checked'}


def clean(text,limit=120):
    return re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]', '',str(text)).replace('#','').strip()[:limit]


def read_context(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_uid!=os.getuid() or p.stat().st_mode & 0o077:
        raise ValueError('unsafe_workbench_context')
    value=json.loads(p.read_text())
    if not isinstance(value,dict) or value.get('version')!=1:raise ValueError('invalid_workbench_context')
    return value


def native_metadata(directory,session=None):
    result={'status':'empty','sessions':0,'prompt_tokens':None,'completion_tokens':None,'cost':None,'active_responses':0,'response_age':None}
    paths=[p for p in Path(directory).glob('*.db') if p.is_file() and not p.is_symlink()]
    if not paths:return result
    if len(paths)!=1:return dict(result,status='unavailable')
    try:
        with closing(sqlite3.connect(paths[0].absolute().as_uri()+'?mode=ro',uri=True,timeout=.15)) as c:
            c.execute('PRAGMA query_only=ON');deadline=time.monotonic()+.2
            c.set_progress_handler(lambda:int(time.monotonic()>deadline),500)
            required={'id','prompt_tokens','completion_tokens','cost'}
            if not required.issubset({r[1] for r in c.execute('PRAGMA table_info(sessions)')}):return dict(result,status='unavailable')
            query='SELECT count(*),coalesce(sum(prompt_tokens),0),coalesce(sum(completion_tokens),0),coalesce(sum(cost),0) FROM sessions'
            args=()
            if session:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',session):return dict(result,status='unavailable')
                query+=' WHERE id=?';args=(session,)
            count,prompt,completion,cost=c.execute(query,args).fetchone()
            if count:
                result.update(status='ok',sessions=int(count),prompt_tokens=int(prompt) if prompt else None,
                    completion_tokens=int(completion) if completion else None,cost=float(cost) if cost and math.isfinite(cost) else None)
            columns={r[1] for r in c.execute('PRAGMA table_info(messages)')}
            if {'role','finished_at','created_at','session_id'}.issubset(columns):
                cutoff=int(time.time())-900
                where="role='assistant' AND (finished_at IS NULL OR finished_at=0) AND created_at>?"
                params=(cutoff,)
                if session:where+=' AND session_id=?';params+=(session,)
                active,first=c.execute('SELECT count(*),min(created_at) FROM messages WHERE '+where,params).fetchone()
                result['active_responses']=int(active)
                if first:result['response_age']=max(0,int(time.time()-first))
    except (sqlite3.Error,OSError,TypeError,ValueError):result['status']='unavailable'
    return result


def cpu_ticks():
    try:
        if sys.platform=='darwin':
            lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib');lib.mach_host_self.restype=ctypes.c_uint
            values=(ctypes.c_uint32*4)();size=ctypes.c_uint32(4)
            if lib.host_statistics(lib.mach_host_self(),3,ctypes.byref(values),ctypes.byref(size))!=0:return None
            return [int(values[0])+int(values[1])+int(values[3]),int(values[2])]
        if sys.platform.startswith('linux'):
            ticks=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
            idle=ticks[3]+(ticks[4] if len(ticks)>4 else 0)
            return [sum(ticks[:8])-idle,idle]
    except (OSError,ValueError,IndexError):pass
    return None


def memory_usage():
    try:
        if sys.platform=='darwin':
            total=int(subprocess.check_output(['/usr/sbin/sysctl','-n','hw.memsize'],timeout=1))
            raw=subprocess.check_output(['/usr/bin/vm_stat'],timeout=1,text=True)
            page=int(re.search(r'page size of (\d+)',raw).group(1))
            values={m.group(1):int(m.group(2)) for m in re.finditer(r'^([^:\n]+):\s*(\d+)',raw,re.M)}
            available=sum(values.get(k,0) for k in ['Pages free','Pages inactive','Pages speculative'])*page
        elif sys.platform.startswith('linux'):
            values={m.group(1):int(m.group(2))*1024 for m in re.finditer(r'^(\w+):\s*(\d+)',Path('/proc/meminfo').read_text(),re.M)}
            total=values['MemTotal'];available=values.get('MemAvailable',values.get('MemFree',0))
        else:return None
        return round(max(0,min(100,100*(total-available)/total)),1)
    except (OSError,ValueError,KeyError,AttributeError,subprocess.SubprocessError):return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def probe_api(config):
    p=Path(config)/'harmonika-api-key'
    try:
        if p.is_symlink() or p.stat().st_mode & 0o077 or p.stat().st_uid!=os.getuid():return {'status':'not_configured'}
        key=p.read_text().strip()
        if not key or '\n' in key:return {'status':'not_configured'}
        request=urllib.request.Request('https://chat.harmonika.id/v1/models',headers={'Authorization':'Bearer '+key})
        started=time.monotonic()
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=2) as r:
            if r.status!=200:return {'status':'unavailable'}
            r.read(16384)
        return {'status':'online','latency_ms':round((time.monotonic()-started)*1000)}
    except Exception:return {'status':'offline'}


def sample(context,previous=None,remote=True):
    previous=previous or {};now=time.time();ticks=cpu_ticks();cpu=None;old=previous.get('_cpu_ticks')
    if ticks and isinstance(old,list) and len(old)==2:
        busy=ticks[0]-old[0];idle=ticks[1]-old[1]
        if busy>=0 and idle>=0 and busy+idle>0:cpu=round(100*busy/(busy+idle),1)
    try:d=shutil.disk_usage(context['project']);disk=round(100*d.used/d.total,1)
    except OSError:disk=None
    result={'version':1,'sampled_at':now,'cpu':cpu,'ram':memory_usage(),'disk':disk,'_cpu_ticks':ticks,
            'native':native_metadata(context['native_dir'],context.get('session')),
            'api':previous.get('api',{'status':'not_checked'}),'api_checked_at':previous.get('api_checked_at',0),
            'graphics':previous.get('graphics','not_checked'),'registry_errors':previous.get('registry_errors',[]),
            'latency_history':previous.get('latency_history',[])[-23:],'events':previous.get('events',[])[-99:]}
    if remote and now-result['api_checked_at']>=15:
        api=probe_api(context['config']);result.update(api=api,api_checked_at=now)
        if api.get('latency_ms') is not None:result['latency_history'].append(api['latency_ms'])
        if api.get('status')!=previous.get('api',{}).get('status'):
            result['events'].append({'at':now,'level':'INFO' if api.get('status')=='online' else 'WARN','code':'engine_'+api.get('status','unknown')})
        try:
            # Reuse the gateway's redacted mechanics-only discovery contract.
            import importlib.util
            spec=importlib.util.spec_from_file_location('workbench_state_reader',Path(__file__).with_name('state_store.py'))
            reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
            # Only query the owner's configured installation; fixture HOME never
            # borrows its key during PTY tests.
            if context.get('allow_discovery'):
                value=reader.engine_discovery_status();diag=reader.sanitize_engine_diagnostics(value.get('diagnostics'))
                result['graphics']=diag['graphics']['status']
                result['registry_errors']=[*diag['registry_errors']['recent'],*diag['graphics_errors']['recent']][:10]
        except Exception:pass
    return result


def load_sample(context_file,refresh=False):
    context=read_context(context_file);target=Path(context_file).with_name('telemetry.json')
    previous={}
    try:
        if target.is_file() and not target.is_symlink() and target.stat().st_size<262144 and target.stat().st_uid==os.getuid() and not target.stat().st_mode & 0o077:
            previous=json.loads(target.read_text())
    except (OSError,ValueError):pass
    if not isinstance(previous,dict):previous={}
    if not refresh:return previous
    result=sample(context,previous,remote=not context.get('offline',False))
    temp=target.with_name('.telemetry-'+str(os.getpid()))
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as f:json.dump(result,f)
        os.replace(temp,target)
    finally:
        if temp.exists():temp.unlink()
    return result


def sparkline(values):
    if not values:return '—'
    low=min(values);span=max(values)-low
    return ''.join('▁▂▃▄▅▆▇█'[int((v-low)/span*7) if span else 0] for v in values[-24:])


def status_line(data,compact=False):
    api=data.get('api',{});label={'online':'Online','offline':'Offline','not_configured':'Belum terhubung'}.get(api.get('status'),'Memeriksa')
    fields=[label]
    if isinstance(api.get('latency_ms'),(int,float)):fields.append('API '+str(round(api['latency_ms']))+'ms')
    for key,title in ([] if compact else [('cpu','CPU'),('ram','RAM')]):
        if isinstance(data.get(key),(int,float)):fields.append(title+' '+str(round(data[key]))+'%')
    active=data.get('native',{}).get('active_responses',0)
    if active:fields.append('Respons '+str(active))
    fields.append('F2 · F3 · F4' if compact else 'F2 Metrik · F3 Info · F4 File')
    return clean(' | '.join(fields),210)
