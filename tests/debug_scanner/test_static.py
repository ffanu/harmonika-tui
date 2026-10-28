"""Tests for the Harmonika debug_scanner pure static source scanner.

Loads backend/debug_scanner/static.py directly (no package import), exercises
every active operation with positive and negative fixtures, and checks the
redaction, hostile-path, bounds and honest-labeling guarantees.
"""
import hashlib
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
_SPEC=importlib.util.spec_from_file_location('debug_scanner_static',ROOT/'backend/debug_scanner/static.py')
static=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(static)

def scan(op,*files):
    return static.static_scan(op,{'files':[{'path':p,'content':c} for p,c in files]})

def rules(result):return sorted(f['rule_id'] for f in result['findings'])

CLEAN_PY='def add(a, b):\n    return a + b\n'
CLEAN_JS='el.textContent = userInput;\nif (a === b) { console.log("ok"); }\n'

class ContractTests(unittest.TestCase):
    def test_static_ops_exact_set(self):
        self.assertIsInstance(static.STATIC_OPS,frozenset)
        self.assertEqual(sorted(static.STATIC_OPS),sorted({
            'sast.code.review','sast.ast.parse','sast.pattern.match','sast.complexity',
            'sast.cross.ref','sast.lint.run','sast.dependencies','sec.secret.scan',
            'sec.sqli.test','sec.xss.test','sec.cmd.injection','lang.python.scan',
            'lang.javascript.scan'}))
        self.assertNotIn('sast.data.flow',static.STATIC_OPS)
        self.assertNotIn('sec.sqli',static.STATIC_OPS)
    def test_operation_validation(self):
        with self.assertRaises(ValueError) as e:self.assertRaises(ValueError,static.static_scan,'sec.sqli',{'files':[]}) or static.static_scan('sec.sqli',{'files':[]})
        self.assertEqual(str(e.exception),'unknown_operation')
        with self.assertRaises(ValueError) as e:static.static_scan(123,{'files':[]})
        self.assertEqual(str(e.exception),'invalid_operation')
    def test_payload_schema_exact(self):
        for payload in ({},{'files':[],'authorized':True},{'files':[],'extra':1},{'files':'x'},{'files':[{'path':'a.py'}]},{'files':[{'path':'a.py','content':'x','lang':'python'}]},{'files':[{'path':'a.py','content':1}]},{'files':[{'path':1,'content':'x'}]}):
            with self.subTest(payload=payload),self.assertRaises(ValueError) as e:static.static_scan('sast.code.review',payload)
            self.assertIn(str(e.exception),('invalid_input','invalid_files','invalid_file','invalid_path','invalid_content'))
        self.assertEqual(scan('sast.code.review',('a.py','x=1\n'))['scanned_files'],1)
    def test_hostile_paths(self):
        cases=(('../x.py','path_traversal'),('a/../b.py','path_traversal'),('/abs/x.py','absolute_path'),
               ('~/x.py','absolute_path'),('..\\x.py','path_backslash'),('a\nb.py','path_control_character'),
               ('.env','private_source_identifier'),('.env.production','private_source_identifier'),
               ('id_rsa','private_source_identifier'),('server.pem','private_source_identifier'),
               ('token.key','private_source_identifier'),('secrets/app.py','private_source_identifier'),
               ('creds/credentials.yaml','private_source_identifier'),('dir/','invalid_path'),('a:b.py','invalid_path'))
        for path,code in cases:
            with self.subTest(path=path),self.assertRaises(ValueError) as e:scan('sast.code.review',(path,'x=1\n'))
            self.assertEqual(str(e.exception),code)
    def test_duplicate_path_and_no_source_leak_in_errors(self):
        secret='TOPSECRETVALUE1234'
        with self.assertRaises(ValueError) as e:static.static_scan('sast.code.review',{'files':[{'path':'a.py','content':secret},{'path':'a.py','content':secret}]})
        self.assertEqual(str(e.exception),'duplicate_path')
        self.assertNotIn(secret,str(e.exception))
    def test_input_bounds(self):
        with self.assertRaises(ValueError) as e:static.static_scan('sast.code.review',{'files':[{'path':'f%d.py'%i,'content':'x=1\n'} for i in range(21)]})
        self.assertEqual(str(e.exception),'too_many_files')
        with self.assertRaises(ValueError) as e:static.static_scan('sast.code.review',{'files':[{'path':'f%d.py'%i,'content':'x'*3500} for i in range(20)]})
        self.assertEqual(str(e.exception),'input_too_large')
        r=scan('sast.code.review',*[('f%d.py'%i,'x=1\n') for i in range(20)])
        self.assertEqual(r['scanned_files'],20)
    def test_finding_cap(self):
        src=''.join('def f%d():\n try:\n  pass\n except:\n  pass\n'%i for i in range(400))
        r=scan('sast.lint.run',('many.py',src))
        self.assertEqual(len(r['findings']),static.MAX_FINDINGS)
        self.assertTrue(any('Finding limit' in l for l in r['limitations']))
    def test_result_contract(self):
        py='def f(x):\n    return eval(x)\n'
        r=scan('sast.code.review',('users/john.doe@corp/home/app.py',py),('ui/app.js','el.innerHTML = userInput;\n'))
        for key in ('status','operation','findings','scanned_files','limitations','confirmed_vulnerabilities','source_executed'):
            self.assertIn(key,r)
        self.assertFalse(r['confirmed_vulnerabilities']);self.assertFalse(r['source_executed'])
        self.assertEqual(r['operation'],'sast.code.review');self.assertEqual(r['status'],'ok')
        for f in r['findings']:
            self.assertEqual(set(f),{'rule_id','severity','cwe','confidence_kind','evidence','message'})
            self.assertIn(f['severity'],('critical','high','medium','low','info'))
            self.assertIn(f['confidence_kind'],('proven','heuristic_candidate','rule_based_candidate'))
            self.assertEqual(set(f['evidence']),{'file','line','sha256'})
            self.assertIsInstance(f['evidence']['line'],int);self.assertGreaterEqual(f['evidence']['line'],1)
        hashed={f['evidence']['file']:f['evidence']['sha256'] for f in r['findings']}
        self.assertEqual(hashed['app.py'],hashlib.sha256(py.encode()).hexdigest())
        json.loads(json.dumps(r))
    def test_path_pii_redaction(self):
        r=scan('sast.code.review',('users/john.doe@corp/home/app.py','def f(x):\n    return eval(x)\n'))
        self.assertEqual([f['evidence']['file'] for f in r['findings']],['app.py'])
        self.assertNotIn('john.doe',json.dumps(r));self.assertNotIn('users/',json.dumps(r))
    def test_determinism(self):
        files=('a.py','import os\n'), ('b.js','if (a == b) { debugger; }\n')
        self.assertEqual(scan('sast.code.review',*files),scan('sast.code.review',*files))
    def test_empty_files_allowed(self):
        r=static.static_scan('sast.code.review',{'files':[]})
        self.assertEqual(r['status'],'no_input');self.assertEqual(r['findings'],[]);self.assertEqual(r['scanned_files'],0)
        self.assertTrue(any('No source files supplied' in l for l in r['limitations']))

class OperationTests(unittest.TestCase):
    def test_code_review_positive_negative(self):
        py='import hashlib\nimport subprocess\n\ndef go(cur, uid, cmd):\n    cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n    subprocess.run(cmd, shell=True)\n    return eval(cmd)\n\ndef h(pw):\n    return hashlib.md5(pw).hexdigest()\n'
        js='el.innerHTML = userInput;\n'
        r=scan('sast.code.review',('app.py',py),('app.js',js))
        self.assertIn('PY.SEC.SQLI_CANDIDATE',rules(r));self.assertIn('PY.SEC.CMD_INJECTION',rules(r))
        self.assertIn('PY.SEC.EVAL_EXEC',rules(r));self.assertIn('PY.SEC.WEAK_HASH',rules(r))
        self.assertIn('JS.XSS.HTML_SINK',rules(r))
        self.assertEqual(scan('sast.code.review',('clean.py',CLEAN_PY),('clean.js',CLEAN_JS))['findings'],[])
    def test_ast_parse_summary(self):
        py='import os\n\nclass A:\n    pass\n\ndef one():\n    return 1\n\ndef two():\n    return 2\n'
        r=scan('sast.ast.parse',('src/tool.py',py))
        entry=[e for e in r['summary']['files'] if e['file']=='tool.py'][0]
        self.assertEqual(entry['parse'],'ok');self.assertEqual(entry['imports'],1)
        self.assertEqual(entry['classes'],1);self.assertEqual(entry['functions'],2)
        self.assertEqual(entry['module'],'src.tool');self.assertGreater(entry['ast_nodes'],0)
        self.assertEqual(r['findings'],[])
    def test_ast_parse_syntax_error_and_needs_toolchain(self):
        r=scan('sast.ast.parse',('bad.py','def broken(:\n'))
        self.assertEqual(rules(r),['PY.SYNTAX.ERROR'])
        self.assertEqual([e['parse'] for e in r['summary']['files']],['syntax_error'])
        self.assertEqual(r['findings'][0]['confidence_kind'],'proven')
        r=scan('sast.ast.parse',('a.js','var x = 1;\n'))
        self.assertEqual(r['status'],'needs_toolchain')
    def test_pattern_match(self):
        r=scan('sast.pattern.match',('a.py','def f(a=[]):\n    try:\n        return a\n    except:\n        pass\n'), ('b.js','if (a == b) { debug(); }\n'), ('c.txt','TODO: fix this\n'))
        self.assertIn('PY.LINT.MUTABLE_DEFAULT',rules(r));self.assertIn('PY.LINT.BARE_EXCEPT',rules(r))
        self.assertIn('JS.LINT.LOOSE_EQUALITY',rules(r));self.assertIn('GEN.PATTERN.TODO',rules(r))
        self.assertEqual(scan('sast.pattern.match',('clean.py',CLEAN_PY),('clean.js',CLEAN_JS))['findings'],[])
    def test_complexity(self):
        src='def complex_fn(a):\n'+''.join('    if a > %d:\n        a += 1\n'%i for i in range(12))+'    return a\n'
        r=scan('sast.complexity',('big.py',src))
        self.assertEqual(rules(r),['PY.COMPLEXITY.HIGH'])
        self.assertIn('complex_fn',r['findings'][0]['message']);self.assertIn('13',r['findings'][0]['message'])
        fn=[x for x in r['summary']['files'][0]['functions'] if x['name']=='complex_fn'][0]
        self.assertEqual(fn['complexity'],13);self.assertEqual(r['summary']['threshold'],10)
        self.assertEqual(scan('sast.complexity',('ok.py','def simple(a):\n    return a\n'))['findings'],[])
    def test_cross_ref(self):
        r=scan('sast.cross.ref',('a.py','import json\nimport b\n'), ('b.py','VALUE = 1\n'))
        self.assertEqual(r['findings'],[])
        mods={e['module']:e for e in r['summary']['modules']}
        self.assertEqual(mods['b']['resolution'],'supplied');self.assertEqual(mods['b']['imported_by'],['a.py'])
        self.assertEqual(mods['json']['resolution'],'external')
        self.assertEqual({e['file']:e['module'] for e in r['summary']['files']},{'a.py':'a','b.py':'b'})
        r=scan('sast.cross.ref',('a.js','var x = 1;\n'))
        self.assertEqual(r['status'],'needs_toolchain')
    def test_lint_rules(self):
        r=scan('sast.lint.run',('l.py','import os\n'))
        self.assertIn('PY.LINT.UNUSED_IMPORT',rules(r))
        r=scan('sast.lint.run',('l.py','import os\nimport os\n'))
        self.assertIn('PY.LINT.DUPLICATE_IMPORT',rules(r))
        r=scan('sast.lint.run',('l.py','import os\ndef os():\n    pass\n'))
        self.assertIn('PY.LINT.DUPLICATE_DEFINITION',rules(r))
        r=scan('sast.lint.run',('l.py','def f(x):\n    if x == None:\n        return 1\n    return 2\n'))
        self.assertIn('PY.LINT.NONE_COMPARE',rules(r))
        r=scan('sast.lint.run',('a.js','var x = 1;\n'))
        self.assertEqual(r['status'],'needs_toolchain')
    def test_dependencies(self):
        pkg='{"dependencies":{"lodash":"4.17.20"},"devDependencies":{"jest":"^29.0.0"}}'
        req='flask==2.0.1\nrequests>=2.25\n# note\n-r other.txt\n'
        r=scan('sast.dependencies',('package.json',pkg),('requirements.txt',req))
        npm=[m for m in r['summary']['manifests'] if m['manager']=='npm'][0]
        pip=[m for m in r['summary']['manifests'] if m['manager']=='pip'][0]
        self.assertEqual(npm['entries'],[{'name':'lodash','version':'4.17.20'},{'name':'jest','version':'^29.0.0'}])
        self.assertEqual(pip['entries'],[{'name':'flask','version':'==2.0.1'},{'name':'requests','version':'>=2.25'}])
        self.assertIn('DEP.MANIFEST.DIRECTIVE',rules(r))
        self.assertIsNone(re.search(r'cve-\d',json.dumps(r).lower()))
        r=scan('sast.dependencies',('package.json','{oops'))
        self.assertIn('DEP.MANIFEST.INVALID',rules(r))
        r=scan('sast.dependencies',('app.py','x=1\n'))
        self.assertTrue(any('No supported dependency manifests' in l for l in r['limitations']))
    def test_sqli_positive_negative(self):
        pos=(('f-string','def q(cur, uid):\n    cur.execute(f"SELECT * FROM users WHERE id = {uid}")\n'),
             ('concat','def q(cur, uid):\n    cur.execute("DELETE FROM t WHERE id = " + uid)\n'),
             ('percent','def q(cur, uid):\n    cur.execute("DELETE FROM t WHERE id = %s" % uid)\n'),
             ('format','def q(cur, uid):\n    cur.execute("SELECT * FROM t WHERE id = {}".format(uid))\n'))
        for name,src in pos:
            with self.subTest(case=name):
                r=scan('sec.sqli.test',('app.py',src))
                self.assertEqual(rules(r),['PY.SEC.SQLI_CANDIDATE'])
                f=r['findings'][0]
                self.assertEqual(f['cwe'],89);self.assertEqual(f['confidence_kind'],'heuristic_candidate');self.assertEqual(f['evidence']['line'],2)
        neg=(('parameterized','def q(cur, uid):\n    cur.execute("SELECT * FROM users WHERE id = ?", (uid,))\n'),
             ('named','def q(cur, uid):\n    cur.execute("SELECT * FROM users WHERE id = :id", {"id": uid})\n'),
             ('kwarg','def q(cur, uid):\n    cur.execute(q, parameters=(uid,))\n'),
             ('no-sql-fstring','def q(cur, uid):\n    cur.execute(f"hello {uid}")\n'),
             ('literal','def q(cur):\n    cur.execute("SELECT 1")\n'))
        for name,src in neg:
            with self.subTest(case=name):self.assertEqual(scan('sec.sqli.test',('app.py',src))['findings'],[])
    def test_xss_positive_negative(self):
        r=scan('sec.xss.test',('app.js','el.innerHTML = userInput;\ndocument.write(data);\nvar c = { dangerouslySetInnerHTML: { __html: h } };\nif (a == b) { eval(expr); }\n'))
        self.assertEqual(rules(r),['JS.XSS.DOCUMENT_WRITE','JS.XSS.HTML_SINK','JS.XSS.REACT_DANGEROUS_HTML'])
        for f in r['findings']:
            self.assertEqual(f['cwe'],79);self.assertEqual(f['confidence_kind'],'rule_based_candidate')
        neg=('el.textContent = userInput;\n','var s = "x.innerHTML = y";\n','// el.innerHTML = user\n','el.className = cls;\n')
        for src in neg:
            with self.subTest(src=src[:20]):self.assertEqual(scan('sec.xss.test',('a.js',src))['findings'],[])
    def test_cmd_injection(self):
        r=scan('sec.cmd.injection',('run.py','import subprocess\nimport os\ndef go(cmd):\n    subprocess.run(cmd, shell=True)\n    subprocess.run("ls -la", shell=True)\n    os.system(cmd)\n    subprocess.run([cmd])\n    subprocess.run(cmd, shell=False)\n    os.popen("ls")\n'))
        hits=[(f['evidence']['line'],f['severity']) for f in r['findings']]
        self.assertEqual(hits,[(4,'high'),(5,'medium'),(6,'high'),(9,'medium')])
        self.assertTrue(all(f['cwe']==78 and f['confidence_kind']=='heuristic_candidate' for f in r['findings']))
    def test_eval_exec_and_weak_hash(self):
        r=scan('sast.code.review',('a.py','def f(x):\n    return eval(x)\n'))
        self.assertIn('PY.SEC.EVAL_EXEC',rules(r))
        self.assertEqual(scan('sast.code.review',('a.py','import ast\ndef f(x):\n    return ast.literal_eval(x)\n'))['findings'],[])
        r=scan('sast.code.review',('a.py','import hashlib\ndef h(p):\n    return hashlib.md5(p).hexdigest()\n'))
        self.assertIn('PY.SEC.WEAK_HASH',rules(r))
        self.assertEqual(scan('sast.code.review',('a.py','import hashlib\ndef h1(p):\n    return hashlib.md5(p, usedforsecurity=False)\ndef h2(p):\n    return hashlib.sha256(p).hexdigest()\n'))['findings'],[])
    def test_lang_python_scan(self):
        src='import subprocess\n\ndef go(cmd):\n    subprocess.run(cmd, shell=True)\n    return eval(cmd)\n'
        r=scan('lang.python.scan',('app.py',src),('app.js','el.innerHTML = user;\n'))
        self.assertIn('PY.SEC.CMD_INJECTION',rules(r));self.assertIn('PY.SEC.EVAL_EXEC',rules(r))
        self.assertNotIn('JS.XSS.HTML_SINK',rules(r))
        self.assertTrue(any('skipped' in l for l in r['limitations']))
        self.assertEqual(scan('lang.python.scan',('clean.py',CLEAN_PY))['findings'],[])
    def test_lang_javascript_scan(self):
        src='el.innerHTML = userInput;\nif (a == b) { eval(expr); }\ndebugger;\n'
        r=scan('lang.javascript.scan',('app.js',src))
        for rule in ('JS.XSS.HTML_SINK','JS.LINT.LOOSE_EQUALITY','JS.SEC.EVAL','JS.LINT.DEBUGGER'):
            self.assertIn(rule,rules(r))
        self.assertEqual(scan('lang.javascript.scan',('clean.js',CLEAN_JS))['findings'],[])

class SecretTests(unittest.TestCase):
    def test_secret_detection_positive(self):
        hdr='-----BEGIN RSA ' + 'PRIVATE KEY-----'
        ftr='-----END RSA ' + 'PRIVATE KEY-----'
        pem=f'{hdr}\nMIIBdummy\n{ftr}\n'
        r=scan('sec.secret.scan',('auth.py',pem))
        self.assertIn('SEC.SECRET.PRIVATE_BLOCK',rules(r))
        r=scan('sec.secret.scan',('conf.py','API_KEY = "sk-proj-abcdefghijklmnop1234"\n'))
        self.assertIn('SEC.SECRET.TOKEN_FORMAT',rules(r))
        r=scan('sec.secret.scan',('cfg.py','password = "sup3rs3cretpass"\n'))
        self.assertIn('SEC.SECRET.ASSIGNMENT',rules(r))
        r=scan('sec.secret.scan',('cfg.py','URL = "https://user:hunter22@example.com/api"\n'))
        self.assertIn('SEC.SECRET.URL_CREDENTIALS',rules(r))
        r=scan('sec.secret.scan',('headers.txt','Authorization: Bearer abcdefghijklmnopqrst\n'))
        self.assertIn('SEC.SECRET.BEARER',rules(r))
    def test_secret_values_never_in_output(self):
        cases=(('t.py','API_KEY = "sk-proj-abcdefghijklmnop1234"\n'),
               ('t.py','password = "sup3rs3cretpass"\n'),
               ('t.py','URL = "https://aladdin:opensesame@internal.example.net/db"\n'))
        for path,src in cases:
            with self.subTest(path=path):
                dump=json.dumps(scan('sec.secret.scan',(path,src)))
                for secret in ('sk-proj-abcdefghijklmnop1234','sup3rs3cretpass','opensesame','internal.example.net','https://'):
                    self.assertNotIn(secret,dump)
    def test_secret_comment_and_placeholder_false_positives(self):
        cases=(('a.py','# token = "sk-proj-abcdefghijklmnop"\n'),
               ('a.py','# password = "hunter2secretpw"\n'),
               ('a.js','// api_key = "abcdefgh12345678"\n'),
               ('a.py','password = "changeme"\n'),
               ('a.py','password = "your_api_key_here"\n'),
               ('a.py','password = get_password()\n'))
        for path,src in cases:
            with self.subTest(path=path,src=src[:24]):
                self.assertEqual(scan('sec.secret.scan',(path,src))['findings'],[])
    def test_secret_line_numbers_and_cwe(self):
        src='x = 1\ny = 2\npassword = "sup3rs3cretpass"\n'
        f=scan('sec.secret.scan',('a.py',src))['findings'][0]
        self.assertEqual(f['evidence']['line'],3);self.assertEqual(f['cwe'],798)
        self.assertEqual(f['confidence_kind'],'rule_based_candidate')

if __name__=='__main__':unittest.main()
