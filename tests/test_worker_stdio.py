"""Actual isolated children with cp1252 streams; no user data/network/dependencies."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

TOOLS=Path(__file__).resolve().parents[1]/'runtime/tools'
TEXT='Harmonika 日本語 العربية Sunda 🌏'

class WorkerStdio(unittest.TestCase):
    def child(self,script):
        result=subprocess.run([sys.executable,'-I','-c',script,str(TOOLS),TEXT],capture_output=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        self.assertTrue(result.stdout.isascii(), 'JSON child transport must be locale independent')
        return json.loads(result.stdout)

    def test_ssh_and_remote_unicode_response(self):
        for worker in ('ssh_connector.py','remote_connector.py'):
            script="""import importlib.util,io,json,sys
from pathlib import Path
sys.stdout.reconfigure(encoding='cp1252',errors='strict')
s=importlib.util.spec_from_file_location('worker',Path(sys.argv[1])/WORKER)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
text=sys.argv[2]
m.invoke=lambda *args:{'ok':True,'result':text}
sys.argv=['worker','invoke']
sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps({'skill_id':'fixture','input':{}},ensure_ascii=False).encode('utf-8')),encoding='cp1252')
m.main()
""".replace('WORKER',repr(worker))
            self.assertEqual(self.child(script)['result'],TEXT)

    def test_document_unicode_without_optional_dependencies(self):
        script="""import io,json,runpy,sys,types
from pathlib import Path
sys.stdout.reconfigure(encoding='cp1252',errors='strict')
registry=types.ModuleType('skill_registry')
class Registry:
 def validate_input(self,*args):pass
 def route(self,*args):return ('inspect','.pdf')
registry.Registry=Registry;sys.modules['skill_registry']=registry
documents=types.ModuleType('documents')
documents.scoped=lambda root,path:Path(path)
documents.inspect=lambda path:{'text':sys.argv[2]}
sys.modules['documents']=documents
sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps({'skill_id':'fixture','input':{'path':'fixture.pdf'},'project_root':'fixture'},ensure_ascii=False).encode('utf-8')),encoding='cp1252')
runpy.run_path(str(Path(sys.argv[1])/'document_skill_worker.py'),run_name='__main__')
"""
        self.assertEqual(self.child(script)['result']['text'],TEXT)

    def test_network_worker_reads_utf8_bytes_and_returns_ascii_json(self):
        script="""import importlib.util,io,json,sys,types
from pathlib import Path
sys.stdout.reconfigure(encoding='cp1252',errors='strict')
s=importlib.util.spec_from_file_location('worker',Path(sys.argv[1])/'net_ssh_exec.py')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
p=types.ModuleType('paramiko')
class Client:
 def set_missing_host_key_policy(self,*args):pass
 def connect(self,**kwargs):pass
 def exec_command(self,command,**kwargs):
  assert command==sys.argv[2]
  return (None,types.SimpleNamespace(channel=None),None)
 def close(self):pass
p.SSHClient=Client;p.AutoAddPolicy=type('AutoAddPolicy',(),{})
sys.modules['paramiko']=p
m.drain_channel=lambda *args:{'ok':True,'output':sys.argv[2]}
sys.stdin=io.TextIOWrapper(io.BytesIO(json.dumps({'host':'127.0.0.1','username':'fixture','command':sys.argv[2]},ensure_ascii=False).encode('utf-8')),encoding='cp1252')
assert m.main()==0
"""
        self.assertEqual(self.child(script)['output'],TEXT)

if __name__=='__main__':unittest.main()
