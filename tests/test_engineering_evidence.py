import importlib.util
import json
import unittest
from pathlib import Path

_SPEC=importlib.util.spec_from_file_location('engineering_evidence',Path(__file__).resolve().parent.parent/'runtime/tools/engineering_evidence.py')
ee=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(ee)

HAI='hai_live_'+'a'*64
TG='1234567890:AA'+'b'*32
PEM='-----BEGIN '+'RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END '+'RSA PRIVATE KEY-----'
OUTPUT_KEYS={'request','context','files','diff','logs','evidence','warnings','redaction_count','truncated'}

def fail_code(func,*args):
    try:func(*args)
    except ValueError as error:return str(error)
    raise AssertionError('expected ValueError')

def line_count(text):return text.count('\n')

class Redact(unittest.TestCase):
    def test_supported_secret_families(self):
        cases=[
            ('hai api token','key='+HAI,HAI),
            ('telegram bot token','bot '+TG,TG),
            ('openai style token','k=sk-'+'c'*24,'sk-'+'c'*24),
            ('google api key','k=AIza'+'0123456789'*4,'AIza'+'0123456789'*4),
            ('google oauth token','k=ya29.'+'x'*20,'ya29.'+'x'*20),
            ('google code secret','k=GOCSPX-'+'y'*14,'GOCSPX-'+'y'*14),
            ('cloudflare token','k=cfat_'+'z'*22,'cfat_'+'z'*22),
            ('bearer header','Authorization: Bearer '+'q'*40,'Bearer '+'q'*40),
            ('postgres credential url','postgres://admin:hunter2pw@db.internal:5432/app','hunter2pw'),
            ('https credential url','see https://alice:s3cret@x.example/p','s3cret'),
            ('uppercase secret key','SECRET_KEY='+'v'*20,'SECRET_KEY='+'v'*20),
            ('db password env','DB_PASSWORD='+'w'*12,'DB_PASSWORD='+'w'*12),
            ('quoted api key json','{"api_key": "abcd1234xyz"}','abcd1234xyz'),
            ('cookie header','Cookie: session='+'c'*24,'session='+'c'*24),
            ('query token','https://x.example/c?token='+'t'*18+'&keep=1','token='+'t'*18),
            ('assigned password','password = "hunter2z"','hunter2z'),
            ('pem private key',PEM,'MIIEowIBAAKCAQ'),
        ]
        for name,text,marker in cases:
            with self.subTest(name=name):
                out=ee.redact(text)
                self.assertIsInstance(out,str)
                self.assertNotIn(marker,out)
                self.assertIn('[REDACTED',out)
    def test_preserves_line_count_for_pem_and_assignments(self):
        for text in ('a\n'+PEM+'\nb\n','x\r\nDB_PASSWORD=zz1234\r\n','one\ntwo\npassword=hunter2z\n'):
            with self.subTest(text=text):
                out=ee.redact(text)
                self.assertEqual(line_count(out),line_count(text))
                self.assertEqual(out.count('\r'),text.count('\r'))
    def test_ansi_stripping_keeps_newlines(self):
        out=ee.redact('x\x1b]0;t\ny\x07z')
        self.assertEqual(line_count(out),line_count('x\x1b]0;t\ny\x07z'))
        self.assertEqual(ee.redact('\x1b[31mRED\x1b[0m plain\nline2'),'RED plain\nline2')
    def test_strips_control_and_bidi_characters(self):
        self.assertEqual(ee.redact('a\x7fb\x00c\u202ed'),'abcd')
    def test_benign_text_unchanged(self):
        for text in ('hello world','see /usr/local/bin/python','x = 5','https://example.com/no-creds','line1\nline2\n'):
            self.assertEqual(ee.redact(text),text)
    def test_rejects_non_string_without_echo(self):
        for value in (None,5,b'x',[],{},3.5):
            with self.subTest(value=value):
                self.assertEqual(fail_code(ee.redact,value),'invalid_text')

class BoundedJson(unittest.TestCase):
    def test_oversize_reports_input_too_large(self):
        self.assertEqual(fail_code(ee.bounded_json,{'a':'x'*200},100),'input_too_large')
    def test_unserializable_reports_invalid_json(self):
        circular={};circular['self']=circular
        self.assertEqual(fail_code(ee.bounded_json,circular,10**6),'invalid_json')
        self.assertEqual(fail_code(ee.bounded_json,{'a':float('nan')},10**6),'invalid_json')

class PrepareValidation(unittest.TestCase):
    def test_minimal_payload_shape_and_defaults(self):
        result=ee.prepare({'request':'hello'})
        self.assertEqual(set(result),OUTPUT_KEYS)
        self.assertEqual(result['request'],'hello')
        self.assertEqual(result['context'],'')
        self.assertEqual(result['files'],[])
        self.assertEqual(result['diff'],'')
        self.assertEqual(result['logs'],'')
        self.assertEqual(result['evidence'],[])
        self.assertEqual(result['redaction_count'],0)
        self.assertIs(result['truncated'],False)
        self.assertEqual(result['warnings'],[])
    def test_field_limits_in_isolation(self):
        self.assertIsInstance(ee.prepare({'request':'r'*8000}),dict)
        self.assertIsInstance(ee.prepare({'request':'r','context':'c'*12000}),dict)
        self.assertIsInstance(ee.prepare({'request':'r','diff':'d'*64000}),dict)
        self.assertIsInstance(ee.prepare({'request':'r','logs':'l'*32000}),dict)
        self.assertIsInstance(ee.prepare({'request':'r','files':[{'path':'p'*240,'content':'f'*32000,'language':'x'*32}]}),dict)
    def test_rejects_invalid_payloads_without_echoing_values(self):
        cases=[
            ({},None),
            (None,None),('x',None),([],None),
            ({'request':''},None),
            ({'request':'   '},None),
            ({'request':None},None),
            ({'request':7},'7'),
            ({'request':'a'*8001},'a'*100),
            ({'request':'ok','context':None},'None'),
            ({'request':'ok','context':'c'*12001},'c'*100),
            ({'request':'ok','diff':'d'*64001},'d'*100),
            ({'request':'ok','logs':'l'*32001},'l'*100),
            ({'request':'ok','files':{}},'{}'),
            ({'request':'ok','files':'notalist'},'notalist'),
            ({'request':'ok','files':[None]},'None'),
            ({'request':'ok','files':[{'path':'a.py'}]},'a.py'),
            ({'request':'ok','files':[{'content':'x'}]},'content'),
            ({'request':'ok','files':[{'path':'a.py','content':'x','lang':'py'}]},'lang'),
            ({'request':'ok','files':[{'path':5,'content':'x'}]},'path'),
            ({'request':'ok','files':[{'path':'a.py','content':5}]},'content'),
            ({'request':'ok','files':[{'path':'a.py','content':'x','language':None}]},'None'),
            ({'request':'ok','files':[{'path':'a.py','content':'x','language':'l'*33}]},'l'*33),
            ({'request':'ok','files':[{'path':'a.py','content':'c'*32001}]},'c'*100),
            ({'request':'ok\ud800'},'ok'),
            ({'request':'ok','unknown':1},'unknown'),
        ]
        for payload,marker in cases:
            with self.subTest(marker=marker):
                code=fail_code(ee.prepare,payload)
                self.assertTrue(code)
                if marker is not None:self.assertNotIn(marker,code)
    def test_payload_byte_bound(self):
        oversize={'request':'r'*8000,'context':'c'*12000,'diff':'d'*64000,'logs':'l'*32000,'files':[{'path':'a.py','content':'f'*16000}]}
        self.assertEqual(fail_code(ee.prepare,oversize),'input_too_large')
        self.assertIsInstance(ee.prepare(dict(oversize,diff='d'*62000)),dict)
    def test_rejects_private_and_invalid_paths_as_identifiers(self):
        paths=['/etc/passwd','\\etc passwd','C:/tmp/a.py','../x.py','a/../b.py',
               'a\x00b.py','a\tb.py','a\x7fb.py','.env','config/.env','.env.local','keys/id_rsa',
               'keys/id_dsa','keys/id_ecdsa','cert.pem','server.key','secrets.json',
               'src/secrets/a.py','.ssh/config','.aws/credentials','x'*241,'a: b.py',
               'credentials.json']
        for path in paths:
            with self.subTest(path=path):
                code=fail_code(ee.prepare,{'request':'ok','files':[{'path':path,'content':'x'}]})
                self.assertIn(code,('invalid_source_identifier','private_source_identifier','invalid_text'))
                self.assertNotIn(path,code)
        normalized=['./a.py','a//b.py','id_rsa.pub']
        for path in normalized:
            with self.subTest(path=path):
                self.assertIsInstance(ee.prepare({'request':'ok','files':[{'path':path,'content':'x'}]}),dict)
        result=ee.prepare({'request':'ok','files':[{'path':'src/lib/a.b.py','content':'x'},{'path':'x'*240,'content':'y'}]})
        self.assertEqual([f['path'] for f in result['files']],['src/lib/a.b.py','x'*240])
    def test_rejects_wrong_file_counts(self):
        small={'path':'a.py','content':'x'}
        self.assertIsInstance(ee.prepare({'request':'ok','files':[small]*8}),dict)
        self.assertEqual(fail_code(ee.prepare,{'request':'ok','files':[small]*9}),'invalid_files')

class PrepareEvidence(unittest.TestCase):
    def test_line_numbers_preserved_across_blank_and_redacted_lines(self):
        result=ee.prepare({'request':'r','files':[{'path':'a.py','content':'one\n\npassword = hunter2z\nfour'}]})
        self.assertEqual([e['line'] for e in result['evidence']],[1,3,4])
        self.assertEqual([e['id'] for e in result['evidence']],['e1','e2','e3'])
        self.assertIn('[REDACTED',result['evidence'][1]['text'])
        self.assertNotIn('hunter2z',json.dumps(result))
    def test_evidence_fields_and_sources(self):
        result=ee.prepare({'request':'r','files':[{'path':'a.py','content':'alpha\nbeta'}],'logs':'log line','diff':'+diff line'})
        self.assertEqual(result['evidence'][0],{'id':'e1','source':'file','path':'a.py','line':1,'text':'alpha'})
        sources={e['source'] for e in result['evidence']}
        self.assertEqual(sources,{'file','diff','logs'})
        self.assertEqual({e['path'] for e in result['evidence'] if e['source']=='diff'},{'supplied.diff'})
        self.assertEqual({e['path'] for e in result['evidence'] if e['source']=='logs'},{'supplied.log'})
    def test_evidence_cap_is_bounded_and_honest(self):
        result=ee.prepare({'request':'r','files':[{'path':'a.py','content':'\n'.join('L%d'%i for i in range(300))}]})
        self.assertEqual(len(result['evidence']),200)
        self.assertEqual([e['id'] for e in result['evidence']][:3],['e1','e2','e3'])
        self.assertEqual(result['evidence'][-1]['id'],'e200')
        self.assertTrue(result['truncated'])
        self.assertTrue(any('Evidence excerpt limit reached' in w for w in result['warnings']))
    def test_exactly_two_hundred_lines_is_not_truncated(self):
        result=ee.prepare({'request':'r','files':[{'path':'a.py','content':'\n'.join('L%d'%i for i in range(200))}]})
        self.assertEqual(len(result['evidence']),200)
        self.assertFalse(result['truncated'])
    def test_long_evidence_line_cut_is_flagged(self):
        result=ee.prepare({'request':'r','files':[{'path':'a.py','content':'y'*1000}]})
        self.assertEqual(len(result['evidence'][0]['text']),240)
        self.assertTrue(result['truncated'])
    def test_logs_and_diff_not_starved_by_large_files(self):
        result=ee.prepare({'request':'r',
                           'files':[{'path':'a.py','content':'\n'.join('L%d'%i for i in range(300))}],
                           'logs':'token='+HAI+' failed','diff':'+changed'})
        sources={e['source'] for e in result['evidence']}
        self.assertIn('logs',sources)
        self.assertIn('diff',sources)
    def test_deterministic_output(self):
        payload={'request':'r','files':[{'path':'a.py','content':'a\nb'}],'logs':'x','diff':'y'}
        self.assertEqual(ee.prepare(payload),ee.prepare(payload))
    def test_does_not_mutate_payload(self):
        payload={'request':'r','files':[{'path':'a.py','content':'a','language':'py'}]}
        before=json.dumps(payload);ee.prepare(payload)
        self.assertEqual(before,json.dumps(payload))
    def test_result_is_json_serializable(self):
        json.dumps(ee.prepare({'request':'r\x1b[31m','files':[{'path':'a.py','content':'é\nß'}]}))

class RedactionCount(unittest.TestCase):
    def test_counts_only_real_substitutions(self):
        result=ee.prepare({'request':'note [REDACTED literal, no secret here'})
        self.assertEqual(result['redaction_count'],0)
        self.assertEqual(result['warnings'],[])
        result=ee.prepare({'request':'r','logs':'k='+HAI+' and DB_PASSWORD=abc12345'})
        self.assertEqual(result['redaction_count'],2)
        self.assertTrue(any('redacted' in w.lower() for w in result['warnings']))

class SanitizeConversation(unittest.TestCase):
    def test_redacts_all_contents_and_keeps_roles(self):
        messages=[{'role':'system','content':'sys'},{'role':'user','content':'k='+HAI},{'role':'assistant','content':'ok'}]
        clean=ee.sanitize_conversation(messages)
        self.assertEqual([m['role'] for m in clean],['system','user','assistant'])
        self.assertNotIn(HAI,json.dumps(clean))
        self.assertIn('[REDACTED',clean[1]['content'])
        self.assertEqual([set(m) for m in clean],[{'role','content'}]*3)
    def test_bounds_and_shapes(self):
        self.assertEqual(fail_code(ee.sanitize_conversation,'x'),'invalid_messages')
        self.assertEqual(fail_code(ee.sanitize_conversation,[]),'invalid_messages')
        self.assertEqual(fail_code(ee.sanitize_conversation,[None]),'invalid_message')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user'}]),'invalid_text')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':'c','extra':1}]),'invalid_message')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'tool','content':'c'}]),'invalid_message')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':5,'content':'c'}]),'invalid_message')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':5}]),'invalid_text')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'SECRETROLE_XYZ','content':'c'}]),'invalid_message')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':'ok\ud800'}]),'invalid_json')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':'a'*102401}]),'invalid_text')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':'x'}]*81),'invalid_messages')
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'user','content':'a'*3400}]*80),'input_too_large')
    def test_accepts_message_and_byte_limits(self):
        self.assertIsInstance(ee.sanitize_conversation([{'role':'user','content':'a'*102400}]),list)
        self.assertEqual(len(ee.sanitize_conversation([{'role':'user','content':'a'*3000}]*80)),80)
    def test_requires_user_message(self):
        self.assertEqual(fail_code(ee.sanitize_conversation,[{'role':'assistant','content':'hi'}]),'user_required')
    def test_deterministic(self):
        messages=[{'role':'user','content':'k='+HAI}]
        self.assertEqual(ee.sanitize_conversation(messages),ee.sanitize_conversation(messages))

class FromConversation(unittest.TestCase):
    def test_fenced_code_becomes_redacted_snippet_with_honest_warning(self):
        message='please fix\n```python\npassword = "hunter2z"\nx = 1\n```'
        out=ee.from_conversation([{'role':'user','content':message}])
        self.assertEqual(set(out),{'messages','input','warnings'})
        files=out['input']['files']
        self.assertEqual(len(files),1)
        self.assertEqual(files[0]['path'],'snippet-1.py')
        self.assertEqual(files[0]['language'],'python')
        self.assertIn('[REDACTED',files[0]['content'])
        self.assertNotIn('hunter2z',json.dumps(out))
        self.assertTrue(any('not files read from disk' in w for w in out['warnings']))
        prepared=ee.prepare(out['input'])
        snippet=[e for e in prepared['evidence'] if e['source']=='file']
        self.assertEqual([e['line'] for e in snippet],[1,2])
    def test_snippet_content_is_supplied_not_fabricated(self):
        message='```python\nCODE42 = 1\n```'
        out=ee.from_conversation([{'role':'user','content':message}])
        self.assertIn(out['input']['files'][0]['content'],message)
        self.assertTrue(out['input']['files'][0]['path'].startswith('snippet-'))
    def test_only_user_messages_feed_files_diff_logs(self):
        messages=[{'role':'assistant','content':'```go\nASSISTCODE123\n```'},
                  {'role':'user','content':'```python\nUSERCODE123\n```'}]
        out=ee.from_conversation(messages)
        dump=json.dumps(out['input'])
        self.assertNotIn('ASSISTCODE123',dump)
        self.assertIn('USERCODE123',dump)
        self.assertEqual(len(out['input']['files']),1)
        self.assertIn('ASSISTCODE123',out['messages'][0]['content'])
    def test_diff_log_fences_and_traceback_prose(self):
        message='```diff\n+a = 1\n```\n```log\n2026-01-01 ERR boom\n```\nservice died\nValueError: nope'
        out=ee.from_conversation([{'role':'user','content':message}])
        self.assertIn('+a = 1',out['input']['diff'])
        self.assertIn('ERR boom',out['input']['logs'])
        self.assertIn('ValueError: nope',out['input']['logs'])
    def test_clipping_warnings(self):
        out=ee.from_conversation([{'role':'user','content':'u'*9000}])
        self.assertLessEqual(len(out['input']['request']),8000)
        self.assertTrue(any('Request clipped at 8000 characters' in w for w in out['warnings']))
        fences=[{'role':'user','content':''.join('```python\nCODE%d\n```\n'%i for i in range(12))}]
        out=ee.from_conversation(fences)
        self.assertEqual(len(out['input']['files']),8)
        self.assertTrue(any('eight' in w.lower() for w in out['warnings']))
    def test_byte_budget_drops_oldest_snippet_first(self):
        messages=[{'role':'user','content':'```python\nSNIP%d\n'%i+'x'*29980+'\n```'} for i in range(1,9)]
        out=ee.from_conversation(messages)
        dump=json.dumps(out['input'])
        self.assertLess(len(out['input']['files']),8)
        self.assertNotIn('SNIP1',dump)
        self.assertIn('SNIP8',dump)
        self.assertTrue(any('128 KiB' in w for w in out['warnings']))
        self.assertIsInstance(ee.prepare(out['input']),dict)
    def test_requires_user_message_and_bounds(self):
        self.assertEqual(fail_code(ee.from_conversation,[{'role':'assistant','content':'hi'}]),'user_required')
        self.assertEqual(fail_code(ee.from_conversation,[{'role':'user','content':'x'}]*81),'invalid_messages')
    def test_deterministic(self):
        messages=[{'role':'user','content':'```python\nA=1\n```\nValueError: x'}]
        self.assertEqual(ee.from_conversation(messages),ee.from_conversation(messages))

class ModuleIntegrity(unittest.TestCase):
    def test_stdlib_only_and_no_capability_imports(self):
        source=Path(_SPEC.origin).read_text()
        for banned in ('subprocess','socket','urllib','requests','open(','eval(','exec(','__import__','os.system','shutil'):
            self.assertNotIn(banned,source)
        for line in source.splitlines():
            if line.startswith(('import ','from ')):
                self.assertTrue(any(line.startswith(allowed) for allowed in ('import json','import re','from pathlib')),line)

if __name__=='__main__':unittest.main()
