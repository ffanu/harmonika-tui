import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class Activation(unittest.TestCase):
    def test_owner_private_adapter_schema_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            owner=Path(directory)
            prefix="PRIVATE_VALUE='synthetic-preserved'\ndef unrelated():\n    return 42\n"
            spec={'name':'qa_matrix','description':'old','inputSchema':{'type':'object','properties':{'url':{'type':'string'}},'required':['url']}}
            (owner/'mcp_server.py').write_text(prefix+'def tool_qa_matrix(args):\n    script="qa_matrix.cjs"\n    return script\nTOOLS=[\n    '+repr(spec)+',\n]\n')
            cmd=[sys.executable,str(ROOT/'scripts/activate-mockup-quality.py'),'--owner',directory]
            first=subprocess.run(cmd,capture_output=True,text=True,check=True)
            self.assertIn("'updated_files': 3",first.stdout)
            source=(owner/'mcp_server.py').read_text();self.assertTrue(source.startswith(prefix));namespace={};exec(source,namespace)
            self.assertEqual(namespace['unrelated'](),42)
            self.assertIn('frames',namespace['TOOLS'][0]['inputSchema']['properties'])
            for name in ['mockup_quality.py','qa_matrix.cjs']:self.assertEqual((owner/name).stat().st_mode&0o777,0o600)
            second=subprocess.run(cmd,capture_output=True,text=True,check=True)
            self.assertIn("'updated_files': 0",second.stdout)

    def test_unknown_adapter_stays_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'mcp_server.py';data=b'def tool_qa_matrix(args):\n    return 7\n';p.write_bytes(data)
            r=subprocess.run([sys.executable,str(ROOT/'scripts/activate-mockup-quality.py'),'--owner',directory],capture_output=True)
            self.assertNotEqual(r.returncode,0);self.assertEqual(p.read_bytes(),data)

if __name__=='__main__':unittest.main()
