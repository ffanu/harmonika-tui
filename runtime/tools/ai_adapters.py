"""Explicit inference adapter registry; no TUI, history or tool execution."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit
if __package__:
    from .ai_integration import AIAdapter,IntegrationError,OpenAICompatibleAdapter,AIIntegration
else:
    from ai_integration import AIAdapter,IntegrationError,OpenAICompatibleAdapter,AIIntegration
CONFIG=Path(os.environ.get('HARMONIKA_CONFIG_DIR',str(Path.home()/'.config/harmonika')))
PORT=18184
MAX_BODY=2*1024*1024
GatewayError=IntegrationError

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise GatewayError('redirect_refused')
def opener():return urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def bounded_json(response,limit=MAX_BODY):
    body=response.read(limit+1)
    if len(body)>limit:raise GatewayError('response_too_large')
    result=json.loads(body)
    if not isinstance(result,dict):raise GatewayError('invalid_response')
    return result

def settings(config=CONFIG):
    file=config/'tui-network.json';result={'port':PORT,'local_model':'','fallback_enabled':True,'primary_adapter':'teacher','fallback_adapter':'ollama','custom_base_url':'http://127.0.0.1:11435/v1','custom_model':'','custom_tools':False,'custom_image_input':False}
    if file.exists():
        value=json.loads(file.read_text())
        if not isinstance(value,dict):raise GatewayError('invalid_settings')
        for k in result:
            if k in value:result[k]=value[k]
    if type(result['port']) is not int or not 1024<=result['port']<=65535:raise GatewayError('invalid_port')
    if not isinstance(result['local_model'],str) or len(result['local_model'])>120:raise GatewayError('invalid_local_model')
    if type(result['fallback_enabled']) is not bool:raise GatewayError('invalid_settings')
    for name in ['primary_adapter','fallback_adapter']:
        if not isinstance(result[name],str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',result[name]):raise GatewayError('invalid_adapter_name')
    if any(type(result[name]) is not bool for name in ['custom_tools','custom_image_input']):raise GatewayError('invalid_custom_capabilities')
    if not isinstance(result['custom_model'],str) or len(result['custom_model'])>120:raise GatewayError('invalid_custom_model')
    return result

def local_ready(config,request=None):
    request=request or (lambda req:opener().open(req,timeout=1))
    try:
        with closing(request(urllib.request.Request('http://127.0.0.1:11434/api/tags'))) as response:tags=bounded_json(response,262144).get('models',[])
        models=[m['name'] for m in tags if isinstance(m,dict) and isinstance(m.get('name'),str) and isinstance(m.get('size'),int) and m['size']>0 and not m['name'].endswith(':cloud') and not m.get('remote_host') and not m.get('remote_model')]
        selected=config.get('local_model') or (models[0] if len(models)==1 else '')
        if selected not in models:return {'ready':False,'reason':'local_model_required'}
        req=urllib.request.Request('http://127.0.0.1:11434/api/show',data=json.dumps({'model':selected}).encode(),headers={'Content-Type':'application/json'})
        with closing(request(req)) as response:info=bounded_json(response,262144)
        if info.get('remote_host') or info.get('remote_model'):return {'ready':False,'reason':'remote_model_refused'}
        caps=info.get('capabilities',[])
        if 'completion' not in caps:return {'ready':False,'reason':'local_model_incompatible'}
        return {'ready':True,'model':selected,'tools':'tools' in caps}
    except Exception:return {'ready':False,'reason':'local_runtime_unavailable'}


def local_payload(request,stream,ready):
    available=ready()
    if not available.get('ready'):raise IntegrationError(available.get('reason','offline_unavailable'))
    if request.tools and not available.get('tools'):raise IntegrationError('offline_tools_unavailable')
    if any(m.get('content') is not None and not isinstance(m['content'],str) for m in request.messages):raise IntegrationError('offline_media_unavailable')
    body=request.transport_payload(available['model'],stream);body.pop('reasoning_effort',None)
    return body,'local'

class AdapterRegistry:
    def __init__(self):self._factories={}
    def register(self,name,factory):
        if not isinstance(name,str) or not re.fullmatch('[a-z][a-z0-9_-]{0,31}',name) or name in self._factories or not callable(factory):raise IntegrationError('invalid_adapter_registration')
        self._factories[name]=factory
    def create(self,name,**context):
        if name not in self._factories:raise IntegrationError('unknown_adapter')
        adapter=self._factories[name](**context)
        if not isinstance(adapter,AIAdapter):raise IntegrationError('invalid_adapter')
        return adapter

def teacher(*,key,config,open_request,ready):
    return OpenAICompatibleAdapter('https://chat.harmonika.id/v1/chat/completions',lambda request,stream:(request.transport_payload('harmonika-ai',stream),key),open_request)

def ollama(*,key,config,open_request,ready):
    # key is deliberately not captured or forwarded to the local transport.
    return OpenAICompatibleAdapter('http://127.0.0.1:11434/v1/chat/completions',lambda request,stream:local_payload(request,stream,ready),open_request)

def custom(*,key,config,open_request,ready):
    base=config.get('custom_base_url','http://127.0.0.1:11435/v1')
    if not isinstance(base,str):raise IntegrationError('invalid_custom_endpoint')
    parsed=urlsplit(base)
    if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'} or parsed.username or parsed.password or parsed.query or parsed.fragment:raise IntegrationError('custom_endpoint_must_be_loopback')
    try:port=parsed.port or 80
    except ValueError:raise IntegrationError('invalid_custom_endpoint') from None
    if not 1<=port<=65535:raise IntegrationError('invalid_custom_endpoint')
    model=config.get('custom_model','')
    if not isinstance(model,str) or not model or len(model)>120:raise IntegrationError('custom_model_required')
    def prepare(request,stream):
        if request.tools and not config.get('custom_tools',False):raise IntegrationError('custom_tools_unavailable')
        if not config.get('custom_image_input',False) and any(m.get('content') is not None and not isinstance(m['content'],str) for m in request.messages):raise IntegrationError('custom_media_unavailable')
        return request.transport_payload(model,stream),'local'
    return OpenAICompatibleAdapter(base.rstrip('/')+'/chat/completions',prepare,open_request)

REGISTRY=AdapterRegistry()
for name,factory in [('teacher',teacher),('ollama',ollama),('custom',custom)]:REGISTRY.register(name,factory)

def build_integration(key,config,open_request=None,ready=None,registry=None,credential_resolver=None):
    registry=registry or REGISTRY
    open_request=open_request or (lambda req:opener().open(req,timeout=20))
    ready=ready or (lambda:local_ready(config))
    primary=config.get('primary_adapter','teacher');fallback=config.get('fallback_adapter','ollama')
    def create(name):
        scoped_key=credential_resolver(name) if credential_resolver else (key if name=='teacher' else '')
        return registry.create(name,key=scoped_key,config=config,open_request=open_request,ready=ready)
    selected=create(primary)
    secondary=create(fallback) if config.get('fallback_enabled',True) and primary!=fallback else None
    return AIIntegration(selected,secondary)
