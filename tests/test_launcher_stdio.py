"""Launcher subprocess JSON must not depend on the Windows ANSI code page."""
import ast
import importlib.util
import json
import locale
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'runtime/harmonika.py'
TEXT='Harmonika 日本語 العربية 🌏'

class LauncherStdio(unittest.TestCase):
    def test_every_text_subprocess_and_text_file_uses_utf8(self):
        tree=ast.parse(SOURCE.read_text(encoding='utf-8'))
        for call in (node for node in ast.walk(tree) if isinstance(node,ast.Call)):
            if not isinstance(call.func,ast.Attribute):continue
            values={k.arg:k.value for k in call.keywords}
            if call.func.attr=='run' and isinstance(call.func.value,ast.Name) and call.func.value.id=='subprocess':
                if isinstance(values.get('text'),ast.Constant) and values['text'].value is True:
                    self.assertEqual(getattr(values.get('encoding'),'value',None),'utf-8',call.lineno)
                    self.assertEqual(getattr(values.get('errors'),'value',None),'replace',call.lineno)
            elif call.func.attr in ('read_text','write_text'):
                self.assertEqual(getattr(values.get('encoding'),'value',None),'utf-8',call.lineno)

    def test_real_preflight_child_decoding_under_cp1252_locale(self):
        with tempfile.TemporaryDirectory() as temporary:
            area=Path(temporary).resolve()
            with patch.dict(os.environ,{'HARMONIKA_TERMINAL_HOME':str(area)}):
                spec=importlib.util.spec_from_file_location('launcher_stdio_fixture',SOURCE)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            documents=area/'skills/harmonika-documents/scripts/documents.py'
            documents.parent.mkdir(parents=True);documents.write_text('# fixture',encoding='utf-8')
            run=subprocess.run
            results=[]
            def child(argv,**kwargs):
                text='harmonika v1.2.3 '+TEXT if '--version' in argv else json.dumps({'schema_version':1,'formats':{'fixture':TEXT}},ensure_ascii=False)
                script='import sys;sys.stdout.buffer.write('+repr(text.encode('utf-8'))+')'
                result=run([sys.executable,'-I','-c',script],**kwargs)
                results.append(result.stdout)
                return result
            with patch.object(module,'verify_files'),patch.object(module.subprocess,'run',side_effect=child),patch.object(locale,'getencoding',return_value='cp1252',create=True),patch.object(locale,'getpreferredencoding',return_value='cp1252'):
                module.preflight(area,{'files':{}})
            self.assertGreaterEqual(len(results),3)
            self.assertTrue(all(TEXT in result for result in results))

    def test_actual_cp1252_json_stdout_and_utf8_atomic_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            script="""import importlib.util,json,os,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='cp1252',errors='strict')
os.environ['HARMONIKA_TERMINAL_HOME']=sys.argv[2]
s=importlib.util.spec_from_file_location('fixture',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
text=sys.argv[3]
m.ensure_harmonika_downloads=lambda *args:None
m.doctor=lambda:{'ok':True,'fixture':text}
m.atomic_state(Path(sys.argv[2])/'state',text)
sys.argv=['harmonika','doctor'];m.main()
"""
            result=subprocess.run([sys.executable,'-I','-c',script,str(SOURCE),temporary,TEXT],capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            self.assertTrue(result.stdout.isascii())
            self.assertEqual(json.loads(result.stdout)['fixture'],TEXT)
            self.assertEqual((Path(temporary)/'state').read_bytes(),TEXT.encode('utf-8'))

if __name__=='__main__':unittest.main()
