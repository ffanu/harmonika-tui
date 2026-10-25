"""Bounded supplied-evidence preparation; no filesystem or network capability."""
import json
import re
from pathlib import PurePosixPath

MAX_BYTES=131072
FIELDS={'request','context','files','diff','logs'}
PEM=re.compile(r'-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----',re.S)
TOKEN=re.compile(r'\b(?:hai_(?:live|internal)_[a-fA-F0-9]{32,}|(?:sk|sk-proj)-[A-Za-z0-9_-]{16,}|(?:cfat|cfut)_[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|ya29\.[A-Za-z0-9._-]{16,}|GOCSPX-[A-Za-z0-9_-]{12,}|[0-9]{8,12}:[A-Za-z0-9_-]{30,})\b')
ASSIGN=re.compile(r'''(?im)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|[A-Za-z0-9_]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*|authorization|cookie|set-cookie)\b["']?\s*[:=]\s*)(?:["'][^"'\r\n]*["']|[^\s,;\r\n]+)''')
BEARER=re.compile(r'(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{3,}')
CREDS=re.compile(r'(?i)([a-z][a-z0-9+.-]{1,32}://)([^\s/@:]+):([^\s/@]+)@')
QUERY=re.compile(r'(?i)([?&](?:token|api_key|key|secret|password|access_token|authorization|session)=)[^\s&#]+')
AUTH_LINE=re.compile(r'(?im)^(\s*(?:Authorization|Cookie|Set-Cookie)[ \t]*:[ \t]*).*$')
ANSI=re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b\r\n]*(?:\x07|\x1b\\))')

def redact(text):
    return _redact(text)[0]

def _redact(text):
    if type(text) is not str:raise ValueError('invalid_text')
    count=[0]
    def pem(m):count[0]+=1;return '[REDACTED PRIVATE BLOCK]'+('\n'*m.group(0).count('\n'))
    def mark(repl):
        def sub(m):count[0]+=1;return m.expand(repl)
        return sub
    text=PEM.sub(pem,text);text=TOKEN.sub(mark('[REDACTED]'),text)
    text=AUTH_LINE.sub(mark(r'\1[REDACTED]'),text)
    text=BEARER.sub(mark(r'\1[REDACTED]'),text);text=ASSIGN.sub(mark(r'\1[REDACTED]'),text)
    text=CREDS.sub(mark(r'\1[REDACTED]@'),text);text=QUERY.sub(mark(r'\1[REDACTED]'),text)
    text=ANSI.sub('',text)
    return ''.join(c for c in text if c in '\n\r\t' or (ord(c)>=32 and ord(c)!=127 and c not in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069')),count[0]

def bounded_json(value,maximum):
    try:encoded=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    except (TypeError,ValueError,OverflowError,RecursionError,UnicodeError):raise ValueError('invalid_json') from None
    if len(encoded)>maximum:raise ValueError('input_too_large')

def text(value,limit,required=False):
    if type(value) is not str or len(value)>limit or (required and not value.strip()):raise ValueError('invalid_text')
    return value

def source_path(value):
    text(value,240,True)
    p=PurePosixPath(value)
    if value.startswith(('/', '\\')) or '\\' in value or ':' in value or any(ord(c)<32 or ord(c)==127 for c in value) or '..' in p.parts:raise ValueError('invalid_source_identifier')
    bad={'.ssh','.aws','.config','.codex','.claude','.harmonika','.git','secrets','credentials','node_modules','venv','.venv'}
    if any(x.lower() in bad for x in p.parts):raise ValueError('private_source_identifier')
    name=p.name.lower()
    if name=='.env' or name.startswith('.env.') or name in {'id_rsa','id_dsa','id_ecdsa','id_ed25519','.npmrc','.netrc','.git-credentials','.htpasswd','.gitcookies','authorized_keys','harmonika-api-key','secrets.json','secrets.yml','secrets.yaml','credentials.json','credentials.yml','credentials.yaml'} or name.endswith(('.pem','.key','.p12','.pfx','.keystore','.token','.sqlite','.db')):raise ValueError('private_source_identifier')
    return value

def prepare(payload):
    if type(payload) is not dict or set(payload)-FIELDS or 'request' not in payload:raise ValueError('invalid_input')
    bounded_json(payload,MAX_BYTES)
    redactions=[0]
    def _r(value,limit,required=False):
        out,n=_redact(text(value,limit,required));redactions[0]+=n;return out
    result={'request':_r(payload['request'],8000,True),'context':_r(payload.get('context',''),12000),'diff':_r(payload.get('diff',''),64000),'logs':_r(payload.get('logs',''),32000),'files':[]}
    files=payload.get('files',[])
    if type(files) is not list or len(files)>8:raise ValueError('invalid_files')
    for item in files:
        if type(item) is not dict or set(item)-{'path','content','language'} or not {'path','content'}<=set(item):raise ValueError('invalid_file')
        result['files'].append({'path':source_path(item['path']),'content':_r(item['content'],32000),'language':text(item.get('language',''),32)})
    result['redaction_count']=redactions[0]
    result['warnings']=['Sensitive values redacted before analysis'] if result['redaction_count'] else []
    result['truncated']=False;result['evidence']=[];cut=[False]
    if 'PREPARATION WARNINGS:' in result['context']:
        note=result['context'].split('PREPARATION WARNINGS:',1)[1][:1000]
        result['warnings'].append('Conversation evidence preparation: '+note)
        if re.search(r'clipped|shortened|Only eight|not all',note):result['truncated']=True
    queues=[('file',f['path'],f['content'].splitlines()) for f in result['files']]
    if result['diff']:queues.append(('diff','supplied.diff',result['diff'].splitlines()))
    if result['logs']:queues.append(('logs','supplied.log',result['logs'].splitlines()))
    state=[0]*len(queues)
    while len(result['evidence'])<200:
        advanced=False
        for i in range(len(queues)):
            if len(result['evidence'])>=200:break
            source,path,lines=queues[i];idx=state[i]
            while idx<len(lines) and not lines[idx].strip():idx+=1
            if idx>=len(lines):state[i]=idx;continue
            line=lines[idx]
            if len(line)>240:line=line[:240];cut[0]=True
            result['evidence'].append({'id':'e%d'%(len(result['evidence'])+1),'source':source,'path':path,'line':idx+1,'text':line})
            state[i]=idx+1;advanced=True
        if not advanced:break
    if len(result['evidence'])>=200 and any(any(l.strip() for l in queues[i][2][state[i]:]) for i in range(len(queues))):result['truncated']=True
    if cut[0]:result['truncated']=True
    if result['truncated']:result['warnings'].append('Evidence excerpt limit reached; source remains bounded to supplied input')
    return result


def sanitize_conversation(messages):
    if type(messages) is not list or not messages or len(messages)>80:raise ValueError('invalid_messages')
    bounded_json(messages,262144)
    clean=[]
    for m in messages:
        if type(m) is not dict or set(m)-{'role','content'} or m.get('role') not in ('system','user','assistant'):raise ValueError('invalid_message')
        clean.append({'role':m['role'],'content':redact(text(m.get('content'),102400))})
    if not any(m['role']=='user' for m in clean):raise ValueError('user_required')
    return clean

FENCES=re.compile(r'^```([^\r\n]*)\r?\n(.*?)^```[ \t]*(?:\r?\n|$)',re.M|re.S)
EXT={'python':'py','py':'py','javascript':'js','js':'js','typescript':'ts','ts':'ts','go':'go','java':'java','kotlin':'kt','rust':'rs','sql':'sql','json':'json','bash':'sh','sh':'sh','html':'html','css':'css'}

def from_conversation(messages):
    clean=sanitize_conversation(messages);users=[m['content'] for m in clean if m['role']=='user'][-8:]
    files=[];diff=[];logs=[];warnings=[]
    for value in users:
        for match in FENCES.finditer(value):
            lang=(match.group(1).strip().split() or ['text'])[0].lower();content=match.group(2)
            if lang=='diff':diff.append(content)
            elif lang in ('log','logs','text','console','traceback','output','stderr'):logs.append(content)
            elif len(files)<8:
                files.append({'path':'snippet-%d.%s'%(len(files)+1,EXT.get(lang,'txt')),'language':lang[:32],'content':content[:32000]})
                if len(content)>32000:warnings.append('A code snippet was clipped at 32000 characters')
            else:warnings.append('Only eight supplied code snippets included')
        stripped=FENCES.sub('',value)
        if re.search(r'Traceback|\b(?:[A-Za-z]*Error|Exception):|panic:|ECONNREFUSED|ETIMEDOUT',stripped):logs.append(stripped)
    request=FENCES.sub('',users[-1]).strip() or 'Analyze the supplied evidence according to the preceding request.'
    if len(request)>8000:warnings.append('Request clipped at 8000 characters')
    context='\n\n'.join(FENCES.sub('',v).strip() for v in users[:-1])
    if len(context)>11000:warnings.append('Earlier context clipped; not all conversation evidence included')
    payload={'request':request[:8000],'context':context[-11000:],'files':files,'diff':'\n'.join(diff)[:64000],'logs':'\n'.join(logs)[:32000]}
    if len('\n'.join(diff))>64000 or len('\n'.join(logs))>32000:warnings.append('Diff or logs clipped to operation limit')
    # Drop the oldest source first until canonical UTF8 budget is respected.
    while True:
        try:bounded_json(payload,MAX_BYTES-2048);break
        except ValueError:
            warnings.append('Earlier source shortened to respect 128 KiB evidence budget')
            if payload['files']:payload['files'].pop(0)
            elif len(payload['diff'])>1000:payload['diff']=payload['diff'][:len(payload['diff'])//2]
            elif len(payload['logs'])>1000:payload['logs']=payload['logs'][:len(payload['logs'])//2]
            else:raise
    if files:warnings.append('Snippet paths and line numbers refer to pasted code, not files read from disk')
    if warnings:payload['context']=(payload['context']+'\n\nPREPARATION WARNINGS: '+'; '.join(dict.fromkeys(warnings)))[:12000]
    prepare(payload)
    return {'messages':clean,'input':payload,'warnings':list(dict.fromkeys(warnings))}
