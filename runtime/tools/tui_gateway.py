"""Private loopback chat gateway: bounded streaming, reasoning and pre-output failover."""
import argparse
from contextlib import closing
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import urllib.request

# The edge gateway translates the stable TUI wire contract to the generic engine.
TOOLS=Path(__file__).resolve().parent
if __package__:
    from . import ai_integration as integration
    from . import ai_adapters as adapters
else:
    if str(TOOLS) not in sys.path:sys.path.insert(0,str(TOOLS))
    import ai_integration as integration
    import ai_adapters as adapters
CONFIG=adapters.CONFIG
PORT=adapters.PORT
MAX_BODY=adapters.MAX_BODY
GatewayError=integration.IntegrationError
Normalizer=integration.Normalizer
settings=adapters.settings
local_ready=adapters.local_ready
opener=adapters.opener
bounded_json=adapters.bounded_json

class Engine:
    def __init__(self,key,config,open_request=None,ready=None,wrapper=None):
        self.key=key;self.config=config
        self.open_request=open_request or (lambda req:opener().open(req,timeout=120))
        self.ready=ready or (lambda:local_ready(config));self.slots=threading.BoundedSemaphore(2)
        self.wrapper=wrapper or adapters.build_integration(key,config,self.open_request,lambda:self.ready())
    def local_body(self,body):
        return adapters.local_payload(integration.AIRequest.from_openai(body),body.get('stream',False),self.ready)[0]
    def stream(self,body):
        request=integration.AIRequest.from_openai(body)
        yield from integration.openai_stream(self.wrapper.stream(request))
    def complete(self,body):
        request=integration.AIRequest.from_openai(body)
        return integration.openai_response(self.wrapper.complete(request))

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def authenticated(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.engine.key)
    def json(self,code,value):
        data=json.dumps(value).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        if not self.authenticated():return self.json(401,{'error':{'code':'invalid_token'}})
        if self.path=='/v1/models':return self.json(200,{'object':'list','data':[{'id':'harmonika-ai','object':'model','owned_by':'Harmonika Ai'}]})
        if self.path=='/health':return self.json(200,{'ok':True,'provider':'Harmonika Ai','reasoning_parser':True,'integration_contract':1,'local':{k:v for k,v in self.server.engine.ready().items() if k!='model'}})
        self.json(404,{'error':{'code':'not_found'}})
    def do_POST(self):
        if not self.authenticated():return self.json(401,{'error':{'code':'invalid_token'}})
        if self.path!='/v1/chat/completions':return self.json(404,{'error':{'code':'not_found'}})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=MAX_BODY:raise GatewayError('invalid_body_size')
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict) or not isinstance(body.get('messages'),list) or not body['messages'] or any(not isinstance(m,dict) for m in body['messages']):raise GatewayError('invalid_messages')
            if body.get('n',1)!=1:raise GatewayError('unsupported_count')
            if type(body.get('stream',False)) is not bool:raise GatewayError('invalid_stream')
            integration.AIRequest.from_openai(body)
        except Exception:return self.json(400,{'error':{'code':'invalid_request'}})
        engine=self.server.engine
        if not engine.slots.acquire(False):return self.json(429,{'error':{'code':'in_progress'}})
        started=False
        try:
            if not body.get('stream'):return self.json(200,engine.complete(body))
            for event in engine.stream(body):
                if not started:
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.send_header('Connection','close');self.end_headers();started=True;self.close_connection=True
                raw=event if isinstance(event,str) else json.dumps(event,ensure_ascii=False)
                self.wfile.write(('data: '+raw+'\n\n').encode());self.wfile.flush()
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as e:
            code=str(e) if isinstance(e,GatewayError) else 'upstream_unavailable'
            if not started:self.json(503,{'error':{'code':code,'message':'Harmonika Ai belum dapat menyelesaikan respons.'}})
            else:
                try:self.wfile.write(('data: '+json.dumps({'error':{'code':code}})+'\n\n').encode());self.wfile.flush()
                except OSError:pass
        finally:engine.slots.release()

class Server(ThreadingHTTPServer):
    daemon_threads=True
    def get_request(self):
        sock,address=super().get_request();sock.settimeout(25);return sock,address

def read_key(config=CONFIG):
    file=config/'harmonika-api-key'
    if file.is_symlink() or (os.name!='nt' and (file.stat().st_mode & 0o077)):raise GatewayError('unsafe_key_permissions')
    key=file.read_text().strip()
    if not key or '\n' in key or '\r' in key:raise GatewayError('invalid_key')
    return key

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['serve','status']);args=parser.parse_args();config=settings()
    if args.action=='status':
        running=False;health={}
        try:
            req=urllib.request.Request('http://127.0.0.1:'+str(config['port'])+'/health',headers={'Authorization':'Bearer '+read_key()})
            with closing(opener().open(req,timeout=2)) as response:health=bounded_json(response);running=health.get('ok') is True
        except Exception:pass
        print(json.dumps({'provider':'Harmonika Ai','gateway_running':running,'integration_contract_available':1,'integration_contract_active':health.get('integration_contract')==1,'reasoning_parser_active':running,'local':{k:v for k,v in local_ready(config).items() if k!='model'},'port':config['port'],'fallback_enabled':config['fallback_enabled']}));return
    server=Server(('127.0.0.1',config['port']),Handler);server.engine=Engine(read_key(),config)
    try:server.serve_forever(poll_interval=.25)
    finally:server.server_close()
if __name__=='__main__':
    try:main()
    except Exception:print(json.dumps({'ok':False,'error':'gateway_unavailable'}),file=sys.stderr);sys.exit(1)
