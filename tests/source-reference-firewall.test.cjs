'use strict';
const test=require('node:test'),a=require('node:assert/strict'),f=require('../backend/planner/secret-firewall.cjs');
const allowed=content=>{a.equal(f.scrubText(content),content);a.doesNotThrow(()=>f.assertToolRequest('write',{path:'app.py',content}));};
const blocked=content=>a.throws(()=>f.assertToolRequest('write',{path:'app.py',content}),/tool_secret_reference_required/);
test('Python input password and request cookie references are source, not embedded values',()=>{
 allowed("def login(supplied_password):\n return {'password': supplied_password}\n");
 allowed("def login(self):\n cookie = self.headers.get('Cookie', '')\n return cookie\n");
 allowed("def login(payload):\n password = payload.get('password', '')\n return password\n");
 allowed("import secrets\nsession_cookie = secrets.token_urlsafe(32)\n");
 allowed("def header(sid):\n cookie = f\"sid={sid}; HttpOnly; SameSite=Strict; Path=/\"\n return cookie\n");
});
test('Python literal credentials and literal-bound identifiers remain blocked',()=>{
 for(const source of ["import json\nx={'password':'inline-secret'}\n", "import json\nactual='inline-secret'\nx={'password':actual}\n", "import json\nx={'cookie':lookup('inline-secret')}\n", "def login(payload):\n password=payload.get('password','inline-default')\n", "import json\na='inline-secret'\nb=a\nx={'password':b}\n", "def header(sid):\n cookie=f'sid={sid}inline-secret'\n"])blocked(source);
});
test('parser does not execute source or allow unsafe secret-file reads',()=>{
 blocked("import os\ncookie=open('/etc/shadow').read()\n");
 blocked("import pathlib\ncookie=pathlib.Path('.env').read_text()\n");
 blocked("import os\ncookie=os.system('private-fixture-command')\n");
});
test('raw JSON/header/query credentials have no source exception',()=>{
 for(const source of ['Cookie: inline-secret','?password=inline-secret','password=inline-secret','const x=1; ?cookie=inline-secret'])blocked(source);
 a.throws(()=>f.assertToolRequest('request',{headers:{Cookie:'cookieRef'}}),/tool_secret_reference_required/);
});
test('JS and HTML runtime form references are preserved; literal/suffixed values are blocked',()=>{
 allowed("const data={password: form.password};");
 allowed("<script>const data={password: document.querySelector('#password').value};</script>");
 for(const source of ["const data={password:'inline-secret'};", "const passwordRef='inline-secret';const data={password:passwordRef};", "const data={password: form.password+'inline-secret'};", "const data={password: form.password.concat('inline-secret')};"])blocked(source);
});
test('UTF16 offsets preserve Unicode references without exempting nearby literals',()=>{
 allowed("# 🌊\ndef login(value):\n return {'password':value}\n");
 blocked("# 🌊\ndef login(value):\n return {'password':value,'cookie':'inline-secret'}\n");
});
test('existing known keys and source literal headers still redact',()=>{
 for(const source of ["import json\nx={'password':'hai_live_abcdefghijklmnopqrstuv'}\n", "import json\nheaders={'Cookie':'sid=inline-secret'}\n"])blocked(source);
});
test('source in prose, fences and numbered read results retains runtime references only',()=>{
 for(const source of ["Read this source:\ndef login(value):\n return {'password':value}\n", "Source:\n```python\ndef login(value):\n return {'password':value}\n```\nEnd.", "Read file:\n1: def login(value):\n2:  return {'password':value}\n"] )allowed(source);
 blocked("Read file:\n1: def login(value):\n2:  return {'password':value}\npassword='inline-secret'");
});

test('code suffixes cannot discard a literal binding in the complete module',()=>{
 blocked("actual='inline-secret'\ndef login():\n return {'password':actual}\n");
});

test('indented Python edit fragments preserve request references, not embedded values',()=>{
 const value="    def read(self):\n        cookie = self.headers.get('Cookie', '')\n        return cookie\n";
 allowed("    def read(self):\n        cookie = SimpleCookie(self.headers.get('Cookie', ''))\n        return cookie\n");
 blocked("    def read(self):\n        cookie = SimpleCookie('sid=inline-secret')\n        return cookie\n");
 allowed(value);a.doesNotThrow(()=>f.assertToolRequest('edit',{path:'app.py',old_text:'    def read(self):\n        pass\n',new_text:value}));
 blocked("    def read(self):\n        cookie = 'inline-secret'\n        return cookie\n");
});
test('rejection diagnostics log only fixed categories and numeric counts',()=>{
 const logs=[],old=console.warn;console.warn=line=>logs.push(JSON.parse(line));
 try{blocked("import json\nx={'password':'private-synthetic-value-for-test'}\n")}finally{console.warn=old}
 a.equal(logs.length,1);a.equal(logs[0].event,'credential_firewall_reject');a.ok(!JSON.stringify(logs).includes('private-synthetic-value-for-test'));a.ok(Object.values(logs[0].mechanics).every(Number.isSafeInteger));a.ok(logs[0].mechanics.quoted_literal>=1);
});

test('empty expiring cookie source is not an embedded session credential',()=>{
 allowed("def logout():\n return {'Set-Cookie':'sid=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'}\n");
 blocked("def logout():\n return {'Set-Cookie':'sid=inline-secret; Max-Age=0; Path=/'}\n");
 blocked("def logout():\n return {'Set-Cookie':'sid=; Max-Age=0; Private=inline-secret'}\n");
});
test('expression-wrapped Python literal bindings cannot become credential references',()=>{
 for(const body of ["t=f'inline-secret'", "t='inline-secret'.strip()", "t=base64.b64decode('aW5saW5lLXNlY3JldA==')", "t,u='inline-secret',1", "t=None\nfor t in ['inline-secret']: pass"]){blocked('import json,base64\n'+body+"\nx={'password':t}\n")}
 blocked("import json\ndef f(t=f'inline-secret'):\n return {'password':t}\n");
});
test('JS plain assignments, wrapped calls, aliases and iteration cannot hide literals',()=>{
 for(const body of ["x='inline-secret'", "const x=String('inline-secret')", "const x=atob('aW5saW5lLXNlY3JldA==')", "const x=['inline-secret'][0]", "const t='inline-secret';const x=t", "const x={value:'inline-secret'}"]){blocked(body+";const data={password:"+(body.includes('x={')?'x.value':'x')+"};")}
 blocked("for(const x of ['inline-secret']){const data={password:x};}");
});
test('non-newline Unicode separators cannot shift AST spans onto a credential literal',()=>{
 for(const sep of ['\u2028','\u2029','\u0085','\v','\f']){
  allowed("import json\n# marker"+sep+"same physical line\ndef f(value):\n return {'password':value}\n");
  blocked("import json\n# marker"+sep+"same physical line\ndef f(value):\n return {'password':value,'cookie':'inline-secret'}\n");
 }
});
test('source-region selection cannot drop literal bindings before a function',()=>{
 blocked("Source follows:\nt=f'inline-secret'\ndef f():\n return {'password':t}\n");
 blocked("import json\n# const marker\nt,u='inline-secret',1\nx={'password':t}\n");
});
test('aggregate parsing budget fails closed without executing unbounded source strings',()=>{
 const budget={remaining:0,deadline:Date.now()+10000};
 a.throws(()=>f.scrubText('def unique_budget_fixture_914(value):\n return {"password":value}\n',null,budget),/tool_input_complexity/);
});
test('balanced JS bracket references remain source while inline array literals do not',()=>{
 allowed('const data={password: form.fields[index]};');
 blocked("const form=['inline-secret'];const data={password:form[index]};");
});
test('flattened/plural credential fields and alternative private key paths stay blocked',()=>{
 for(const args of [{'headers.Cookie':'inline-secret'},{passwords:['inline-secret']},{cookies:'inline-secret'}])a.throws(()=>f.assertToolRequest('request',args),/tool_secret_reference_required/);
 for(const path of ['id_ecdsa','id_ecdsa_sk','id_dsa'])a.throws(()=>f.assertToolRequest('read',{path}),/tool_secret_reference_required/);
});
test('cookie header concatenation and empty logout values carry no literal credential',()=>{
 allowed("import secrets\nsid=secrets.token_urlsafe(32)\ncookie='sid='+sid+'; Path=/; HttpOnly; SameSite=Strict'\n");
 allowed("def logout():\n return {'Set-Cookie':'harmonika_session=; Path=/; HttpOnly; SameSite=Lax'}\n");
 blocked("import json\nsid='inline-secret'\ncookie='sid='+sid+'; Path=/'\n");
 blocked("def f(sid):\n cookie='sid=inline-secret'+sid+'; Path=/'\n");
 blocked("def logout():\n return {'Set-Cookie':'sid=; Path=/; Domain=inline-secret'}\n");
});
test('mechanical call diagnostics reveal neither function names nor source values',()=>{
 const logs=[],old=console.warn;console.warn=line=>logs.push(JSON.parse(line));
 try{blocked("def sample():\n cookie = private_synthetic_function('private-synthetic-argument')\n");}
 finally{console.warn=old;}
 a.equal(logs[0].mechanics.other_call,1);
 a.ok(!JSON.stringify(logs).includes('private_synthetic_function'));
 a.ok(!JSON.stringify(logs).includes('private-synthetic-argument'));
 a.ok(Object.values(logs[0].mechanics).every(Number.isSafeInteger));
});
test('camelCase source-path fields retain private path restrictions',()=>{
 a.throws(()=>f.assertToolRequest('read',{filePath:'/tmp/.ssh/id_ed25519'}),/tool_secret_reference_required/);
 a.doesNotThrow(()=>f.assertToolRequest('read',{filePath:'app.py'}));
});
