"""Protected lab HTTP/auth/worker. Application inputs are never logged."""
import hashlib,hmac,json,os,re,sqlite3,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs,unquote
from workspace import WorkspaceStore
DATA=Path('/data');WEB=Path('/app/web');CONFIG=json.loads((DATA/'access.json').read_text());STORE=WorkspaceStore(DATA/'app.sqlite');STOP=threading.Event()
def encode(v):return json.dumps(v,separators=(',',':'),ensure_ascii=False).encode()
def identity(headers):
 values=headers.get_all('Authorization',[]);cookies=headers.get_all('Cookie',[])
 if len(values)>1 or len(cookies)>1:return None
 token=''
 if values:
  if not values[0].startswith('Bearer '):return None
  token=values[0][7:]
 elif cookies:
  from http.cookies import SimpleCookie
  c=SimpleCookie();c.load(cookies[0]);token=c['lab_session'].value if 'lab_session' in c else ''
 if not token.isascii():return None
 return next((o for o in ['alpha','beta'] if hmac.compare_digest(token,CONFIG[o])),None)
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*a):pass
 def reply(self,status,value,ctype='application/json',headers=None):
  body=value if isinstance(value,bytes) else encode(value);self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('Connection','close')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers()
  try:self.wfile.write(body)
  except (BrokenPipeError,ConnectionResetError):pass
  self.close_connection=True
 def body(self):
  n=int(self.headers.get('Content-Length','0'))
  if not 0<n<=16384:raise ValueError('invalid_body')
  return json.loads(self.rfile.read(n))
 def do_GET(self):self.handle_request()
 def do_POST(self):self.handle_request()
 def do_PATCH(self):self.handle_request()
 def handle_request(self):
  try:self.dispatch()
  except (ValueError,TypeError,KeyError,json.JSONDecodeError):self.reply(422,{'error':'invalid_payload'})
  except sqlite3.Error:self.reply(503,{'error':'storage_unavailable'})
  except Exception:self.reply(503,{'error':'application_unavailable'})
 def dispatch(self):
  url=urlsplit(self.path);path=unquote(url.path);method=self.command
  if method=='GET' and path in ['/','/app.js','/style.css']:
   name={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[path];self.reply(200,(WEB/name).read_bytes(),{'/':'text/html; charset=utf-8','/app.js':'text/javascript','/style.css':'text/css'}[path]);return
  if method=='GET' and path=='/healthz':self.reply(200,{'status':'ok'});return
  if method=='GET' and path=='/readyz':
   try:
    c=sqlite3.connect(DATA/'app.sqlite',timeout=.1);c.execute('BEGIN IMMEDIATE');c.rollback();c.close();self.reply(200,{'ready':True})
   except sqlite3.Error:self.reply(503,{'error':'database_busy'})
   return
  if path=='/login' and method=='POST':
   body=self.body();owner=body.get('username')
   if owner not in ['alpha','beta'] or body.get('password')!='demo-'+owner:self.reply(401,{'error':'unauthorized'});return
   self.reply(200,{'owner':owner},headers={'Set-Cookie':'lab_session='+CONFIG[owner]+'; HttpOnly; SameSite=Strict; Path=/'});return
  if path=='/logout' and method=='POST':self.reply(200,{'logged_out':True},headers={'Set-Cookie':'lab_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'});return
  owner=identity(self.headers)
  if owner is None:self.reply(401,{'error':'unauthorized'});return
  if self.headers.get('X-Owner') not in [None,owner]:self.reply(403,{'error':'forged_scope'});return
  match=re.fullmatch(r'/p/(one|two)(/.*)',path)
  if not match:self.reply(404,{'error':'not_found'});return
  project,route=match.groups();qs=parse_qs(url.query,keep_blank_values=True);key=self.headers.get('Idempotency-Key','');status=404;value={'error':'not_found'}
  if route=='/tickets':
   if method=='GET':status,value=STORE.list_tickets(owner,project)
   elif method=='POST':status,value=STORE.create_ticket(owner,project,key,self.body())
  elif route.startswith('/tickets/') and method=='PATCH':
   body=self.body()
   if set(body)!={'revision','status'}:raise ValueError()
   status,value=STORE.update_ticket(owner,project,route.split('/')[-1],body['revision'],{'status':body['status']})
  elif route=='/jobs' and method=='POST':
   body=self.body();status,value=STORE.submit(owner,project,key,body)
   supplied=self.headers.get('X-Lab-Crash','');privileged=supplied.isascii() and hmac.compare_digest(supplied,CONFIG['operator'])
   marker=DATA/('crash-spent-'+os.environ['LAB_RELEASE'])
   if privileged and status==202 and not marker.exists():
    with marker.open('x') as f:f.write(value['job']['id']);f.flush();os.fsync(f.fileno())
    os._exit(42)
  elif route=='/jobs/lookup' and method=='GET':status,value=STORE.lookup(owner,project,qs.get('key',[''])[0])
  elif re.fullmatch(r'/jobs/[a-f0-9]{32}/cancel',route) and method=='POST':status,value=STORE.cancel(owner,project,route.split('/')[2])
  elif re.fullmatch(r'/jobs/[a-f0-9]{32}',route) and method=='GET':status,value=STORE.get(owner,project,route.split('/')[2])
  elif re.fullmatch(r'/artifacts/[a-f0-9]{32}',route) and method=='GET':
   status,value=STORE.artifact(owner,project,route.split('/')[2]);self.reply(status,value,'text/csv; charset=utf-8' if status==200 else 'application/json',{'Content-Disposition':'attachment; filename="result.csv"'} if status==200 else None);return
  elif route=='/events' and method=='GET':
   scope=hashlib.sha256((owner+'\0'+project).encode()).hexdigest()[:16];cursor=self.headers.get('Last-Event-ID');after=qs.get('after',['0'])[0]
   if cursor:
    if not re.fullmatch(scope+r':(?:0|[1-9][0-9]{0,15})',cursor):self.reply(422,{'error':'invalid_cursor'});return
    after=cursor.split(':')[1]
   if not re.fullmatch(r'(?:0|[1-9][0-9]{0,15})',after):self.reply(422,{'error':'invalid_cursor'});return
   status,value=STORE.events(owner,project,int(after),100)
   if status==200:
    body=b': finite event page\n\n'+b''.join(('id: '+scope+':'+str(e['id'])+'\nevent: job\ndata: '+encode(e).decode()+'\n\n').encode() for e in value['events']);self.reply(200,body,'text/event-stream');return
  self.reply(status,value)
def worker():
 while not STOP.wait(.05):
  try:STORE.tick()
  except Exception:pass
if __name__=='__main__':
 threading.Thread(target=worker,daemon=True).start();ThreadingHTTPServer(('0.0.0.0',18081),Handler).serve_forever()
