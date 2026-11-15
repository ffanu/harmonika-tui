"""Private Mac transport. Fixed scope, per-operation receipts, no secrets in stdout."""
import base64,fcntl,hashlib,http.cookiejar,importlib.util,json,os,re,sys,time,urllib.request,urllib.error,shutil,uuid,socket,ssl
from pathlib import Path
REPO=Path('/Users/id/Projects/harmonika-tui');ROOT=Path(Path('/tmp/harmonika-level12-root.path').read_text().strip());PROJECT=ROOT/'project';M=json.loads((ROOT/'lab.json').read_text());PRIVATE=ROOT/'operator'
spec=importlib.util.spec_from_file_location('transport',str(REPO/'scripts/level12-operator.py'));T=importlib.util.module_from_spec(spec);spec.loader.exec_module(T)
def remote(q):
 code="import json,subprocess,sys;p=subprocess.run(['/usr/bin/python3',PATH],input=sys.stdin.buffer.read(),capture_output=True);print(json.dumps({'exit_code':p.returncode,'value':json.loads(p.stdout)}))".replace('PATH',repr(M['remote_root']+'/control.py'))
 result=T.python(code,q,timeout=80)
 if result['exit_code']:
  error=result['value'].get('error','remote_operation_failed')
  raise RuntimeError(error if isinstance(error,str) and re.fullmatch('[a-z_0-9]+',error) else 'remote_operation_failed')
 return result['value']
def protected():
 meta=json.loads((ROOT/'project-meta.json').read_text())
 if not all(hashlib.sha256((PROJECT/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()):raise RuntimeError('protected_file_changed')
def build(q):
 release=q.get('release');phase=json.loads((ROOT/'WORK-STATE.json').read_text())['phase']
 if not re.fullmatch(r'v[12](?:-r[1-3])?',release or ''):raise RuntimeError('release_invalid')
 if phase=='baseline' and not release.startswith('v1'):raise RuntimeError('phase_denied')
 protected();files=q.get('files');allowed=json.loads((PROJECT/'tools/lab.json').read_text())['build_files'];assert set(files)==set(allowed)
 content={n:base64.b64decode(files[n],validate=True) for n in allowed};assert all(len(x)<500000 for x in content.values())
 for extra in ['http_api.py']:
  source=PROJECT/extra
  if source.exists():
   if source.is_symlink() or not source.is_file() or source.stat().st_size>500000:raise RuntimeError('helper_source_invalid')
   content[extra]=source.read_bytes();compile(content[extra],extra,'exec')
 for n in ['app.py','crm.py','security.py']:compile(content[n],n,'exec')
 assert content['security.py']==(REPO/'scripts/level12-lab/security.py').read_bytes()
 for f in (PROJECT/'static/vendor').rglob('*'):
  if f.is_file():content[str(f.relative_to(PROJECT))]=f.read_bytes()
 hashes={n:hashlib.sha256(b).hexdigest() for n,b in sorted(content.items())};sha=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest();manifest={'release':release,'hashes':hashes,'source_sha256':sha,'at':time.time()};private=ROOT/'releases'/release
 if private.exists():
  old=json.loads((private/'manifest.json').read_text())
  if old['source_sha256']!=sha:raise RuntimeError('immutable_release_conflict')
  return {**old,'replayed':True}
 staging=ROOT/'build-staging'/uuid.uuid4().hex;staging.mkdir(parents=True,mode=0o700);t=T.connection()
 try:
  import paramiko
  sf=paramiko.SFTPClient.from_transport(t);base=M['remote_root']+'/releases/'+release;tmp=base+'.staging-'+uuid.uuid4().hex
  try:
   with sf.file(base+'/manifest.json','r') as f:old=json.loads(f.read())
  except IOError:old=None
  if old is not None:
   if old['source_sha256']!=sha:raise RuntimeError('immutable_release_conflict')
   manifest=old
  else:
   sf.mkdir(tmp,mode=0o755);dirs={tmp}
   for n,b in content.items():
    dest=tmp+'/'+n;parent=Path(dest).parent;chain=[]
    while str(parent) not in dirs:chain.append(str(parent));parent=parent.parent
    for d in reversed(chain):sf.mkdir(d,mode=0o755);dirs.add(d)
    with sf.file(dest,'wb') as f:f.write(b)
    sf.chmod(dest,0o444)
    with sf.file(dest,'rb') as f:
     if hashlib.sha256(f.read()).hexdigest()!=hashes[n]:raise RuntimeError('upload_hash_mismatch')
   with sf.file(tmp+'/manifest.json','w') as f:f.write(json.dumps(manifest))
   sf.chmod(tmp+'/manifest.json',0o444);sf.rename(tmp,base)
  for n,b in content.items():
   target=staging/n;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
  (staging/'manifest.json').write_text(json.dumps(manifest,indent=2));private.parent.mkdir(exist_ok=True);os.rename(staging,private);sf.close()
 except Exception:
  # Incomplete staging has no published identity and is never executable.
  shutil.rmtree(staging,ignore_errors=True);raise
 finally:t.close()
 return manifest
def fetch(path,method='GET',body=None,headers=None,jar=None):
 if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or len(path)>1000:raise RuntimeError('path_invalid')
 url='https://'+M['domain']+path;h={'Origin':'https://'+M['domain'],**(headers or {})};data=None if body is None else json.dumps(body).encode();h['Content-Type']='application/json';req=urllib.request.Request(url,data=data,headers=h,method=method)
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):return None
 handlers=[NoRedirect()]
 if jar is not None:handlers.append(urllib.request.HTTPCookieProcessor(jar))
 opener=urllib.request.build_opener(*handlers)
 try:r=opener.open(req,timeout=20)
 except urllib.error.HTTPError as e:r=e
 except urllib.error.URLError as e:
  reason=e.reason
  if isinstance(reason,ssl.SSLCertVerificationError):code='tls_verification_failed'
  elif isinstance(reason,ssl.SSLError):code='tls_handshake_failed'
  elif isinstance(reason,socket.gaierror):code='dns_unavailable'
  elif isinstance(reason,(TimeoutError,socket.timeout)):code='connection_timeout'
  elif isinstance(reason,ConnectionRefusedError):code='endpoint_not_listening'
  else:code='endpoint_unavailable'
  raise RuntimeError(code) from None
 except (TimeoutError,socket.timeout):raise RuntimeError('connection_timeout') from None
 raw=r.read(1048577)
 if len(raw)>1048576:raise RuntimeError('response_budget')
 try:value=json.loads(raw)
 except:value=raw.decode('utf-8','replace')
 return {'status':r.status,'body':value,'headers':{k:v for k,v in r.headers.items() if k.lower() in ['content-type','content-disposition','strict-transport-security','x-content-type-options','content-security-policy','x-frame-options']} }
def api(q,internal=False):
 path=q.get('path','/healthz')
 if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or len(path)>1000:raise RuntimeError('path_invalid')
 role=q.get('role','admin');assert role in ['admin','staff-a','staff-b','anonymous'];jar=http.cookiejar.CookieJar();csrf='';mode=q.get('auth_mode')
 if role!='anonymous' and mode!='missing':
  cookie_file=PRIVATE/('cookies-'+role+'.lwp');cache_file=PRIVATE/('session-'+role+'.json');jar=http.cookiejar.LWPCookieJar(str(cookie_file));cached=False
  if cookie_file.exists() and cache_file.exists():
   try:
    meta=json.loads(cache_file.read_text());jar.load(ignore_discard=True,ignore_expires=False)
    if meta['expires']>time.time() and len(jar):csrf=meta['csrf'];cached=True
   except (ValueError,OSError,KeyError,http.cookiejar.LoadError):cached=False
  if not cached:
   auth=json.loads((PRIVATE/'auth.json').read_text());login=fetch('/api/login','POST',{'username':role,'password':auth[role]},jar=jar)
   if login['status']!=200:raise RuntimeError('fixture_login_failed')
   csrf=login['body']['csrf'];jar.save(ignore_discard=True,ignore_expires=False);os.chmod(cookie_file,0o600);cache_file.write_text(json.dumps({'csrf':csrf,'expires':time.time()+3300}));os.chmod(cache_file,0o600)
 h={'X-CSRF-Token':csrf}
 if q.get('key'):h['Idempotency-Key']=q['key']
 if mode=='invalid':jar.clear();h['Cookie']='hl12_session=invalid_token_aaaaaaaaaaaaaaa'
 if mode=='forged':h['X-Role']='admin';h['X-Owner']='admin'
 if mode=='missing_csrf':h.pop('X-CSRF-Token')
 if mode=='invalid_csrf':h['X-CSRF-Token']='bad'
 if mode=='foreign_origin':h['Origin']='https://invalid.example'
 result=fetch(path,q.get('method','GET'),q.get('body'),h,jar)
 if role!='anonymous' and mode not in ['missing','invalid'] and (result['status']==401 or path=='/api/logout' and result['status']==200):
  for f in [PRIVATE/('cookies-'+role+'.lwp'),PRIVATE/('session-'+role+'.json')]:f.unlink(missing_ok=True)
 if isinstance(result['body'],dict) and 'csrf' in result['body']:result['body']={k:v for k,v in result['body'].items() if k!='csrf'}
 return result
def main():
 q=json.load(sys.stdin);op=q.get('op');phase=json.loads((ROOT/'WORK-STATE.json').read_text())['phase'];lock=(ROOT/'operator.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
 if op=='capabilities':value={'phase':phase,'ops':['status','build','deploy','api','browser','audit','logs','resources','history']+(['fault','backup','restore'] if phase=='revision_recovery' else [])}
 elif phase=='design':raise RuntimeError('design_only')
 elif op=='build':value=build(q)
 elif op=='api':value=api(q)
 elif op in ['status','resources','audit','logs','history','deploy','fault','backup','restore']:
  if op in ['fault','backup','restore'] and phase!='revision_recovery':raise RuntimeError('phase_denied')
  if op=='deploy':
   protected()
   if phase=='baseline' and not str(q.get('release','')).startswith('v1'):raise RuntimeError('phase_denied')
  value=remote({k:v for k,v in q.items() if k in ['op','release','operation','kind','action','label']})
 else:raise RuntimeError('operation_denied')
 receipt={'op':op,'at':time.time(),'ok':True,'result':value};log=ROOT/'operations.jsonl'
 with log.open('a') as f:f.write(json.dumps(receipt)+'\n')
 print(json.dumps(value))
if __name__=='__main__':
 if sys.argv[1:]==['--browser-fixture']:
  print(json.dumps({'origin':'https://'+M['domain'],'password':json.loads((PRIVATE/'auth.json').read_text())['admin']}));sys.exit(0)
 try:main()
 except Exception as e:
  code=str(e) if isinstance(e,RuntimeError) and re.fullmatch('[a-z_0-9]+',str(e)) else type(e).__name__
  with (ROOT/'operator-errors.jsonl').open('a') as f:f.write(json.dumps({'at':time.time(),'error':code})+'\n')
  print(json.dumps({'error':code}));sys.exit(1)
