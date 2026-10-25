import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime/tools'))
from skill_registry import Registry,SkillError
from skill_executor import SkillExecutor,ExecutionContext

class EngineeringRegistryTests(unittest.TestCase):
    def test_four_read_only_operations(self):
        items=[x for x in Registry().list() if x['id'].startswith('engineering.')]
        self.assertEqual(len(items),4)
        for item in items:
            self.assertEqual(item['worker'],'engineering')
            self.assertEqual(item['permissions'],{'filesystem':[],'network':False,'shell':False})
            self.assertEqual(Registry().route(item['id']),('analyze',''))
    def test_actor_and_execution_injection_denied(self):
        for field in ('principal_id','project_root','command','headers','model','api_key'):
            with self.subTest(field=field),self.assertRaises(SkillError):
                Registry().validate_input('engineering.debug',{'request':'debug',field:'x'})
    def test_sensitive_or_outside_source_identifiers_denied(self):
        for path in ('/etc/passwd','../app.py','.env','id_rsa','secrets/token.txt'):
            with self.subTest(path=path),self.assertRaises(SkillError):
                Registry().validate_input('engineering.code_review',{'request':'review','files':[{'path':path,'content':'text'}]})

class EngineeringExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_no_shell_or_data_persistence(self):
        with tempfile.TemporaryDirectory(prefix='harmonika-engineering-') as td:
            base=Path(td).resolve();project=base/'project';project.mkdir()
            executor=SkillExecutor(base/'state',python=sys.executable)
            ctx=ExecutionContext('local:test-engineering',str(project),frozenset(['engineering.code_review']),False,'tui')
            result=await executor.execute('engineering.code_review',{'request':'Review kode', 'files':[{'path':'app.py','content':'# PRIVATE_EVIDENCE_MARKER_7821\nprint("hello")'}]},ctx)
            self.assertIn(result['status'],('completed','degraded'),result)
            self.assertEqual(result['result']['operation'],'engineering.code_review')
            self.assertFalse(result['result']['method']['code_executed'])
            self.assertNotIn(b'PRIVATE_EVIDENCE_MARKER_7821',executor.store.path.read_bytes())
    async def test_missing_evidence_is_clarification(self):
        with tempfile.TemporaryDirectory(prefix='harmonika-engineering-') as td:
            base=Path(td).resolve();project=base/'project';project.mkdir()
            executor=SkillExecutor(base/'state',python=sys.executable)
            ctx=ExecutionContext('local:test-engineering',str(project),frozenset(['engineering.debug']),False,'tui')
            result=await executor.execute('engineering.debug',{'request':'Tolong debug masalah ini'},ctx)
            self.assertEqual(result['status'],'clarification_required',result)
    async def test_permission_checked_before_dispatch(self):
        with tempfile.TemporaryDirectory(prefix='harmonika-engineering-') as td:
            base=Path(td).resolve();project=base/'project';project.mkdir()
            executor=SkillExecutor(base/'state',python=sys.executable)
            ctx=ExecutionContext('local:test-engineering',str(project),frozenset(),False,'tui')
            with self.assertRaises(SkillError):await executor.execute('engineering.debug',{'request':'Debug'},ctx)

if __name__=='__main__':unittest.main()
