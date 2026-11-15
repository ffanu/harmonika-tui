"""Synthetic login fixture connector, loopback only. Never returns cookie headers."""
import http.cookiejar,json,urllib.request,urllib.error,urllib.parse
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):return None
class DemoClient:
 def __init__(self,url):
  u=urllib.parse.urlsplit(url)
  if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ['', '/']:raise ValueError('fixture_loopback_only')
  self.base=url.rstrip('/');self._jar=http.cookiejar.CookieJar();self._opener=urllib.request.build_opener(NoRedirect,urllib.request.HTTPCookieProcessor(self._jar))
 def request(self,path,body=None,method=None,headers=None):
  if not isinstance(path,str) or not path.startswith('/') or path.startswith('//'):raise ValueError('relative_fixture_path_required')
  req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**(headers or {})})
  try:r=self._opener.open(req,timeout=15)
  except urllib.error.HTTPError as e:r=e
  with r:raw=r.read();code=r.status
  try:value=json.loads(raw) if raw else None
  except ValueError:value=raw
  return code,value
 def login(self,owner):
  if owner not in ['alpha','beta']:raise ValueError('unknown_fixture_alias')
  return self.request('/api/login',{'username':owner,'password':'demo-'+owner})
