"""Immutable lab HTTP transport. Synthetic data only; no external network."""
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from harmonika_lab_contracts import __version__
import storage

ROOT=Path('/data')
RELEASE=os.environ.get('LAB_RELEASE','unknown')
def database():return storage.connect(ROOT/'items.sqlite')
connection=database();connection.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,status,value):
        body=json.dumps(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body);self.wfile.flush()
    def do_GET(self):
        if self.path=='/healthz':return self.reply(200,{'alive':True,'release':RELEASE,'dependency_version':__version__,'uid':os.geteuid()})
        if self.path=='/readyz':
            try:
                c=database();c.execute('SELECT count(*) FROM items').fetchone();c.close()
                ready=os.environ.get('LAB_FORCE_UNREADY')!='1'
            except Exception:ready=False
            return self.reply(200 if ready else 503,{'ready':ready,'release':RELEASE})
        if self.path=='/items':
            c=database()
            try:return self.reply(200,{'items':storage.items(c),'release':RELEASE})
            finally:c.close()
        self.reply(404,{'error':'not_found'})
    def do_POST(self):
        if self.path=='/__exam/crash':
            self.reply(202,{'crash_requested':True})
            threading.Timer(.2,lambda:os._exit(42)).start();return
        if self.path!='/items':return self.reply(404,{'error':'not_found'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096:raise ValueError()
            payload=json.loads(self.rfile.read(length));c=database()
            try:code,value=storage.put(c,payload)
            finally:c.close()
            self.reply(code,value)
        except (ValueError,TypeError,KeyError):self.reply(400,{'error':'invalid_input'})
        except Exception:self.reply(500,{'error':'storage_failure'})

ThreadingHTTPServer(('127.0.0.1',18081),Handler).serve_forever()
