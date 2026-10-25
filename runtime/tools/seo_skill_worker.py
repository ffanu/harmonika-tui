"""SEO worker for the shared registry, independent from LLM providers."""
import json
import os
from pathlib import Path
import resource
import signal
import sys

class WorkerAbort(BaseException):
    def __init__(self,code):self.code=code

def abort(signum,frame):
    raise WorkerAbort('stopped' if signum==signal.SIGTERM else 'resource_limit')

signal.signal(signal.SIGTERM,abort)
signal.signal(signal.SIGXCPU,abort)
resource.setrlimit(resource.RLIMIT_CPU,(18,20))
# Browser workers have multiple owned processes; executor gives the job its own session.
# Chromium reserves large virtual address spaces; do not inherit an AS cap into browser jobs.
TOOLS=Path(__file__).resolve().parent;sys.path.insert(0,str(TOOLS))
BASE=TOOLS.parent.parent if TOOLS.parent.name=='runtime' else TOOLS.parent
sys.path.insert(0,str(BASE/'skills/harmonika-seo/scripts'))
from skill_registry import Registry
from skill_executor import scoped
from seo_network import SafeFetcher,validate_url
from seo_extra import google_config,response,google,performance,backlinks,drift
from seo_visual import capture,browser_capabilities
from seo_analyzers import analyze


def capabilities(principal=None):
    operations={}
    for item in Registry().list():
        if item.get('worker')=='seo':operations[item['id']]={'status':'available','reason':None}
    operations['seo.performance']={'status':'degraded','reason':'live_pagespeed_provider_quota_and_network_required'}
    browser_ready=browser_capabilities()
    operations['seo.visual']={'status':'available' if browser_ready else 'unavailable','reason':'javascript_disabled_render_only' if browser_ready else 'browser_runtime_missing'}
    _,reason=google_config(principal or 'local:'+str(os.getuid()))
    for name in ('search_console','ga4'):
        operations['seo.'+name]={'status':'unavailable' if reason else 'degraded','reason':reason or 'configured_connection_not_verified'}
    return {'schema_version':1,'operations':operations}

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--capabilities']:
            print(json.dumps(capabilities()));sys.exit(0)
        raw=sys.stdin.buffer.read(1048577)
        if len(raw)>1048576:raise ValueError('input_too_large')
        body=json.loads(raw);name=body['skill_id'];payload=body['input'];registry=Registry();registry.validate_input(name,payload)
        item=registry.get(name);root=Path(body['project_root']).resolve()
        if sys.platform.startswith('linux') and name!='seo.visual':resource.setrlimit(resource.RLIMIT_AS,(1024*1024*1024,1024*1024*1024))
        for field in item.get('path_fields',[]):scoped(root,payload[field])
        if 'url' in payload:payload['url']=validate_url(payload['url'])
        fetcher=SafeFetcher(max_requests=20 if name=='seo.visual' else 8,max_bytes=8388608 if name=='seo.performance' else 2097152,budget_seconds=62 if name=='seo.performance' else 22,timeout_seconds=55 if name=='seo.performance' else 8)
        if name=='seo.performance':result=performance(payload,fetcher,body.get('principal_id',''))
        elif name=='seo.visual':result=capture(payload,fetcher)
        elif name=='seo.drift':result=drift(payload,fetcher)
        elif name=='seo.backlinks':result=backlinks(payload,fetcher)
        elif name in ('seo.search_console','seo.ga4'):result=google(name,payload,fetcher,body.get('principal_id',''))
        else:result=analyze(name,payload,fetcher)
        result['content_is_untrusted']=True
        for part in json.JSONEncoder(ensure_ascii=False,allow_nan=False).iterencode({'ok':True,'result':result}):sys.stdout.write(part)
    except WorkerAbort as error:
        sys.stderr.write(json.dumps({'code':error.code}));sys.exit(2)
    except Exception as error:
        code=getattr(error,'code','seo_worker_failed')
        if not isinstance(code,str) or not code.replace('_','').isalnum() or len(code)>64:code='seo_worker_failed'
        sys.stderr.write(json.dumps({'code':code}));sys.exit(2)
