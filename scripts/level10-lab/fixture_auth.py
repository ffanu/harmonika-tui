"""Private fixture bearer identity; no values leave the lab transport."""
import hmac,json
from pathlib import Path
ROOT=Path('/data')
def headers(owner):
 if owner not in ['alpha','beta']:return {}
 return {'Authorization':'Bearer '+json.loads((ROOT/'access.json').read_text())[owner]}
def identity(h):
 values=h.get_all('Authorization',[])
 if len(values)!=1:return None,401
 value=values[0]
 if not value.startswith('Bearer ') or value.count(' ')!=1:return None,401
 token=value[7:];owner=None
 if not token.isascii():return None,401
 for name,secret in json.loads((ROOT/'access.json').read_text()).items():
  if hmac.compare_digest(token,secret):owner=name
 if owner is None:return None,401
 claims=h.get_all('X-Lab-Owner',[])
 if len(claims)>1 or claims and claims[0]!=owner:return None,403
 return owner,200
