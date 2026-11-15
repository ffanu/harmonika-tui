"""Parse source regions, never execute them; return bounded numeric spans only."""
import ast,json,sys,resource,re,textwrap
from http.cookies import SimpleCookie
if sys.platform.startswith('linux'):
 resource.setrlimit(resource.RLIMIT_AS,(134217728,134217728))
 resource.setrlimit(resource.RLIMIT_CPU,(1,1))
text=sys.stdin.read(262145)
if len(text)>262144:print('[]');raise SystemExit
u16=lambda s:len(s.encode('utf-16-le'))//2
def source_lines(s):return [m.group() for m in re.finditer(r'[^\r\n]*(?:\r\n|\r|\n|$)',s) if m.group()]
original_lines=source_lines(text);original_starts=[];offset=0
for line in original_lines:original_starts.append(offset);offset+=u16(line)
candidates=[(text,[(x,0) for x in original_starts])]
# Explicit code fences, a source suffix after prose, or numbered file-read lines.
for match in list(re.finditer(r'```(?:python|py)?\r?\n([\s\S]*?)\r?\n```',text))[:4]:
 code=match.group(1);start=u16(text[:match.start(1)]);mapping=[]
 for line in source_lines(code):mapping.append((start,0));start+=u16(line)
 candidates.append((code,mapping))
for i,line in enumerate(original_lines):
 if re.match(r'^\s*(?:from |import |def |class |for |with |[A-Za-z_][A-Za-z0-9_]*\s*(?::[^=\r\n]+)?=)',line) and len(candidates)<8:candidates.append((''.join(original_lines[i:]),[(x,0) for x in original_starts[i:]]))
group=[];mapping=[]
for i,line in enumerate(original_lines+['']):
 match=re.match(r'^\s*\d+: ?',line)
 if match:group.append(line[match.end():]);mapping.append((original_starts[i],u16(match.group())))
 elif group:
  if len(candidates)<12:candidates.append((''.join(group),mapping))
  group=[];mapping=[]

def cookie_shape(value,wanted):
 if not isinstance(value,str) or '\r' in value or '\n' in value or len(value)>512:return False
 try:
  jar=SimpleCookie();jar.load(value)
 except Exception:return False
 if len(jar)!=1:return False
 morsel=next(iter(jar.values()))
 if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,39}',morsel.key) or morsel.value!=wanted:return False
 allowed={'path':['','/'],'domain':[''],'max-age':['','0'],'expires':['','Thu, 01 Jan 1970 00:00:00 GMT'],'secure':['',True],'httponly':['',True],'samesite':['','Strict','Lax','None','strict','lax','none']}
 return all(not val or key in allowed and val in allowed[key] for key,val in morsel.items())
def cookie_clear(value):return cookie_shape(value,'')
known_literals=set()
def parse(code,mapping):
 dedented=textwrap.dedent(code)
 if dedented!=code:
  oldlines=source_lines(code);newlines=source_lines(dedented)
  if len(oldlines)!=len(newlines):return None
  mapping=[(a,b+u16(old)-u16(new)) for (a,b),old,new in zip(mapping,oldlines,newlines)]
  code=dedented
 try:tree=ast.parse(code)
 except (SyntaxError,ValueError,RecursionError,MemoryError):return None
 lines=source_lines(code)
 def position(line,column):return mapping[line-1][0]+mapping[line-1][1]+u16(lines[line-1].encode()[:column].decode('utf8'))
 tainted=set(known_literals)
 def target_names(n):
  if isinstance(n,ast.Name):return [n.id]
  if isinstance(n,(ast.Tuple,ast.List)):return [v for x in n.elts for v in target_names(x)]
  if isinstance(n,ast.Attribute):return [ast.unparse(n)]
  if isinstance(n,ast.Subscript):return target_names(n.value)
  return []
 bindings=[]
 for node in ast.walk(tree):
  if isinstance(node,ast.Assign):bindings.extend((t,node.value) for t in node.targets)
  elif isinstance(node,(ast.AnnAssign,ast.NamedExpr)):bindings.append((node.target,node.value))
  elif isinstance(node,(ast.For,ast.AsyncFor,ast.comprehension)):bindings.append((node.target,node.iter))
  elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
   tainted.add(node.name)
   args=node.args.posonlyargs+node.args.args
   bindings.extend((ast.Name(id=arg.arg),value) for arg,value in zip(args[-len(node.args.defaults):],node.args.defaults))
   bindings.extend((ast.Name(id=arg.arg),value) for arg,value in zip(node.args.kwonlyargs,node.args.kw_defaults) if value is not None)
 def cookie_format(n):
  if not isinstance(n,(ast.JoinedStr,ast.BinOp)):return False
  parts=[];dynamic=[]
  def flatten(value):
   if isinstance(value,ast.Constant) and isinstance(value.value,str):parts.append(value.value);return True
   if isinstance(value,ast.JoinedStr):return all(flatten(v) for v in value.values)
   if isinstance(value,ast.BinOp) and isinstance(value.op,ast.Add):return flatten(value.left) and flatten(value.right)
   if isinstance(value,ast.FormattedValue):
    if value.format_spec is not None or value.conversion!=-1:return False
    value=value.value
   if reference(value):parts.append('SAFE_EXAM_NONCE');dynamic.append(True);return True
   return False
  return flatten(n) and len(dynamic)==1 and cookie_shape(''.join(parts),'SAFE_EXAM_NONCE')
 def reference(n):
  if isinstance(n,ast.Constant):return cookie_clear(n.value)
  if isinstance(n,ast.Name):return not n.id.startswith('__') and n.id not in tainted
  if isinstance(n,ast.Attribute):return not n.attr.startswith('__') and ast.unparse(n) not in tainted and reference(n.value)
  if isinstance(n,ast.Subscript):return reference(n.value) and (reference(n.slice) or isinstance(n.slice,ast.Constant) and n.slice.value in ['password','cookie','Cookie','token','authorization','Authorization'])
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='SimpleCookie' and n.func.id not in tainted and not n.keywords:
   return not n.args or len(n.args)==1 and reference(n.args[0])
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and reference(n.func.value) and not n.keywords:
   if n.func.attr=='get' and len(n.args) in [1,2] and isinstance(n.args[0],ast.Constant) and n.args[0].value in ['password','cookie','Cookie','token','authorization','Authorization']:
    return len(n.args)==1 or isinstance(n.args[1],ast.Constant) and n.args[1].value in ['',None]
   if isinstance(n.func.value,ast.Name) and n.func.value.id=='secrets' and n.func.attr in ['token_hex','token_urlsafe']:
    return not n.args or len(n.args)==1 and isinstance(n.args[0],ast.Constant) and type(n.args[0].value) is int and 0<n.args[0].value<=4096
  if isinstance(n,ast.Call) and not n.keywords and isinstance(n.func,ast.Attribute):
   if ast.unparse(n.func)=='json.loads' and reference(n.func.value) and len(n.args)==1 and reference(n.args[0]):return True
   if ast.unparse(n.func)=='self.rfile.read' and len(n.args)==1 and reference(n.args[0]):return True
   if n.func.attr in ['encode','decode'] and reference(n.func.value) and (not n.args or len(n.args)==1 and isinstance(n.args[0],ast.Constant) and n.args[0].value in ['utf8','utf-8']):return True
  return cookie_format(n)
 for _ in range(32):
  before=len(tainted)
  for target,value in bindings:
   if value is not None and not reference(value):tainted.update(target_names(target))
  if len(tainted)==before:break
 else:return []
 known_literals.update(tainted)
 spans=[]
 for node in ast.walk(tree):
  values=[]
  if isinstance(node,(ast.Assign,ast.AnnAssign,ast.NamedExpr)):values=[node.value]
  elif isinstance(node,ast.Dict):values=node.values
  elif isinstance(node,ast.keyword):values=[node.value]
  elif isinstance(node,ast.Compare) and reference(node.left) and all(reference(v) for v in node.comparators):values=[node]
  for value in values:
   if value is not None and (reference(value) or isinstance(value,ast.Compare)) and hasattr(value,'end_lineno'):
    spans.append([position(value.lineno,value.col_offset),position(value.end_lineno,value.end_col_offset)])
  if len(spans)>=2048:break
 return spans
whole=parse(*candidates[0])
if whole is not None:print(json.dumps(whole));raise SystemExit
# Collect literal-bound names across reference fragments before emitting spans.
for code,mapping in candidates[1:]:parse(code,mapping)
spans=[]
for code,mapping in candidates[1:]:
 if len(spans)>=2048:break
 spans.extend((parse(code,mapping) or [])[:2048-len(spans)])
print(json.dumps(spans))
