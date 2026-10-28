import hashlib,json,re
class ScannerError(ValueError):
 def __init__(self,code):self.code=code;super().__init__(code)
def need(value,code):
 if not value:raise ScannerError(code)
def fields(v,allowed,required=None):
 need(type(v) is dict and set(v)<=set(allowed) and set(required if required is not None else allowed)<=set(v),'invalid_fields')
def bounded(v):
 stack=[(v,0)];count=0
 while stack:
  item,depth=stack.pop();count+=1;need(depth<=12 and count<=15000,'input_complexity_limit')
  kind=type(item);need(kind in (dict,list,str,int,float,bool,type(None)),'invalid_json')
  if kind is dict:
   need(all(type(k) is str for k in item),'invalid_json_key');stack.extend((x,depth+1) for x in item);stack.extend((x,depth+1) for x in item.values())
  elif kind is list:stack.extend((x,depth+1) for x in item)
  elif kind is str:need(not any(0xD800<=ord(c)<=0xDFFF for c in item),'invalid_unicode')
  elif kind is int:need(item.bit_length()<=128,'invalid_integer')
 try:raw=json.dumps(v,allow_nan=False,ensure_ascii=True).encode()
 except (ValueError,TypeError):raise ScannerError('invalid_json') from None
 need(len(raw)<=65536,'input_byte_limit')
def text(v,maximum=1000):
 need(type(v) is str and 1<=len(v)<=maximum and not any(ord(c)<32 and c not in '\n\t\r' for c in v),'invalid_text');return v
def integer(v,low=0,high=1000000):need(type(v) is int and low<=v<=high,'invalid_integer');return v
def digest(v):return hashlib.sha256(v.encode() if type(v) is str else v).hexdigest()
def safe_id(v):
 need(type(v) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',v),'invalid_id');return v
def redacted_label(v):return 'ref_'+digest(text(v,300))[:16]
def redact(v):
 value=text(v,20000)
 # No values from a log/config enter the result. This helper is only for authored titles.
 value=re.sub(r'https?://\S+','[url]',value)
 value=re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}','[email]',value)
 value=re.sub(r'(?i)\b(?:bearer\s+\S+|(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*\S+)','[credential]',value)
 value=re.sub(r'\b(?:sk|hai|sp_apikey|ghp|github_pat|xoxb)[_-][A-Za-z0-9_-]{6,}\b','[credential]',value)
 value=re.sub(r'\b\d[\d +().-]{7,}\d\b','[number]',value)
 return value[:200]
