"""Reusable protected stdlib HTTP/auth runtime for the scoped fullstack engine."""
import errno,json,mimetypes,os,socket,sqlite3,sys,threading,re
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,unquote
from security import AuthError,Security
LIMIT=32768
CSP="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
class HTTPError(Exception):
 def __init__(self,status,code):self.status=status;self.code=code;super().__init__(code)
def classify(e):
 if getattr(e,'sqlite_errorcode',None) in [5,6] or isinstance(e,sqlite3.OperationalError) and str(e).lower() in ['database is locked','database table is locked']:return 503,'sqlite_busy'
 if hasattr(e,'status') and hasattr(e,'code') and isinstance(e.status,int) and isinstance(e.code,str) and re.fullmatch('[a-z][a-z0-9_]{0,63}',e.code):return e.status,e.code
 return 503,'internal_error'
class RuntimeHandler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.0'
 def log_message(self,*args):pass
 def log_error(self,*args):pass
 def setup(self):
  super().setup();self.connection.settimeout(8)
 def reply(self,status,body,headers=None):
  raw=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode('utf-8');self.bytes_reply(status,raw,{'Content-Type':'application/json; charset=utf-8',**(headers or {})})
 def bytes_reply(self,status,raw,headers=None):
  self.send_response(status)
  defaults={'Content-Type':'application/octet-stream','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':CSP,'Cache-Control':'no-store','Content-Length':str(len(raw))}
  for k,v in {**defaults,**(headers or {})}.items():self.send_header(k,v)
  self.end_headers()
  if self.command!='HEAD':self.wfile.write(raw)
 def body(self):
  text=self.headers.get('Content-Length','0')
  if not text.isdigit():raise HTTPError(422,'json_invalid')
  size=int(text)
  if size>LIMIT:raise HTTPError(413,'body_too_large')
  if self.headers.get('Transfer-Encoding'):raise HTTPError(422,'json_invalid')
  try:value=json.loads(self.rfile.read(size).decode('utf-8'))
  except (ValueError,UnicodeError):raise HTTPError(422,'json_invalid') from None
  if not isinstance(value,dict):raise HTTPError(422,'json_invalid')
  return value
 def dispatch(self):
  try:
   method=self.command;u=urlsplit(self.path);path=unquote(u.path)
   if any(x in path for x in ['..','\x00','\\']) or len(self.path)>2000:return self.reply(404,{'error':'not_found'})
   if method not in ['GET','POST','PATCH','DELETE']:return self.reply(405,{'error':'method_not_allowed'})
   if method=='GET' and path in self.server.static_files:
    source=self.server.static_files[path]
    if not source.is_file() or source.is_symlink():return self.reply(404,{'error':'not_found'})
    return self.bytes_reply(200,source.read_bytes(),{'Content-Type':mimetypes.guess_type(str(source))[0] or 'application/octet-stream'})
   if path=='/api/login' and method=='POST':
    self.server.security.check_origin(self.headers);result=self.server.security.login(self.body(),self.headers,self.client_address[0]);return self.reply(200,result[0],{'Set-Cookie':result[1]})
   if method=='GET' and path=='/healthz':return self.reply(200,{'status':'ok','release':self.server.release})
   if method=='GET' and path=='/readyz':
    conn=sqlite3.connect(self.server.db_path,timeout=.2)
    try:conn.execute('BEGIN IMMEDIATE');conn.rollback()
    finally:conn.close()
    return self.reply(200,{'ready':True})
   principal=None;data=None
   if path.startswith('/api/'):
    principal,csrf=self.server.security.resolve(self.headers)
    if method in ['POST','PATCH','DELETE']:self.server.security.require_mutation(self.headers,csrf)
    if path=='/api/session' and method=='GET':return self.reply(200,{'user':principal,'csrf':csrf})
    if path=='/api/logout' and method=='POST':
     if self.headers.get('Content-Length','0')!='0' or self.headers.get('Transfer-Encoding'):
      if self.body():raise HTTPError(422,'logout_invalid')
     return self.reply(200,{'logged_out':True},{'Set-Cookie':self.server.security.logout(self.headers)})
    if method in ['POST','PATCH','DELETE']:data=self.body()
   try:query=parse_qs(u.query,keep_blank_values=True,max_num_fields=12)
   except ValueError:raise HTTPError(422,'query_invalid') from None
   if any(len(v)!=1 for v in query.values()):raise HTTPError(422,'query_invalid')
   return self.route(method,path,{k:v[0] for k,v in query.items()},data,principal)
  except (BrokenPipeError,ConnectionResetError,socket.timeout):return
  except Exception as error:
   status,code=classify(error)
   try:self.reply(status,{'error':code})
   except (BrokenPipeError,ConnectionResetError,socket.timeout):pass
 def route(self,method,path,query,data,principal):return self.reply(404,{'error':'not_found'})
 do_GET=dispatch;do_POST=dispatch;do_PATCH=dispatch;do_DELETE=dispatch;do_PUT=dispatch;do_HEAD=dispatch;do_OPTIONS=dispatch
class RuntimeServer(ThreadingHTTPServer):
 daemon_threads=True;allow_reuse_address=True
 def __init__(self,*args,**kwargs):self.slots=threading.BoundedSemaphore(8);super().__init__(*args,**kwargs)
 def process_request(self,request,client_address):
  if not self.slots.acquire(blocking=False):
   try:request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: 21\r\nConnection: close\r\n\r\n{"error":"http_busy"}')
   finally:self.shutdown_request(request)
   return
  try:super().process_request(request,client_address)
  except Exception:self.slots.release();raise
 def process_request_thread(self,request,client_address):
  try:super().process_request_thread(request,client_address)
  finally:self.slots.release()
 def handle_error(self,request,client_address):print(json.dumps({'event':'http_runtime_error','code':'request_failed'}),file=sys.stderr)
def create_server(handler,store,db_path,config_path,origin,release,source_dir,preferred=0):
 security=Security(db_path,config_path,origin)
 if type(preferred) is not int or preferred==3000 or preferred<1024 or preferred>65535:preferred=0
 try:server=RuntimeServer(('127.0.0.1',preferred),handler)
 except OSError as e:
  if e.errno!=errno.EADDRINUSE:raise
  server=RuntimeServer(('127.0.0.1',0),handler)
 if server.server_port==3000:server.server_close();raise HTTPError(503,'port_forbidden')
 server.store=store;server.security=security;server.db_path=str(db_path);server.release=release;root=Path(source_dir).resolve()
 names=['index.html','app.js','style.css','news.html','news.js','vendor/bootstrap/bootstrap.min.css','vendor/bootstrap/bootstrap.bundle.min.js','vendor/bootstrap/LICENSE','vendor/admin-lte/adminlte.min.css','vendor/admin-lte/adminlte.min.js','vendor/admin-lte/LICENSE']
 server.static_files={'/static/'+n:root/'static'/n for n in names};server.static_files['/']=root/'static/index.html';server.static_files['/news.html']=root/'static/news.html';return server
def serve(handler,store_factory):
 try:
  data=Path(os.environ['DATA_DIR']);data.mkdir(exist_ok=True);db=data/'crm.sqlite';store=store_factory(db)
  try:preferred=int(os.environ.get('PORT','0'))
  except ValueError:preferred=0
  server=create_server(handler,store,db,os.environ['AUTH_CONFIG'],os.environ['PUBLIC_ORIGIN'],os.environ['APP_RELEASE'],Path(__file__).parent,preferred)
  ready=data/'ready.json';temp=data/'ready.next';temp.write_text(json.dumps({'pid':os.getpid(),'port':server.server_port,'release':server.release}));os.chmod(temp,0o600)
  with temp.open('rb') as f:os.fsync(f.fileno())
  os.replace(temp,ready);fd=os.open(data,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  server.serve_forever(poll_interval=.2)
 except Exception as e:
  _,code=classify(e);print(json.dumps({'event':'runtime_start_failed','code':code}),file=sys.stderr);raise SystemExit(1) from None
