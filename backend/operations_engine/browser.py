"""Owned loopback-only Playwright assertions against a bounded project snapshot."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from ._adapter import artifact_root, checked, environment, execute, project, relative, snapshot, tool

WORKER=Path(__file__).with_name('browser_worker.cjs')
ACTIONS={'click','fill','check','select','assert_visible','assert_text','assert_count','assert_value'}


def _runtime(config):
    node=tool(config,'node');module=config.get('tools',{}).get('playwright_module')
    if not node or not isinstance(module,str) or not Path(module).is_absolute() or not Path(module,'package.json').is_file():return None
    try:
        module=str(Path(module).resolve(strict=True))
        checked(module,directory=True)
        code,raw=execute([node,str(WORKER),'--capabilities'],cwd=WORKER.parent,data={'playwright_module':module},timeout=5,env=environment(),maximum=16384)
        result=json.loads(raw.decode('utf-8'))
        if code!=0 or result.get('available') is not True:return None
        return node,module,result.get('version')
    except (OSError,ValueError,TypeError):return None


def capabilities(config):
    runtime=_runtime(config)
    return {'status':'available' if runtime else 'needs_toolchain','enabled':config.get('browser_policy') is True,'playwright_version':runtime[2] if runtime else None,'verified_run':False,'reason':None if runtime else 'browser_runtime_missing','scope':'owned_loopback_static_project','retention_seconds':86400}


def _validate(payload):
    if not isinstance(payload,dict) or set(payload)-{'path','steps','viewport'}:raise ValueError('invalid_browser_payload')
    path=relative(payload.get('path'))
    if path.suffix.lower() not in ('.html','.htm'):raise ValueError('browser_html_entry_required')
    steps=payload.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=24:raise ValueError('invalid_browser_steps')
    assertions=0
    for step in steps:
        if not isinstance(step,dict) or set(step)-{'action','selector','value','count'} or step.get('action') not in ACTIONS:raise ValueError('invalid_browser_step')
        selector=step.get('selector')
        if not isinstance(selector,dict) or set(selector)-{'by','value','name'} or selector.get('by') not in ('css','test_id','role'):raise ValueError('invalid_browser_selector')
        value=selector.get('value')
        if not isinstance(value,str) or not value or len(value)>200 or any(ord(c)<32 for c in value) or '>>' in value:raise ValueError('invalid_browser_selector')
        if selector['by']=='role' and not re.fullmatch('[a-z]{2,30}',value):raise ValueError('invalid_browser_selector')
        if 'name' in selector and (selector['by']!='role' or not isinstance(selector['name'],str) or not 1<=len(selector['name'])<=200):raise ValueError('invalid_browser_selector')
        if step['action'] in ('fill','select','assert_text','assert_value') and (not isinstance(step.get('value'),str) or len(step['value'])>1000):raise ValueError('invalid_browser_value')
        if step['action']=='assert_count' and (type(step.get('count')) is not int or not 0<=step['count']<=1000):raise ValueError('invalid_browser_count')
        if step['action'].startswith('assert_'):assertions+=1
    if not assertions:raise ValueError('browser_assertion_required')
    view=payload.get('viewport',{'width':1280,'height':900})
    if not isinstance(view,dict) or set(view)!={'width','height'} or any(type(view.get(k)) is not int for k in ('width','height')) or not 320<=view['width']<=1920 or not 240<=view['height']<=1080:raise ValueError('invalid_browser_viewport')
    return str(path),steps,view


class _Static(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,root,files):
        self.root,self.files=root,set(files)
        self.requests,self.bytes,self.lock=0,0,threading.Lock()
        super().__init__(('127.0.0.1',0),_Handler)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_POST(self):self.send_error(405)
    def do_GET(self):
        try:
            if self.headers.get('Host')!='127.0.0.1:'+str(self.server.server_port):raise ValueError('host_denied')
            path=unquote(urlsplit(self.path).path).lstrip('/')
            relative(path)
            if path not in self.server.files:raise ValueError('file_denied')
            data=(self.server.root/path).read_bytes()
            if len(data)>4*1024*1024:raise ValueError('file_limit')
            with self.server.lock:
                self.server.requests+=1;self.server.bytes+=len(data)
                if self.server.requests>256 or self.server.bytes>64*1024*1024:raise ValueError('http_budget')
            self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self' data:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers();self.wfile.write(data)
        except (OSError,ValueError):self.send_error(404)


def run(config,payload):
    entry,steps,viewport=_validate(payload)
    if config.get('browser_policy') is not True:raise ValueError('browser_not_allowed')
    root,selected=project(config,'.');checked(root/entry)
    runtime=_runtime(config)
    if not runtime:return {'ok':False,'status':'needs_toolchain','reason':'browser_runtime_missing'}
    artifacts=artifact_root(config);run_id='browser_'+uuid4().hex;out=artifacts/run_id;out.mkdir(mode=0o700)
    created=int(time.time());retention={'schema_version':1,'created_at':created,'expires_at':created+86400}
    (out/'retention.json').write_text(json.dumps(retention),encoding='utf-8');(out/'retention.json').chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='browser-source-',dir=artifacts) as temporary, tempfile.TemporaryDirectory(prefix='browser-traces-',dir=artifacts) as trace_temporary:
        stage=Path(temporary);copied=snapshot(root,selected,stage)
        if entry not in copied['files']:raise ValueError('browser_entry_excluded')
        server=_Static(stage,copied['files']);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            code,raw=execute([runtime[0],str(WORKER)],cwd=stage,data={'playwright_module':runtime[1],'origin':'http://127.0.0.1:'+str(server.server_port),'entry':entry,'steps':steps,'viewport':viewport,'artifacts':str(out),'trace_temporary':str(trace_temporary)},timeout=25,env=environment(),maximum=131072,watch=lambda:sum(p.stat().st_size for p in out.rglob('*') if p.is_file())<=32*1024*1024)
        finally:server.shutdown();server.server_close();thread.join(timeout=2)
    if code!=0:raise ValueError('browser_execution_failed')
    try:data=json.loads(raw.decode('utf-8'))
    except (ValueError,UnicodeError):raise ValueError('invalid_browser_report') from None
    if not isinstance(data,dict) or data.get('status') not in ('passed','failed','needs_toolchain') or not isinstance(data.get('steps'),list):raise ValueError('invalid_browser_report')
    # Rebuild the DTO, never forward arbitrary browser/page output or exception text.
    rows=[]
    for i,row in enumerate(data['steps']):
        if i>=len(steps) or not isinstance(row,dict) or row.get('index')!=i or type(row.get('passed')) is not bool:raise ValueError('invalid_browser_report')
        rows.append({'index':i,'action':steps[i]['action'],'passed':row['passed'],'error':None if row['passed'] else 'assertion_or_action_failed'})
    counts={name:data.get(name,0) for name in ('blocked_requests','blocked_popups','blocked_downloads','page_errors')}
    if any(type(value) is not int or not 0<=value<=10000 for value in counts.values()):raise ValueError('invalid_browser_report')
    passed=data['status']=='passed' and len(rows)==len(steps) and all(row['passed'] for row in rows) and not any(counts.values())
    files=[]
    for name in ('trace.zip','screenshot.png'):
        file=out/name
        if file.is_file() and not file.is_symlink():
            size=file.stat().st_size
            if size>16*1024*1024:raise ValueError('browser_artifact_limit')
            file.chmod(0o600);files.append({'id':run_id+'/'+name,'bytes':size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'expires_at':created+86400,'private':True})
    return {'ok':passed,'status':'passed' if passed else 'needs_toolchain' if data['status']=='needs_toolchain' else 'failed','reason':'browser_launch_unavailable' if data['status']=='needs_toolchain' else None,'execution_performed':data['status']!='needs_toolchain','steps':rows,'assertions_requested':sum(step['action'].startswith('assert_') for step in steps),'artifacts':files,'retention_seconds':86400,'coverage':'owned_loopback_static_project','files_copied':copied['files_copied'],'files_skipped':copied['files_skipped'],**counts,'limitations':['Only the selected static project snapshot and supplied assertions were tested. No external site, customer session, package script, application server or deployment was accessed.']}
