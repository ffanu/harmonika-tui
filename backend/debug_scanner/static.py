"""Pure standard-library static source scanner over caller-supplied files.

Never executes supplied source and never touches the filesystem or network.
All exceptions carry machine codes only, so source text cannot leak through
errors. Findings reference sanitized file labels (basename), line numbers and
content hashes; they never contain source snippets, secret values or URLs.
Candidate matches are explicitly labeled and are never confirmed exploits.
"""
import ast
import hashlib
import io
import json
import re
import token as _token
import tokenize
from pathlib import PurePosixPath

__all__=['STATIC_OPS','static_scan']

MAX_FILES=20
MAX_TOTAL_BYTES=60000
MAX_AST_NODES=200000
MAX_FINDINGS=300
COMPLEXITY_THRESHOLD=10

SEVERITIES={'critical','high','medium','low','info'}
CONFIDENCE_KINDS={'proven','heuristic_candidate','rule_based_candidate'}

BASE_LIMIT='All findings are static-analysis candidates; none are confirmed exploitable vulnerabilities'
TOKEN_NOTE='Token-based scanning uses heuristic comment and string filtering; verify matches manually'
SECRET_NOTE='Secret detection is pattern based; values are never included in output'
NO_CVE_NOTE='No vulnerability database was consulted; no CVE or risk claims are made'
NO_MANIFEST_NOTE='No supported dependency manifests (package.json, package-lock.json, requirements*.txt) supplied'
NEEDS_TOOLCHAIN_NOTE='No Python sources supplied; analysis of other languages requires an external toolchain'

_PRIVATE_DIRS={'.ssh','.aws','.config','.codex','.claude','.harmonika','.git','secrets','credentials','node_modules','venv','.venv'}
_PRIVATE_NAMES={'.env','id_rsa','id_dsa','id_ecdsa','id_ed25519','.npmrc','.netrc','.git-credentials','.htpasswd','.gitcookies','authorized_keys','harmonika-api-key','secrets.json','secrets.yml','secrets.yaml','credentials.json','credentials.yml','credentials.yaml'}
_PRIVATE_SUFFIX=('.pem','.key','.p12','.pfx','.keystore','.token','.sqlite','.db')

_SAFE_TOKEN_RE=re.compile(r'^[A-Za-z_][A-Za-z0-9_.-]{0,63}$')
_IDENT_PART_RE=re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_SAFE_PKG_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._@/-]{0,213}$')
_SAFE_VER_RE=re.compile(r'^[A-Za-z0-9.+~^<>=!*-]{1,40}$')

_SQL_KW_RE=re.compile(r'(?is)\b(?:select|insert|update|delete|drop|alter|create|truncate|replace|merge|grant|revoke|union|exec|execute)\b')
_PEM_RE=re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----')
_TOKEN_RE=re.compile(r'\b(?:sk-proj-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{16,}|hai_(?:live|internal)_[a-fA-F0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[A-Za-z0-9_-]{30,}|GOCSPX-[A-Za-z0-9_-]{12,}|ya29\.[A-Za-z0-9._-]{16,})\b')
_URL_CRED_RE=re.compile(r'(?i)\b[a-z][a-z0-9+.-]{1,32}://[^\s/:@"]+:[^\s/@"]+@')
_BEARER_RE=re.compile(r'(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}')
_ASSIGN_RE=re.compile(r'''(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token)\b["']?\s*[:=]\s*["']([^"'\s]{8,200})["']''')
_TODO_RE=re.compile(r'\b(?:TODO|FIXME|XXX|HACK)\b')

_JS_PATTERNS=(
 ('JS.XSS.HTML_SINK',re.compile(r'\b(?:inner|outer)HTML\s*=(?!=)'),'high',79,'xss','Assignment to innerHTML/outerHTML sink (token-based XSS candidate)'),
 ('JS.XSS.DOCUMENT_WRITE',re.compile(r'\bdocument\s*\.\s*write(?:ln)?\s*\('),'high',79,'xss','document.write sink (token-based XSS candidate)'),
 ('JS.XSS.REACT_DANGEROUS_HTML',re.compile(r'\bdangerouslySetInnerHTML\b'),'high',79,'xss','React dangerouslySetInnerHTML usage (token-based XSS candidate)'),
 ('JS.SEC.EVAL',re.compile(r'\beval\s*\('),'high',95,'eval','eval() call (token-based code-injection candidate)'),
 ('JS.LINT.LOOSE_EQUALITY',re.compile(r'(?<![=!<>])==(?!=)|(?<![<>!=])!=(?!=)'),'low',None,'lint','Loose equality (==/!=) enables type coercion'),
 ('JS.LINT.DEBUGGER',re.compile(r'\bdebugger\b'),'low',489,'lint','Leftover debugger statement'),
)

_PLACEHOLDER_VALUES={'changeme','change_me','example','sample','placeholder','dummy','test','testing','todo','fixme','none','null','redacted','password','secret','notsosecret','yourpassword','your_secret'}

def _safe_token(name):
    return name if type(name) is str and _SAFE_TOKEN_RE.match(name) else '[redacted]'

def _language(path):
    suffix=PurePosixPath(path).suffix.lower()
    if suffix in ('.py','.pyw'):return 'python'
    if suffix in ('.js','.jsx','.mjs','.cjs','.ts','.tsx'):return 'javascript'
    return 'other'

def _check_path(path):
    if type(path) is not str or not path or len(path)>240:raise ValueError('invalid_path')
    if any(ord(c)<32 or ord(c)==127 for c in path):raise ValueError('path_control_character')
    if path.startswith('/') or path.startswith('~'):raise ValueError('absolute_path')
    if '\\' in path:raise ValueError('path_backslash')
    parts=PurePosixPath(path).parts
    if '..' in parts:raise ValueError('path_traversal')
    if ':' in path or path.endswith('/') or any(x in ('','.','..') for x in path.split('/')):raise ValueError('invalid_path')
    if any(x.lower() in _PRIVATE_DIRS for x in parts):raise ValueError('private_source_identifier')
    name=PurePosixPath(path).name.lower()
    if name=='.env' or name.startswith('.env.') or name in _PRIVATE_NAMES or name.endswith(_PRIVATE_SUFFIX):
        raise ValueError('private_source_identifier')

class _File:
    __slots__=('path','label','content','language','sha256','tree','error_line','parsed','nodes','code_view','secret_view')
    def __init__(self,path,content):
        self.path=path;self.label=PurePosixPath(path).name;self.content=content
        self.language=_language(path)
        self.sha256=hashlib.sha256(content.encode('utf-8')).hexdigest()
        self.tree=None;self.error_line=None;self.parsed=False;self.nodes=0
        self.code_view=None;self.secret_view=None

class _Ctx:
    def __init__(self,files):
        self.files=files;self.node_count=0;self.syntax_failed=0
    def python(self):return [f for f in self.files if f.language=='python']
    def javascript(self):return [f for f in self.files if f.language=='javascript']

def _validated_files(payload):
    if type(payload) is not dict:raise ValueError('invalid_input')
    if set(payload)!={'files'}:raise ValueError('invalid_input')
    files=payload['files']
    if type(files) is not list:raise ValueError('invalid_files')
    if len(files)>MAX_FILES:raise ValueError('too_many_files')
    seen=set();total=0;out=[]
    for item in files:
        if type(item) is not dict or set(item)!={'path','content'}:raise ValueError('invalid_file')
        path=item['path'];content=item['content']
        _check_path(path)
        if type(content) is not str:raise ValueError('invalid_content')
        if path in seen:raise ValueError('duplicate_path')
        seen.add(path)
        try:
            total+=len(content.encode('utf-8'))+len(path.encode('utf-8'))
        except UnicodeError:raise ValueError('invalid_encoding') from None
        if total>MAX_TOTAL_BYTES:raise ValueError('input_too_large')
        out.append(_File(path,content))
    counts={}
    for f in out:counts[f.label]=counts.get(f.label,0)+1
    for f in out:
        if counts[f.label]>1 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',f.label):f.label='file_'+hashlib.sha256(f.path.encode()).hexdigest()[:16]+PurePosixPath(f.path).suffix.lower()
    return out

def _parse(ctx,f):
    if f.parsed:return f.tree
    f.parsed=True
    try:
        tree=ast.parse(f.content)
    except (SyntaxError,ValueError,RecursionError) as exc:
        line=getattr(exc,'lineno',None)
        f.error_line=line if isinstance(line,int) and line>0 else 1
        ctx.syntax_failed+=1
        return None
    count=0
    for _ in ast.walk(tree):count+=1
    f.nodes=count;ctx.node_count+=count
    if ctx.node_count>MAX_AST_NODES:raise ValueError('ast_limit_exceeded')
    f.tree=tree
    return tree

def _finding(rule_id,severity,cwe,kind,f,line,message):
    return {'rule_id':rule_id,'severity':severity,'cwe':cwe,'confidence_kind':kind,
            'evidence':{'file':f.label,'line':line if isinstance(line,int) and line>0 else 1,'sha256':f.sha256},
            'message':message}

def _line_of(text,pos):return text.count('\n',0,pos)+1

def _strip_py_comments(source):
    try:
        lines=[list(l) for l in source.split('\n')]
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type==_token.COMMENT and tok.end[0]==tok.start[0]:
                row=tok.start[0]-1
                if 0<=row<len(lines):
                    ln=lines[row]
                    for i in range(tok.start[1],min(tok.end[1],len(ln))):ln[i]=' '
        return '\n'.join(''.join(l) for l in lines)
    except (tokenize.TokenError,SyntaxError,IndentationError,ValueError,IndexError):
        return source

def _mask(text,mask_strings,hash_comments,block_comments=True):
    out=[];i=0;n=len(text);quote=None;comment=None
    while i<n:
        c=text[i]
        if comment=='line':
            if c=='\n':comment=None;out.append(c)
            else:out.append(' ')
            i+=1;continue
        if comment=='block':
            if c=='*' and i+1<n and text[i+1]=='/':
                comment=None;out.append('  ');i+=2;continue
            out.append('\n' if c=='\n' else ' ');i+=1;continue
        if quote is not None:
            if c=='\\' and i+1<n:
                if mask_strings:out.append('  ')
                else:out.append(c);out.append(text[i+1])
                i+=2;continue
            if c==quote:quote=None;out.append(c);i+=1;continue
            out.append(' ' if mask_strings else c);i+=1;continue
        if c=='/':
            if i+1<n and text[i+1]=='/':comment='line';out.append('  ');i+=2;continue
            if block_comments and i+1<n and text[i+1]=='*':comment='block';out.append('  ');i+=2;continue
        elif c=='#' and hash_comments:comment='line';out.append(' ');i+=1;continue
        if c in '"\'`':quote=c;out.append(c);i+=1;continue
        out.append(c);i+=1;continue
    return ''.join(out)

def _secret_view(f):
    if f.secret_view is None:
        if f.language=='python':f.secret_view=_strip_py_comments(f.content)
        elif f.language=='javascript':f.secret_view=_mask(f.content,False,False)
        else:f.secret_view=_mask(f.content,False,True)
    return f.secret_view

def _code_view(f):
    if f.code_view is None:f.code_view=_mask(f.content,True,False)
    return f.code_view

def _placeholder(value):
    low=value.lower()
    if low in _PLACEHOLDER_VALUES:return True
    if set(low)<= {'x'}:return True
    if any(ch in value for ch in '<>${}%'):return True
    if low.startswith('your_') or low.endswith('_here'):return True
    if 'example' in low or 'redacted' in low or 'placeholder' in low:return True
    return False

def _secret_findings(ctx,f):
    view=_secret_view(f);out=[];claimed=set()
    for rule,cwe,sev,rx in (('SEC.SECRET.PRIVATE_BLOCK',798,'high',_PEM_RE),
                            ('SEC.SECRET.TOKEN_FORMAT',798,'high',_TOKEN_RE),
                            ('SEC.SECRET.URL_CREDENTIALS',798,'high',_URL_CRED_RE),
                            ('SEC.SECRET.BEARER',798,'medium',_BEARER_RE)):
        for m in rx.finditer(view):
            line=_line_of(view,m.start());claimed.add(line)
            out.append(_finding(rule,sev,cwe,'rule_based_candidate',f,line,_secret_message(rule)))
    for m in _ASSIGN_RE.finditer(view):
        line=_line_of(view,m.start())
        if line in claimed or _placeholder(m.group(2)):continue
        msg='Credential-like assignment to %s with literal value (value withheld)'%_safe_token(m.group(1).lower())
        out.append(_finding('SEC.SECRET.ASSIGNMENT','medium',798,'rule_based_candidate',f,line,msg))
    return out

def _secret_message(rule):
    return {'SEC.SECRET.PRIVATE_BLOCK':'Private key block detected in source',
            'SEC.SECRET.TOKEN_FORMAT':'Hardcoded credential token matching a known provider format',
            'SEC.SECRET.URL_CREDENTIALS':'URL containing embedded credentials',
            'SEC.SECRET.BEARER':'HTTP bearer token literal'}[rule]

def _todo_findings(f):
    return [_finding('GEN.PATTERN.TODO','info',None,'rule_based_candidate',f,_line_of(f.content,m.start()),'TODO/FIXME marker present in source')
            for m in _TODO_RE.finditer(f.content)]

def _js_findings(ctx,f,kinds):
    view=_code_view(f);out=[]
    for rule,rx,sev,cwe,tag,msg in _JS_PATTERNS:
        if tag not in kinds:continue
        for m in rx.finditer(view):
            out.append(_finding(rule,sev,cwe,'rule_based_candidate',f,_line_of(view,m.start()),msg))
    return out

def _dotted(func):
    parts=[]
    while isinstance(func,ast.Attribute):
        parts.append(func.attr);func=func.value
    if isinstance(func,ast.Name):
        parts.append(func.id);return list(reversed(parts))
    return None

def _leaves(node,out):
    if isinstance(node,ast.BinOp):
        _leaves(node.left,out);_leaves(node.right,out)
    elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='format' and isinstance(node.func.value,ast.Constant):
        out.append(('str',node.func.value.value));out.append(('dyn',node))
    elif isinstance(node,ast.JoinedStr):
        for v in node.values:
            if isinstance(v,ast.FormattedValue):out.append(('dyn',v))
            elif isinstance(v,ast.Constant) and isinstance(v.value,str):out.append(('str',v.value))
    elif isinstance(node,ast.Constant) and isinstance(node.value,str):out.append(('str',node.value))
    else:out.append(('dyn',node))

def _py_sqli(f,tree):
    out=[]
    # Local assignment propagation only; not whole-program taint/symbolic proof.
    scopes=[tree]+[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    for scope in scopes:
        nodes=[];stack=list(ast.iter_child_nodes(scope))
        while stack:
            node=stack.pop()
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):continue
            nodes.append(node);stack.extend(ast.iter_child_nodes(node))
        nodes.sort(key=lambda n:(getattr(n,'lineno',0),getattr(n,'col_offset',0)))
        bound={}
        def leaves(node,depth=0,visited=None):
            visited=set() if visited is None else visited
            if depth>12:return [('dyn',None)]
            if isinstance(node,ast.Name) and node.id in bound and node.id not in visited:
                return leaves(bound[node.id],depth+1,visited|{node.id})
            if isinstance(node,ast.BinOp):return leaves(node.left,depth+1,visited)+leaves(node.right,depth+1,visited)
            result=[];_leaves(node,result);return result
        for node in nodes:
            if isinstance(node,(ast.Assign,ast.AnnAssign)) and node.value is not None:
                for target in (node.targets if isinstance(node,ast.Assign) else [node.target]):
                    if isinstance(target,ast.Name):bound[target.id]=node.value
            if not isinstance(node,ast.Call):continue
            parts=_dotted(node.func)
            if not parts or parts[-1] not in ('execute','executemany','executescript') or not node.args:continue
            parts_found=leaves(node.args[0]);consts=[v for k,v in parts_found if k=='str']
            # Bound parameters do not sanitize a dynamically constructed table/column.
            if any(k=='dyn' for k,v in parts_found) and consts and _SQL_KW_RE.search(' '.join(consts)):
                out.append(_finding('PY.SEC.SQLI_CANDIDATE','high',89,'heuristic_candidate',f,node.lineno,
                                    'Constructed SQL reaches an execute sink; inspect trusted identifiers and parameter binding'))
    return out

def _literal_str(node):
    return isinstance(node,ast.Constant) and isinstance(node.value,str)

def _py_cmd(f,tree):
    out=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        parts=_dotted(node.func)
        if parts in (['subprocess','run'],['subprocess','call'],['subprocess','check_call'],['subprocess','check_output'],['subprocess','Popen'],['Popen']):
            if not any(kw.arg=='shell' and isinstance(kw.value,ast.Constant) and kw.value.value is True for kw in node.keywords):continue
            literal=_literal_str(node.args[0]) if node.args else False
        elif parts in (['os','system'],['os','popen']):
            literal=_literal_str(node.args[0]) if node.args else False
        else:continue
        if literal:
            out.append(_finding('PY.SEC.CMD_INJECTION','medium',78,'heuristic_candidate',f,node.lineno,
                                'Shell invoked with literal argument (subprocess shell=True / os.system / os.popen)'))
        else:
            out.append(_finding('PY.SEC.CMD_INJECTION','high',78,'heuristic_candidate',f,node.lineno,
                                'Shell invoked with dynamic argument; candidate command injection (subprocess shell=True / os.system / os.popen)'))
    return out

def _py_dynamic(f,tree):
    out=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        if _dotted(node.func) in (['eval'],['exec']):
            out.append(_finding('PY.SEC.EVAL_EXEC','high',95,'heuristic_candidate',f,node.lineno,
                                'Use of builtin eval/exec on potentially dynamic input'))
    return out

def _py_weakhash(f,tree):
    out=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        parts=_dotted(node.func)
        if not parts or parts[-1] not in ('md5','sha1'):continue
        if not (len(parts)==1 or parts==['hashlib',parts[-1]]):continue
        if any(kw.arg=='usedforsecurity' and isinstance(kw.value,ast.Constant) and kw.value.value is False for kw in node.keywords):continue
        out.append(_finding('PY.SEC.WEAK_HASH','medium',327,'heuristic_candidate',f,node.lineno,
                            'Weak hash algorithm %s used; unsuitable for passwords or signatures'%parts[-1]))
    return out

def _py_antipatterns(f,tree):
    out=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.ExceptHandler) and node.type is None:
            out.append(_finding('PY.LINT.BARE_EXCEPT','low',396,'proven',f,node.lineno,
                                'Bare except clause swallows all exceptions including KeyboardInterrupt/SystemExit'))
        elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            for default in list(node.args.defaults)+[d for d in node.args.kw_defaults if d is not None]:
                if isinstance(default,(ast.List,ast.Dict,ast.Set)):
                    out.append(_finding('PY.LINT.MUTABLE_DEFAULT','low',None,'proven',f,node.lineno,
                                        'Mutable default argument in function %s'%_safe_token(node.name)))
        elif isinstance(node,ast.Compare):
            for op,comp in zip(node.ops,node.comparators):
                if isinstance(op,(ast.Eq,ast.NotEq)) and isinstance(comp,ast.Constant) and comp.value is None:
                    out.append(_finding('PY.LINT.NONE_COMPARE','low',None,'proven',f,node.lineno,
                                        'Equality comparison against None (use is / is not)'))
    return out

def _py_import_lint(f,tree):
    out=[]
    used=set();strings=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Name):used.add(node.id)
        elif isinstance(node,ast.Constant) and isinstance(node.value,str):strings.add(node.value)
    bound={};imports=[];import_keys={}
    for node in tree.body:
        names=[]
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):names=[node.name]
        elif isinstance(node,ast.Import):
            for a in node.names:
                nm=a.asname or a.name.split('.')[0];names.append(nm)
                imports.append({'bound':nm,'line':node.lineno})
                import_keys.setdefault(('abs',a.name,a.asname),[]).append(node.lineno)
        elif isinstance(node,ast.ImportFrom):
            mod='.'*node.level+(node.module or '')
            for a in node.names:
                if a.name=='*':continue
                nm=a.asname or a.name;names.append(nm)
                imports.append({'bound':nm,'line':node.lineno})
                import_keys.setdefault(('from',mod,a.name,a.asname),[]).append(node.lineno)
        for nm in names:
            if nm in bound:
                out.append(_finding('PY.LINT.DUPLICATE_DEFINITION','low',None,'proven',f,node.lineno,
                                    'Module-level name %s is shadowed by a later import or definition'%_safe_token(nm)))
            else:bound[nm]=node.lineno
    for key,lines in import_keys.items():
        if len(lines)>1:
            label=key[1]+(' as '+key[2] if len(key)>2 and key[2] else '') if key[0]=='abs' else 'from '+key[1]+' import '+key[2]
            out.append(_finding('PY.LINT.DUPLICATE_IMPORT','low',None,'proven',f,lines[1],
                                'Duplicate import of %s in this file'%_safe_token(label)))
    for imp in imports:
        if imp['bound'] not in used and imp['bound'] not in strings:
            out.append(_finding('PY.LINT.UNUSED_IMPORT','low',None,'proven',f,imp['line'],
                                'Imported name %s is not referenced in the supplied file (file-scope analysis)'%_safe_token(imp['bound'])))
    return out

def _complexity(fn):
    score=1;stack=list(ast.iter_child_nodes(fn))
    while stack:
        node=stack.pop()
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda)):continue
        if isinstance(node,(ast.If,ast.For,ast.While,ast.AsyncFor,ast.ExceptHandler,ast.IfExp,ast.Assert)):score+=1
        elif isinstance(node,ast.BoolOp):score+=max(1,len(node.values)-1)
        elif isinstance(node,ast.comprehension):score+=1+len(node.ifs)
        stack.extend(ast.iter_child_nodes(node))
    return score

def _py_complexity_findings(f,tree):
    out=[]
    for node in ast.walk(tree):
        if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        score=_complexity(node)
        if score>COMPLEXITY_THRESHOLD:
            sev='medium' if score>20 else 'low'
            out.append(_finding('PY.COMPLEXITY.HIGH',sev,None,'proven',f,node.lineno,
                                'Function %s cyclomatic complexity is %d (threshold %d)'%(_safe_token(node.name),score,COMPLEXITY_THRESHOLD)))
    return out

def _syntax_finding(f):
    return _finding('PY.SYNTAX.ERROR','medium',None,'proven',f,f.error_line or 1,
                    'Python syntax error prevents AST parsing')

def _py_all(ctx,f):
    tree=_parse(ctx,f)
    if tree is None:return [_syntax_finding(f)]
    out=_py_sqli(f,tree)+_py_cmd(f,tree)+_py_dynamic(f,tree)+_py_weakhash(f,tree)
    out+=_py_antipatterns(f,tree)+_py_import_lint(f,tree)+_py_complexity_findings(f,tree)
    return out

def _module_name(path):
    parts=list(PurePosixPath(path).parts)
    if not parts:return None
    if parts[-1]=='__init__.py':parts=parts[:-1]
    else:
        stem=PurePosixPath(parts[-1]).stem
        parts=parts[:-1]+[stem]
    if not parts or not all(_IDENT_PART_RE.match(p) for p in parts):return None
    return '.'.join(parts)

def _py_security_summary_note(ctx,lims):
    if ctx.syntax_failed:lims.append('Syntax errors prevented full AST analysis of %d Python file(s)'%ctx.syntax_failed)

def _h_ast_parse(ctx):
    py=ctx.python()
    if ctx.files and not py:return ('needs_toolchain',[],[NEEDS_TOOLCHAIN_NOTE],None)
    findings=[];files=[]
    for f in py:
        tree=_parse(ctx,f)
        entry={'file':f.label,'sha256':f.sha256,'language':'python','module':_module_name(f.path),
               'parse':'ok' if tree is not None else 'syntax_error','ast_nodes':0,'functions':0,'classes':0,'imports':0}
        if tree is None:
            findings.append(_syntax_finding(f))
        else:
            entry['ast_nodes']=f.nodes
            for node in ast.walk(tree):
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):entry['functions']+=1
                elif isinstance(node,ast.ClassDef):entry['classes']+=1
                elif isinstance(node,(ast.Import,ast.ImportFrom)):entry['imports']+=1
        files.append(entry)
    lims=[];_py_security_summary_note(ctx,lims)
    for f in ctx.files:
        if f.language!='python':
            files.append({'file':f.label,'sha256':f.sha256,'language':f.language,'module':None,
                          'parse':'skipped_not_python','ast_nodes':0,'functions':0,'classes':0,'imports':0})
            lims.append('%d non-Python file(s) skipped by the Python AST parser'%sum(1 for x in ctx.files if x.language!='python'))
    return ('ok',findings,lims,{'files':files})

def _h_complexity(ctx):
    py=ctx.python()
    if ctx.files and not py:return ('needs_toolchain',[],[NEEDS_TOOLCHAIN_NOTE],None)
    findings=[];files=[]
    for f in py:
        tree=_parse(ctx,f)
        if tree is None:
            findings.append(_syntax_finding(f));continue
        fns=[{'name':_safe_token(n.name),'line':n.lineno,'complexity':_complexity(n)}
             for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
        fns.sort(key=lambda x:(-x['complexity'],x['line']))
        files.append({'file':f.label,'functions':fns,'max':fns[0]['complexity'] if fns else 0})
        findings+=_py_complexity_findings(f,tree)
    lims=[];_py_security_summary_note(ctx,lims)
    return ('ok',findings,lims,{'files':files,'threshold':COMPLEXITY_THRESHOLD})

def _record_import(imports,name,label):
    labels=imports.setdefault(name,[])
    if label not in labels:labels.append(label)

def _resolves(name,modset):
    if name in modset:return True
    return any(m.startswith(name+'.') for m in modset) or any(name.startswith(m+'.') for m in modset)

def _h_cross_ref(ctx):
    py=ctx.python()
    if ctx.files and not py:return ('needs_toolchain',[],[NEEDS_TOOLCHAIN_NOTE],None)
    mods={}
    for f in py:
        m=_module_name(f.path)
        if m:mods[m]=f.label
    modset=set(mods);imports={}
    for f in py:
        tree=_parse(ctx,f)
        if tree is None:continue
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for a in node.names:_record_import(imports,a.name,f.label)
            elif isinstance(node,ast.ImportFrom):
                base='.'*node.level+(node.module or '')
                if node.level==0 and not node.module:continue
                if node.level>0:
                    _record_import(imports,base,f.label)
                else:
                    for a in node.names:_record_import(imports,base+'.'+a.name,f.label)
    entries=[]
    for name in sorted(imports):
        if name.startswith('.'):
            resolution='relative'
        elif _resolves(name,modset):resolution='supplied'
        else:resolution='external'
        entries.append({'module':_safe_token(name),'resolution':resolution,'imported_by':imports[name]})
    files_summary=[{'file':f.label,'module':_module_name(f.path)} for f in py]
    lims=[];_py_security_summary_note(ctx,lims)
    return ('ok',[],lims,{'files':files_summary,'modules':entries})

def _h_lint(ctx):
    py=ctx.python()
    if ctx.files and not py:return ('needs_toolchain',[],[NEEDS_TOOLCHAIN_NOTE],None)
    findings=[];lims=[]
    for f in py:
        tree=_parse(ctx,f)
        if tree is None:findings.append(_syntax_finding(f));continue
        findings+=_py_antipatterns(f,tree)+_py_import_lint(f,tree)
    _py_security_summary_note(ctx,lims)
    other=sum(1 for x in ctx.files if x.language!='python')
    if other:lims.append('%d non-Python file(s) skipped; linting them requires an external toolchain'%other)
    return ('ok',findings,lims,None)

def _h_pattern_match(ctx):
    findings=[];lims=[]
    for f in ctx.files:
        if f.language=='python':
            tree=_parse(ctx,f)
            if tree is not None:findings+=_py_antipatterns(f,tree)
        elif f.language=='javascript':
            findings+=_js_findings(ctx,f,('lint',))
        findings+=_todo_findings(f)
    _py_security_summary_note(ctx,lims)
    js=sum(1 for x in ctx.files if x.language=='javascript')
    if js:lims.append(TOKEN_NOTE)
    return ('ok',findings,lims,None)

def _h_code_review(ctx):
    findings=[];lims=[];other=0;js=0
    for f in ctx.files:
        if f.language=='python':findings+=_py_all(ctx,f)
        elif f.language=='javascript':
            js+=1;findings+=_js_findings(ctx,f,('xss','eval','lint'))
        else:other+=1
        findings+=_secret_findings(ctx,f)
    _py_security_summary_note(ctx,lims)
    if js:lims.append('JavaScript findings are token-based candidates; no JavaScript parser is available')
    if other:lims.append('%d file(s) of unsupported languages were not code-analyzed (supported: Python, JavaScript/TypeScript)'%other)
    lims.append(SECRET_NOTE)
    return ('ok',findings,lims,None)

def _h_lang_python(ctx):
    findings=[];lims=[];other=0
    for f in ctx.files:
        if f.language!='python':other+=1;continue
        findings+=_py_all(ctx,f)+_secret_findings(ctx,f)
    _py_security_summary_note(ctx,lims)
    if other:lims.append('%d non-Python file(s) skipped by lang.python.scan'%other)
    lims.append(SECRET_NOTE)
    return ('ok',findings,lims,None)

def _h_lang_javascript(ctx):
    findings=[];lims=[];other=0;js=0
    for f in ctx.files:
        if f.language!='javascript':other+=1;continue
        js+=1;findings+=_js_findings(ctx,f,('xss','eval','lint'))+_secret_findings(ctx,f)
    if not js:lims.append('No JavaScript/TypeScript sources supplied')
    if other:lims.append('%d non-JavaScript file(s) skipped by lang.javascript.scan'%other)
    lims.append(TOKEN_NOTE);lims.append(SECRET_NOTE)
    return ('ok',findings,lims,None)

def _h_secret_scan(ctx):
    findings=[];lims=[SECRET_NOTE]
    for f in ctx.files:findings+=_secret_findings(ctx,f)
    return ('ok',findings,lims,None)

def _h_sqli(ctx):
    findings=[];lims=[]
    for f in ctx.python():
        tree=_parse(ctx,f)
        if tree is None:findings.append(_syntax_finding(f));continue
        findings+=_py_sqli(f,tree)
    other=sum(1 for x in ctx.files if x.language!='python')
    if other:lims.append('Operation analyzes Python AST sinks; %d non-Python file(s) skipped'%other)
    return ('ok',findings,lims,None)

def _h_cmd(ctx):
    findings=[];lims=[]
    for f in ctx.python():
        tree=_parse(ctx,f)
        if tree is None:findings.append(_syntax_finding(f));continue
        findings+=_py_cmd(f,tree)
    other=sum(1 for x in ctx.files if x.language!='python')
    if other:lims.append('Operation analyzes Python AST sinks; %d non-Python file(s) skipped'%other)
    return ('ok',findings,lims,None)

def _h_xss(ctx):
    findings=[];lims=[TOKEN_NOTE]
    js=ctx.javascript()
    for f in js:findings+=_js_findings(ctx,f,('xss',))
    if not js:lims.append('No JavaScript/TypeScript sources supplied for XSS sink scan')
    return ('ok',findings,lims,None)

def _dep_entries_npm(f,data):
    entries=[]
    if not isinstance(data,dict):return None
    for section in ('dependencies','devDependencies','peerDependencies','optionalDependencies'):
        block=data.get(section)
        if not isinstance(block,dict):continue
        for name,spec in block.items():
            if not isinstance(name,str):continue
            version=spec if isinstance(spec,str) and _SAFE_VER_RE.match(spec) else '[redacted]'
            entries.append({'name':name if _SAFE_PKG_RE.match(name) else '[redacted]','version':version})
    return entries

def _h_dependencies(ctx):
    findings=[];manifests=[]
    for f in ctx.files:
        base=f.label.lower()
        if base in ('package.json','package-lock.json','npm-shrinkwrap.json'):
            try:data=json.loads(f.content)
            except ValueError:data=None
            entries=_dep_entries_npm(f,data)
            if entries is None:
                findings.append(_finding('DEP.MANIFEST.INVALID','low',None,'proven',f,1,'Dependency manifest could not be parsed as JSON'))
                manifests.append({'file':f.label,'manager':'npm','parse':'invalid','entries':[]})
            else:manifests.append({'file':f.label,'manager':'npm','parse':'ok','entries':entries})
        elif base.startswith('requirements') and base.endswith('.txt'):
            entries=[];skipped=0
            for raw in f.content.splitlines():
                line=raw.split('#',1)[0].strip()
                if not line:continue
                if line.startswith(('-r','-e','--')):skipped+=1;continue
                m=re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)$',line)
                if not m:skipped+=1;continue
                ver=m.group(2).strip()
                entries.append({'name':m.group(1) if _SAFE_PKG_RE.match(m.group(1)) else '[redacted]',
                                'version':ver if not ver or _SAFE_VER_RE.match(ver) else '[redacted]'})
            manifests.append({'file':f.label,'manager':'pip','parse':'ok','entries':entries})
            if skipped:
                findings.append(_finding('DEP.MANIFEST.DIRECTIVE','info',None,'proven',f,1,
                                         'Requirements include/edit directives were skipped (%d lines)'%skipped))
    lims=[NO_CVE_NOTE]
    if not manifests:lims.append(NO_MANIFEST_NOTE)
    return ('ok',findings,lims,{'manifests':manifests})

def _dedupe(findings):
    seen=set();out=[]
    for f in findings:
        key=(f['rule_id'],f['evidence']['file'],f['evidence']['line'],f['evidence']['sha256'])
        if key in seen:continue
        seen.add(key);out.append(f)
    return out

_HANDLERS={
 'sast.code.review':_h_code_review,
 'sast.ast.parse':_h_ast_parse,
 'sast.pattern.match':_h_pattern_match,
 'sast.complexity':_h_complexity,
 'sast.cross.ref':_h_cross_ref,
 'sast.lint.run':_h_lint,
 'sast.dependencies':_h_dependencies,
 'sec.secret.scan':_h_secret_scan,
 'sec.sqli.test':_h_sqli,
 'sec.xss.test':_h_xss,
 'sec.cmd.injection':_h_cmd,
 'lang.python.scan':_h_lang_python,
 'lang.javascript.scan':_h_lang_javascript,
}

STATIC_OPS=frozenset(_HANDLERS)

def static_scan(operation,payload):
    if type(operation) is not str:raise ValueError('invalid_operation')
    if operation not in STATIC_OPS:raise ValueError('unknown_operation')
    files=_validated_files(payload)
    ctx=_Ctx(files)
    status,findings,lims,summary=_HANDLERS[operation](ctx)
    expected='python' if operation in ('sec.sqli.test','sec.cmd.injection','lang.python.scan') else 'javascript' if operation in ('sec.xss.test','lang.javascript.scan') else None
    if expected and files and not any(f.language==expected for f in files):status='needs_toolchain'
    findings=_dedupe(findings)
    findings.sort(key=lambda x:(x['evidence']['file'],x['evidence']['line'],x['rule_id']))
    truncated=len(findings)>MAX_FINDINGS
    if truncated:
        findings=findings[:MAX_FINDINGS]
        lims.append('Finding limit (%d) reached; additional findings suppressed'%MAX_FINDINGS)
    if not files:lims.append('No source files supplied');status='no_input'
    lims.append(BASE_LIMIT)
    result={'status':status,'operation':operation,'findings':findings,'scanned_files':len(files),
            'limitations':sorted(set(lims)),'confirmed_vulnerabilities':False,'source_executed':False,'truncated':truncated}
    if summary is not None and summary!={}:result['summary']=summary
    return result
