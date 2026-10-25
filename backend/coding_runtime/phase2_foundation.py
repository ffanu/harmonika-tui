"""Phase2A pure change planner; not registered or activated on production yet.

Reads use the existing scoped FileStore; output is a proposed file set for a
future copy-on-write publish transaction. No host shell, dependency download,
model-selected executable, or filesystem mutation occurs in this module.
"""
import difflib
import hashlib
import re
from pathlib import PurePosixPath

class FoundationError(ValueError):
    def __init__(self,code):super().__init__(code);self.code=code

def path(value):
    if type(value) is not str or not value or len(value)>240 or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value):raise FoundationError('invalid_path')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in ('','.','..') for x in value.split('/')):raise FoundationError('invalid_path')
    if any(x.startswith('.') for x in p.parts):raise FoundationError('private_path_denied')
    return str(p)

def symbol(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',value):raise FoundationError('invalid_name')
    return value

def digest(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def unified(pathname,before,after):
    return ''.join(difflib.unified_diff(before.splitlines(keepends=True),after.splitlines(keepends=True),fromfile='a/'+pathname,tofile='b/'+pathname))

def patch_existing(pathname,source,patch):
    if type(patch) is not str or len(patch.encode('utf8'))>524288:raise FoundationError('invalid_patch')
    if source and not source.endswith('\n'):raise FoundationError('patch_newline_unsupported')
    lines=patch.splitlines(keepends=True)
    if len(lines)<3 or lines[0]!='--- a/'+pathname+'\n' or lines[1]!='+++ b/'+pathname+'\n':raise FoundationError('patch_target_mismatch')
    old=source.splitlines(keepends=True);cursor=0;out=[];i=2;hunks=0
    while i<len(lines):
        m=re.fullmatch(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: [^\n]*)?\n',lines[i])
        if not m:raise FoundationError('invalid_patch_hunk')
        start=int(m[1]);oc=int(m[2] or 1);ns=int(m[3]);nc=int(m[4] or 1)
        offset=start-1 if oc else start
        if offset<cursor or offset>len(old):raise FoundationError('patch_range_conflict')
        out.extend(old[cursor:offset]);cursor=offset;i+=1;seen_old=seen_new=0
        expected_new=len(out)+1 if nc else len(out)
        if ns!=expected_new:raise FoundationError('patch_new_range_conflict')
        while i<len(lines) and not lines[i].startswith('@@ '):
            line=lines[i];i+=1
            if not line or line[0] not in ' +-':raise FoundationError('invalid_patch_line')
            if not line.endswith('\n'):raise FoundationError('patch_newline_unsupported')
            kind,text=line[0],line[1:]
            if kind in ' -':
                if cursor>=len(old) or old[cursor]!=text:raise FoundationError('patch_context_conflict')
                cursor+=1;seen_old+=1
            if kind in ' +':out.append(text);seen_new+=1
            if seen_old>oc or seen_new>nc:raise FoundationError('patch_count_mismatch')
        if (seen_old,seen_new)!=(oc,nc):raise FoundationError('patch_count_mismatch')
        hunks+=1
        if hunks>128:raise FoundationError('patch_hunk_limit')
    if not hunks:raise FoundationError('empty_patch')
    out.extend(old[cursor:]);return ''.join(out)

def _read(fs,name):
    value=fs.execute('code.read',{'path':name})
    content=value['content']
    if len(content.encode())>524288:raise FoundationError('file_too_large')
    return content,value['sha256']

def _create(fs,name,text):
    try:fs.execute('code.read',{'path':name})
    except Exception as e:
        if getattr(e,'code',None) not in ('not_found','file_not_found'):raise
    else:raise FoundationError('file_exists')
    return {'path':name,'content':text,'mode':'create'}

def plan(skill,args,fs):
    if type(args) is not dict:raise FoundationError('invalid_input')
    keys={
      'code.diff':({'path','new_content'},{'path','new_content'}),
      'code.patch':({'path','patch','expected_sha256'},{'path','patch','expected_sha256'}),
      'scaffold.project':({'path','template','name'},{'path','template','name'}),
      'scaffold.component':({'path','name'},{'path','name'}),
      'scaffold.api':({'path','route','method'},{'path','route','method'}),
    }
    if skill not in keys:raise FoundationError('unknown_foundation_skill')
    allowed,required=keys[skill]
    if set(args)-allowed or not required<=set(args):raise FoundationError('invalid_input')
    name=path(args['path']);writes=[];notes=[]
    if skill=='code.diff':
        before,sha=_read(fs,name);after=args['new_content']
        if type(after) is not str or len(after.encode())>524288:raise FoundationError('invalid_content')
        return {'skill_id':skill,'status':'preview','writes':[],'diff':unified(name,before,after),'source_sha256':sha,'patch_applicable':not any(v and not v.endswith('\n') for v in (before,after))}
    if skill=='code.patch':
        before,sha=_read(fs,name)
        if type(args['expected_sha256']) is not str or sha!=args['expected_sha256']:raise FoundationError('hash_mismatch')
        after=patch_existing(name,before,args['patch'])
        if len(after.encode())>524288:raise FoundationError('file_too_large')
        writes=[{'path':name,'content':after,'mode':'replace','expected_sha256':sha}]
    elif skill=='scaffold.project':
        title=symbol(args['name']);template=args['template']
        if template=='python_http':
            main="import json\nfrom http.server import BaseHTTPRequestHandler, HTTPServer\n\nclass Handler(BaseHTTPRequestHandler):\n    def do_GET(self):\n        status = 200 if self.path == '/health' else 404\n        self.send_response(status)\n        self.send_header('Content-Type', 'application/json')\n        self.end_headers()\n        self.wfile.write(json.dumps({'ok': status == 200}).encode())\n\nif __name__ == '__main__':\n    HTTPServer(('127.0.0.1', 8080), Handler).serve_forever()\n"
            test="import unittest\nfrom app import Handler\nclass Smoke(unittest.TestCase):\n    def test_handler_exists(self):\n        self.assertTrue(callable(Handler.do_GET))\n"
            files={'app.py':main,'test_app.py':test,'README.md':'# '+title+'\n\nRun: python3 app.py\nTest: python3 -m unittest discover\nLocal health: http://127.0.0.1:8080/health\n'}
        elif template=='node_http':
            main="const http = require('node:http');\nfunction createServer() { return http.createServer((req,res) => { const ok=req.url==='/health'; res.writeHead(ok?200:404,{'Content-Type':'application/json'}); res.end(JSON.stringify({ok})); }); }\nif (require.main === module) createServer().listen(8080,'127.0.0.1');\nmodule.exports = {createServer};\n"
            test="const test=require('node:test'); const assert=require('node:assert/strict'); const {createServer}=require('./app.cjs'); test('server factory',()=>{const server=createServer();assert.equal(typeof server.listen,'function');server.close();});\n"
            files={'app.cjs':main,'app.test.cjs':test,'README.md':'# '+title+'\n\nRun: node app.cjs\nTest: node --test\nLocal health: http://127.0.0.1:8080/health\n'}
        else:raise FoundationError('unsupported_template')
        writes=[_create(fs,name+'/'+p,c) for p,c in files.items()]
        notes=['Standard-library starter only; no third-party dependency or deployment performed.']
    elif skill=='scaffold.component':
        component=symbol(args['name'])
        if not component[0].isupper() or not name.endswith('.jsx'):raise FoundationError('react_component_name_required')
        text="import React from 'react';\n\nexport default function "+component+"({ title = '"+component+"', children }) {\n  return <section aria-label={title}><h2>{title}</h2>{children}</section>;\n}\n"
        writes=[_create(fs,name,text)];notes=['React component template; compilation requires the matching React toolchain in Phase2B.']
    elif skill=='scaffold.api':
        route=args['route'];method=args['method']
        if type(route) is not str or not re.fullmatch(r'/[a-zA-Z0-9/_-]{0,120}',route) or method not in ('GET','POST'):raise FoundationError('invalid_route')
        if not name.endswith('.py'):raise FoundationError('python_api_template_required')
        text='''"""Generated route helper. Connect to the application router explicitly."""\nROUTE = '''+repr(route)+'\nMETHOD = '+repr(method)+"\n\ndef handle(method, path):\n    if method != METHOD or path != ROUTE:\n        return 404, {'error': 'not_found'}\n    return 200, {'ok': True}\n"
        writes=[_create(fs,name,text)];notes=['Standalone Python route helper; existing application routing stays unchanged until explicitly integrated.']
    return {'skill_id':skill,'status':'preview','writes':writes,'notes':notes,'production_registered':False}
