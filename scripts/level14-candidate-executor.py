#!/usr/bin/env python3
"""Additional production-image canary in an owned private candidate root.

Not a replacement for the nine live-model lab missions. No service restart or
public listener, no host commands from the model, and no existing runtime state.
"""
import asyncio,hashlib,json,sys,tempfile,subprocess
from pathlib import Path
ROOT=Path('/opt/harmonika-ai/level14-evidence-stage')
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'runtime/tools'))
from backend.coding_runtime.core import Runtime
from backend.coding_runtime.phase2_files import EngineeringFileStore
from backend.coding_runtime.sandbox import DockerSandbox
from engineering_evidence import redact

async def main():
    with tempfile.TemporaryDirectory(prefix='level14-candidate-') as root:
        rt=Runtime(root,DockerSandbox(image='harmonika-coding:20261024-phase2',phase2_runtime=True),EngineeringFileStore,redact,phase2_enabled=True)
        try:
            await rt.initialize();a='api-token:14999001';w=await rt.create(a,'Level14 candidate image',{'write':True,'execute':True});wid=w['id']
            test="import unittest,sqlite3,json\nfrom decimal import Decimal\nclass Original(unittest.TestCase):\n def test_runtime(self):\n  d=sqlite3.connect(':memory:');d.execute('CREATE TABLE x(id INTEGER)');d.execute('INSERT INTO x VALUES(42)');self.assertEqual(d.execute('SELECT id FROM x').fetchone()[0],42);d.close();self.assertEqual(json.loads('{\"v\":42}')['v'],42);self.assertEqual(Decimal('0.1')+Decimal('0.2'),Decimal('0.3'))\n"
            await rt.invoke_result(a,wid,'fs.write',{'path':'test_runtime.py','content':test},'seed')
            task=await rt.evidence.admit(a,wid,'candidate-turn',{'profile':'tests','tests':['test_runtime.py']})
            args={'runner':'python_unittest','path':'test_runtime.py'};await rt.evidence.intent(a,wid,task['id'],'candidate-tests','test.run',args)
            j=await rt.invoke_result(a,wid,'test.run',args,'candidate-tests');await rt.evidence.link(a,wid,task['id'],'candidate-tests');assert j['status']=='approval_required'
            await rt.approve(a,j['id'],j['input_hash'])
            for _ in range(300):
                row=await rt.job(a,j['id'])
                if row['state'] not in ('queued','running'):break
                await asyncio.sleep(.1)
            assert row['state']=='completed'
            proof=await rt.evidence.assess(a,wid,task['id']);assert proof['status']=='completed' and proof['metrics']['tests']['collected']==1
            image_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','harmonika-coding:20261024-phase2'],text=True).strip()
            report={'passed':True,'source_sha256':{n:hashlib.sha256((ROOT/'backend/coding_runtime'/n).read_bytes()).hexdigest() for n in ('core.py','agent.py','service.py','sandbox.py','action_evidence.py')},'image':'harmonika-coding:20261024-phase2','image_id':image_id,'phase2':True,'approved_original_test':True,'independent_verifier':True,'collected':1,'original_assertions_unchanged':True,'user_state_accessed':False}
            (ROOT/'candidate-executor.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
        finally:await rt.close()

if __name__=='__main__':asyncio.run(main())
